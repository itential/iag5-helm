# IAG5 Helm Chart — Pre-flight Verification

|  |  |
|---|---|
| **Chart** | `iag5` |
| **Chart Version** | `1.0.5` |
| **App Version** | `5.1.1` |
| **Repo** | `https://github.com/itential/iag5-helm` |
| **Purpose** | Environment readiness checks before install or upgrade |

All checks below must pass before proceeding. Each command must exit `0` and produce the expected output. A single failure is a blocker.

---

## 1. Namespace

```bash
kubectl get namespace <NAMESPACE>
```

**Expected:**

```
NAME          STATUS   AGE
<NAMESPACE>   Active   ...
```

---

## 2. Node Readiness

```bash
kubectl get nodes
```

**Expected:** Every node shows `STATUS: Ready`. Any node in `NotReady` or `Unknown` blocks pod scheduling.

---

## 3. Required Secrets

The chart does not create these secrets. They must exist in the target namespace before install.

### 3.1 `itential-gateway-secrets` — always required

Used by both server and runner pods. Mounted at `/etc/gateway/keys/encryption.key`.

Verify the secret exists and contains the correct key:

```bash
kubectl get secret itential-gateway-secrets -n <NAMESPACE> \
  -o jsonpath='{.data}' | jq 'keys'
```

**Expected:** `["gatewayEncryptionKey"]`

Verify the key is 256 characters (base64-decoded):

```bash
kubectl get secret itential-gateway-secrets -n <NAMESPACE> \
  -o jsonpath='{.data.gatewayEncryptionKey}' | base64 -d | wc -c
```

**Expected:** `256`

---

### 3.2 Image Pull Secret — always required

Must match the name set in `imagePullSecrets[0].name` in values. Must be type `kubernetes.io/dockerconfigjson`.

```bash
kubectl get secret <IMAGE_PULL_SECRET_NAME> -n <NAMESPACE> \
  -o jsonpath='{.type}'
```

**Expected:** `kubernetes.io/dockerconfigjson`

Verify the registry entry is present:

```bash
kubectl get secret <IMAGE_PULL_SECRET_NAME> -n <NAMESPACE> \
  -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d | jq '.auths | keys'
```

**Expected:** At least one registry hostname (e.g. the ECR endpoint).

---

### 3.3 `itential-ca` — required when `issuer.enabled: true` (default)

This is the CA secret referenced by the Issuer. cert-manager uses it to sign the IAG5 TLS certificate. Must contain both `tls.crt` and `tls.key`.

```bash
kubectl get secret itential-ca -n <NAMESPACE> \
  -o jsonpath='{.data}' | jq 'keys'
```

**Expected:** `["tls.crt","tls.key"]`

Verify the CA certificate is not expired:

```bash
kubectl get secret itential-ca -n <NAMESPACE> \
  -o jsonpath='{.data.tls\.crt}' | base64 -d | openssl x509 -noout -dates
```

**Expected:** `notAfter` is in the future.

> If `issuer.kind: ClusterIssuer`, the `itential-ca` secret must exist in the namespace cert-manager is running in (typically `cert-manager`), not the application namespace.

---

### 3.4 `etcd-client-certs` — required when `storeBackend: etcd` and `etcdUseTLS: true`

Mounted at `/etc/ssl/etcd` in all pods. Must contain `ca.crt`, `tls.crt`, and `tls.key`.

```bash
kubectl get secret etcd-client-certs -n <NAMESPACE> \
  -o jsonpath='{.data}' | jq 'keys'
```

**Expected:** `["ca.crt","tls.crt","tls.key"]`

---

### 3.5 `etcd-peer-certs` — required when `etcd.enabled: true` and `etcd.auth.tls.enabled: true`

Used by the etcd dependency for peer TLS. Must contain `ca.crt`, `tls.crt`, and `tls.key`.

```bash
kubectl get secret etcd-peer-certs -n <NAMESPACE> \
  -o jsonpath='{.data}' | jq 'keys'
```

**Expected:** `["ca.crt","tls.crt","tls.key"]`

---

### 3.6 `dynamodb-aws-secrets` — required when `storeBackend: dynamodb`

Injected via `envFrom` into all pods. Must contain the four AWS credential keys.

```bash
kubectl get secret dynamodb-aws-secrets -n <NAMESPACE> \
  -o jsonpath='{.data}' | jq 'keys'
```

**Expected:** `["AWS_ACCESS_KEY_ID","AWS_REGION","AWS_SECRET_ACCESS_KEY","AWS_SESSION_TOKEN"]`

---

## 4. Persistent Volume — required when `etcd.enabled: true` and `etcd.persistence.enabled: true`

IAG5 server and runner pods do not use PVCs. The etcd dependency does when persistence is enabled (`etcd.persistence.storageClass` defaults to `iag5-ebs-gp3`).

