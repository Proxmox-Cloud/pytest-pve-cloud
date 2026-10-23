---
name: pxc-integration
description: >
  Agent testing and development instructions for all Proxmox Cloud (pve-cloud) repositories.
  Read this skill immediately after you have checked out / cloned a Proxmox Cloud repository
  and again when you encounter problems with the pytest testing suite.
---

# Proxmox Cloud Integration Testing

Before creating a merge request, ALWAYS execute pytest tests ONLY inside the `tests/integration` directory, rely ONLY on the vcluster-mcp server for debugging kubernetes workloads and iterate as long as needed to get the tests to pass.

The testing suite will automatically, via the `integration_config` fixture (look up how to works using source-rag-mcp), deploy and initialize a connection to a kubernetes vcluster. For direct access to kubernetes rely strictly on the vcluster-mcp mcp server!

## Terraform

Proxmox Cloud includes repositories that are Terraform modules. You also test these EXCLUSIVELY through the pytest testing suite in the same `tests/integration` folder!

For Terraform repositories there is the additional `tests/integration/scenarios` folder. This folder contains several Terraform configurations that are invoked by the pytest terraform scenario fixtures usually located at `tests/integration/fixtures.py`.

## Executing Tests

Initially run `source tests/setup-integration-env.sh` ONCE, this will setup our test environment and activate the python virtual environment that contains the `pytest` cli tool. You should only need to run this once, however if you run into the error `bash: pytest: command not found` you might need to run it again, or directly activate the virtual environment referenced in the script.

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

For debugging / development you MUST rely EXCLUSIVELY on the vcluster-mcp server, as this is your only way to interact with the infrastructure deployed by the testing suite.

By passing the custom pytest flag `--skip-cleanup` you can prevent the teardown of our testing infrastructure and deployments. This then allows use to use the vcluster-mcp mcp server to figure out what went wrong. 

The testing suite generates a new namespace for each session writes it in the current working directory inside the `.itgr-test-ns` file. Use this as the namespace for vcluster-mcp tool calls!

### Terraform

NEVER use the terraform cli directly! Your only way of executing terraform is through the pytest testing suite and its fixtures. 

If you need to understand the core mechanism of how the pytest-pve-cloud testing module applies terraform use source-rag-mcp to query for `apply_integration / delete_ integration functions`. These functions do all the heavy lifting for you.

#### helm_release resource

For integration testing terraform scenarios you should always set `upgrade_install = true` for all terraform `helm_release` resources. This ensures proper behaviour during development, without this you will run into `cannot re-use a name that is still in use` and waste time on manual cleanup operations.

When you encounter errors NEVER guess helm values, ALWAYS use the mcp-helm mcp server to debug!

### kubectl

NEVER execute any kubectl commands directly! You do not have direct access to any kubernetes cluster! For any kubernetes related options ONLY use the vcluster-mcp mcp server.

When writing tests you have access via the `integration_config` to a python kubernetes client, but this can only be used inside pytests.

### Helm Charts

Helm charts are widely used accross Proxmox Cloud repositories. In ansible they are deployed using the `kubernetes.core.helm` module and in terraform via the `helm_release` resource. Helm repositories not yet present in the cache might need to be added manually using `helm repo add <repo-shortname> <repo-url>`, followed by a `helm repo update`.

If you encounter ANY problems / errors with deploying / applying a chart or before making ANY changes to a chart ALWAYS consolidate with the mcp-helm mcp server first.

### Cleanup Procedure

To clean up all remnants of pytest tests executed with the `--skip-cleanup` flag, you need to simply run the tests again but this time WITHOUT the flag. Do this final run without the flag after creating your merge request.

## Writing Tests

New features must include new integration tests or extend existing tests. Analyze existing tests in the `tests/integration` directory for patterns and examples on how to implement them.

If tests fail, proceed to the **Debugging / Development** section to analyze and fix the errors.

### Using TF_VAR_pytest_cwd for Path Access

The `TF_VAR_pytest_cwd` environment variable is **always available** in all integration Terraform configurations. 

This variable should be used in Terraform configurations to access the main module or submodules for testing. 

To gain access to this simply define it as a `variable "pytest_cwd" {...}` block in the integration testing terraform configuration. 

Use source-rag-mcp to find out further details when needed.

### Forwarding Ports from Pods

The `forward_ports_local_addr` context manager can be used to forward ports from services or pods. This is useful for testing APIs. Preferably use a service for testing. The context manager is typically used together with the `integration_config` fixture. Use it in combination with python `requests` to write assertions against endpoints.

Use source-rag-mcp to find out further details.
