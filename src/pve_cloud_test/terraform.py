import logging
import os
import shutil
import subprocess

logger = logging.getLogger(__name__)


def apply(module_name, scenario_name, v1, upgrade=False, inject_rc=False):
    logger.info(f"applying terraform {scenario_name}")
    os.environ["PG_SCHEMA_NAME"] = f"pytest-{module_name}-{scenario_name}"

    # now we can set env / vars and apply our test scenario
    init_cmd = ["terraform", "init"]
    if upgrade:
        init_cmd.append("--upgrade")

    init_env = os.environ.copy()
    if inject_rc:
        init_env["TF_CLI_CONFIG_FILE"] = f"{os.getcwd()}/tests/.terraformrc-e2e"

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
