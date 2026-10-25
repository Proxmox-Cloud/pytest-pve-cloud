# Python pkg - pytest-pve-cloud

Pytest project for pve cloud, contains basic methods needed in all e2e and tddog tests.

It also is host of tools for agentic integration testing utils like:

* vcluster testing namespace + mcp
* code rag database + mcp
* integration testing driven development agent SKILL.md

Deployments for agentic development need an existing kubernetes cluster, this module also bundles an unregistered
terraform module that sets up a kubernetes namespace with all needed apis / mcps / vector db for development.

At the moment this agent development stack is tailor made for Proxmox Cloud, but it principles and tools for agent safe / sandboxed
development can be extended to any software project. 

The core assumption behind the design of this stack is that anything an agent touches can and will break eventually. LLMs are highly sophisticated dice rollers, that simply run commands based on statistics. Entrusting these machines with real infrastructure is waiting for a disaster to happen.

## Development

Run `pip install -e .` to dynamically work with the package.

## TDD Watchdog

special variables for tddog.toml:

* `$VERSION` => will be replaced with version timestamp
* `$REGISTRY_IP` => will be replaced with first cli parameter which should point to the local ip address of your dev machine
* `$ARCH` => for tf provider builds, will automatically insert a golang conform arch of the current system

for a project to work with tdd dog it needs a git semver tag. Tddog creates local version artifacts taking the latest semver and replacing the patch version with a granular current timestamp long. This way in most other projects we simply have do use the pessimistic version operator and do an dependency update and are good to go.

python packages are dynamically versioned via setup tools and a _version.py file that is located in the src/PACKAGE folder. Tddog will generate this file, aswell as prod ci pipelines that also create it using the most recent prod tag.


## Qdrant Debug

``` sh
curl -s -X POST \
  http://qdrant.pve-cloud-integration:6333/collections/pxc_source/points/count \
  -H 'Content-Type: application/json' \
  -d '{"exact": true}' | jq

```
