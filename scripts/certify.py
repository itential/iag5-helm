#!/usr/bin/env python3
"""
certify.py

Post-installation certification report for IAG5 (Itential Automation Gateway 5).
Collects cluster state via kubectl, inspects TLS certificates used for mTLS
communication with Gateway Manager, and writes a markdown report suitable for
sharing with customers or archiving as installation documentation.

Since IAG5 exposes only a gRPC port (no HTTP REST API), all checks are kubectl-based:
cluster resources, per-pod process and version inspection, and TLS certificate
analysis from both Kubernetes secrets and pod-mounted volumes.

Requirements:
  Python 3.8+. No third-party packages required.
  kubectl must be configured for the target cluster.
  openssl is used for certificate inspection when available on PATH.

TLS / mTLS checks performed:
  - cert-manager Certificate object status (Ready condition, renewal time, SANs)
  - TLS secret certificate: subject, issuer, SANs, validity window, days remaining
  - CA certificate (itential-ca by default): subject, issuer, validity
  - Certificate chain verification via local openssl verify
  - Per-pod: mounted certificate inspection and chain verification via kubectl exec
  - Gateway Manager connection settings extracted from pod environment

Usage:
  python3 certify.py
  python3 certify.py -n <namespace>
  python3 certify.py -n <namespace> --ca-secret <name>
  python3 certify.py -n <namespace> --tls-secret <name>
  python3 certify.py -n <namespace> --skip-exec
  python3 certify.py -n <namespace> --skip-secrets

If -n / --namespace is not provided the script uses the active kubectl context namespace.

Output:
  iag5-certify-{namespace}-{YYYY-MM-DD}.md written to the current directory.
  If the file cannot be written the report is printed to stdout instead.
"""

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

# Underscore-delimited tokens that mark a value as sensitive when found as whole
# parts of an environment variable key name. Matching on whole parts avoids false
# positives like "bypass" matching "pass".
_SENSITIVE_KEYS = frozenset({
    "password", "passwd", "secret", "token", "credential",
    "apikey", "key", "privatekey", "accesskey", "sessiontoken",
})

# Mount paths for TLS files inside IAG5 pods (server and runner).
_GATEWAY_CERT_PATH = "/etc/ssl/gateway/tls.crt"
_GATEWAY_CA_PATH   = "/etc/ssl/gateway/ca.crt"


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args():
    """
    Parse and return command-line arguments.
    argparse handles validation and exits with a usage message on bad input,
    so no additional error handling is needed here.
    """
    p = argparse.ArgumentParser(
        description="Collect IAG5 state and produce a markdown certification report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--namespace", "-n", metavar="NS",
        help="Kubernetes namespace to query. "
             "Defaults to the active kubectl context namespace.",
    )
    p.add_argument(
        "--ca-secret", metavar="NAME", default="itential-ca",
        help="Name of the Kubernetes secret holding the CA certificate. "
             "Defaults to 'itential-ca'.",
    )
    p.add_argument(
        "--tls-secret", metavar="NAME",
        help="Name of the TLS secret to inspect. When omitted the script discovers "
             "it automatically from cert-manager Certificate objects in the namespace.",
    )
    p.add_argument(
        "--skip-exec", action="store_true",
        help="Skip all kubectl exec commands. Use when exec permissions are restricted. "
             "Cluster resources and TLS secret inspection are still collected.",
    )
    p.add_argument(
        "--skip-secrets", action="store_true",
        help="Omit Kubernetes secret contents and pod environment variables from the report. "
             "cert-manager Certificate metadata, pod process checks, and version info are still collected.",
    )
    return p.parse_args()


# ── Availability checks ───────────────────────────────────────────────────────

def _kubectl_available():
    """
    Return True if kubectl is present on PATH, False otherwise.
    Uses shutil.which so it works on all platforms without spawning a process.
    """
    return shutil.which("kubectl") is not None


def _openssl_available():
    """
    Return True if openssl is present on PATH, False otherwise.
    All certificate inspection degrades gracefully when openssl is absent.
    """
    return shutil.which("openssl") is not None


