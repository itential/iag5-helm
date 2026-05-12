# iag5 Helm Chart

## What This Chart Does

Deploys **Itential Automation Gateway 5 (IAG5)** on Kubernetes. IAG5 is a gRPC-based gateway service that connects the Itential Platform to downstream execution targets (Ansible, scripts, OpenTofu, etc.).

The chart supports two deployment modes:
- **Simple**: One server pod, no distributed execution
- **Distributed**: One or more server pods + N runner pods, each with its own Service for DNS-based discovery

---

## Chart Metadata

| Field | Value |
|-------|-------|
| Chart version | 1.0.5 |
| App version | 5.1.1 (override per env) |
| API version | v2 |
| Main chart path | `charts/iag5/` |

---

## Dependencies

All three are optional and toggled via values:

| Dependency | Version | Toggle | Notes |
|------------|---------|--------|-------|
| etcd (Bitnami) | 11.3.6 | `etcd.enabled` | Required when `storeBackend: etcd` |
| cert-manager (Jetstack) | v1.18.2 | `certManager.enabled` | Skip if already installed in cluster |
| external-dns | 1.18.0 | `external-dns.enabled` | Optional, off by default |

---

## Templates

| Template | What It Creates | Condition |
|----------|----------------|-----------|
| `deployment-server.yaml` | Server Deployment | `serverSettings.replicaCount > 0` |
| `deployment-runner.yaml` | N Runner Deployments (loop) | `runnerSettings.replicaCount > 0` |
| `service.yaml` | LoadBalancer Service for servers | Always |
| `service-runner.yaml` | ClusterIP Service per runner (loop) | `runnerSettings.replicaCount > 0` |
| `certificate.yaml` | cert-manager Certificate | `certificate.enabled` |
| `issuer.yaml` | cert-manager Issuer/ClusterIssuer | `issuer.enabled` |
| `_helpers.tpl` | Named template helpers | — |
| `NOTES.txt` | Post-install summary | — |

---

## Key Values

### Deployment Shape

```yaml
serverSettings:
  replicaCount: 1           # Number of server pods
  connectEnabled: true      # Whether to register with Itential Platform
  connectHosts: itential.example.com:8080
  connectInsecureEnabled: false

runnerSettings:
  replicaCount: 0           # 0 = simple mode, N = distributed mode
```

### Application Settings

```yaml
clusterId: cluster_1        # Identifier for this IAG instance
logLevel: DEBUG
storeBackend: memory        # Options: memory | local | etcd | dynamodb
hostname: iag5.example.com
port: 50051
useTLS: true
```

### Image

```yaml
repository: ""              # Must be provided — ECR path, no default
tag: 5.1.1-amd64
pullPolicy: IfNotPresent
imagePullSecrets:
  - name: ""               # Must point to a valid pull secret
```

### TLS / cert-manager

```yaml
certManager:
  enabled: true            # Set false if cert-manager already exists in cluster

issuer:
  enabled: true
  kind: Issuer             # Issuer or ClusterIssuer
  name: iag5-ca-issuer
  caSecretName: itential-ca  # Pre-existing CA secret

certificate:
  enabled: true
  duration: 2160h          # 90 days
  renewBefore: 48h
```

The certificate template auto-generates DNS SANs for:
- The base hostname
- The server Service name
- Every runner Service name (`{svc}-runner-{n}`)
- All `.svc` and `.svc.cluster.local` variants

### Storage Backends

**memory** (default): in-process, no persistence, no extra config needed.

**etcd**: requires `etcd.enabled: true` (or an external etcd) plus:
```yaml
etcdHosts: etcd.default.svc.cluster.local:2379
etcdUseTLS: true
etcdUseClientCertAuth: true
etcdTlsSecretName: etcd-tls-secret
```

**dynamodb**:
```yaml
storeBackend: dynamodb
dynamodbTableName: your-table-name
# Requires secret: dynamodb-aws-secrets
```

---

## Required Pre-Existing Secrets

These must exist in the namespace before install — the chart does **not** create them:

| Secret Name | Keys | Purpose |
|-------------|------|---------|
| `itential-ca` | `tls.crt`, `tls.key` | CA cert used by the Issuer |
| `itential-gateway-secrets` | `encryptionKey` | 256-char base64 encryption key |
| `<imagePullSecrets[].name>` | Docker config | Pull image from ECR |
| `etcd-client-certs` | `ca.crt`, `tls.crt`, `tls.key` | Etcd mTLS (if etcd backend + TLS) |
| `dynamodb-aws-secrets` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_REGION` | DynamoDB auth |

---

## Distributed Execution (Runners)

When `runnerSettings.replicaCount > 0`:

- N separate `Deployment` objects are created, one per runner
- Each runner gets its own `ClusterIP` `Service` named `{service-name}-runner-{n}`
- Runners announce their address as: `{service-name}-runner-{n}.{namespace}.svc.cluster.local`
- The certificate SANs are automatically expanded to include all runner DNS names
- Runners run the command: `/usr/local/bin/iagctl runner`

---

## Resource Defaults

| Component | CPU Request | CPU Limit | Mem Request | Mem Limit |
|-----------|-------------|-----------|-------------|-----------|
| Server | 1 | 2 | 2Gi | 4Gi |
| Runner | 4 | 6 | 8Gi | 10Gi |
| Etcd (when enabled) | 2 | 4 | 8Gi | 10Gi |

---

## Service

- Type: `LoadBalancer` (default)
- Port: `50051` (gRPC)
- AWS NLB annotations included by default
- Selector targets pods with label `app.kubernetes.io/component: server`

---

## Probes

Both liveness and readiness use `exec: pgrep iagctl`:
- Liveness: initialDelaySeconds 10, period 10
- Readiness: initialDelaySeconds 5, period 10

---

## Example Values Files

| File | Use Case |
|------|----------|
| `values-aws-simple.yaml` | Single server, memory backend, no runners |
| `values-aws-eks-NickA.yaml` | Nick's personal test env on EKS, ClusterIssuer, no runners |
| `values-aws-distributed-etcd.yaml` | Full distributed setup with etcd backend |
| `values-perf-simple.yaml` | Perf test, single server |
| `values-perf-distributed-etcd-perf.yaml` | Perf test, distributed + etcd |

---

## Testing

Unit tests use the `helm unittest` plugin:
```bash
helm unittest charts/iag5
```

Test files are in `charts/iag5/templates/tests/`. Snapshots live in `__snapshot__/`.

Helm integration test hooks are in `charts/iag5/tests/` and run with:
```bash
helm test <release-name>
```

---

## Common Gotchas

1. **`repository` has no default** — always supply the ECR image path.
2. **`certManager.enabled: false`** when cert-manager is already installed cluster-wide (common in shared clusters).
3. **`issuer.kind: ClusterIssuer`** if you're using a cluster-scoped issuer (see `values-aws-eks-NickA.yaml`).
4. **runner replicaCount starts at 0** — distributed mode is opt-in.
5. **etcd TLS secrets must be created before install** when using the etcd backend.
6. **`connectInsecureEnabled`** must match the Itential Platform's TLS configuration.
