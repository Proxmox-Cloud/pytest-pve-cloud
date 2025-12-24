# Python pkg - pytest-pve-cloud

Pytest project for pve cloud, contains basic methods needed in all e2e and tddog tests.

## Development

Run `pip install -e .` to dynamically work with the package.

## TDD Watchdog

special variables for tddog.toml:

* `$VERSION` => will be replaced with version timestamp
* `$REGISTRY_IP` => will be replaced with first cli parameter which should point to the local ip address of your dev machine
* `$ARCH` => for tf provider builds, will automatically insert a golang conform arch of the current system

for a project to work with tdd dog it needs a git semver tag. Tddog creates local version artifacts taking the latest semver and replacing the patch version with a granular current timestamp long. This way in most other projects we simply have do use the pessimistic version operator and do an dependency update and are good to go.

python packages are dynamically versioned via setup tools and a _version.py file that is located in the src/PACKAGE folder. Tddog will generate this file, aswell as prod ci pipelines that also create it using the most recent prod tag.