def _kubectl_context_namespace():
    """
    Return the namespace set in the active kubectl context.
    Falls back to 'default' if the context has no namespace set or if the
    kubectl config command fails for any reason.
    """
    try:
        r = subprocess.run(
            ["kubectl", "config", "view", "--minify", "-o",
             "jsonpath={.contexts[0].context.namespace}"],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() or "default"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return "default"
    except Exception:
        return "default"


# ── kubectl helpers ───────────────────────────────────────────────────────────

def _kubectl_run(args, namespace=None):
    """
    Execute a kubectl command and return its stdout as a string, or None on failure.
    Appends -n <namespace> when namespace is provided. Failures are printed to
    stderr so the operator can diagnose issues without re-running commands manually.
    Never raises — all exceptions return None.
    """
    cmd = ["kubectl"] + args + (["-n", namespace] if namespace else [])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return r.stdout.strip() or None
        detail = r.stderr.strip() or "no error detail"
        print(
            f"  [kubectl] Command failed (exit {r.returncode}): {' '.join(cmd)}\n"
            f"            {detail}",
            file=sys.stderr,
        )
        return None
    except subprocess.TimeoutExpired:
        print(f"  [kubectl] Timed out after 30s: {' '.join(cmd)}", file=sys.stderr)
        return None
    except (FileNotFoundError, OSError) as exc:
        print(f"  [kubectl] OS error running kubectl: {exc}", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"  [kubectl] Unexpected error: {exc}", file=sys.stderr)
        return None


def _kubectl_exec(pod, namespace, command):
    """
    Run a command inside a pod via kubectl exec and return its stdout, or None on failure.
    Used for per-pod checks: version, process list, environment, and TLS inspection.
    Failures are printed to stderr but never raised. Callers treat None as 'not available'.
    """
    cmd = ["kubectl", "exec", pod, "-n", namespace, "--"] + command
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return r.stdout.strip() or None
        detail = r.stderr.strip() or "no error detail"
        print(
            f"  [kubectl exec] Failed (exit {r.returncode}): {' '.join(cmd)}\n"
            f"                 {detail}",
            file=sys.stderr,
        )
        return None
    except subprocess.TimeoutExpired:
        print(f"  [kubectl exec] Timed out: {' '.join(cmd)}", file=sys.stderr)
        return None
    except (FileNotFoundError, OSError) as exc:
        print(f"  [kubectl exec] OS error: {exc}", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"  [kubectl exec] Unexpected error: {exc}", file=sys.stderr)
        return None


# ── Kubernetes secret helpers ─────────────────────────────────────────────────

def _get_secret_data(secret_name, key, namespace):
    """
    Retrieve a single key from a Kubernetes secret and return its base64-decoded value.
    Fetches the full secret as JSON and decodes in Python to avoid jsonpath escaping
    issues with keys that contain dots (e.g. 'tls.crt').
    Returns None if the secret or key does not exist, or on any error.
    """
    raw = _kubectl_run(["get", "secret", secret_name, "-o", "json"], namespace=namespace)
    if not raw:
        return None
    try:
        encoded = json.loads(raw).get("data", {}).get(key)
        if not encoded:
            return None
        return base64.b64decode(encoded).decode("utf-8", errors="replace")
    except (json.JSONDecodeError, ValueError, Exception):
        return None


# ── Certificate inspection ────────────────────────────────────────────────────

def _parse_cert_pem(pem):
    """
    Parse a PEM-encoded certificate using the local openssl binary.
    Returns a dict with subject, issuer, not_before, not_after, sans, and days_left.
    Returns a dict with '_error' on any failure so callers can render an error note
    without crashing the report.
    """
    if not _openssl_available():
        return {
            "_error": "openssl not found on PATH — install openssl to enable certificate inspection"
        }
    if not pem or not pem.strip():
        return {"_error": "empty certificate data"}
    try:
        r = subprocess.run(
            ["openssl", "x509", "-text", "-noout"],
            input=pem, capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return {"_error": r.stderr.strip() or f"openssl x509 exited {r.returncode}"}
        text = r.stdout

        def _grep(pattern):
            """Extract the first capture group from a regex, returning '—' if not found."""
            m = re.search(pattern, text)
            return m.group(1).strip() if m else "—"

        # SANs appear on the continuation line after "Subject Alternative Name:".
        san = "—"
        san_m = re.search(r"Subject Alternative Name:\s*\n([ \t]+\S[^\n]*)", text)
        if san_m:
            san = san_m.group(1).strip()

        # Calculate days remaining until expiry by parsing the enddate subcommand output.
        days_left = None
        try:
            r2 = subprocess.run(
                ["openssl", "x509", "-enddate", "-noout"],
                input=pem, capture_output=True, text=True, timeout=10,
            )
            if r2.returncode == 0:
                enddate_str = r2.stdout.strip().replace("notAfter=", "")
                expiry_dt = datetime.strptime(
                    enddate_str, "%b %d %H:%M:%S %Y %Z"
                ).replace(tzinfo=timezone.utc)
                days_left = (expiry_dt - datetime.now(tz=timezone.utc)).days
        except Exception:
            pass

        return {
            "subject":    _grep(r"Subject:\s*(.+)"),
            "issuer":     _grep(r"Issuer:\s*(.+)"),
            "not_before": _grep(r"Not Before\s*:\s*(.+)"),
            "not_after":  _grep(r"Not After\s*:\s*(.+)"),
            "sans":       san,
            "days_left":  days_left,
        }
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"_error": f"openssl invocation failed: {exc}"}
    except Exception as exc:
        return {"_error": f"unexpected error parsing certificate: {exc}"}


def _verify_cert_chain(cert_pem, ca_pem):
    """
    Verify a PEM certificate against a PEM CA certificate using 'openssl verify'.
    Writes both to temporary files, runs the verification, and cleans up the temp
    files in a finally block regardless of outcome.
    Returns (True, message) on success, (False, message) on verification failure,
    or (None, reason) when verification cannot be attempted (missing data or openssl).
    """
    if not _openssl_available():
        return None, "openssl not available"
    if not cert_pem or not ca_pem:
        return None, "missing certificate or CA data"
    cert_file = None
    ca_file   = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".crt", delete=False) as cf:
            cf.write(cert_pem)
            cert_file = cf.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".crt", delete=False) as caf:
            caf.write(ca_pem)
            ca_file = caf.name
        r = subprocess.run(
            ["openssl", "verify", "-CAfile", ca_file, cert_file],
            capture_output=True, text=True, timeout=10,
        )
        msg = (r.stdout + r.stderr).strip()
        return r.returncode == 0, msg
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, f"openssl verify failed: {exc}"
    except Exception as exc:
        return None, f"unexpected error during chain verification: {exc}"
    finally:
        for path in (cert_file, ca_file):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


