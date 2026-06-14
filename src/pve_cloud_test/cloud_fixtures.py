import functools
import inspect
import logging
import os
import re

import jsonschema
import paramiko
import pytest
import redis
import yaml
from proxmoxer import ProxmoxAPI
from pve_cloud.lib.inventory import (get_cloud_domain, get_pve_inventory,
                                     get_target_cluster)
from pve_cloud.lib.ssh import connect_host

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

            # get pytest request object to extract globally set --fixture-tags
            request = kwargs.get("request")
            if request is None:
                raise RuntimeError(
                    f"Cannot find request object defined in {func.__name__} fixture args! Pytest requests object needs to be in params for cloud_fixture to work!"
                )

            # if this is defined we skip fixtures alltogether
            skip_fixtures = request.config.getoption("--skip-fixtures")
            if skip_fixtures:
                logger.info(
                    f"Skipping fixture {func.__name__} due --skip-fixtures flag"
                )

                # mimic fixture returns and pass blanks
                yield
                return

            # filter out fixtures that are not specifically targeted
            allowed_tags_opt = request.config.getoption("--fixture-tags")
            if allowed_tags_opt:
                allowed_tags = allowed_tags_opt.split(",")
                if not any(tag in allowed_tags for tag in func._tags):
                    logger.info(f"Skipping fixture {func.__name__} due to tags")

                    # mimic fixture returns and pass blanks
                    yield
                    return

            # when fixtures are executed we give the option to skip the cleanup part
            skip_cleanup = request.config.getoption("--skip-cleanup")

            result = func(*args, **kwargs)

            if inspect.isgenerator(result):
                logger.info("is generator")

                if skip_cleanup:
                    logger.info(
                        "yielding and skipping cleanup due to --skip-cleanup flag"
                    )

                    yield next(
                        result, None
                    )  # might not yield conditionally (like setup_ceph_dhcp_lxcs fixture)

                else:
                    yield from result

            else:
                logger.info("is result")
                yield result  # still yield because of wrappers

        return wrapper

    return decorator


# load the test environment yaml from parameters
@pytest.fixture(scope="session")
def get_test_env(request):
    test_pve_yaml_file = os.getenv("PVE_CLOUD_TEST_CONF")
    assert test_pve_yaml_file

    assert test_pve_yaml_file is not None
    with open(test_pve_yaml_file, "r") as file:
        test_pve_conf = yaml.safe_load(file)

    logger.info(f"terraform inv file {test_pve_yaml_file}")

    # load schema and validate
    with open(
        os.path.dirname(os.path.realpath(__file__)) + "/test_env_schema.yaml"
    ) as file:
        test_env_schema = yaml.safe_load(file)

    jsonschema.validate(instance=test_pve_conf, schema=test_env_schema)

    # render vlan tag
    if "pve_test_net0_vlan_tag" in test_pve_conf:
        test_pve_conf["net0_vlan_tag_rendered"] = (
            f",tag={test_pve_conf['pve_test_net0_vlan_tag']}"
        )

    # validate that target copy pve system is directly accessible (no jump host validation supported)
    copy_cloud_domain = get_cloud_domain(
        test_pve_conf["kubernetes"]["k8s_tls_copy_target_pve"]
    )
    copy_pve_inventory = get_pve_inventory(copy_cloud_domain)

    copy_target_cluster = get_target_cluster(
        copy_pve_inventory,
        test_pve_conf["kubernetes"]["k8s_tls_copy_target_pve"],
        target_cloud_domain=copy_cloud_domain,
    )

    assert "jump_hosts" not in copy_pve_inventory[copy_target_cluster]

    return test_pve_conf


def get_first_host(get_test_env):
    return get_test_env["pve_test_cluster_hosts"][
        next(iter(get_test_env["pve_test_cluster_hosts"]))
    ]["ansible_host"]


@pytest.fixture(scope="session")
def fetch_default_gw_ns(get_test_env):

    with connect_host(
        get_first_host(get_test_env), get_test_env.get("pve_test_cluster_jump_host")
    ) as client:

        _, stdout, _ = client.exec_command(
            "ip route show default 2>/dev/null | awk '{print $3}'"
        )
        gateway = stdout.read().decode("utf-8").strip()
        logger.info(gateway)

        _, stdout, _ = client.exec_command(
            "grep -E '^nameserver [0-9]+' /etc/resolv.conf 2>/dev/null | awk '{print $2}'"
        )
        nameservers = stdout.read().decode("utf-8").strip().splitlines()
        logger.info(nameservers)

    return gateway, " ".join(nameservers)


@pytest.fixture(scope="session")
def get_cloud_secrets(get_test_env):
    logger.info("setting pve cloud auth env variables for tf")

    with connect_host(
        get_first_host(get_test_env), get_test_env.get("pve_test_cluster_jump_host")
    ) as ssh:
        _, stdout, _ = ssh.exec_command("sudo cat /etc/pve/cloud/secrets/patroni.pass")
        patroni_pass = stdout.read().decode("utf-8")

        pg_conn_str = f"postgres://postgres:{patroni_pass}@{get_test_env['pve_test_cluster_floating_internal']}:5000/tf_states?sslmode=disable"
        pg_conn_str_orm = f"postgresql+psycopg2://postgres:{patroni_pass}@{get_test_env['pve_test_cluster_floating_internal']}:5000/pve_cloud?sslmode=disable"

        # fetch bind update key for ingress dns validation
        _, stdout, _ = ssh.exec_command("sudo cat /etc/pve/cloud/secrets/internal.key")
        bind_key_file = stdout.read().decode("utf-8")

        bind_internal_key = re.search(r'secret\s+"([^"]+)";', bind_key_file).group(1)

    return {
        "bind_internal_key": bind_internal_key,
        "pg_conn_str": pg_conn_str,
        "pg_conn_str_orm": pg_conn_str_orm,
    }


# connect proxmoxer to pve cluster
@pytest.fixture(scope="session")
def get_proxmoxer(get_test_env):
    first_test_host = get_test_env["pve_test_cluster_hosts"][
        next(iter(get_test_env["pve_test_cluster_hosts"]))
    ]

    proxmox = ProxmoxAPI(
        first_test_host["ansible_host"], user="root", backend="ssh_paramiko"
    )
    nodes = proxmox.nodes.get()

    assert nodes

    return proxmox
