# IAG5 Helm Chart

This chart is designed to enable the easy and rapid deployment of IAG5 environments and
architectures. Requires Kubernetes version `1.31` or greater and Helm version `v3.15.0`.

## IAG5 Architectures

There are a few different architectures that are available when running IAG5. This chart is capable
of building two of them:

- A simple server architecture that is invoked from a client. The client is not a part of the chart.
- A distributed architecture that includes a server and runners that is invoked from a client. The
client is not a part of the chart.

For information about installing a client please visit the IAG5 repo.

### Requirements & Dependencies

| Repository | Name | Version |
|:-----------|:-----|:--------|
| <https://charts.bitnami.com/bitnami> | etcd | 11.3.0 |
| <https://charts.jetstack.io> | cert-manager | 1.12.3 |
| <https://kubernetes-sigs.github.io/external-dns/> | external-dns | 1.17.0 |

#### Secrets

The chart assumes the following secrets, they are not included in the Chart.

##### imagePullSecrets

This is the secret that will pull the image from the Itential ECR. Name to be determined by the user
 of the chart and that name must be provided in the values file (`imagePullSecrets[0].name`).

##### itential-ca

This secret represents the CA used by cert-manager to derive all the TLS certificates. Name to be
provided by the user of the chart in the values file (`issuer.caSecretName`) if using cert-manager.

##### itential-gateway-secrets

This secret contains several sensitive values that the application may use. They are loaded into the
pod as environment variables. Some are optional and depend on your implementation. The creation of
this secret is left out of the chart to allow for flexibility with its creation.

| Secret Key | Description | Required? |
|:-----------|:------------|:----------|
| encryptionKey | A private key used to encrypt and decrypt secrets within IAG5, a base 64 256 character string is recommended. | true |

##### dynamodb-aws-secrets

When using DynamoDB as the backend store, IAG5 needs AWS credentials to authenticate. There are
two options:

**Option 1 (recommended): IRSA (IAM Roles for Service Accounts)**

On EKS, configure a Kubernetes service account annotated with an IAM role ARN. The EKS webhook
injects a short-lived token into the pod and the AWS SDK exchanges it for temporary credentials
automatically. No Kubernetes secret is required. See the Service Account (IRSA) section below.

**Option 2: Static credentials**

Create a Secret named `dynamodb-aws-secrets` with the following keys:

- AWS_ACCESS_KEY_ID
- AWS_SECRET_ACCESS_KEY
- AWS_SESSION_TOKEN
- AWS_REGION

The chart marks this secret as optional. When IRSA is in use the secret should be absent or
removed — if it exists, the static credentials take precedence over IRSA in the AWS SDK
credential chain.

#### Service Account (IRSA)

On EKS, the recommended way to give IAG5 pods access to DynamoDB is IRSA. IRSA works by
annotating a Kubernetes service account with an IAM role ARN. When a pod starts, the EKS webhook
injects a short-lived OIDC token. The AWS SDK exchanges it for temporary, scoped credentials via
`sts:AssumeRoleWithWebIdentity` — no passwords, no rotation, no secrets to distribute.

**IAM policy — required DynamoDB permissions**

The IAM role attached to the service account must allow the following actions on the DynamoDB
table and its indexes:

- `dynamodb:GetItem`
- `dynamodb:PutItem`
- `dynamodb:UpdateItem`
- `dynamodb:DeleteItem`
- `dynamodb:Query`
- `dynamodb:Scan`
- `dynamodb:DescribeTable`
- `dynamodb:BatchWriteItem`
- `dynamodb:DescribeTimeToLive`
- `dynamodb:UpdateTimeToLive`

IAG5 checks and configures TTL on the table at startup. Both `DescribeTimeToLive` and
`UpdateTimeToLive` are required — pods will fail to start without them.

**DynamoDB table schema**

The table must be created with these keys:

| Key | Type | Role |
|:----|:-----|:-----|
| `namespace` | String | Partition key |
| `key` | String | Sort key |

On-demand capacity mode is recommended for variable IAG5 workloads.

**IAM trust policy**

