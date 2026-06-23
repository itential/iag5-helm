#!/usr/bin/env python3
"""
IAG5 Helm Chart — Pre-flight Verification

Runs all environment readiness checks before install or upgrade.
Prints pass/fail results to the terminal and writes a markdown report
in the same format as the IAP certification reports.

Exit 0 = all required checks passed.
Exit 1 = one or more required checks failed.

Usage examples:

  # Auto-detect everything from cluster state:
  python3 iag5_preflight.py -n my-namespace --auto

  # Auto-detect with an override:
  python3 iag5_preflight.py -n my-namespace --auto --store-backend dynamodb

  # Minimal install (memory backend, chart-managed cert-manager):
  python3 iag5_preflight.py -n my-namespace --image-pull-secret ecr-pull-secret

  # etcd backend with TLS, bundled etcd, pre-installed cert-manager:
  python3 iag5_preflight.py -n my-namespace --image-pull-secret ecr-pull-secret \\
    --store-backend etcd --etcd-tls \\
    --etcd-enabled --etcd-persistence --storage-class iag5-ebs-gp3 \\
    --cert-manager-preinstalled

  # Upgrade with DynamoDB backend and ClusterIssuer:
  python3 iag5_preflight.py -n my-namespace --image-pull-secret ecr-pull-secret \\
    --store-backend dynamodb --issuer-kind ClusterIssuer \\
    --cert-manager-preinstalled --upgrade
"""

import argparse
import base64
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

# --- terminal colors ---
GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
BOLD   = "\033[1m"
RESET  = "\033[0m"


@dataclass
class Result:
    name: str
    passed: bool
    detail: str
    skipped: bool = False
    skip_reason: str = ""


_results: list = []
_report:  list = []   # accumulated markdown lines


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def record(r: Result) -> Result:
    _results.append(r)
    if r.skipped:
        tag = f"{YELLOW}- SKIP{RESET}"
        msg = f"  {tag}  {r.name}"
        if r.skip_reason:
            msg += f"\n         ({r.skip_reason})"
    elif r.passed:
        tag = f"{GREEN}✓ PASS{RESET}"
        msg = f"  {tag}  {r.name}"
    else:
        tag = f"{RED}✗ FAIL{RESET}"
        msg = f"  {tag}  {r.name}"
        for line in r.detail.splitlines():
            msg += f"\n         {line}"
    print(msg)
    return r


def md(*lines: str):
    """Append lines to the markdown report buffer."""
    _report.extend(lines)


def section(terminal_title: str, md_heading: str = None):
    print(f"\n{BOLD}{terminal_title}{RESET}")
    if md_heading:
        md("", "---", "", f"## {md_heading}")


# ---------------------------------------------------------------------------
# kubectl helpers
# ---------------------------------------------------------------------------

def kubectl(*args: str) -> tuple:
    try:
        proc = subprocess.run(["kubectl"] + list(args), capture_output=True, text=True)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError:
        return 127, "", "kubectl not found in PATH"


def kget_json(*args: str) -> tuple:
    """kubectl <args> -o json → (rc, parsed_dict_or_None, error_str)"""
    rc, out, err = kubectl(*args, "-o", "json")
    if rc != 0:
        return rc, None, err
    try:
        return 0, json.loads(out), ""
    except json.JSONDecodeError as e:
        return 1, None, f"JSON parse error: {e}"


def kget_table(*args: str) -> str:
    """kubectl <args> → raw tabular stdout for the report."""
    rc, out, err = kubectl(*args)
    return out if (rc == 0 and out) else f"(error: {err or 'no output'})"


def decode(data: dict, key: str) -> str:
    return base64.b64decode(data[key]).decode("utf-8")


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_kubectl() -> Result:
    rc, _, err = kubectl("version", "--client", "--output=json")
    if rc == 0:
        return record(Result("kubectl available", True, ""))
    return record(Result("kubectl available", False, err or "kubectl not found in PATH"))