Verify the storage class exists:

```bash
kubectl get storageclass <STORAGE_CLASS_NAME>
```

**Expected:**

```
NAME               PROVISIONER       RECLAIMPOLICY   VOLUMEBINDINGMODE      ALLOWVOLUMEEXPANSION
iag5-ebs-gp3       ebs.csi.aws.com   Delete          WaitForFirstConsumer   true
```

`WaitForFirstConsumer` is expected and valid — binding is deferred until a pod is scheduled to a node. `Immediate` is also acceptable.

**Upgrade only** — verify existing etcd PVCs are bound and not in a failed state:

```bash
kubectl get pvc -n <NAMESPACE> -l app.kubernetes.io/name=etcd
```

**Expected:** All PVCs show `STATUS: Bound`. A PVC in `Pending` or `Lost` will block the etcd pod from starting.

---

## 5. cert-manager

### 5.1 Installation check

The chart installs cert-manager as a dependency when `certManager.enabled: true` (default). If cert-manager is already installed cluster-wide, set `certManager.enabled: false` in values to skip re-installation. If skipping, it must already be running.

```bash
kubectl get pods -n cert-manager
```

**Expected:** All three cert-manager pods are `Running`:

```
NAME                                       READY   STATUS    RESTARTS   AGE
cert-manager-...                           1/1     Running   0          ...
cert-manager-cainjector-...                1/1     Running   0          ...
cert-manager-webhook-...                   1/1     Running   0          ...
```

---

### 5.2 Required CRDs

These CRDs must be present regardless of who manages cert-manager:

```bash
kubectl get crd \
  certificates.cert-manager.io \
  certificaterequests.cert-manager.io \
  issuers.cert-manager.io \
  clusterissuers.cert-manager.io
```

**Expected:** All four CRDs returned with no error.

---

### 5.3 Issuer scope and name conflict

The chart creates an `Issuer` (namespace-scoped, default) or `ClusterIssuer` (cluster-scoped) controlled by `issuer.kind`. The default issuer name is `iag5-ca-issuer` (set by `issuer.name`).

Check whether an issuer with the same name already exists. On install it will be overwritten; on upgrade this is expected.

```bash
# Issuer
kubectl get issuer <ISSUER_NAME> -n <NAMESPACE>

# ClusterIssuer (if issuer.kind: ClusterIssuer)
kubectl get clusterissuer <ISSUER_NAME>
```

If a pre-existing issuer references a different CA secret than `issuer.caSecretName` in values, the install will silently replace it. Confirm the CA name matches.

---

### 5.4 Stale certificate resources — upgrade only

On upgrade, cert-manager reconciles the Certificate resource. A Certificate stuck in a non-Ready state can delay TLS secret renewal and cause pods to fail on startup.

```bash
kubectl get certificate -n <NAMESPACE> \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}'
```

**Expected:** Any existing certificates show `True`. A certificate showing `False` must be investigated before upgrading:

```bash
kubectl describe certificate <CERT_NAME> -n <NAMESPACE>
```

---

## Summary

| # | Check | Command returns | Required for |
|---|---|---|---|
| 1 | Namespace exists | `Active` | All |
| 2 | All nodes Ready | `STATUS: Ready` on every node | All |
| 3.1 | `itential-gateway-secrets` key `gatewayEncryptionKey` present, 256 chars | `["gatewayEncryptionKey"]` / `256` | All |
| 3.2 | Image pull secret is `dockerconfigjson` type with registry entry | `kubernetes.io/dockerconfigjson` | All |
| 3.3 | `itential-ca` has `tls.crt` + `tls.key`, cert not expired | Both keys present, `notAfter` in future | `issuer.enabled: true` |
| 3.4 | `etcd-client-certs` has `ca.crt` + `tls.crt` + `tls.key` | All 3 keys present | `storeBackend: etcd` + `etcdUseTLS: true` |
| 3.5 | `etcd-peer-certs` has `ca.crt` + `tls.crt` + `tls.key` | All 3 keys present | `etcd.enabled: true` + TLS |
| 3.6 | `dynamodb-aws-secrets` has all 4 AWS credential keys | All 4 keys present | `storeBackend: dynamodb` |
| 4 | Storage class exists and provisioner is available | Storageclass returned | `etcd.enabled: true` + `persistence.enabled: true` |
| 4 (upgrade) | etcd PVCs are Bound | `STATUS: Bound` | Upgrade + etcd persistence |
| 5.1 | cert-manager pods Running | All 3 pods `Running` | `certManager.enabled: false` |
| 5.2 | cert-manager CRDs installed | All 4 CRDs present | Any cert-manager usage |
| 5.3 | Issuer name conflict reviewed | No unexpected pre-existing issuer | `issuer.enabled: true` |
| 5.4 | No stale certificates blocking renewal | All certificates `Ready: True` | Upgrade only |
