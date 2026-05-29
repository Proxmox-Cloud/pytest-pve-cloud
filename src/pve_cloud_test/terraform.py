import getpass
import logging
import os
import shutil
import subprocess

import netifaces
from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)


def get_ipv4(iface):
    if iface in netifaces.interfaces():
        info = netifaces.ifaddresses(iface)
        ipv4 = info.get(netifaces.AF_INET, [{}])[0].get("addr")
        return ipv4
    return None


def apply(module_name, scenario_name, v1, upgrade=False, inject_rc=False):
    logger.info(f"applying terraform {scenario_name}")
    os.environ["PG_SCHEMA_NAME"] = f"pytest-{module_name}-{scenario_name}"

    # now we can set env / vars and apply our test scenario
    init_cmd = ["terraform", "init"]
    if upgrade:
        init_cmd.append("--upgrade")

    # render terraformrc jinja2
    j2_env = Environment(loader=FileSystemLoader(f"{os.getcwd()}/tests"))
    rc_tmp = j2_env.get_template(".terraformrc-e2e.j2")

    with open(f"{os.getcwd()}/tests/.terraformrc-e2e", "w") as f:
        f.write(rc_tmp.render({"user_name": getpass.getuser()}))

    init_env = os.environ.copy()
    if inject_rc:
        init_env["TF_CLI_CONFIG_FILE"] = f"{os.getcwd()}/tests/.terraformrc-e2e"

    # current machine IPV4 made accessible for tf var
    os.environ["TF_VAR_dev_machine_ipv4"] = get_ipv4(os.getenv("TDDOG_LOCAL_IFACE"))

    subprocess.run(
        init_cmd,
        cwd=f"{os.getcwd()}/tests/scenarios/{scenario_name}",
        env=init_env,
        check=True,
        text=True,
    )
    subprocess.run(
        ["terraform", "apply", "-auto-approve"],
        cwd=f"{os.getcwd()}/tests/scenarios/{scenario_name}",
        check=True,
        text=True,
    )

    # wait and assert all pods are running
    while True:
        all_pods_running = True

        for pod in v1.list_pod_for_all_namespaces().items:
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


def destroy(scenario_name):
    logger.info(f"destroying terraform {scenario_name}")
    subprocess.run(
        ["terraform", "destroy", "-auto-approve"],
        cwd=f"{os.getcwd()}/tests/scenarios/{scenario_name}",
        check=True,
        text=True,
    )

    shutil.rmtree(f"{os.getcwd()}/tests/scenarios/{scenario_name}/.terraform")