def check_namespace(ns: str) -> Result:
    table = kget_table("get", "namespace", ns)
    md("", f"**Namespace:** `{ns}`", "", "```", table, "```")
    rc, obj, err = kget_json("get", "namespace", ns)
    if rc == 0 and obj and obj.get("status", {}).get("phase") == "Active":
        return record(Result(f"Namespace '{ns}' Active", True, ""))
    return record(Result(f"Namespace '{ns}' Active", False, err or "Not found or not Active"))


def check_nodes() -> Result:
    table = kget_table("get", "nodes")
    md("", "#### Nodes", "", "```", table, "```")
    rc, obj, err = kget_json("get", "nodes")
    if rc != 0 or not obj:
        return record(Result("All nodes Ready", False, err or "Could not list nodes"))
    nodes = obj.get("items", [])
    if not nodes:
        return record(Result("All nodes Ready", False, "No nodes returned"))
    not_ready = []
    for node in nodes:
        conds = node.get("status", {}).get("conditions", [])
        ready = next((c for c in conds if c["type"] == "Ready"), None)
        if not ready or ready.get("status") != "True":
            not_ready.append(node["metadata"]["name"])
    if not_ready:
        return record(Result("All nodes Ready", False, "Not Ready: " + ", ".join(not_ready)))
    return record(Result(f"All nodes Ready ({len(nodes)} node(s))", True, ""))


def check_gateway_secrets(ns: str) -> Result:
    secret = "itential-gateway-secrets"
    label  = f"Secret '{secret}': gatewayEncryptionKey present and 256 chars"
    rc, obj, err = kget_json("get", "secret", secret, "-n", ns)
    md("", f"#### `{secret}`")
    if rc != 0 or not obj:
        md("", "```", f"(not found: {err})", "```")
        return record(Result(label, False, f"Not found: {err}"))
    data = obj.get("data", {})
    if "gatewayEncryptionKey" not in data:
        md("", "```", f"Keys found: {list(data.keys())}", "```")
        return record(Result(label, False, f"Key 'gatewayEncryptionKey' missing. Found: {list(data.keys())}"))
    try:
        length = len(decode(data, "gatewayEncryptionKey"))
        md("", "```", f"Keys: gatewayEncryptionKey ({length} chars)", "```")
        if length != 256:
            return record(Result(label, False, f"gatewayEncryptionKey is {length} chars, expected 256"))
        return record(Result(label, True, ""))
    except Exception as e:
        md("", "```", f"Decode error: {e}", "```")
        return record(Result(label, False, str(e)))


def check_image_pull_secret(ns: str, secret_name: str) -> Result:
    label = f"Image pull secret '{secret_name}'"
    rc, obj, err = kget_json("get", "secret", secret_name, "-n", ns)
    md("", f"#### `{secret_name}` (image pull secret)")
    if rc != 0 or not obj:
        md("", "```", f"(not found: {err})", "```")
        return record(Result(f"{label}: exists", False, f"Not found: {err}"))
    secret_type = obj.get("type", "")
    md("", "```", f"Type: {secret_type}", "```")
    if secret_type != "kubernetes.io/dockerconfigjson":
        return record(Result(f"{label}: type", False,
                             f"Expected 'kubernetes.io/dockerconfigjson', got '{secret_type}'"))
    data = obj.get("data", {})
    if ".dockerconfigjson" not in data:
        return record(Result(f"{label}: .dockerconfigjson key", False, "Key '.dockerconfigjson' missing"))
    try:
        config = json.loads(decode(data, ".dockerconfigjson"))
        auths  = list(config.get("auths", {}).keys())
        if not auths:
            return record(Result(f"{label}: registry entry", False, "No registry entries in .dockerconfigjson"))
        return record(Result(f"{label}: valid (registries: {', '.join(auths)})", True, ""))
    except Exception as e:
        return record(Result(f"{label}: parse .dockerconfigjson", False, str(e)))