The role's trust policy must allow the EKS cluster's OIDC provider to assume the role. The OIDC
provider ID is unique per cluster — do not copy it from another role. Verify it by decoding a
service account token:

```bash
kubectl create token <service-account-name> -n <namespace> --audience sts.amazonaws.com \
  | python3 -c "import sys,base64,json; p=sys.stdin.read().strip().split('.')[1]; print(json.loads(base64.b64decode(p+'=='))['iss'])"
```

The `iss` field contains the authoritative OIDC issuer URL for that cluster.

**Helm values**

```yaml
serviceAccount:
  create: true
  name: iag5-serviceaccount
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::<account-id>:role/<role-name>
  automountServiceAccountToken: false

applicationSettings:
  storeBackend: dynamodb
  dynamodbTableName: <table-name>
```

**Verify IRSA is active after deployment**

```bash
kubectl exec -n <namespace> <iag5-pod> -- env | grep AWS_ROLE_ARN
```

**Deploying to a different EKS cluster**

Each EKS cluster has its own OIDC provider ID. To reuse the same IAM role on a new cluster, add
a second trust statement to the role referencing the new cluster's OIDC provider. No changes to
the values file are needed — the role ARN stays the same.

**Troubleshooting**

`AccessDenied: Not authorized to perform sts:AssumeRoleWithWebIdentity` — the OIDC provider ID in
the trust policy does not match the cluster. Decode a service account token (see above) to find
the correct ID and update the trust policy.

`AccessDeniedException: not authorized to perform dynamodb:DescribeTimeToLive` — the IAM policy
is missing `DescribeTimeToLive` or `UpdateTimeToLive`. Add both actions.

#### Certificates

The chart will require a Certificate Authority to be added to the Kubernetes environment. This is
used by the chart when running with TLS flags enabled. The chart will use this CA to generate the
necessary certificates using a Kubernetes `Issuer` which is included. The Issuer will issue the
certificates using the CA. The certificates are then included using a Kubernetes `Secret` which is
mounted by the pods. Creating and adding this CA is outside of the scope of this chart.

Both the `Issuer` and the `Certificate` objects are realized by using the widely used Kubernetes
add-on called `cert-manager`. Cert-manager is responsible for making the TLS certificates required
by using the CA that was installed separately. The installation of cert-manager is outside the scope
of this chart. To check if this is already installed run this command:

```bash
kubectl get crds | grep cert-manager
```

#### Etcd

Etcd is a strongly consistent, distributed key-value store that provides a reliable way to store
data that needs to be accessed by a distributed system or cluster of machines. It gracefully handles
leader elections during network partitions and can tolerate machine failure, even in the leader
node. This is a required component when running the application in "distributed" mode.

