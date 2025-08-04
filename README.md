# IAG5 Helm Chart

This chart is designed to enable the easy and rapid deployment of IAG5 environments and
architectures.

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
| https://charts.bitnami.com/bitnami | etcd | 11.3.0 |
| https://charts.jetstack.io | cert-manager | 1.12.3 |
| https://kubernetes-sigs.github.io/external-dns/ | external-dns | 1.17.0 |

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
# Set the number of runner replicas to zero
runnerSettings:
  replicaCount: 0
  env:
    ...

# Set the number of server replicas to one
serverSettings:
  replicaCount: 1
  env:
    GATEWAY_SERVER_DISTRIBUTED_EXECUTION: false
    ...

# Configure all pods with these values
applicationSettings:
  env:
    GATEWAY_COMMANDER_ENABLED: false
    GATEWAY_STORE_BACKEND: "memory"
```

### Distributed Server

An IAG5 distributed server is a server with a configurable number of runners. The server instructs
the runners to run IAG5 services, the runners obey. The server responds to a client, like the simple
server architecture. That client could be a local installation of IAG5 or an Itential Platform
server. It requires an Etcd cluster that it uses for communication. It consists of many pods. It
can support TLS connections.

The creation of the Etcd cluster is outside of the scope of this chart. Itential routinely uses the
helm chart provided by [bitnami](https://artifacthub.io/packages/helm/bitnami/etcd).

To create this environment the values file must provide the appropriate values for `serverSettings` and
`runnerSettings`. When the replicaCount is greater than zero in the `serverSettings` config
object the chart will create a pod as a server. When the replicaCount is greater than zero in the
`runnerSettings` config object the chart will create that many runners. The values in
`applicationSettings` effect all pods. These configuration sections in values.yaml file can also
contain all of the environment variables for a pod. For example, this will create a distributed
server:

```yaml
# Set the number of runner replicas to the desired number of runners
runnerSettings:
  replicaCount: 5
  env:
    ...

# Set the number of server replicas to one
serverSettings:
  replicaCount: 1
  env:
    GATEWAY_SERVER_DISTRIBUTED_EXECUTION: true
    ...

# Configure all pods with these values
applicationSettings:
  env:
    GATEWAY_COMMANDER_ENABLED: false
    GATEWAY_STORE_BACKEND: "etcd"
    GATEWAY_STORE_ETCD_HOSTS: "etcd.default.svc.cluster.local:2379"
```

### Using TLS connections

To enable TLS connections between IAG5 and Etcd you can use a configuration like this:

```yaml
# Set the number of runner replicas to the desired number of runners
runnerSettings:
  replicaCount: 5
  env:
    ...

# Set the number of server replicas to one
serverSettings:
  replicaCount: 1
  env:
    GATEWAY_SERVER_DISTRIBUTED_EXECUTION: true
    ...

# Configure all pods with these values
applicationSettings:
  etcdTlsSecretName: etcd-tls-secret
  env:
    GATEWAY_COMMANDER_ENABLED: false
    GATEWAY_STORE_BACKEND: "etcd"
    GATEWAY_STORE_ETCD_HOSTS: "etcd.default.svc.cluster.local:2379"
    GATEWAY_STORE_ETCD_USE_TLS: true
    GATEWAY_STORE_ETCD_CA_CERTIFICATE_FILE: /etc/ssl/etcd/ca.crt
    GATEWAY_STORE_ETCD_CERTIFICATE_FILE: /etc/ssl/etcd/tls-client.crt
    GATEWAY_STORE_ETCD_CLIENT_CERT_AUTH: true
    GATEWAY_STORE_ETCD_PRIVATE_KEY_FILE: /etc/ssl/etcd/tls-client.key
