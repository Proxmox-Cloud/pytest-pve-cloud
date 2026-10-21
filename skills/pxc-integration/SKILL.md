---
name: pxc-integration
description: >
  Agent testing instructions for all Proxmox Cloud (pve-cloud) repositories.
  Read this skill immediately after you have checked out / cloned a Proxmox Cloud repository
  and again when you encounter problems with the pytest testing suite.
---

# Proxmox Cloud Integration Testing

Before creating a merge request, ALWAYS execute pytest tests ONLY inside the `tests/integration` directory and iterate as long as needed to get the tests to pass.

The testing suite will automatically, via the `integration_config` fixture, deploy and initialize a connection to a kubernetes vcluster. For direct access rely strictly on the vcluster-mcp mcp server!

## Terraform

Proxmox Cloud includes repositories that are Terraform modules. You also test these exclusively through the pytest testing suite in the same `tests/integration` folder! For Terraform repositories there is the additional `tests/integration/scenarios` folder. This folder contains several Terraform configurations that are invoked by the pytest terraform scenario fixtures usually located at `tests/integration/fixtures.py`.

## Executing Tests

Initially ALWAYS run `source tests/setup-integration-env.sh`, this will setup our test environment and activate the python virtual environment that contains the `pytest` cli tool. You should only need to run this once, however if you run into the error `bash: pytest: command not found` you might need to run it again, or directly activate the virtual environment referenced in the script.

All tests are executed via `pytest`:

```bash
# Run entire integration suite
pytest -s tests/integration/

# Run a specific test
pytest -s tests/integration/test_modules.py::test_xyz
```

Execute the `source tests/setup-integration-env.sh` script and the `pytest` commands seperately.

The pytest commands also might take a long time to complete as they bootstrap and deploy infrastructure.

They also produce a lot of highly relevant output so ALWAYS use atleast a generous `| tail -n 500` for truncating it.

## Debugging / Development

For debugging / development you should rely exclusively on the vcluster-mcp server, as this is your only way to interact with the infrastructure deployed by the testing suite.

By passing the custom pytest flag `--skip-cleanup` you can the teardown of our testing infrastructure and deployments. This then allows use to use the vcluster-mcp mcp server to figure out what went wrong. 

The testing suite generates a new namespace for each run and writes it in the current working directory inside the `.itgr-test-ns` file. Use this as the namespac for vcluster-mcp tool calls!

When you run the test with the `--skip-cleanup` flag it is ESSENTIAL to run the tests again at the end WITHOUT the flag once more! Only this way we clean up our testing infrastructure properly.

#### Debugging Terraform

Rely exclusively on the pytest testing suite and its terraform scenario fixtures. If you absolutely must run terraform direclty look for a `.debug.env` file in the scenarios folder, that gets written by the testing suite. Before running any terraform command run `source` on that .env file!

## Writing Tests

New features must include new integration tests or extend existing tests. Analyze existing tests in the `tests/integration` directory for patterns and examples on how to implement them.

If tests fail, proceed to the **Debugging / Development** section to analyze and fix the errors.

### Using TF_VAR_pytest_cwd for Path Access

The `TF_VAR_pytest_cwd` environment variable is **always available** in all integration Terraform configurations. 

This variable should be used in Terraform configurations to access the main module or submodules for testing. 

To gain access to this simply define it as a `variable "pytest_cwd" {...}` block in the integration testing terraform configuration. 

The pytest testing suite always passes this and it will always point to the root of the main module.

### Forwarding Ports from Pods

The `forward_ports_local_addr` context manager can be used to forward ports from services or pods. This is useful for testing APIs. Preferably use a service for testing. The context manager is typically used together with the `integration_config` fixture.

#### Parameters

- `target_name`: Name of the pod or service
- `namespace`: Kubernetes namespace (from `integration_config` fixture)
- `ports`: List of port mappings (e.g., `["8080:80"]`)
- `target_type`: Either `"pod"` or `"service"` (default: `"pod"`)
- `kubeconfig_path`: Path to kubeconfig file from `integration_config` fixture

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