# ── Formatters ────────────────────────────────────────────────────────────────

def _redact_env(text):
    """
    Walk lines of 'env' command output and replace values whose key contains a
    sensitive token (matched on underscore-delimited parts) with '[REDACTED]'.
    Lines without an '=' sign are passed through unchanged.
    """
    lines = []
    for line in (text or "").splitlines():
        if "=" not in line:
            lines.append(line)
            continue
        k, _, v = line.partition("=")
        name_parts = set(re.split(r"[_\-]", k.lower()))
        if name_parts & _SENSITIVE_KEYS:
            lines.append(f"{k}=[REDACTED]")
        else:
            lines.append(f"{k}={v}")
    return "\n".join(lines)


# ── Markdown helpers ──────────────────────────────────────────────────────────

def _table(headers, rows):
    """
    Build a GitHub-Flavored Markdown table string from a list of headers and rows.
    Pipe characters inside cell content are escaped so they do not break the table.
    Rows with fewer cells than headers are padded with '—'; extra cells are dropped.
    Returns an error string rather than raising on any failure.
    """
    try:
        ncols = len(headers)
        lines = [
            "| " + " | ".join(str(h) for h in headers) + " |",
            "|" + "|".join("---" for _ in range(ncols)) + "|",
        ]
        for row in rows:
            cells = [str(c).replace("|", "\\|") for c in list(row)[:ncols]]
            while len(cells) < ncols:
                cells.append("—")
            lines.append("| " + " | ".join(cells) + " |")
        return "\n".join(lines)
    except Exception as exc:
        return f"_Table render error: {exc}_"


def _safe_render(name, func, data):
    """
    Call a render function with data and return its string output.
    Catches any exception and returns an error blockquote instead so that a
    malformed or unexpected data shape for one section cannot prevent the rest
    of the report from being written.
    """
    try:
        return func(data)
    except Exception as exc:
        return (
            f"> **Render error in '{name}':** `{exc}`  \n"
            f"> Data type received: `{type(data).__name__}`.\n\n"
        )


# ── Kubernetes resource collection ────────────────────────────────────────────

# Cluster-level kubectl checks. Each entry: (display label, kubectl args, namespaced).
# namespaced=True appends -n <namespace>; namespaced=False queries cluster-wide.
_K8S_CHECKS = [
    ("Pods",                     ["get", "pods", "-o", "wide"],                 True),
    ("Deployments",              ["get", "deployments"],                         True),
    ("Services",                 ["get", "services"],                            True),
    ("Certificates",             ["get", "certificates"],                        True),
    ("Issuers",                  ["get", "issuers"],                             True),
    ("ClusterIssuers",           ["get", "clusterissuers"],                      False),
    ("StatefulSets",             ["get", "statefulsets"],                        True),
    ("Persistent Volume Claims", ["get", "pvc"],                                 True),
    ("Nodes",                    ["get", "nodes"],                               False),
    ("Pod Resource Usage",       ["top", "pods"],                                True),
]


