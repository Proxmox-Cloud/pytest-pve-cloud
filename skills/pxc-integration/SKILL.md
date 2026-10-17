---
name: pxc-integration
description: >
  Agent testing instructions for all Proxmox Cloud (pve-cloud) related repositories.
  Read this skill immediatly after you have checked out / cloned a Proxmox Cloud repository.
---

# Proxmox Cloud Integration Testing

Before creating a merge request, ALWAYS execute tests inside the `tests/integration` directory.

## Executing Tests

Initially run `source tests/setup-integration-env.sh` once, this creates the necessary testing environment and activates it!

All tests are executed via `pytest`:

```bash
# Run entire integration suite
pytest -s tests/integration/

# Run a specific test
pytest -s tests/integration/test_modules.py::test_xyz
```

## Debugging / Development

Use the `--skip-cleanup` pytest flag to instruct the testing suite to keep test infrastructure for manual inspection:

After running your integration test you can use the following snipped to gain `kubectl` access to the testing infrastructure:

```bash
# fetch the vcluster api kubeconfig via pytest-pve-cloud cli command:
agent-kubeconfig > .agent-kubeconfig.yml

# set enviroment variables for kubectl
export KUBECONFIG=$(pwd)/.agent-kubeconfig.yml
INTEGRATION_NS=$(cat .itgr-test-ns)

# use whatever kubectl commands to analyze, for example:
kubectl get pods -n $INTEGRATION_NS 

# or commands like `kubectl exec ...`, `kubectl logs ...` to analyze the state of deployments
```

## Writing Tests

New features must include new integration tests or extend existing test! Analyze existing tests in the `tests/integration` directoy for patterns and examples on how to implement them.