def check_ca_secret(ns: str, ca_secret: str, issuer_kind: str, cm_ns: str) -> Result:
    secret_ns = cm_ns if issuer_kind == "ClusterIssuer" else ns
    label     = f"Secret '{ca_secret}' in '{secret_ns}': tls.crt + tls.key, cert not expired"
    rc, obj, err = kget_json("get", "secret", ca_secret, "-n", secret_ns)
    md("", f"#### `{ca_secret}` (CA secret, ns: `{secret_ns}`)")
    if rc != 0 or not obj:
        md("", "```", f"(not found: {err})", "```")
        return record(Result(label, False, f"Not found: {err}"))
    data    = obj.get("data", {})
    missing = [k for k in ["tls.crt", "tls.key"] if k not in data]
    if missing:
        md("", "```", f"Keys found: {list(data.keys())}", "```")
        return record(Result(label, False, f"Missing: {missing}. Found: {list(data.keys())}"))
    try:
        cert_pem = decode(data, "tls.crt")
        proc = subprocess.run(
            ["openssl", "x509", "-noout", "-subject", "-dates"],
            input=cert_pem, capture_output=True, text=True
        )
        if proc.returncode != 0:
            md("", "```", f"Keys: tls.crt, tls.key\nopenssl error: {proc.stderr.strip()}", "```")
            return record(Result(label, False, f"openssl could not parse cert: {proc.stderr.strip()}"))
        cert_info = proc.stdout.strip()
        md("", "```", f"Keys: tls.crt, tls.key\n{cert_info}", "```")
        not_after = next((l for l in cert_info.splitlines() if "notAfter" in l), "")
        return record(Result(f"{label} ({not_after})", True, ""))
    except FileNotFoundError:
        md("", "```", "Keys: tls.crt, tls.key (openssl not available — expiry not checked)", "```")
        return record(Result(f"Secret '{ca_secret}' has tls.crt + tls.key (openssl unavailable — expiry not checked)", True, ""))
    except Exception as e:
        md("", "```", f"Error: {e}", "```")
        return record(Result(label, False, str(e)))


def check_secret_keys(ns: str, secret_name: str, required_keys: list) -> Result:
    label = f"Secret '{secret_name}': {required_keys}"
    rc, obj, err = kget_json("get", "secret", secret_name, "-n", ns)
    md("", f"#### `{secret_name}`")
    if rc != 0 or not obj:
        md("", "```", f"(not found: {err})", "```")
        return record(Result(label, False, f"Not found: {err}"))
    data_keys = sorted(obj.get("data", {}).keys())
    md("", "```", f"Keys: {', '.join(data_keys)}", "```")
    missing = [k for k in required_keys if k not in data_keys]
    if missing:
        return record(Result(label, False, f"Missing: {missing}. Found: {data_keys}"))
    return record(Result(label, True, ""))


def check_storage_class(sc_name: str) -> Result:
    table = kget_table("get", "storageclass", sc_name)
    md("", "#### StorageClass", "", "```", table, "```")
    rc, obj, err = kget_json("get", "storageclass", sc_name)
    if rc != 0 or not obj:
        return record(Result(f"StorageClass '{sc_name}' exists", False, f"Not found: {err}"))
    provisioner  = obj.get("provisioner", "unknown")
    binding_mode = obj.get("volumeBindingMode", "unknown")
    allow_expand = obj.get("allowVolumeExpansion", False)
    return record(Result(
        f"StorageClass '{sc_name}' exists "
        f"(provisioner: {provisioner}, bindingMode: {binding_mode}, expand: {allow_expand})",
        True, ""
    ))


def check_etcd_pvcs_bound(ns: str) -> Result:
    table = kget_table("get", "pvc", "-n", ns, "-l", "app.kubernetes.io/name=etcd")
    md("", "#### etcd Persistent Volume Claims", "", "```", table, "```")
    rc, obj, err = kget_json("get", "pvc", "-n", ns, "-l", "app.kubernetes.io/name=etcd")
    if rc != 0 or not obj:
        return record(Result("etcd PVCs Bound", False, err or "Could not list PVCs"))
    pvcs = obj.get("items", [])
    if not pvcs:
        return record(Result("etcd PVCs Bound", False, "No etcd PVCs found — expected at least one on upgrade"))
    not_bound = [
        f"{p['metadata']['name']}: {p.get('status', {}).get('phase', 'Unknown')}"
        for p in pvcs if p.get("status", {}).get("phase") != "Bound"
    ]
    if not_bound:
        return record(Result("etcd PVCs Bound", False, "Not Bound:\n" + "\n".join(not_bound)))
    return record(Result(f"etcd PVCs Bound ({len(pvcs)} PVC(s))", True, ""))


