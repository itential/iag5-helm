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
| `serviceaccount.yaml` | Kubernetes ServiceAccount | `serviceAccount.create` |
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
  env: {}                   # Arbitrary env vars injected into server pods

runnerSettings:
  replicaCount: 0           # 0 = simple mode, N = distributed mode
  env: {}                   # Arbitrary env vars injected into runner pods

applicationSettings:
  env: {}                   # Arbitrary env vars injected into all pods
# Full list: https://docs.itential.com/docs/iag5-config-variables
```

### Application Settings

```yaml
# Top-level
hostname: iag5.example.com
port: 50051                # Also wired to GATEWAY_SERVER_PORT/GATEWAY_RUNNER_PORT, not just containerPort
useTLS: true

# Nested under applicationSettings:
applicationSettings:
  clusterId: cluster_1      # Identifier for this IAG instance
  logLevel: DEBUG
  storeBackend: memory      # Options: memory | local | etcd | dynamodb
```

### Feature Toggles, Terminal, Registry, and Venv Settings

All under `applicationSettings`, applied identically to both server and runner pods:

```yaml
applicationSettings:
  # Feature toggles
  featuresAnsibleEnabled: true
  featuresHostkeysEnabled: true
  featuresMcpEnabled: false    # Deliberately false -- vendor default is true (Gateway 5.5+).
                                # Opt-in so upgrading doesn't silently expose new MCP surface area.
  featuresOpentofuEnabled: true
  featuresPythonEnabled: true

  # Terminal output
  noColor: false
  terminalTimestampTimezone: "utc"

  # Service registry
  registryDefaultOverridable: true

  # Virtual environment cleanup (Gateway 5.4+)
  venvRetentionPeriod: "30d"
  venvSweepInterval: "24h"

  # Logging (beyond logLevel above)
  logConsoleJson: false
  logFileEnabled: false   # Deliberately false -- vendor default is true. Containers already have
                           # stdout captured by the cluster's logging stack; writing to a file
                           # inside the ephemeral container filesystem is not useful here.
  logFileJson: false       # Only rendered when logFileEnabled is true
  logServerDir: "/var/log/gateway"  # Only rendered when logFileEnabled is true
  logTimestampTimezone: "utc"
```

**Note on `server_ha_is_primary`**: the vendor's `[connect]` section has a `server_ha_is_primary` flag for designating one HA node as primary. This chart intentionally does not implement it -- `serverSettings.replicaCount > 1` deploys interchangeable replicas via a plain `Deployment`, with no stable per-pod identity to pin a "primary" designation to (that would require a `StatefulSet`). Gateway Manager arbitrates which replica's connection is active without an explicit primary flag.

### Outbound Proxy (Gateway Manager connection)

```yaml
serverSettings:
  connectProxyUrl: "http://proxy.example.com:8080"   # Empty disables proxying (default)
  connectProxySecretName: "my-proxy-secret"           # Optional -- only needed if the proxy requires auth
```

When `connectProxySecretName` is set, it must contain `proxyUsername`/`proxyPassword` keys -- these are injected via `secretKeyRef`, consistent with how every other credential in this chart is handled (never as plaintext values).

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
applicationSettings:
  storeBackend: etcd
  etcdHosts: etcd.default.svc.cluster.local:2379
  etcdUseTLS: true
  etcdUseClientCertAuth: true
  etcdTlsSecretName: etcd-tls-secret
```

**dynamodb**:
```yaml
applicationSettings:
  storeBackend: dynamodb
  dynamodbTableName: your-table-name
```

AWS credentials are provided either via IRSA (recommended) or a static `dynamodb-aws-secrets` Kubernetes secret. The secret is optional -- if absent the AWS SDK falls through to IRSA. See the Service Account (IRSA) section in README.md for the full setup guide.

Required IAM permissions: `GetItem`, `PutItem`, `UpdateItem`, `DeleteItem`, `Query`, `Scan`, `DescribeTable`, `BatchWriteItem`, `DescribeTimeToLive`, `UpdateTimeToLive`. IAG5 calls `DescribeTimeToLive` and `UpdateTimeToLive` on startup -- missing either will crash the pod.

---

## Required Pre-Existing Secrets

These must exist in the namespace before install — the chart does **not** create them:

| Secret Name | Keys | Purpose |
|-------------|------|---------|
| `itential-ca` | `tls.crt`, `tls.key` | CA cert used by the Issuer |
| `itential-gateway-secrets` | `gatewayEncryptionKey` | 256-char base64 encryption key |
| `<imagePullSecrets[].name>` | Docker config | Pull image from ECR |
| `etcd-client-certs` | `ca.crt`, `tls.crt`, `tls.key` | Etcd mTLS (if etcd backend + TLS) |
| `dynamodb-aws-secrets` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_REGION` | DynamoDB static credentials (optional when using IRSA) |
| `<serverSettings.connectProxySecretName>` | `proxyUsername`, `proxyPassword` | Gateway Manager proxy auth (optional, only if the proxy requires credentials) |

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

Controlled by `resources.enabled` (default: `true`). Set to `false` to apply no limits (useful for dev/lab).

| Component | CPU Request | CPU Limit | Mem Request | Mem Limit |
|-----------|-------------|-----------|-------------|-----------|
| Server | 1 | 2 | 2Gi | 4Gi |
| Runner | 4 | 6 | 8Gi | 10Gi |
| Etcd (when enabled) | 2 | 4 | 8Gi | 10Gi |

---

## Service

- Type: `LoadBalancer` (default)
- Name: `iag5-service` (default) — optional override via `service.name`. Changing it also renames all runner services (`{name}-runner-{n}`) and updates cert SANs automatically.
- Port: `50051` (gRPC)
- AWS NLB annotations included by default
- Selector targets pods with label `app.kubernetes.io/component: server`

---

## Probes

Both liveness and readiness use `exec: pgrep iagctl`:
- Liveness: initialDelaySeconds 10, period 10
- Readiness: initialDelaySeconds 5, period 10

---

## Testing

Unit tests use the `helm unittest` plugin:
```bash
helm unittest charts/iag5
```

Unit test files are in `charts/iag5/tests/`. Snapshots live in `charts/iag5/tests/__snapshot__/`.

Helm integration test hooks (post-install) are in `charts/iag5/templates/tests/` and run with:
```bash
helm test <release-name>
```

---

## Service Account (IRSA)

```yaml
serviceAccount:
  create: false        # Set true to create the ServiceAccount
  name: ""             # Defaults to chart fullname when empty
  annotations: {}      # Add eks.amazonaws.com/role-arn here for IRSA
  automountServiceAccountToken: false
```

When `create: false` (default), pods use the namespace `default` service account. For IRSA, set `create: true`, give it a name, and annotate it with the IAM role ARN. The role trust policy must reference the cluster's OIDC provider -- see README.md for details.

---

## Common Gotchas

1. **`repository` has no default** — always supply the ECR image path.
2. **`certManager.enabled: false`** when cert-manager is already installed cluster-wide (common in shared clusters).
3. **`issuer.kind: ClusterIssuer`** if you're using a cluster-scoped issuer — common in shared clusters where the issuer is managed outside this chart.
4. **runner replicaCount starts at 0** — distributed mode is opt-in.
5. **etcd TLS secrets must be created before install** when using the etcd backend.
6. **`connectInsecureEnabled`** must match the Itential Platform's TLS configuration.
7. **OIDC provider ID is cluster-specific** — never copy it from another IAM role. Decode a service account token from the target cluster to get the correct `iss` value: `kubectl create token <sa> -n <ns> --audience sts.amazonaws.com | python3 -c "import sys,base64,json; p=sys.stdin.read().strip().split('.')[1]; print(json.loads(base64.b64decode(p+'=='))['iss'])"`
8. **DynamoDB requires `DescribeTimeToLive` and `UpdateTimeToLive`** in addition to the standard CRUD permissions. IAG5 calls both on startup and will crash without them.
9. **`port` actually matters now** — it's wired to `GATEWAY_SERVER_PORT`/`GATEWAY_RUNNER_PORT`, not just `containerPort`. Before this was fixed, overriding `port` away from `50051` silently broke connectivity: Kubernetes routed to the new port while `iagctl` kept listening on its own default.
10. **Never add `| default "true"` (or any non-empty default) to a boolean values.yaml field in a template.** Sprig's `default` treats an explicit `false` as "empty" and substitutes the fallback anyway, silently coercing `false` back to `true`. Since every boolean already has a real default in `values.yaml`, templates should render it with a plain `| quote` — no `default` filter needed or safe to use.