def _collect_k8s(namespace):
    """
    Run all kubectl checks defined in _K8S_CHECKS and return their output in a dict.
    Each value is the command's stdout string or None when the command failed or
    returned no output (e.g. no pods exist, metrics server unavailable).
    Never raises — individual failures are silently recorded as None.
    """
    data = {"namespace": namespace}
    for label, args, namespaced in _K8S_CHECKS:
        try:
            data[label] = _kubectl_run(args, namespace=namespace if namespaced else None)
        except Exception as exc:
            print(f"  [kubectl] Unexpected error collecting '{label}': {exc}", file=sys.stderr)
            data[label] = None
        status = "ok" if data[label] else "not available"
        print(f"  {label}: {status}")
    return data


# ── TLS collection ────────────────────────────────────────────────────────────

def _collect_tls(namespace, ca_secret_name, tls_secret_name=None, skip_secrets=False):
    """
    Collect TLS certificate data from cert-manager Certificate objects and
    Kubernetes secrets. Discovers TLS secrets automatically from cert-manager
    objects first; the explicit tls_secret_name is added when provided.
    Parses each certificate with local openssl and verifies chains against the CA.
    Returns a dict with keys: ca_secret_name, certs, tls_secrets, ca_cert, chain_results,
    and _secrets_skipped (bool). When skip_secrets is True, secret reads and chain
    verification are omitted; cert-manager Certificate metadata is still collected.
    Never raises.
    """
    data = {
        "ca_secret_name":   ca_secret_name,
        "certs":            [],
        "tls_secrets":      {},
        "ca_cert":          None,
        "chain_results":    {},
        "_secrets_skipped": skip_secrets,
    }

    # Discover cert-manager Certificate objects. Filter by IAG5 labels first;
    # fall back to all Certificates in the namespace when none match the label.
    cert_json_raw = _kubectl_run(
        ["get", "certificates", "-o", "json", "-l", "app.kubernetes.io/name=iag5"],
        namespace=namespace,
    )
    if not cert_json_raw:
        cert_json_raw = _kubectl_run(
            ["get", "certificates", "-o", "json"], namespace=namespace
        )

    # Build the list of TLS secret names to inspect — start with the explicit
    # override so it appears first in the report if provided.
    tls_secret_names = []
    if tls_secret_name:
        tls_secret_names.append(tls_secret_name)

    if cert_json_raw:
        try:
            for item in json.loads(cert_json_raw).get("items", []):
                meta      = item.get("metadata", {})
                spec      = item.get("spec", {})
                status    = item.get("status", {})
                cond_list = status.get("conditions") or []
                conds     = {
                    c["type"]: c for c in cond_list
                    if isinstance(c, dict) and "type" in c
                }
                ready   = conds.get("Ready", {})
                secret  = spec.get("secretName", "")
                cert_info = {
                    "name":          meta.get("name", "—"),
                    "secret_name":   secret,
                    "issuer_name":   (spec.get("issuerRef") or {}).get("name", "—"),
                    "issuer_kind":   (spec.get("issuerRef") or {}).get("kind", "—"),
                    "duration":      spec.get("duration", "—"),
                    "renew_before":  spec.get("renewBefore", "—"),
                    "dns_names":     spec.get("dnsNames") or [],
                    "ready":         ready.get("status", "—"),
                    "ready_message": ready.get("message", ""),
                    "not_after":     status.get("notAfter", "—"),
                    "renewal_time":  status.get("renewalTime", "—"),
                }
                data["certs"].append(cert_info)
                if secret and secret not in tls_secret_names:
                    tls_secret_names.append(secret)
        except (json.JSONDecodeError, TypeError, KeyError, Exception) as exc:
            print(f"  [tls] Failed to parse Certificate JSON: {exc}", file=sys.stderr)

    print(f"  cert-manager Certificates: {len(data['certs'])} found")

    if skip_secrets:
        print("  TLS secrets: skipped (--skip-secrets)")
        print(f"  CA secret '{ca_secret_name}': skipped (--skip-secrets)")
        return data

    # Inspect each TLS secret. tls.crt is the leaf cert. ca.crt (when present)
    # is the issuing CA bundled by cert-manager — save it as a fallback for
    # chain verification if the dedicated CA secret is unavailable.
    ca_pem_fallback = None
    for sname in tls_secret_names:
        print(f"  TLS secret '{sname}': ", end="", flush=True)
        cert_pem = _get_secret_data(sname, "tls.crt", namespace)
        if cert_pem:
            parsed = _parse_cert_pem(cert_pem)
            parsed["_pem"] = cert_pem
            data["tls_secrets"][sname] = parsed
            print("ok")
        else:
            data["tls_secrets"][sname] = {
                "_error": f"secret '{sname}' not found or has no tls.crt key"
            }
            print("not available")

        if not ca_pem_fallback:
            candidate = _get_secret_data(sname, "ca.crt", namespace)
            if candidate:
                ca_pem_fallback = candidate

    # Inspect the dedicated CA secret. tls.crt is the standard key for cert-manager
    # CA issuers; fall back to ca.crt for manually created secrets.
    print(f"  CA secret '{ca_secret_name}': ", end="", flush=True)
    ca_pem = (
        _get_secret_data(ca_secret_name, "tls.crt", namespace)
        or _get_secret_data(ca_secret_name, "ca.crt", namespace)
        or ca_pem_fallback
    )
    if ca_pem:
        parsed_ca = _parse_cert_pem(ca_pem)
        parsed_ca["_pem"] = ca_pem
        data["ca_cert"] = parsed_ca
        print("ok")
    else:
        print("not available")

    # Verify each TLS cert against the CA using 'openssl verify'.
    for sname, parsed in data["tls_secrets"].items():
        cert_pem = parsed.get("_pem")
        if cert_pem and ca_pem:
            ok, msg = _verify_cert_chain(cert_pem, ca_pem)
            data["chain_results"][sname] = (ok, msg)

    return data


