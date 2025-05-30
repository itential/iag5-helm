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

The creation of the Etcd cluster is outside of the scope of this chart. Itential routinely uses the
helm chart provided by [bitnami](https://artifacthub.io/packages/helm/bitnami/etcd).

#### DNS

This is not a requirement but more of an explanation.

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
helm install iag5 ./iag5
```