This chart was developed with the Bitnami Etcd chart. For more information see the [Bitnami Chart](https://artifacthub.io/packages/helm/bitnami/etcd).

#### DNS

This is an optional requirement.

Itential used the ExternalDNS project to facilitate the creation of DNS entries. ExternalDNS
synchronizes exposed Kubernetes Services and Ingresses with DNS providers. This is not a
requirement for running the chart. The chart and the application can run without this if needed. We
chose to use this to provide us with external addresses.

For more information see the [ExternalDNS project](https://github.com/kubernetes-sigs/external-dns).

### Simple Server

An IAG5 simple server is a simple All-in-one architecture that can run automations that are invoked
from a client. That client could be a local installation of IAG5, or an Itential Platform server. It
runs all automations in isolated execution environments and runs on a single pod runs all
automations in memory and runs on a single pod. It can support TLS connections.

To create this environment the values file must provide the appropriate values for `serverSettings`
and `runnerSettings`. When the replicaCount is greater than zero in the `serverSettings` config
object the chart will create a pod as a server. When the number of runners is greater than zero the
chart will create pods as runners. The values in `applicationSettings` effect all pods. These
configuration sections in values.yaml file can also contain all of the environment variables for a
pod. For example, this will create a simple server:

```yaml
hostname: iag5.example.com
port: 50051
useTLS: true

# Set the number of runner replicas to zero
runnerSettings:
  replicaCount: 0
  env: {}
    ...

# Set the number of server replicas to one
serverSettings:
  replicaCount: 1
  connectEnabled: true
  connectHosts: "itential.example.com" # The hostname of the Itential server
  connectInsecureEnabled: true
  env: {}
    ...

# Configure all pods with these values
applicationSettings:
  clusterId: cluster_1
  storeBackend: memory
  env: {}
```

### Distributed Server

An IAG5 distributed server is a server with a configurable number of runners. The server instructs
the runners to run IAG5 services, the runners obey. The server responds to a client, like the simple
server architecture. That client could be a local installation of IAG5 or an Itential Platform
server. It requires an Etcd cluster that it uses for communication. It consists of many pods. It
can support TLS connections.

The chart will create the Etcd cluster when the `etcd.enabled` value is set to true. Itential
routinely uses the helm chart provided by [Bitnami](https://artifacthub.io/packages/helm/bitnami/etcd).
This chart is listed as a dependency.

To create this environment the values file must provide the appropriate values for `serverSettings` and
`runnerSettings`. When the replicaCount is greater than zero in the `serverSettings` config
object the chart will create a pod as a server. When the replicaCount is greater than zero in the
`runnerSettings` config object the chart will create that many runners. The values in
`applicationSettings` effect all pods. These configuration sections in values.yaml file can also
contain all of the environment variables for a pod. For example, this will create a distributed
server:

```yaml
hostname: iag5.example.com
port: 50051
useTLS: true

# Set the number of runner replicas to the desired number of runners
runnerSettings:
  replicaCount: 3
  env: {}
    ...

# Set the number of server replicas to one
serverSettings:
  replicaCount: 1
  connectEnabled: true
  connectHosts: "itential.example.com" # The hostname of the Itential server
  connectInsecureEnabled: true
  env: {}
    ...

# Configure all pods with these values
applicationSettings:
  etcdTlsSecretName: etcd-tls-secret
  clusterId: cluster_1
  storeBackend: etcd # "etcd", "memory", "dynamodb"

  # Etcd settings
  etcdHosts: "etcd.default.svc.cluster.local:2379"
  env: {}
```

### Using DynamoDB instead of Etcd

The `memory` and `local` backends are single-server only. Any multi-pod topology — multiple
servers, runners, or both — requires etcd or DynamoDB as the backend.

**With IRSA (recommended on EKS):**

```yaml
serviceAccount:
  create: true
  name: iag5-serviceaccount
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::<account-id>:role/<role-name>
  automountServiceAccountToken: false

applicationSettings:
  clusterId: cluster_1
  storeBackend: dynamodb
  dynamodbTableName: "your-table-name"
```

**With static credentials:**

```yaml
applicationSettings:
  clusterId: cluster_1
  storeBackend: dynamodb
  dynamodbTableName: "your-table-name"
```

Create the `dynamodb-aws-secrets` secret before install with `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, and `AWS_REGION`.

### Using TLS connections

To enable TLS connections between IAG5 and Etcd you can use a configuration like this:

```yaml
hostname: iag5.example.com
port: 50051
useTLS: true

# Set the number of runner replicas to the desired number of runners
runnerSettings:
  replicaCount: 3
  env: {}
    ...

# Set the number of server replicas to one
serverSettings:
  replicaCount: 1
  connectEnabled: true
  connectHosts: "itential.example.com" # The hostname of the Itential server
  connectInsecureEnabled: true
  env: {}
    ...

# Configure all pods with these values
applicationSettings:
  etcdTlsSecretName: etcd-tls-secret
  clusterId: cluster_1
  logLevel: DEBUG
  storeBackend: etcd

  # Etcd settings
  etcdUseTLS: true
  etcdUseClientCertAuth: true
  etcdHosts: "etcd.default.svc.cluster.local:2379"
  env: {}
```

This requires an additional Secret to be installed in the Kubernetes environment. That Secret's
name must be provided to the pods as the value of `applicationSettings.etcdTlsSecretName`. The
Deployments will mount this secret as files whose paths are described by
`GATEWAY_STORE_ETCD_CA_CERTIFICATE_FILE`, `GATEWAY_STORE_ETCD_CERTIFICATE_FILE`, and
`GATEWAY_STORE_ETCD_PRIVATE_KEY_FILE`.

### Run the Chart

Clone this repo, adhere to the requirements, modify values.yaml appropriately, and install into your
Kubernetes environment by doing the following:

```bash
helm install iag5 . -f values.yaml
```

### Run the unit tests

This project uses the helm unittest plugin.

```bash
helm unittest .
```
### Run helm tests

This project uses helm tests that should be run after deployment. These are integration tests that run actual pods in your cluster after deployment to verify the application is working correctly.

Helm tests are present in `iag5-helm/charts/iag5/templates/tests/`

Helm looks through ALL rendered Kubernetes manifests in chart for resources that have this specific annotation:

```bash
annotations:
  "helm.sh/hook": test
```
Run all test using below command

```bash
helm test iag5 -n <NAMESPACE> --logs
```
Run individual test using below command

```bash
helm test iag5 -n <NAMESPACE> --filter name=iag5-test-connection --logs
```
Run multiple tests using below command

```bash
helm test iag5 -n <NAMESPACE> --filter 'name=iag5-test-version,name=iag5-test-processes' --logs
```

#### Values

These values are intended to be refined when this chart is implemented. Many of these values are
simply values that were used during development and testing.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| affinity | object | `{}` | Additional affinities |
| applicationSettings.clusterId | string | `"cluster_1"` | The ID that uniquely identifies your gateway instance or a cluster of related gateway instances. This is also used to link a gateway controller node to Gateway Manager so that automations can be sent to a particular cluster. |
| applicationSettings.dynamodbTableName | string | `""` | The DynamoDB table name when storeBackend is set to "dynamodb" |
| applicationSettings.etcdHosts | string | `"etcd.default.svc.cluster.local:2379"` | Sets the etcd hosts that the gateway connects to for backend storage. A host entry consists of an address and port: hostname:port. If there are multiple etcd hosts, enter them as a space separated list: hostname1:port hostname2:port. |
| applicationSettings.etcdTlsSecretName | string | `"etcd-tls-secret"` | The name of the etcd TLS secret. This is mounted as a volume by the deployment and contains the Etcd TLS certs and keys. |
| applicationSettings.etcdUseClientCertAuth | bool | `true` | Enable certificate validation when connecting to Etcd. |
| applicationSettings.etcdUseTLS | bool | `true` | Enable TLS when connecting the Etcd. |
| applicationSettings.logLevel | string | `"DEBUG"` | Sets the verbosity of the logs that the gateway displays to the console and file logs. Possible values are: "TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL", "DISABLED". |
| applicationSettings.storeBackend | string | `"memory"` | Sets the backend type for persistent data storage. Itential Automation Gateway (IAG) uses stores as key-value databases to persistently save objects. IAG supports three types of store backends: "local", "memory", "etcd", "dynamodb" |
| certManager.enabled | bool | `true` | Toggles the use of cert-manager for managing the TLS certificates. Setting this to false means that creation of the TLS certificates will be manual and outside of the chart. |
| certificate.dnsNames | list | `["iag5.example.com"]` | The list of static DNS names to include in the certificate. |
| certificate.duration | string | `"2160h"` | Specifies how long the certificate should be valid for (its lifetime). |
| certificate.enabled | bool | `true` | Toggle to use the certificate object or not |
| certificate.includeServiceIPs | bool | `true` | Include the list of service IP addresses. |
| certificate.ipAddress | list | `[]` | The list of static IPs to include in the certificate, if any. |
| certificate.issuerRef.kind | string | `"Issuer"` | The issuer type |
| certificate.issuerRef.name | string | `"iag5-ca-issuer"` | The name of the issuer with the CA reference. |
| certificate.keyStores | object | `{}` | Specifies any key store properties |
| certificate.privateKey | object | `{}` | Specifies any private key properties |
| certificate.renewBefore | string | `"48h"` | Specifies how long before the certificate expires that cert-manager should try to renew. |
| certificate.subject | object | `{"countries":["US"],"localities":["Atlanta"],"organizations":["Itential"],"postalCodes":["30309"],"provinces":["Georgia"],"streetAddresses":["1350 Spring St NW"]}` | Specifies any subject fields required |
| external-dns.enabled | bool | `false` | Optional dependency to generate a static external DNS name |
| hostname | string | `"iag5.example.com"` | The intended hostname to use |
| image.pullPolicy | string | `"IfNotPresent"` | The image pull policy |
| image.repository | string | `"497639811223.dkr.ecr.us-east-2.amazonaws.com/automation-gateway5"` | The image repository |
| image.tag | string | `"5.1.1-amd64"` | The image tag |
| imagePullSecrets | list | `[{"name":""}]` | The secrets object used to pull the image from the repo |
| issuer.caSecretName | string | `"itential-ca"` | The CA secret to be used by this issuer when creating TLS certificates. |
| issuer.enabled | bool | `true` | Toggle to use the issuer object or not |
| issuer.kind | string | `"Issuer"` | The issuer type. Template defaults to Issuer. |
| issuer.name | string | `"iag5-ca-issuer"` | The name of this issuer. |
| nodeSelector | object | `{}` | Additional nodeSelectors |
| podAnnotations | object | `{}` | Additional pod annotations |
| podLabels | object | `{}` | Additional pod labels |
| podSecurityContext | object | `{}` | Additional pod security context |
| port | int | `50051` | The intended port to use |
| runnerSettings.replicaCount | int | `0` | The number of runners to use. Set to zero to disable distributed runners. |
| securityContext | object | `{}` | Additional security context |
| serviceAccount.annotations | object | `{}` | Annotations to add to the service account. Use to attach an IAM role ARN for IRSA (`eks.amazonaws.com/role-arn`). |
| serviceAccount.automountServiceAccountToken | bool | `false` | Whether to automount the service account token into pods. |
| serviceAccount.create | bool | `false` | Create a Kubernetes service account for IAG5 pods. Required for IRSA. |
| serviceAccount.name | string | `""` | Name of the service account to create or use. When empty and create is true, defaults to the chart fullname. |
| serverSettings.connectEnabled | bool | `true` | Enables or disables the connection to Gateway Manager. |
| serverSettings.connectHosts | string | `"itential.example.com:8080"` | Configures the hostname and port used to connect to Gateway Manager. |
| serverSettings.connectInsecureEnabled | bool | `false` | Determines whether the gateway verifies TLS certificates when it connects to Itential Platform. When set to true, the gateway skips TLS certificate verification. We strongly recommend enabling TLS certificate verification in production environments. |
| serverSettings.replicaCount | int | `1` | The number of servers to use. At least one server must be defined. |
| service.annotations | object | `{"external-dns.alpha.kubernetes.io/hostname":"iag5.example.com","external-dns.alpha.kubernetes.io/ttl":"60","service.beta.kubernetes.io/aws-load-balancer-backend-protocol":"TCP","service.beta.kubernetes.io/aws-load-balancer-internal":"false","service.beta.kubernetes.io/aws-load-balancer-type":"nlb"}` | Annotations on the service object, passed through as is |
| service.name | string | `"iag5-service"` | The name of this Kubernetes service object |
| service.type | string | `"LoadBalancer"` | The service type |
| serviceAccount.annotations | object | `{}` | Annotations added to the service account. Set `eks.amazonaws.com/role-arn` here to enable IRSA. |
| serviceAccount.automountServiceAccountToken | bool | `false` | Controls whether the service account token is automounted into pods. |
| serviceAccount.create | bool | `false` | Specifies whether a service account should be created. |
| serviceAccount.name | string | `""` | The name of the service account to use. Defaults to the chart fullname when create is true and name is empty. |
| tolerations | list | `[]` | Additonal tolerations |
| useTLS | bool | `true` | Turn on TLS connectivity between all the members. All on or all off. |
| volumeMounts | list | `[]` | Additional volumeMounts on the output Deployment definition. |
| volumes | list | `[]` | Additional volumes on the output Deployment definition. |