# ── Pod collection ────────────────────────────────────────────────────────────

def _discover_pods(namespace):
    """
    Return a list of (pod_name, component) tuples for all IAG5 pods in the namespace.
    Queries by the 'app.kubernetes.io/name=iag5' label and parses the full JSON
    response to extract pod names and component labels. JSON parsing is used instead
    of jsonpath templates to avoid relying on kubectl's escape-sequence handling,
    which varies across versions and outputs literal '\\t'/'\\n' rather than
    whitespace on some builds.
    Returns an empty list when no pods are found or kubectl fails.
    """
    raw = _kubectl_run(
        ["get", "pods", "-l", "app.kubernetes.io/name=iag5", "-o", "json"],
        namespace=namespace,
    )
    if not raw:
        return []
    pods = []
    try:
        for item in json.loads(raw).get("items", []):
            name = (item.get("metadata") or {}).get("name", "")
            labels = (item.get("metadata") or {}).get("labels") or {}
            component = labels.get("app.kubernetes.io/component", "unknown")
            if name:
                pods.append((name, component))
    except Exception as exc:
        print(f"  [pods] Failed to parse pod list: {exc}", file=sys.stderr)
    return pods


def _collect_pod(pod_name, component, namespace, skip_secrets=False):
    """
    Collect per-pod health and configuration data via kubectl exec.
    Runs iagctl version, pgrep, ps, env (with sensitive values redacted), and
    openssl certificate inspection on the mounted TLS files.
    Each command is independent — a failure in one does not prevent the others
    from running. Returns a dict; values are None for any check that failed.
    When skip_secrets is True, env and openssl cert inspection are omitted.
    """
    data = {"component": component, "_secrets_skipped": skip_secrets}

    # iagctl version — confirms the binary is present and responsive.
    data["version"] = _kubectl_exec(pod_name, namespace, ["iagctl", "version"])

    # pgrep -a iagctl — confirms the iagctl process is running and shows the command line.
    data["pgrep"] = _kubectl_exec(pod_name, namespace, ["pgrep", "-a", "iagctl"])

    # ps aux — full process list, filtered in the renderer to iagctl lines only.
    data["ps"] = _kubectl_exec(pod_name, namespace, ["ps", "aux"])

    # env — all environment variables, filtered to GATEWAY_* and redacted for secrets.
    # Collected regardless of skip_secrets: values are already redacted by _redact_env
    # and the output is filtered to GATEWAY_* config only — no raw secret material.
    env_raw = _kubectl_exec(pod_name, namespace, ["env"])
    data["env"] = _redact_env(env_raw) if env_raw else None

    # Mounted cert inspection reads certificate bytes that originated from Kubernetes
    # Secrets — omit when --skip-secrets is set, consistent with TLS secret collection.
    if skip_secrets:
        data["tls_cert_text"] = None
        data["tls_verify"]   = None
        return data

    # Probe for openssl before running cert commands. Many minimal container images
    # do not include openssl, and we do not want noisy failures printed to stderr
    # for a predictably absent binary. The TLS secret inspection section already
    # covers the cert content via local openssl, so these are supplementary.
    has_openssl = _kubectl_exec(pod_name, namespace, ["which", "openssl"]) is not None

    data["tls_cert_text"] = _kubectl_exec(
        pod_name, namespace,
        ["openssl", "x509", "-in", _GATEWAY_CERT_PATH, "-text", "-noout"],
    ) if has_openssl else None

    data["tls_verify"] = _kubectl_exec(
        pod_name, namespace,
        ["openssl", "verify", "-CAfile", _GATEWAY_CA_PATH, _GATEWAY_CERT_PATH],
    ) if has_openssl else None

    return data


