import functools
import inspect
import logging
import os

import jsonschema
import pytest
import redis
import yaml
from proxmoxer import ProxmoxAPI

from pve_cloud_test.tdd_watchdog import get_ipv4

logger = logging.getLogger(__name__)


def get_tdd_version(artifact_key):
    if os.getenv("TDDOG_LOCAL_IFACE"):
        # get version for image from redis
        r = redis.Redis(host="localhost", port=6379, db=0)
        local_build_version = r.get(f"version.{artifact_key}").decode()

        if local_build_version:
            logger.info(f"found local version {local_build_version}")

            return local_build_version, get_ipv4(os.getenv("TDDOG_LOCAL_IFACE"))
        else:
            logger.warning(
                f"did not find local build pve cloud version for {artifact_key} even though TDDOG_LOCAL_IFACE env var is defined"
            )

    return None, None


def get_tdd_ip():
    if os.getenv("TDDOG_LOCAL_IFACE"):
        return get_ipv4(os.getenv("TDDOG_LOCAL_IFACE"))

    return None


# this prepends a custom wrapper func to all our e2e fixtures and allows easy toggeling
# cloud fixtures can be annotated with this and and a value tuple of tags as value
# they also automatically get the standard pytest fixture decorator
# depending on the pytest --fixture-tags paramater, which takes a csv of fixture tags
# the fixtures are automatically skipped if not in the csv
def cloud_fixture(*tags):
    def decorator(func):
        func._tags = tags

        logger.info(f"called decorator for {func.__name__}")

        @pytest.fixture(scope="session")
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            logger.info(f"called wrapper for {func.__name__}")

            request = kwargs.get("request")
            if request is None:
                for arg in args:
                    if hasattr(arg, "config"):
                        request = arg
                        break

            if request is None:
                logger.warning("Cannot find request object; running fixture anyway")
                return func(*args, **kwargs)

            allowed_tags_opt = request.config.getoption("--fixture-tags")
            if allowed_tags_opt:
                allowed_tags = allowed_tags_opt.split(",")
                if not any(tag in allowed_tags for tag in func._tags):
                    logger.info(f"Skipping fixture {func.__name__} due to tags")
                    if inspect.isgeneratorfunction(func):
                        yield
                        return
                    else:
                        return

            result = func(*args, **kwargs)

            if inspect.isgenerator(result):
                logger.info("is generator")
                yield from result
            else:
                logger.info("is result")
                return result

        return wrapper

    return decorator


# load the test environment yaml from parameters
@pytest.fixture(scope="session")
def get_test_env(request):
    test_pve_yaml_file = os.getenv("PVE_CLOUD_TEST_CONF")
    assert test_pve_yaml_file

    os.environ["TF_VAR_test_pve_conf"] = test_pve_yaml_file

    assert test_pve_yaml_file is not None
    with open(test_pve_yaml_file, "r") as file:
        test_pve_conf = yaml.safe_load(file)

    # load schema and validate
    with open(
        os.path.dirname(os.path.realpath(__file__)) + "/test_env_schema.yaml"
    ) as file:
        test_env_schema = yaml.safe_load(file)

    jsonschema.validate(instance=test_pve_conf, schema=test_env_schema)

    return test_pve_conf


# connect proxmoxer to pve cluster
@pytest.fixture(scope="session")
def get_proxmoxer(get_test_env):
    first_test_host = get_test_env["pve_test_hosts"][
        next(iter(get_test_env["pve_test_hosts"]))
    ]

    proxmox = ProxmoxAPI(
        first_test_host["ansible_host"], user="root", backend="ssh_paramiko"
    )
    nodes = proxmox.nodes.get()

    assert nodes

    return proxmox
