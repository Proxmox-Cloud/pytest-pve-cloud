---
name: pxc-integration
description: >
  Agent testing instructions for all Proxmox Cloud (pve-cloud) related repositories.
  Read this skill before executing any automated testing!
---

# Proxmox Cloud Integration Testing

Execute tests only located at `tests/integration`.

Initially always once run `source tests/setup-integration-env.sh`, this creates the necessary testing environment and activates it!

## Executing Tests

All tests are executed via `pytest`:

```bash
# Run entire integration suite
pytest -s tests/integration/

# Run a specific test
pytest -s tests/integration/test_modules.py::test_xyz
```

## Debugging

Use the `--skip-cleanup` flag to instruct the testing suite to keep test infrastructure for inspection:

```bash
pytest -s tests/integration/ --skip-cleanup
```

After running the suite once you can then use this snipped to gain `kubectl` access to the testing infrastructure:

```bash
# fetch the vcluster api kubeconfig via pytest-pve-cloud cli command:
agent-kubeconfig > .agent-kubeconfig.yml

# set enviroment variables for kubectl
export KUBECONFIG=$(pwd)/.agent-kubeconfig.yml
INTEGRATION_NS=$(cat .itgr-test-ns)

# use whatever kubectl commands to analyze:
kubectl get pods -n $INTEGRATION_NS

# use additional commands like `kubectl exec ...`, `kubectl logs ...` to analyze
```

## Writing Tests

New features must include or extend. Analyze existing tests in `tests/integration/` for patterns.

## Key Fixtures

- `integration_config` - Session-scoped vcluster/K8s setup
- `cloud_fixture` decorator - Tag-based fixture filtering