# ── Section renderers ─────────────────────────────────────────────────────────

def _render_k8s(data):
    """
    Render the collected kubectl outputs as a Kubernetes resources section.
    Each check gets its own H4 subsection with raw kubectl output in a code block.
    Checks that returned None are shown as 'Not available' — this is normal for
    resources that do not exist or for kubectl top when metrics-server is absent.
    """
    if not isinstance(data, dict):
        return "> **Kubernetes data unavailable.**\n\n"
    namespace = data.get("namespace", "unknown")
    out = [f"**Namespace:** `{namespace}`\n\n"]
    for label, _, _ in _K8S_CHECKS:
        out.append(f"#### {label}\n\n")
        output = data.get(label)
        if output:
            out.append(f"```\n{output}\n```\n\n")
        else:
            out.append("_Not available._\n\n")
    return "".join(out)


def _render_cert_info(info):
    """
    Render a parsed certificate info dict (returned by _parse_cert_pem) as a
    markdown table. Computes an expiry status label from days_left: expired,
    expiring soon (under 7 days), within 30 days, or valid with count.
    Returns an error blockquote when the dict contains an '_error' key.
    """
    if not isinstance(info, dict):
        return f"> **Unexpected cert data type:** `{type(info).__name__}`\n\n"
    if "_error" in info:
        return f"> **Error:** {info['_error']}\n\n"
    days = info.get("days_left")
    if days is None:
        expiry_status = "—"
    elif days < 0:
        expiry_status = f"**EXPIRED** ({abs(days)} days ago)"
    elif days < 7:
        expiry_status = f"**EXPIRING SOON** ({days} days remaining)"
    elif days < 30:
        expiry_status = f"Expiring in {days} days"
    else:
        expiry_status = f"Valid ({days} days remaining)"
    rows = [
        ("Subject",       f"`{info.get('subject', '—')}`"),
        ("Issuer",        f"`{info.get('issuer', '—')}`"),
        ("Not Before",    info.get("not_before", "—")),
        ("Not After",     info.get("not_after", "—")),
        ("Expiry Status", expiry_status),
        ("SANs",          info.get("sans", "—")),
    ]
    return _table(["Field", "Value"], rows) + "\n\n"