```

This requires an additional Secret to be installed in the Kubernetes environment. That Secret's
name must be provided to the pods as the value of `applicationSettings.etcdTlsSecretName`. The
Deployments will mount this secret as files whose paths are described by `GATEWAY_STORE_ETCD_CA_CERTIFICATE_FILE`, `GATEWAY_STORE_ETCD_CERTIFICATE_FILE`, and
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

#### Values

These values are intended to be refined when this chart is implemented. Many of these values are
simply values that were used during development and testing.

| Key | Type | Default | Description |
|:----|:-----|:--------|:------------|
| affinity | object | `{}` | Additional affinities |
| applicationSettings.env.GATEWAY_APPLICATION_CA_CERTIFICATE_FILE | string | `"/etc/ssl/gateway/ca.crt"` | When using certificates with TLS, this variable allows you to set the application CA. This is set on the application level since the CA should be used for all runner, server, and client implementations. |
| applicationSettings.env.GATEWAY_LOG_LEVEL | string | `"INFO"` | Sets the verbosity of the logs that the gateway displays to the console and file logs. Possible values are: TRACE, DEBUG, INFO, WARN, ERROR, FATAL, DISABLED. |
| applicationSettings.env.GATEWAY_STORE_BACKEND | string | `"memory"` | Sets the backend type for persistent data storage. Itential Automation Gateway (IAG) uses stores as key-value databases to persistently save objects. IAG supports three types of store backends: "local", "memory", "etcd", "dynamodb" |
| applicationSettings.env.GATEWAY_STORE_ETCD_CA_CERTIFICATE_FILE | string | `"/etc/ssl/etcd/ca.crt"` | The certificate authority certificate file that the gateway uses when it connects to the etcd store backend. |
| applicationSettings.env.GATEWAY_STORE_ETCD_CERTIFICATE_FILE | string | `"/etc/ssl/etcd/tls-client.crt"` | The public certificate file that the gateway uses when it connects to the etcd store backend. |
| applicationSettings.env.GATEWAY_STORE_ETCD_CLIENT_CERT_AUTH | bool | `false` | Determines the TLS authentication method used when connecting to an etcd store backend and GATEWAY_STORE_ETCD_USE_TLS is set to true. More information on this variable can be found in the documentation here: https://docs.itential.com/docs/gateway-store-variables |
| applicationSettings.env.GATEWAY_STORE_ETCD_HOSTS | string | `""` | Sets the etcd hosts that the gateway connects to for backend storage. A host entry consists of an address and port: hostname:port. If there are multiple etcd hosts, enter them as a space separated list: hostname1:port hostname2:port. |
| applicationSettings.env.GATEWAY_STORE_ETCD_PRIVATE_KEY_FILE | string | `"/etc/ssl/etcd/tls-client.key"` | The private key file that the gateway uses when it connects to the etcd store backend. |
| applicationSettings.env.GATEWAY_STORE_ETCD_USE_TLS | bool | `false` | Determines whether the gateway uses TLS authentication when it connects to the etcd store backend. |
| applicationSettings.etcdTlsSecretName | string | `"etcd-tls-secret"` | The name of the etcd TLS secret. This is mounted as a volume by the deployment and contains the Etcd TLS certs and keys. |
| certManager.enabled | bool | `true` | Toggles the use of cert-manager for managing the TLS certificates. Setting this to false means that creation of the TLS certificates will be manual and outside of the chart. |
| certificate.duration | string | `"2160h"` | Specifies how long the certificate should be valid for (its lifetime). |
| certificate.enabled | bool | `true` | Toggle to use the certificate object or not |
| certificate.issuerRef.kind | string | `"Issuer"` | The issuer type |
| certificate.issuerRef.name | string | `"iag5-ca-issuer"` | The name of the issuer with the CA reference. |
| certificate.renewBefore | string | `"48h"` | Specifies how long before the certificate expires that cert-manager should try to renew. |
| external-dns.enabled | bool | `false` | Optional dependency to generate a static external DNS name |
| hostname | string | `"iag5.example.com"` | The intended hostname to use |
| image.pullPolicy | string | `"IfNotPresent"` | The image pull policy |
| image.repository | string | `"497639811223.dkr.ecr.us-east-2.amazonaws.com/automation-gateway5"` | The image repository |
| image.tag | string | `"5.1.1-amd64"` | The image tag |
| imagePullSecrets | list | `[{"name":""}]` | The secrets object used to pull the image from the repo |
| issuer.caSecretName | string | `"iag5-ca"` | The CA secret to be used by this issuer when creating TLS certificates. |
| issuer.enabled | bool | `true` | Toggle to use the issuer object or not |
| issuer.name | string | `"iag5-ca-issuer"` | The name of this issuer. |
| nodeSelector | object | `{}` | Additional nodeSelectors |
| podAnnotations | object | `{}` | Additional pod annotations |
| podLabels | object | `{}` | Additional pod labels |
| podSecurityContext | object | `{}` | Additional pod security context |
| port | int | `50051` | The intended port to use |
| runnerSettings.env.GATEWAY_RUNNER_CERTIFICATE_FILE | string | `"/etc/ssl/gateway/tls.crt"` | Sets the full path to the certificate file that the gateway runner uses when it connects to a gateway server. This setting is required when GATEWAY_RUNNER_USE_TLS is enabled. If cert-manager is used then this default value doesn't need to change. |
| runnerSettings.env.GATEWAY_RUNNER_PRIVATE_KEY_FILE | string | `"/etc/ssl/gateway/tls.key"` | Sets the full path to the private key file the gateway runner uses when it connects to a gateway server. This setting is required when GATEWAY_RUNNER_USE_TLS is enabled. If cert-manager is used then this default value doesn't need to change. |
| runnerSettings.env.GATEWAY_RUNNER_USE_TLS | bool | `false` | Determines whether or not a gateway runner requires TLS when connecting to a gateway server. |
| runnerSettings.replicaCount | int | `0` | The number of runners to use. Set to zero to disable distributed runners. |
| securityContext | object | `{}` | Additional security context |
| serverSettings.env.GATEWAY_SERVER_CERTIFICATE_FILE | string | `"/etc/ssl/gateway/tls.crt"` | The full path to the certificate file the gateway server uses when it serves connections to gateway clients. This setting is required when GATEWAY_SERVER_USE_TLS is enabled. If cert-manager is used then this default value doesn't need to change. |
| serverSettings.env.GATEWAY_SERVER_PRIVATE_KEY_FILE | string | `"/etc/ssl/gateway/tls.key"` | The full path to the private key file that the gateway server uses when serving connections to gateway clients. Required when GATEWAY_SERVER_USE_TLS is enabled. If cert-manager is used then this default value doesn't need to change. |
| serverSettings.env.GATEWAY_SERVER_USE_TLS | bool | `false` | Determines whether a gateway server requires TLS when serving connections to gateway clients. |
| serverSettings.replicaCount | int | `1` | The number of servers to use. At least one server must be defined. |
| service.annotations | object | `{"external-dns.alpha.kubernetes.io/hostname":"iag5.example.com","external-dns.alpha.kubernetes.io/ttl":"60","service.beta.kubernetes.io/aws-load-balancer-backend-protocol":"tcp","service.beta.kubernetes.io/aws-load-balancer-internal":"false","service.beta.kubernetes.io/aws-load-balancer-type":"nlb"}` | Annotations on the service object, passed through as is |
| service.name | string | `"iag5-service"` | The name of this Kubernetes service object |
| service.type | string | `"LoadBalancer"` | The service type |
| tolerations | list | `[]` | Additonal tolerations |
| volumeMounts | list | `[]` | Additional volumeMounts on the output Deployment definition. |
| volumes | list | `[]` | Additional volumes on the output Deployment definition. |