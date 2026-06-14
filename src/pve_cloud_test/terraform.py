import base64
import getpass
import logging
import os
import shutil
import subprocess
from contextlib import contextmanager

import netifaces
import paramiko
import yaml
from jinja2 import Environment, FileSystemLoader
from pve_cloud.lib.inventory import get_pve_inventory
from pytest_httpserver import HTTPServer

logger = logging.getLogger(__name__)


def get_ipv4(iface):
    if iface in netifaces.interfaces():
        info = netifaces.ifaddresses(iface)
        ipv4 = info.get(netifaces.AF_INET, [{}])[0].get("addr")
        return ipv4
    return None


def get_tf_env_vars(
    module_name, scenario_name, kube_v1, get_test_env, get_kubespray_inv
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

    # write testing kubespray inv and set the path (for provider init)
    tf_env_vars["TF_VAR_e2e_kubespray_inv"] = get_kubespray_inv

    return tf_env_vars


def apply(
    module_name, scenario_name, kube_v1, get_test_env, get_kubespray_inv, extra_env={}
):
    logger.info(f"applying terraform {scenario_name}")

    tf_env_vars = get_tf_env_vars(
        module_name, scenario_name, kube_v1, get_test_env, get_kubespray_inv
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
    module_name, scenario_name, kube_v1, get_test_env, get_kubespray_inv, extra_env={}
):
    logger.info(f"destroying terraform {scenario_name}")

    tf_env_vars = get_tf_env_vars(
        module_name, scenario_name, kube_v1, get_test_env, get_kubespray_inv
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