def _render_tls(data):
    """
    Render the full TLS inspection section. Covers cert-manager Certificate object
    status, TLS secret certificate details with chain verification results, and the
    CA certificate. Each subsection degrades gracefully when data is absent.
    """
    if not isinstance(data, dict):
        return "> **TLS data unavailable.**\n\n"
    out = []
    secrets_skipped = data.get("_secrets_skipped", False)

    # cert-manager Certificate objects — status, renewal schedule, and requested SANs.
    out.append("### cert-manager Certificates\n\n")
    certs = data.get("certs") or []
    if not certs:
        out.append(
            "_No cert-manager Certificate objects found. "
            "TLS certificates may be managed externally or certManager.enabled is false._\n\n"
        )
    else:
        for cert in certs:
            ready       = cert.get("ready", "—")
            ready_label = "Yes" if ready == "True" else ("No" if ready == "False" else ready)
            rows = [
                ("Name",         f"`{cert.get('name', '—')}`"),
                ("Secret",       f"`{cert.get('secret_name', '—')}`"),
                ("Issuer",       f"`{cert.get('issuer_name', '—')}` ({cert.get('issuer_kind', '—')})"),
                ("Duration",     cert.get("duration", "—")),
                ("Renew Before", cert.get("renew_before", "—")),
                ("Ready",        ready_label),
                ("Not After",    cert.get("not_after", "—")),
                ("Renewal Time", cert.get("renewal_time", "—")),
            ]
            if cert.get("ready_message"):
                rows.append(("Message", cert["ready_message"]))
            dns_names = cert.get("dns_names") or []
            if dns_names:
                rows.append(("Requested SANs", ", ".join(f"`{d}`" for d in dns_names)))
            out.append(_table(["Field", "Value"], rows) + "\n\n")

    # TLS secrets — leaf certificate details and chain verification result per secret.
    out.append("### TLS Secrets\n\n")
    if secrets_skipped:
        out.append("_Skipped (--skip-secrets was set)._\n\n")
    else:
        tls_secrets = data.get("tls_secrets") or {}
        if not tls_secrets:
            out.append("_No TLS secrets inspected._\n\n")
        else:
            for sname, parsed in tls_secrets.items():
                out.append(f"#### `{sname}`\n\n")
                out.append(_render_cert_info(parsed))
                chain = (data.get("chain_results") or {}).get(sname)
                if chain is not None:
                    ok, msg = chain
                    if ok is None:
                        out.append(f"> Chain verification skipped: {msg}\n\n")
                    elif ok:
                        out.append(f"> **Chain verification: PASSED**\n\n")
                    else:
                        out.append(f"> **Chain verification: FAILED**\n> `{msg}`\n\n")
                else:
                    out.append("> Chain verification: not attempted (CA data unavailable)\n\n")

    # CA certificate — the root of trust for all mTLS connections.
    out.append("### CA Certificate\n\n")
    if secrets_skipped:
        out.append("_Skipped (--skip-secrets was set)._\n\n")
    else:
        ca_name = data.get("ca_secret_name", "itential-ca")
        ca_cert  = data.get("ca_cert")
        if ca_cert is None:
            out.append(
                f"_Secret `{ca_name}` not found or not readable. "
                "Ensure the secret exists in the namespace before install._\n\n"
            )
        else:
            out.append(f"**Source:** `{ca_name}`\n\n")
            out.append(_render_cert_info(ca_cert))

    return "".join(out)


def _render_pod(pod_name, data):
    """
    Render all per-pod collected data as a report section. Includes component type,
    iagctl version output, process list filtered to iagctl entries, GATEWAY_*
    environment variables (redacted), and TLS certificate details from mounted files.
    Each subsection shows 'Not available' gracefully when data could not be collected.
    """
    if not isinstance(data, dict):
        return f"> **No data collected for pod `{pod_name}`.**\n\n"
    out = []

    out.append(f"**Component:** `{data.get('component', 'unknown')}`\n\n")

    # iagctl version output
    out.append("#### Version\n\n")
    version = data.get("version")
    if version:
        out.append(f"```\n{version}\n```\n\n")
    else:
        out.append("_Not available._\n\n")

    # Process check — pgrep confirms the process is alive; ps provides the command line.
    out.append("#### Processes\n\n")
    pgrep = data.get("pgrep")
    ps    = data.get("ps")
    if pgrep:
        out.append(f"**iagctl (pgrep):**\n```\n{pgrep}\n```\n\n")
    else:
        out.append("**iagctl (pgrep):** _no processes found_\n\n")
    if ps:
        iag_lines = [l for l in ps.splitlines() if "iagctl" in l or l.startswith("USER")]
        if iag_lines:
            out.append("**iagctl entries (ps aux):**\n```\n" + "\n".join(iag_lines) + "\n```\n\n")

    # Environment — GATEWAY_* vars only for focus; sorted for readability.
    out.append("#### Environment (GATEWAY_* variables, redacted)\n\n")
    env = data.get("env")
    if env:
        gw_lines = sorted(l for l in env.splitlines() if l.startswith("GATEWAY_"))
        if gw_lines:
            out.append("```\n" + "\n".join(gw_lines) + "\n```\n\n")
        else:
            out.append("_No GATEWAY_* environment variables found._\n\n")
    else:
        out.append("_Not available._\n\n")

    # Mounted TLS certificate — openssl output from inside the pod.
    out.append("#### Mounted TLS Certificate\n\n")
    if data.get("_secrets_skipped"):
        out.append("_Skipped (--skip-secrets was set)._\n\n")
    else:
        tls_text   = data.get("tls_cert_text")
        tls_verify = data.get("tls_verify")
        if tls_text:
            out.append(f"```\n{tls_text}\n```\n\n")
            if tls_verify is not None:
                if "OK" in (tls_verify or ""):
                    out.append(f"> **Chain verification: PASSED**\n> `{tls_verify}`\n\n")
                else:
                    out.append(f"> **Chain verification:** `{tls_verify or 'no output'}`\n\n")
        else:
            out.append(
                "_Not available. openssl is not present in the pod image, TLS may be "
                "disabled (`useTLS: false`), or the certificate file does not yet exist. "
                "See the TLS Certificate Inspection section for cert details from the "
                "Kubernetes secret._\n\n"
            )

    return "".join(out)


