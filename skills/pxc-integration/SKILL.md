---
name: pxc-integration
description: >
  Agent testing instructions for all Proxmox Cloud (pve-cloud) related repositories.
  Read this skill immediatly after you have checked out / cloned a Proxmox Cloud repository.
---

# Proxmox Cloud Integration Testing

Before creating a merge request, ALWAYS execute tests inside the `tests/integration` directory.

The testing suite will automatically, via the `integration_config` fixture, deploy and initialize the connection to a kubernetes vcluster. 

The fixture itself returns the connection, aswell as a path to a temporary kubeconfig to connect to the vcluster. The vcluster is destroyed in the fixtures teardown.

## Terraform

Proxmox Cloud includes repositories that contain terraform modules. You also test these exclusively through the pytest testing suite! 

For these repositories you will find a additional `scenarios` folder in the `tests/integration` directory. This folder contains several terraform configurations that are invoked by the according pytest scenario fixtures. You do not apply them manually!

## Executing Tests

Initially run `source tests/setup-integration-env.sh` once, this creates the necessary testing environment and activates it!

All tests are executed via `pytest`:

```bash
# Run entire integration suite
pytest -s tests/integration/

# Run a specific test
pytest -s tests/integration/test_modules.py::test_xyz
```

Do not combine the `pytest` commands with the `source` setup command. Also don't truncate the `pytest` commands, their entire output is relevant!

## Debugging / Development

When you encounter errors / timeouts from the testing suite you should do an in depth analysis and try to fix the errors.

By passing the custom pytest flag `--skip-cleanup`, we skip the teardown of our testing infrastructure and deployments. This allows us to directly connect to it for debugging / development purposes using the following script:

```bash
# fetch the vcluster api kubeconfig via pytest-pve-cloud cli command:
agent-kubeconfig > .agent-kubeconfig.yml

# set enviroment variables for kubectl
export KUBECONFIG=$(pwd)/.agent-kubeconfig.yml
INTEGRATION_NS=$(cat .itgr-test-ns)

# use whatever kubectl commands to analyze, for example:
kubectl get pods -n $INTEGRATION_NS

# execute bash commands in a container
kubectl exec <pod-name> -$INTEGRATION_NS -- ls -la 

# forward port of a pod / service, run curl commands and cleanup
kubectl port-forward pod/<pod-name> -n $INTEGRATION_NS 8080:80 > /tmp/<pod-name>-fwd.log 2>&1 &
PF_PID=$!

curl http://localhost:8080/some/endpoint

kill "$PF_PID"

# inspect a pods logs to judge its state
kubectl logs <pod-name> -n $INTEGRATION_NS

```

You can only interact with the pods via the testing fixtures, you do not have direct access to them, they are in an isolated network.

### Debugging Terraform

To debug the terraform configurations in the `tests/integration/scenarios` folder, you can use the following snippet AFTER running the pytest testing suite with the `--skip-cleanup` flag.

```bash
cd tests/integration/scenario/<scenario-name>

# load the enviromental variables containing TF_VAR_ definitions
# these variables contain everything needed for authentication with the vcluster
# integration testing infrastructure. This file is written by the pytest testing suite!
source .debug.env 

# => now you can run terraform commands directly
terraform plan
terraform apply -auto-approvey

```


## Writing Tests

New features must include new integration tests or extend existing test! Analyze existing tests in the `tests/integration` directoy for patterns and examples on how to implement them.
