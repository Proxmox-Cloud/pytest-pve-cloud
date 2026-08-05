import base64
import getpass
import logging
import os
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

import netifaces
import paramiko
import yaml
from jinja2 import Environment, FileSystemLoader
from pve_cloud.lib.inventory import get_pve_inventory
from pve_cloud.orm.alchemy import AcmeX509, ProxmoxCloudSecrets
from pytest_httpserver import HTTPServer
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from pve_cloud_test.cloud_fixtures import *

logger = logging.getLogger(__name__)


def get_ipv4(iface):
    if iface in netifaces.interfaces():
        info = netifaces.ifaddresses(iface)
        ipv4 = info.get(netifaces.AF_INET, [{}])[0].get("addr")
        return ipv4
    return None


def get_tf_env_vars(
    module_name, scenario_name, get_test_env
):
    # set env vars for terraform backend / variables passed via env
    tf_env_vars = {}
    tf_env_vars["PG_SCHEMA_NAME"] = f"pytest-{module_name}-{scenario_name}"

    tf_env_vars["TF_VAR_test_pve_conf"] = os.getenv(
        "PVE_CLOUD_TEST_CONF"
    )  # path to test env

    # current machine IPV4 made accessible for tf var
    tf_env_vars["TF_VAR_dev_machine_ipv4"] = get_ipv4(os.getenv("TDDOG_LOCAL_IFACE"))

    # connect to pve host and collect secrets / conf
    first_test_host = get_test_env["pve_test_cluster_hosts"][
        next(iter(get_test_env["pve_test_cluster_hosts"]))
    ]

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(first_test_host["ansible_host"], username="root")

    _, stdout, _ = ssh.exec_command("sudo cat /etc/pve/cloud/secrets/patroni.pass")
    patroni_pass = stdout.read().decode("utf-8")

    pg_conn_str = f"postgres://postgres:{patroni_pass}@{get_test_env['pve_test_cluster_floating_internal']}:5000/tf_states?sslmode=disable"
    pg_conn_str_orm = f"postgresql+psycopg2://postgres:{patroni_pass}@{get_test_env['pve_test_cluster_floating_internal']}:5000/pve_cloud?sslmode=disable"

    # variables that terraform applies in test will use
    tf_env_vars["PG_CONN_STR"] = pg_conn_str
    tf_env_vars["TF_VAR_pve_cloud_pg_cstr"] = pg_conn_str_orm
    tf_env_vars["TF_VAR_pve_ansible_host"] = first_test_host["ansible_host"]

    pve_inventory = get_pve_inventory(
        get_test_env["cloud_inventory"]["pve_cloud_domain"]
    )
    pve_64 = yaml.safe_dump(pve_inventory)
    tf_env_vars["TF_VAR_pve_inventory_b64"] = base64.b64encode(
        pve_64.encode("utf-8")
    ).decode("utf-8")

    # render terraformrc jinja2 and set env
    j2_env = Environment(loader=FileSystemLoader(f"{os.getcwd()}/tests"))
    rc_tmp = j2_env.get_template(".terraformrc-e2e.j2")

    with open(f"{os.getcwd()}/tests/.terraformrc-e2e", "w") as f:
        f.write(rc_tmp.render({"user_name": getpass.getuser()}))

    tf_env_vars["TF_CLI_CONFIG_FILE"] = f"{os.getcwd()}/tests/.terraformrc-e2e"


    # if the harbor_copy_mirror_host is defined in the kubernetes section, we set is as an env variable
    # to use in the pxc_helm_mirror terraform resource
    if "harbor_copy_mirror_host" in get_test_env["kubernetes"]:
        tf_env_vars["E2E_HARBOR_MIRROR_HOST"] = get_test_env["kubernetes"][
            "harbor_copy_mirror_host"
        ]

    # look for mirror vm presence and rsync terraform provider cache
    engine = create_engine(pg_conn_str_orm)

    with Session(engine) as session:
        stmt = select(ProxmoxCloudSecrets).where(
            ProxmoxCloudSecrets.cloud_domain
            == get_test_env["cloud_inventory"]["pve_cloud_domain"],
            ProxmoxCloudSecrets.secret_name == "cloud-mirror-vm",
        )
        cloud_mirror_vm = session.scalars(stmt).first()

    logger.info(
        f"found cloud mirror vm {cloud_mirror_vm.secret_data['mirror_vm_addr']}"
    )
    if cloud_mirror_vm:
        # create local cache idr
        local_cache_dir = f"{os.getenv('HOME')}/.terraform.d/plugin-cache/"

        if Path(local_cache_dir).exists():
            # rsync local to upstream
            upsync_cmd = [
                "rsync",
                "-avz",
                local_cache_dir,
                f"admin@{cloud_mirror_vm.secret_data['mirror_vm_addr']}:/home/admin/.cache/terraform-plugins/",
            ]
            logger.info(upsync_cmd)
            subprocess.run(upsync_cmd, check=True, text=True)

        Path(local_cache_dir).mkdir(parents=True, exist_ok=True)

        # rsync download
        subprocess.run(
            [
                "rsync",
                "-avz",
                f"admin@{cloud_mirror_vm.secret_data['mirror_vm_addr']}:/home/admin/.cache/terraform-plugins/",
                local_cache_dir,
            ],
            check=True,
            text=True,
        )

        # set the cache dir for terraform subprocess launches
        tf_env_vars["TF_PLUGIN_CACHE_DIR"] = local_cache_dir

    return tf_env_vars


