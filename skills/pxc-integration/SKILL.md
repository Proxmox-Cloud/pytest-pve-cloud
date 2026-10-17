---
name: pxc-integration
description: >
  Agent testing instructions for all Proxmox Cloud (pve-cloud) related repositories.
  Read this skill immediately after you have checked out / cloned a Proxmox Cloud repository.
---

# Proxmox Cloud Integration Testing

Before creating a merge request, ALWAYS execute tests inside the `tests/integration` directory.

The testing suite will automatically, via the `integration_config` fixture, deploy and initialize the connection to a kubernetes vcluster. 

The fixture itself returns the connection, as well as a path to a temporary kubeconfig to connect to the vcluster. The vcluster is destroyed in the pytest fixture's teardown phase.

## Terraform

Proxmox Cloud includes repositories that contain Terraform modules. You also test these exclusively through the pytest testing suite! 

For these repositories you will find an additional `scenarios` folder in the `tests/integration` directory. This folder contains several Terraform configurations that are invoked by the pytest scenario fixtures. Do not apply them manually; the pytest fixtures handle this automatically.

## Executing Tests

Initially run `source tests/setup-integration-env.sh` once. This script sets up the necessary testing environment including dependencies and configuration.

All tests are executed via `pytest`:

```bash
# Run entire integration suite
pytest -s tests/integration/

# Run a specific test
pytest -s tests/integration/test_modules.py::test_xyz
```

Do not combine the `pytest` commands with the `source` setup command. Capture the full output of all `pytest` commands for debugging purposes.

## Debugging / Development

When you encounter errors / timeouts from the testing suite you should do an in depth analysis and try to fix the errors.

By passing the custom pytest flag `--skip-cleanup`, we skip the teardown of our testing infrastructure and deployments. This allows us to directly connect to it for debugging / development purposes using the following script:

```bash
# fetch the vcluster api kubeconfig via pytest-pve-cloud cli command:
agent-kubeconfig > .agent-kubeconfig.yml

# set environment variables for kubectl
export KUBECONFIG=$(pwd)/.agent-kubeconfig.yml
INTEGRATION_NS=$(cat .itgr-test-ns)

# use whatever kubectl commands to analyze, for example:
kubectl get pods -n $INTEGRATION_NS

# execute bash commands in a container
kubectl exec <pod-name> -n $INTEGRATION_NS -- ls -la 

# forward port of a pod / service, run curl commands and cleanup
kubectl port-forward pod/<pod-name> -n $INTEGRATION_NS 8080:80 > /tmp/<pod-name>-fwd.log 2>&1 &
PF_PID=$!

curl http://localhost:8080/some/endpoint

kill "$PF_PID"

# inspect a pods logs to judge its state
kubectl logs <pod-name> -n $INTEGRATION_NS

```

The testing fixtures create isolated pods in the vcluster. Use the debugging procedures below to interact with them after tests complete.

### Debugging Terraform

To debug the Terraform configurations in the `tests/integration/scenarios` folder, you can use the following snippet AFTER running the pytest testing suite with the `--skip-cleanup` flag.

```bash
cd tests/integration/scenarios/<scenario-name>

# load the environmental variables containing TF_VAR_ definitions
# these variables contain everything needed for authentication with the vcluster
# integration testing infrastructure. This file is written by the pytest testing suite!
source .debug.env

# => now you can run Terraform commands directly
terraform plan
terraform apply -auto-approve

```

## Writing Tests

New features must include new integration tests or extend existing tests. Analyze existing tests in the `tests/integration` directory for patterns and examples on how to implement them.

If tests fail, proceed to the **Debugging / Development** section to analyze and fix the errors.

### Forwarding Ports from Pods

The `forward_ports_local_addr` context manager can be used to forward ports from pods or services to your local machine. This is useful for testing APIs inside pods. It is typically used together with the `integration_config` fixture.

#### Example: Forwarding a Pod Port and Testing with requests

```python
import requests
from pve_cloud_test.integration import forward_ports_local_addr

def test_my_api(request, integration_config):
    kubeconfig_path, k8s_client, v1, namespace = integration_config
    
    # Forward port 8080 from the pod to localhost
    with forward_ports_local_addr(
        "my-api-pod",           # pod name
        namespace,              # namespace from integration_config
        ["8080:80"],            # port mapping: local:container
        target_type="pod",      # or "service"
        kubeconfig_path=kubeconfig_path,
    ) as local_addr:
        # local_addr contains the loopback address (e.g., 127.0.0.2)
        api_url = f"http://{local_addr}:8080/health"
        
        # Assert against the API
        response = requests.get(api_url)
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
```

#### Example: Forwarding a Service Port

```python
from pve_cloud_test.integration import forward_ports_local_addr

def test_my_service(request, integration_config):
    kubeconfig_path, k8s_client, v1, namespace = integration_config
    
    # Forward port from a service
    with forward_ports_local_addr(
        "my-service",
        namespace,
        ["9090:3000"],
        target_type="service",
        kubeconfig_path=kubeconfig_path,
    ) as local_addr:
        response = requests.get(f"http://{local_addr}:9090/api/test")
        assert response.status_code == 200
```

### Parameters

- `target_name`: Name of the pod or service
- `namespace`: Kubernetes namespace (from `integration_config` fixture)
- `ports`: List of port mappings (e.g., `["8080:80"]`)
- `target_type`: Either `"pod"` or `"service"` (default: `"pod"`)
- `kubeconfig_path`: Path to kubeconfig file from `integration_config` fixture