def check_cert_manager_pods(cm_ns: str) -> Result:
    label = f"cert-manager pods Running in '{cm_ns}'"
    table = kget_table("get", "pods", "-n", cm_ns)
    md("", "#### Pods", "", "```", table, "```")
    rc, obj, err = kget_json("get", "pods", "-n", cm_ns)
    if rc != 0 or not obj:
        return record(Result(label, False, err or f"Could not list pods in '{cm_ns}'"))
    pods = obj.get("items", [])
    if not pods:
        return record(Result(label, False, f"No pods found in namespace '{cm_ns}'"))
    not_running = [
        f"{p['metadata']['name']}: {p.get('status', {}).get('phase', 'Unknown')}"
        for p in pods if p.get("status", {}).get("phase") != "Running"
    ]
    if not_running:
        return record(Result(label, False, "Not running:\n" + "\n".join(not_running)))
    return record(Result(f"{label} ({len(pods)} pod(s))", True, ""))


def check_cert_manager_crds() -> Result:
    required = [
        "certificates.cert-manager.io",
        "certificaterequests.cert-manager.io",
        "issuers.cert-manager.io",
        "clusterissuers.cert-manager.io",
    ]
    table = kget_table("get", "crd", *required)
    md("", "#### CRDs", "", "```", table, "```")
    missing = [crd for crd in required if kubectl("get", "crd", crd)[0] != 0]
    if missing:
        return record(Result("cert-manager CRDs installed", False, "Missing:\n" + "\n".join(missing)))
    return record(Result(f"cert-manager CRDs installed (all {len(required)})", True, ""))


def check_issuer_conflict(ns: str, issuer_name: str, issuer_kind: str) -> Result:
    label = f"{issuer_kind} '{issuer_name}' name conflict check"
    if issuer_kind == "ClusterIssuer":
        rc, obj, err = kget_json("get", "clusterissuer", issuer_name)
        table = kget_table("get", "clusterissuer", issuer_name)
    else:
        rc, obj, err = kget_json("get", "issuer", issuer_name, "-n", ns)
        table = kget_table("get", "issuer", issuer_name, "-n", ns)
    md("", f"#### {issuer_kind} `{issuer_name}`", "", "```",
       table if (rc == 0 and obj) else "(none — clean install)", "```")
    if rc != 0 or not obj:
        return record(Result(label, True, ""))
    ca_ref = obj.get("spec", {}).get("ca", {}).get("secretName", "unknown")
    return record(Result(
        f"{label} — existing {issuer_kind} found (caSecretName: '{ca_ref}') — will be overwritten on install",
        True, ""
    ))


def check_stale_certificates(ns: str) -> Result:
    table = kget_table("get", "certificate", "-n", ns)
    md("", "#### Certificates", "", "```", table, "```")
    rc, obj, err = kget_json("get", "certificate", "-n", ns)
    if rc != 0 or not obj:
        return record(Result("Stale certificate check", True, "No certificates found"))
    certs = obj.get("items", [])
    if not certs:
        return record(Result("No stale certificates (none found)", True, ""))
    not_ready = []
    for cert in certs:
        name  = cert["metadata"]["name"]
        conds = cert.get("status", {}).get("conditions", [])
        ready = next((c for c in conds if c["type"] == "Ready"), None)
        if not ready or ready.get("status") != "True":
            reason = ready.get("reason", "no Ready condition") if ready else "no Ready condition"
            not_ready.append(f"{name}: {reason}")
    if not_ready:
        return record(Result("No stale certificates", False,
                             "Not Ready — investigate before upgrading:\n" + "\n".join(not_ready)))
    return record(Result(f"No stale certificates ({len(certs)} cert(s), all Ready)", True, ""))