def apply(
        module_name, scenario_name, kube_v1, get_test_env, extra_env={}
):
    logger.info(f"applying terraform {scenario_name}")

    tf_env_vars = get_tf_env_vars(
        module_name, scenario_name, get_test_env
    )

    # create env to pass to tf procs + write sourcable debug.env file
    terraform_env = os.environ.copy()

    with open(
        f"{os.getcwd()}/tests/scenarios/{scenario_name}/.debug.env", "w"
    ) as dbg_env:
        for ek, ev in (tf_env_vars | extra_env).items():
            terraform_env[ek] = ev
            dbg_env.write(f"export {ek}='{ev}'\n")

        # writeout pytest current flag to get same behaviour for tf provider
        dbg_env.write(
            f"export PYTEST_CURRENT_TEST='{os.getenv('PYTEST_CURRENT_TEST')}'"
        )

    subprocess.run(
        ["terraform", "init", "--upgrade"],
        cwd=f"{os.getcwd()}/tests/scenarios/{scenario_name}",
        env=terraform_env,
        check=True,
        text=True,
    )

    subprocess.run(
        ["terraform", "apply", "-auto-approve"],
        cwd=f"{os.getcwd()}/tests/scenarios/{scenario_name}",
        env=terraform_env,
        check=True,
        text=True,
    )

    # wait and assert all pods are running
    while True:
        all_pods_running = True

        for pod in kube_v1.list_pod_for_all_namespaces().items:
            if pod.metadata.deletion_timestamp:
                logger.info(f"skipping pod scheduled for deletion {pod.metadata.name}")
                continue

            phase = pod.status.phase
            assert (
                phase != "Failed"
            ), f"pod {pod.metadata.name} failed!"  # failed pods end tests immediatly

            if phase not in ["Running", "Succeeded"]:
                all_pods_running = False
                logger.info(f"pod {pod.metadata.name} in phase {phase}")

        if all_pods_running:
            break
        else:
            logger.info("pods still initializing")


def destroy(
    module_name, scenario_name, get_test_env, extra_env={}
):
    logger.info(f"destroying terraform {scenario_name}")

    tf_env_vars = get_tf_env_vars(
        module_name, scenario_name, get_test_env
    )

    # create env to pass to tf procs + write sourcable debug.env file
    terraform_env = os.environ.copy()

    for ek, ev in (tf_env_vars | extra_env).items():
        terraform_env[ek] = ev

    subprocess.run(
        ["terraform", "destroy", "-auto-approve"],
        cwd=f"{os.getcwd()}/tests/scenarios/{scenario_name}",
        env=terraform_env,
        check=True,
        text=True,
    )

    shutil.rmtree(f"{os.getcwd()}/tests/scenarios/{scenario_name}/.terraform")


@contextmanager
def get_mc_gw_http_mock():
    server = HTTPServer(host="0.0.0.0", port=8888)
    server.start()

    server.expect_request("/get-client-alertmanagers", method="GET").respond_with_json(
        [
            {
                "secret_name": "e2e-dummy",
                "secret_data": {
                    "host": "alrtmgr.e2e.dummy.domain",
                    "k8s_stack_name": "e2e-dummy-stack",
                    "password": "dummy-pw",
                },
                "cloud_domain": "e2e.dummy.domain",
            }
        ]
    )

    server.expect_request("/get-gotify-master", method="GET").respond_with_json(
        {
            "gotify_present": True,
            "gotify_access": {"host": "gotify.dummy.domain", "password": "dummy-pw"},
        }
    )

    server.expect_request("/get-victoria-clients", method="GET").respond_with_json(
        [
            {
                "secret_name": "e2e-dummy",
                "secret_data": {
                    "host": "vlogs.e2e.dummy.domain",
                    "k8s_stack_name": "e2e-dummy-stack",
                },
                "cloud_domain": "e2e.dummy.domain",
            }
        ]
    )

    server.expect_request("/get-vlselect-auth", method="GET").respond_with_json(
        {"auth_present": True, "vlselect_auth": {"password": "dummy-pw"}}
    )
    try:
        yield server
    finally:
        server.stop()


def launch_gw_mock_manually():
    with get_mc_gw_http_mock():
        input("running server, press key to terminate")
