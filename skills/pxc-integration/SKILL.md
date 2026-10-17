---
name: pxc-integration
description: >
  Integration testing instructions for all Proxmox Cloud (pve-cloud) related repositories.
  Read this skill before executing any automated testing!
---

# Integration Testing Proxmox Cloud

Tests meant for agent execution are always stored at `tests/integration`. Completely ignore
any e2e tests, these are meant for manual execution by human developers only!

## Running Tests

Setup the testing enviroment aswell as bash variables via:

```bash
source tests/setup-integration-env.sh
```

### Execute Tests

```bash
# Run entire integration suite
pytest -s tests/integration/

# Run specific test
pytest -s tests/integration/test_modules.py::test_xyz
```

### Debugging

Use `--skip-cleanup` flag to keep test infrastructure for inspection:

```bash
pytest -s tests/integration/ --skip-cleanup
```

Get kubeconfig for test namespace:

```bash
# fetch the vcluster api kubeconfig via pytest-pve-cloud cli command:
agent-kubeconfig > .agent-kubeconfig.yml

# set enviroment variables for kubectl
export KUBECONFIG=$(pwd)/.agent-kubeconfig.yml
INTEGRATION_NS=$(cat .itgr-test-ns)

# use whatever kubectl commands to analyze
kubectl get pods -n $INTEGRATION_NS
```

Use `kubectl exec` to inspect containers and analyze test state.

## Writing Tests

New features must include integration tests. Analyze existing tests in `tests/integration/` for patterns.

## Key Fixtures

- `integration_config` - Session-scoped vcluster/K8s setup
- `cloud_fixture` decorator - Tag-based fixture filtering