# ── Report assembly ───────────────────────────────────────────────────────────

def _build_report(namespace, generated_at, k8s_data, tls_data, pod_results):
    """
    Assemble the full markdown certification report from all collected data.
    All section renders are wrapped by _safe_render so a failure in any one
    section cannot prevent the rest of the report from being written.
    If report assembly itself fails, returns a minimal error document so a
    file is always produced with diagnostic information.
    """
    try:
        out = []

        out.append("# IAG5 — Certification Report\n\n")
        meta_rows = [
            ["**Generated**", generated_at],
            ["**Namespace**", f"`{namespace}`"],
        ]
        out.append(_table(["", ""], meta_rows) + "\n\n---\n\n")

        out.append("## Kubernetes Resources\n\n")
        out.append(_safe_render("Kubernetes", _render_k8s, k8s_data))
        out.append("---\n\n")

        out.append("## TLS Certificate Inspection\n\n")
        out.append(_safe_render("TLS", _render_tls, tls_data))
        out.append("---\n\n")

        out.append("## Pod Inspection\n\n")
        if not pod_results:
            out.append(
                "_No IAG5 pods found in namespace, or --skip-exec was set._\n\n"
            )
        else:
            for pod_name, pod_data in pod_results.items():
                out.append(f"### `{pod_name}`\n\n")
                # Use a default-argument lambda to capture pod_name by value,
                # preventing the loop variable from being shared across iterations.
                out.append(_safe_render(
                    f"Pod:{pod_name}",
                    lambda d, pn=pod_name: _render_pod(pn, d),
                    pod_data,
                ))
                out.append("---\n\n")

        return "".join(out)

    except Exception as exc:
        return (
            "# IAG5 Certification Report — Assembly Error\n\n"
            f"> Report generation encountered an unexpected error: `{exc}`\n\n"
            f"> Generated at: {generated_at}\n\n"
            f"> Namespace: `{namespace}`\n"
        )


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    """
    Main entry point — orchestrates argument parsing, Kubernetes resource collection,
    TLS certificate inspection, per-pod data gathering, and report writing.

    The script is designed to never raise an unhandled exception and to always
    produce an output file. Every collection step degrades gracefully: failed
    kubectl commands produce 'Not available', failed TLS inspection shows an error
    note inline, and a failed file write falls back to printing the report to stdout
    so the output is never lost.
    """
    args = _parse_args()

    if not _kubectl_available():
        sys.exit(
            "Error: kubectl not found on PATH. "
            "Install kubectl and configure it for the target cluster."
        )

    namespace = args.namespace or _kubectl_context_namespace()
    print(f"\nCertifying IAG5 in namespace: {namespace}\n")

    print("Collecting Kubernetes resources...")
    k8s_data = _collect_k8s(namespace)

    print("\nCollecting TLS certificate information...")
    tls_data = _collect_tls(
        namespace,
        ca_secret_name=args.ca_secret,
        tls_secret_name=args.tls_secret,
        skip_secrets=args.skip_secrets,
    )

    pod_results = {}
    if not args.skip_exec:
        pods = _discover_pods(namespace)
        if pods:
            print(f"\nCollecting per-pod data ({len(pods)} pod(s))...")
            for pod_name, component in pods:
                print(f"  {pod_name} ({component})")
                pod_results[pod_name] = _collect_pod(pod_name, component, namespace,
                                                     skip_secrets=args.skip_secrets)
        else:
            print("\nNo IAG5 pods found — skipping per-pod collection.")
    else:
        print("\nSkipping per-pod collection (--skip-exec).")

    now          = datetime.now(tz=timezone.utc)
    generated_at = now.strftime("%Y-%m-%d %H:%M:%S UTC")
    report       = _build_report(namespace, generated_at, k8s_data, tls_data, pod_results)

    outfile = f"iag5-certify-{namespace}-{now.strftime('%Y-%m-%d')}.md"
    try:
        with open(outfile, "w", encoding="utf-8") as fh:
            fh.write(report)
        print(f"\nReport written to: {outfile}")
    except OSError as exc:
        print(
            f"\nCannot write to '{outfile}': {exc}\n"
            "Printing report to stdout instead:\n",
            file=sys.stderr,
        )
        print(report)


if __name__ == "__main__":
    main()