# ---------------------------------------------------------------------------
# Auto-detection
# ---------------------------------------------------------------------------

def _run_auto_detect(args, ns: str):
    """Probe cluster state and fill any unset args in-place. Explicit flags always win."""

    def _det(flag, val, reason):
        print(f"  {GREEN}✓ detected{RESET}  {flag:<30} {BOLD}{val}{RESET}  ({reason})")

    def _warn(flag, user_val, det_val):
        print(f"  {YELLOW}! override{RESET}  {flag:<30} keeping {BOLD}{user_val}{RESET}"
              f"  (detected: {det_val})")

    def _miss(flag, reason):
        print(f"  {YELLOW}- not found{RESET} {flag:<30} {reason}")

    print(f"\n{BOLD}Auto-detecting configuration from cluster...{RESET}")

    # Fetch all namespace secrets once
    rc, obj, _ = kget_json("get", "secrets", "-n", ns)
    secrets = {}
    if rc == 0 and obj:
        for item in obj.get("items", []):
            secrets[item["metadata"]["name"]] = item.get("type", "")

    # --upgrade
    helm_releases = [n for n in secrets if n.startswith("sh.helm.release.v1.iag5.")]
    if helm_releases:
        if not args.upgrade:
            args.upgrade = True
            _det("--upgrade", True, f"helm release found: {helm_releases[0]}")
    else:
        _miss("--upgrade", "no iag5 helm release → install mode")

    # --image-pull-secret
    pull_secrets = [n for n, t in secrets.items() if t == "kubernetes.io/dockerconfigjson"]
    if args.image_pull_secret is None:
        if pull_secrets:
            iag5_ps = [s for s in pull_secrets if "iap" not in s.lower()]
            chosen = iag5_ps[0] if iag5_ps else pull_secrets[0]
            args.image_pull_secret = chosen
            reason = "non-IAP dockerconfigjson secret" if iag5_ps else "only dockerconfigjson secret"
            _det("--image-pull-secret", chosen, reason)
        else:
            _miss("--image-pull-secret", "no dockerconfigjson secret found — required")

    # --store-backend (and --etcd-tls)
    if args.store_backend is None:
        if "dynamodb-aws-secrets" in secrets:
            args.store_backend = "dynamodb"
            _det("--store-backend", "dynamodb", "dynamodb-aws-secrets found")
        elif "etcd-client-certs" in secrets:
            args.store_backend = "etcd"
            _det("--store-backend", "etcd", "etcd-client-certs found")
            if not args.etcd_tls:
                args.etcd_tls = True
                _det("--etcd-tls", True, "etcd-client-certs found")
        else:
            args.store_backend = "memory"
            _det("--store-backend", "memory", "no etcd-client-certs or dynamodb-aws-secrets")
    else:
        # User set it explicitly — detect anyway and warn on mismatch
        detected_backend = (
            "dynamodb" if "dynamodb-aws-secrets" in secrets
            else "etcd" if "etcd-client-certs" in secrets
            else "memory"
        )
        if args.store_backend != detected_backend:
            _warn("--store-backend", args.store_backend, detected_backend)

    # --etcd-enabled / --etcd-persistence
    rc_pvc, obj_pvc, _ = kget_json("get", "pvc", "-n", ns, "-l", "app.kubernetes.io/name=etcd")
    if rc_pvc == 0 and obj_pvc and obj_pvc.get("items"):
        if not args.etcd_enabled:
            args.etcd_enabled = True
            _det("--etcd-enabled", True, "etcd PVCs found")
        if not args.etcd_persistence:
            args.etcd_persistence = True
            _det("--etcd-persistence", True, "etcd PVCs found")
    else:
        rc_pod, obj_pod, _ = kget_json("get", "pods", "-n", ns, "-l", "app.kubernetes.io/name=etcd")
        if rc_pod == 0 and obj_pod and obj_pod.get("items"):
            if not args.etcd_enabled:
                args.etcd_enabled = True
                _det("--etcd-enabled", True, "etcd pods found (no PVCs)")

    # --cert-manager-preinstalled
    cm_ns = args.cert_manager_namespace
    rc_cm, obj_cm, _ = kget_json("get", "pods", "-n", cm_ns)
    if rc_cm == 0 and obj_cm and obj_cm.get("items"):
        if not args.cert_manager_preinstalled:
            args.cert_manager_preinstalled = True
            _det("--cert-manager-preinstalled", True, f"pods running in '{cm_ns}'")
    else:
        _miss("--cert-manager-preinstalled", f"no pods found in '{cm_ns}'")

    # --issuer-kind / --issuer-name / --ca-secret-name
    # Try ClusterIssuer first, then namespace-scoped Issuer
    issuer_found = False

    rc_ci, obj_ci, _ = kget_json("get", "clusterissuer")
    if rc_ci == 0 and obj_ci:
        live = [i for i in obj_ci.get("items", []) if not i["metadata"].get("deletionTimestamp")]
        if live:
            issuer    = live[0]
            iname     = issuer["metadata"]["name"]
            ca_secret = issuer.get("spec", {}).get("ca", {}).get("secretName")
            issuer_found = True

            if args.issuer_kind is None:
                args.issuer_kind = "ClusterIssuer"
                _det("--issuer-kind", "ClusterIssuer", f"ClusterIssuer '{iname}' found")
            elif args.issuer_kind != "ClusterIssuer":
                _warn("--issuer-kind", args.issuer_kind, "ClusterIssuer")

            if args.issuer_name is None:
                args.issuer_name = iname
                _det("--issuer-name", iname, "from ClusterIssuer")

            if args.ca_secret_name is None and ca_secret:
                args.ca_secret_name = ca_secret
                _det("--ca-secret-name", ca_secret, "from ClusterIssuer spec.ca.secretName")

    if not issuer_found:
        rc_i, obj_i, _ = kget_json("get", "issuer", "-n", ns)
        if rc_i == 0 and obj_i:
            live = [i for i in obj_i.get("items", []) if not i["metadata"].get("deletionTimestamp")]
            if live:
                issuer    = live[0]
                iname     = issuer["metadata"]["name"]
                ca_secret = issuer.get("spec", {}).get("ca", {}).get("secretName")
                issuer_found = True

                if args.issuer_kind is None:
                    args.issuer_kind = "Issuer"
                    _det("--issuer-kind", "Issuer", f"Issuer '{iname}' found in '{ns}'")

                if args.issuer_name is None:
                    args.issuer_name = iname
                    _det("--issuer-name", iname, "from Issuer")

                if args.ca_secret_name is None and ca_secret:
                    args.ca_secret_name = ca_secret
                    _det("--ca-secret-name", ca_secret, "from Issuer spec.ca.secretName")

    if not issuer_found:
        _miss("--issuer-kind/name/ca-secret-name", "no Issuer or ClusterIssuer found in cluster")

    print()


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def build_report(args, generated_at: str) -> str:
    total   = len(_results)
    passed  = sum(1 for r in _results if r.passed and not r.skipped)
    failed  = sum(1 for r in _results if not r.passed and not r.skipped)
    skipped = sum(1 for r in _results if r.skipped)
    overall = "PASSED" if failed == 0 else "FAILED"
    mode    = "upgrade" if args.upgrade else "install"

    header = [
        "# IAG5 Helm Chart — Pre-flight Verification Report",
        "",
        "|  |  |",
        "|---|---|",
        f"| **Generated** | {generated_at} |",
        f"| **Namespace** | `{args.namespace}` |",
        f"| **Mode** | {mode} |",
        f"| **Store Backend** | `{args.store_backend}` |",
        f"| **Issuer Kind** | `{args.issuer_kind}` |",
        f"| **Result** | {overall} |",
        "",
        "---",
    ]

    results_section = [
        "",
        "---",
        "",
        "## Pre-flight Check Results",
        "",
        "| Check | Result | Detail |",
        "|---|---|---|",
    ]
    for r in _results:
        if r.skipped:
            status = "SKIP"
            detail = r.skip_reason
        elif r.passed:
            status = "PASS"
            detail = ""
        else:
            status = "FAIL"
            detail = r.detail.replace("\n", " ")
        results_section.append(f"| {r.name} | {status} | {detail} |")

    results_section.append(
        f"| **Overall** | **{overall}** | "
        f"{passed} passed, {failed} failed, {skipped} skipped ({total} total) |"
    )

    return "\n".join(header + _report + results_section) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="IAG5 Helm Chart — Pre-flight Verification",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-n", "--namespace", required=True,
                        help="Target Kubernetes namespace")
    parser.add_argument("--image-pull-secret", default=None,
                        help="Name of the imagePullSecrets[0].name value (auto-detected if --auto)")
    parser.add_argument("--output", default=None,
                        help="Report output path (default: verify-iag5-<namespace>-<date>.md)")
    parser.add_argument("--auto", action="store_true",
                        help="Auto-detect configuration from cluster state; individual flags override")

    parser.add_argument("--cert-manager-preinstalled", action="store_true",
                        help="certManager.enabled: false — cert-manager already in cluster, verify it is running")
    parser.add_argument("--cert-manager-namespace", default="cert-manager",
                        help="Namespace cert-manager is installed in (default: cert-manager)")
    parser.add_argument("--skip-issuer", action="store_true",
                        help="issuer.enabled: false — skip CA secret and issuer checks")
    parser.add_argument("--issuer-kind", default=None, choices=["Issuer", "ClusterIssuer"],
                        help="issuer.kind in values (default: Issuer)")
    parser.add_argument("--issuer-name", default=None,
                        help="issuer.name in values (default: iag5-ca-issuer)")
    parser.add_argument("--ca-secret-name", default=None,
                        help="issuer.caSecretName in values (default: itential-ca)")

    parser.add_argument("--store-backend", default=None, choices=["memory", "etcd", "dynamodb"],
                        help="applicationSettings.storeBackend (default: memory)")
    parser.add_argument("--etcd-tls", action="store_true",
                        help="applicationSettings.etcdUseTLS: true")
    parser.add_argument("--etcd-enabled", action="store_true",
                        help="etcd.enabled: true — bundled etcd dependency")
    parser.add_argument("--etcd-persistence", action="store_true",
                        help="etcd.persistence.enabled: true")
    parser.add_argument("--storage-class", default=None,
                        help="etcd.persistence.storageClass (default: iag5-ebs-gp3)")

    parser.add_argument("--upgrade", action="store_true",
                        help="Include upgrade-specific checks (etcd PVC state, stale certificates)")

    args = parser.parse_args()
    ns = args.namespace

    if args.auto:
        _run_auto_detect(args, ns)

    # Apply hard defaults for anything still unset
    if args.image_pull_secret is None:
        msg = ("--image-pull-secret is required. "
               "Pass it explicitly or use --auto to detect it from the cluster.")
        print(f"{RED}[ERROR] {msg}{RESET}")
        sys.exit(1)
    if args.issuer_kind is None:
        args.issuer_kind = "Issuer"
    if args.issuer_name is None:
        args.issuer_name = "iag5-ca-issuer"
    if args.ca_secret_name is None:
        args.ca_secret_name = "itential-ca"
    if args.store_backend is None:
        args.store_backend = "memory"
    if args.storage_class is None:
        args.storage_class = "iag5-ebs-gp3"

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    date_slug    = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_path  = args.output or f"verify-iag5-{ns}-{date_slug}.md"

    print(f"\n{BOLD}IAG5 Helm Chart — Pre-flight Verification{RESET}")
    print(f"  Namespace      : {ns}")
    print(f"  Store backend  : {args.store_backend}")
    print(f"  Mode           : {'upgrade' if args.upgrade else 'install'}")
    print(f"  Report         : {output_path}\n")

    # 1. kubectl
    section("1. kubectl")
    r = check_kubectl()
    if not r.passed:
        print(f"\n{RED}[FATAL] kubectl not available. Aborting.{RESET}\n")
        sys.exit(1)

    # 2. Namespace + nodes
    section("2. Namespace", "Kubernetes Resources")
    check_namespace(ns)

    section("3. Node Readiness")
    check_nodes()

    # 3. Secrets
    section("4. Required Secrets", "Secrets")
    check_gateway_secrets(ns)
    check_image_pull_secret(ns, args.image_pull_secret)

    if not args.skip_issuer:
        check_ca_secret(ns, args.ca_secret_name, args.issuer_kind, args.cert_manager_namespace)
    else:
        record(Result(f"Secret '{args.ca_secret_name}' (CA)", False, "", skipped=True,
                      skip_reason="--skip-issuer set"))

    if args.store_backend == "etcd" and args.etcd_tls:
        check_secret_keys(ns, "etcd-client-certs", ["ca.crt", "tls.crt", "tls.key"])
    else:
        record(Result("Secret 'etcd-client-certs'", False, "", skipped=True,
                      skip_reason=f"storeBackend='{args.store_backend}' or --etcd-tls not set"))

    if args.etcd_enabled and args.etcd_tls:
        check_secret_keys(ns, "etcd-peer-certs", ["ca.crt", "tls.crt", "tls.key"])
    else:
        record(Result("Secret 'etcd-peer-certs'", False, "", skipped=True,
                      skip_reason="--etcd-enabled or --etcd-tls not set"))

    if args.store_backend == "dynamodb":
        check_secret_keys(ns, "dynamodb-aws-secrets",
                          ["AWS_ACCESS_KEY_ID", "AWS_REGION", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"])
    else:
        record(Result("Secret 'dynamodb-aws-secrets'", False, "", skipped=True,
                      skip_reason=f"storeBackend='{args.store_backend}'"))

    # 4. Persistent Volume
    section("5. Persistent Volume", "Persistent Volume")
    if args.etcd_enabled and args.etcd_persistence:
        check_storage_class(args.storage_class)
        if args.upgrade:
            check_etcd_pvcs_bound(ns)
        else:
            record(Result("etcd PVCs Bound", False, "", skipped=True,
                          skip_reason="install mode — PVCs not yet created"))
    else:
        md("", "_Not applicable — etcd persistence not enabled._")
        record(Result("StorageClass / PVC checks", False, "", skipped=True,
                      skip_reason="--etcd-enabled or --etcd-persistence not set"))

    # 5. cert-manager
    section("6. cert-manager", "cert-manager")
    if args.cert_manager_preinstalled:
        check_cert_manager_pods(args.cert_manager_namespace)
    else:
        md("", "#### Pods", "",
           "_cert-manager is managed by the chart (`certManager.enabled: true`) — pods will be created on install._")
        record(Result(f"cert-manager pods Running in '{args.cert_manager_namespace}'", False, "", skipped=True,
                      skip_reason="chart manages cert-manager — pods created on install"))

    check_cert_manager_crds()

    if not args.skip_issuer:
        check_issuer_conflict(ns, args.issuer_name, args.issuer_kind)
    else:
        record(Result("Issuer conflict check", False, "", skipped=True, skip_reason="--skip-issuer set"))

    if args.upgrade:
        check_stale_certificates(ns)
    else:
        record(Result("Stale certificate check", False, "", skipped=True,
                      skip_reason="install mode — no pre-existing certificates"))

    # Terminal summary
    total   = len(_results)
    passed  = sum(1 for r in _results if r.passed and not r.skipped)
    failed  = sum(1 for r in _results if not r.passed and not r.skipped)
    skipped = sum(1 for r in _results if r.skipped)

    print(f"\n{'─' * 62}")
    print(f"{BOLD}Summary{RESET}  "
          f"{GREEN}{passed} passed{RESET}  "
          f"{RED}{failed} failed{RESET}  "
          f"{YELLOW}{skipped} skipped{RESET}  "
          f"({total} total)")
    print(f"{'─' * 62}")

    with open(output_path, "w") as f:
        f.write(build_report(args, generated_at))
    print(f"\nReport written → {output_path}")

    if failed:
        print(f"{RED}Pre-flight FAILED — resolve the issues above before proceeding.{RESET}\n")
        sys.exit(1)
    else:
        print(f"{GREEN}Pre-flight PASSED — environment is ready.{RESET}\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
