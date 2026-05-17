# Kubernetes Operator Example

This example shows the declarative operator path for a distributed VeriLoad run.
The `VeriLoadRun` creates the controller Service, controller Job, worker Job,
cluster token Secret, inline config ConfigMap, and artifact PVC.

Install the operator first, either from the generated manifest:

```bash
uv run veriload k8s operator-manifest \
  --output k8s/veriload-operator.yaml \
  --image ghcr.io/acme/veriload-operator:latest
kubectl apply -f k8s/veriload-operator.yaml
```

or from the Helm chart:

```bash
helm install veriload-operator charts/veriload-operator \
  --namespace veriload-system \
  --create-namespace \
  --set image.repository=ghcr.io/acme/veriload-operator \
  --set image.tag=latest
```

Then apply the scenario support files and the run:

```bash
kubectl create namespace loads
kubectl apply -f examples/kubernetes_operator/scenario-files-configmap.yaml
kubectl apply -f examples/kubernetes_operator/veriloadrun.yaml
kubectl get veriloadruns -n loads
```

The scenario image in `spec.image` must contain VeriLoad plus any runtime
dependencies. This example mounts `scenario.py` from a ConfigMap at `/workspace`
so the image can stay generic while the test logic changes independently.

After the controller completes, the operator starts a collector job. The compact
summary lands in `.status.summary`, and full artifacts remain on the
`checkout-load-artifacts` PVC because `artifacts.retain` is true.
