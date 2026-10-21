import base64
import logging
import os
import secrets
import socket
import string
import subprocess
import tempfile
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

import pytest
import requests
import yaml
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

logger = logging.getLogger(__name__)

INTEGRATION_TEST_ID = "".join(
    secrets.choice(string.ascii_lowercase + string.digits) for _ in range(5)
)

logger.info(f"Running tests with integration test id: {INTEGRATION_TEST_ID}")

# generic env var name for kubeconfig path to the k8s cluster hosting pve-cloud-integration namespace
INTEGRATION_KUBECONFIG_ENV_KEY = "PXC_INTEGRATION_KUBECONFIG"
INTEGRATION_NAMESPACE = "pve-cloud-integration"
INTEGRATION_SVC_NAME = "integration-api"  # todo: better naming

# hardcoded tag for images inside images/, build via build-integration-images.sh
INTEGRATION_IMAGE_TAG = "pxc-3-stable"
DEFAULT_VCLUSTER_NAME = "test-vcluster"


def _init_integration_test_dir(v1, namespace):
    """
    Init generic directory inside agent / executing users home dir
    with ssh keys that get injected into integration testing infrastructure.
    """
    test_dir = Path.home() / ".pxc-integration-env"
    test_dir.mkdir(exist_ok=True)

    private_key = test_dir / "id_ed25519"
    public_key = test_dir / "id_ed25519.pub"

    if not private_key.exists():
        subprocess.run(
            [
                "ssh-keygen",
                "-t",
                "ed25519",
                "-f",
                str(private_key),
                "-N",
                "",
                "-C",
                "pxc-integration",
            ],
            check=True,
        )

        private_key.chmod(0o600)
        public_key.chmod(0o644)

    # store the key in k8s
    secret_name = "integration-ssh-key"

    # todo: there should be some id mechanism here
    try:
        v1.delete_namespaced_secret(
            name=secret_name,
            namespace=namespace,
        )
    except ApiException as e:
        if e.status != 404:
            raise

    logger.info("creating ssh key secret")
    secret = client.V1Secret(
        metadata=client.V1ObjectMeta(name=secret_name),
        type="Opaque",
        string_data={
            "id_ed25519": private_key.read_text(encoding="utf-8"),
            "id_ed25519.pub": public_key.read_text(encoding="utf-8"),
        },
    )

    v1.create_namespaced_secret(
        namespace=namespace,
        body=secret,
    )

    return public_key


def _get_wait_vcluster_config(itg_url, cluster_name):
    """
    Connect to the integration service api and
    poll for the vcluster kubeapi config.

    This also serves as a ready check
    """
    for attempt in range(20):
        response = requests.get(f"{itg_url}/cluster-config/{cluster_name}")

        if response.status_code == 200:
            return response.json()["data"]

        time.sleep(5)

    else:
        raise RuntimeError("Failed after 5 attempts")


@pytest.fixture(scope="session")
def integration_config(request):
    """
    Central fixture for launching and establishing a connection
    to the default test-vcluster instance where agents can run amok.
    """
    with ExitStack() as integration_exit:
        itgr_svc_base = f"http://{INTEGRATION_SVC_NAME}.{INTEGRATION_NAMESPACE}"

        # when connecting from outside kubernetes (from some developers machine)
        # we need a kubeconfig to the host k8s and use that to forward to
        # the currently unprotected integration service api
        # todo: implement some sort of additional auth
        if os.getenv(INTEGRATION_KUBECONFIG_ENV_KEY):
            itg_fwd_addr = integration_exit.enter_context(
                forward_ports_local_addr(
                    INTEGRATION_SVC_NAME,
                    INTEGRATION_NAMESPACE,
                    ["8123:80"],
                    target_type="service",
                )
            )

            itgr_svc_base = f"http://{itg_fwd_addr}:8123"

        # create the cluster if it doesnt exit
        vcluster_resp = requests.post(
            f"{itgr_svc_base}/vcluster", json={"name": DEFAULT_VCLUSTER_NAME}
        )
        vcluster_resp.raise_for_status()

        # fetch the cluster config
        vcluster_config = _get_wait_vcluster_config(
            itgr_svc_base, DEFAULT_VCLUSTER_NAME
        )
        # todo: better wait for init
        time.sleep(10)

        # adjust kubeconfig
        config_dict = yaml.safe_load(base64.b64decode(vcluster_config["config"]))

        if os.getenv(INTEGRATION_KUBECONFIG_ENV_KEY):
            # forward vcluster api
            # currently needs to be localhost because vcluster SANs dont contain 127.0.0.2-100
            # todo: this could conflict with local k8s running
            integration_exit.enter_context(
                forward_ports_local_addr(
                    DEFAULT_VCLUSTER_NAME,
                    INTEGRATION_NAMESPACE,
                    ["8443:443"],
                    target_type="service",
                    loopback_addr="127.0.0.1",
                )
            )

            config_dict["clusters"][0]["cluster"]["server"] = f"https://localhost:8443"
        else:
            # connect directly in cluster
            config_dict["clusters"][0]["cluster"][
                "server"
            ] = f"https://{DEFAULT_VCLUSTER_NAME}.{INTEGRATION_NAMESPACE}"

        # init kubernetes api client
        k8s_client = config.new_client_from_config_dict(config_dict)
        v1 = client.CoreV1Api(k8s_client)

        # create namespace inside vcluster for current pytest run
        namespace = f"pxc-itg-run-{INTEGRATION_TEST_ID}"
        logger.info(
            f"Creating dedicated integration testing k8s namespace: {namespace}"
        )

        v1.create_namespace(
            body=client.V1Namespace(metadata=client.V1ObjectMeta(name=namespace))
        )
        # todo: maybe reuse namespace if this file already exist to avoid
        # spam deployments? or is cleanup enough?
        Path(".itgr-test-ns").write_text(namespace)

        # todo: better wait for init
        time.sleep(10)

        _init_integration_test_dir(v1, namespace)

        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False
        ) as tmp_kubeconfig_file:
            yaml.dump(config_dict, tmp_kubeconfig_file)
            tmp_kubeconfig_file.flush()

            yield tmp_kubeconfig_file.name, k8s_client, v1, namespace

        if not request.config.getoption("--skip-cleanup"):
            # final cleanup
            delete_resp = requests.delete(
                f"{itgr_svc_base}/vcluster/{DEFAULT_VCLUSTER_NAME}"
            )
            delete_resp.raise_for_status()


def _wait_for_pod_ready(v1, namespace, pod_name, timeout=300):
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            pod = v1.read_namespaced_pod(name=pod_name, namespace=namespace)

            # Check conditions
            if pod.status.conditions:
                for condition in pod.status.conditions:
                    if condition.type == "Ready":
                        if condition.status == "True":
                            print(f"✓ Pod '{pod_name}' is Ready!")
                            return pod
                        elif condition.status == "False":
                            print(
                                f"  Pod '{pod_name}' is not Ready: {condition.reason} - {condition.message}"
                            )
                        elif condition.status == "Unknown":
                            print(f"  Pod '{pod_name}' status is Unknown")

            # Check phase
            phase = pod.status.phase
            print(f"  Current status: {phase} (Phase: {phase})")

            # Optional: Print events for debugging
            if phase in ["Pending", "ContainerCreating"]:
                # You could also fetch events here for more details
                pass

            time.sleep(3)  # Poll every 3 seconds

        except ApiException as e:
            if e.status == 404:
                print(f"Pod '{pod_name}' not found. It may have been deleted.")
                return None
            print(f"API error while checking pod: {e}")
            time.sleep(3)

    print(f"✗ Timeout waiting for pod '{pod_name}' to become Ready.")
    return None


@contextmanager
def forward_ports_local_addr(
    target_name,
    namespace,
    ports=None,
    target_type="pod",
    loopback_addr=None,
    kubeconfig_path=None,
):
    if not kubeconfig_path and not os.getenv(INTEGRATION_KUBECONFIG_ENV_KEY):
        raise RuntimeError(
            f"Function only works with env var {INTEGRATION_KUBECONFIG_ENV_KEY} set or kubeconfig_path arg set!"
        )

    if target_type == "service" and not ports:
        raise ValueError(
            'Specifying target type "service" requires passing a list of portforwardings!'
        )

    # first find free loopback addr
    if not loopback_addr:
        for i in range(2, 100):
            addr = f"127.0.0.{i}"
            try:
                logger.info(f"trying {addr}")
                required_ports = []
                if target_type == "pod":
                    required_ports.append(2222)

                if ports:
                    required_ports.extend(
                        int(mapping.split(":")[0]) for mapping in ports
                    )

                for port in required_ports:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        s.bind((addr, port))

                loopback_addr = addr
                break
            except OSError:
                continue

    if not loopback_addr:
        raise RuntimeError("Could not get loopback addr for forwarding!")

    fwd_cmd = [
        "kubectl",
        "--kubeconfig",
        (
            os.getenv(INTEGRATION_KUBECONFIG_ENV_KEY)
            if not kubeconfig_path
            else kubeconfig_path
        ),
        "port-forward",
        f"{target_type}/{target_name}",
        "-n",
        namespace,
        "--address",
        loopback_addr,
    ]

    if target_type == "pod":
        fwd_cmd += ["2222:22"]  # always forward ssh for pods

    if ports:
        fwd_cmd += ports

    logger.info(fwd_cmd)

    proc = None
    try:
        proc = subprocess.Popen(
            fwd_cmd,
        )

        time.sleep(3)  # short sleep for network
        yield loopback_addr
    finally:
        if proc:
            proc.terminate()
            proc.wait()


@contextmanager
def _launch_pod(v1, namespace, pod, hostname):
    v1.create_namespaced_pod(namespace=namespace, body=pod)

    pod = _wait_for_pod_ready(v1, namespace, hostname)

    try:
        yield pod.status.pod_ip
    finally:
        v1.delete_namespaced_pod(name=hostname, namespace=namespace)


@contextmanager
def launch_containers(namespace, v1, containers, kubeconfig_path):
    container_inv = {}

    with ExitStack() as container_exit:
        for hostname, container_def in containers.items():
            # right now images have to be publicly available
            # the also need an active openssh server
            # also their default entrypoint needs to be a wait forever
            if "image_kind" in container_def:
                image_kind = container_def["image_kind"]
                image = f"{os.getenv("PXC_INTEGRATION_REGISTRY")}/{image_kind}:{INTEGRATION_IMAGE_TAG}"
            elif "image" in container_def:
                image = container_def["image"]
            else:
                raise ValueError("No fitting image vars defined!")

            # todo: also turn this into a context
            pod = client.V1Pod(
                api_version="v1",
                kind="Pod",
                metadata=client.V1ObjectMeta(name=hostname, namespace=namespace),
                spec=client.V1PodSpec(
                    containers=[
                        client.V1Container(
                            name="main",
                            image=image,
                            image_pull_policy="Always",
                            volume_mounts=[
                                client.V1VolumeMount(
                                    name="trusted-ssh-key",
                                    mount_path="/root/.ssh/authorized_keys",
                                    sub_path="id_ed25519.pub",
                                )
                            ],
                            readiness_probe=(
                                client.V1Probe(
                                    _exec=client.V1ExecAction(
                                        command=container_def["readiness_probe"][
                                            "command"
                                        ]
                                    ),
                                    **container_def["readiness_probe"][
                                        "args"
                                    ],  # unpack additional args
                                )
                                if "readiness_probe" in container_def
                                else None
                            ),
                            env=(
                                [
                                    client.V1EnvVar(name=key, value=val)
                                    for key, val in container_def["env"].items()
                                ]
                                if "env" in container_def
                                else None
                            ),
                        )
                    ],
                    volumes=[
                        client.V1Volume(
                            name="trusted-ssh-key",
                            secret=client.V1SecretVolumeSource(
                                secret_name="integration-ssh-key",
                            ),
                        )
                    ],
                    restart_policy="Never",
                ),
            )

            # launch the pod with auto cleanup
            k8s_pod_ip = container_exit.enter_context(
                _launch_pod(v1, namespace, pod, hostname)
            )

            # create managed forward if using remote k8s
            if kubeconfig_path:
                pod_addr = container_exit.enter_context(
                    forward_ports_local_addr(
                        hostname,
                        namespace,
                        container_def.get("ports"),
                        kubeconfig_path=kubeconfig_path,
                    )
                )
                container_inv[hostname] = {
                    "ansible_user": container_def["user"],
                    "ansible_host": pod_addr,
                    "ansible_port": 2222,
                    "ansible_ssh_private_key_file": str(
                        Path("~/.pxc-integration-env/id_ed25519").expanduser()
                    ),
                    "k8s_pod_ip": k8s_pod_ip,
                } | (container_def["env"] if "env" in container_def else {})
            else:
                container_inv[hostname] = {
                    "ansible_user": container_def["user"],
                    "ansible_host": k8s_pod_ip,
                    "ansible_port": 22,
                    "ansible_ssh_private_key_file": str(
                        Path("~/.pxc-integration-env/id_ed25519").expanduser()
                    ),
                    "k8s_pod_ip": k8s_pod_ip,
                } | (container_def["env"] if "env" in container_def else {})

        logger.info(container_inv)

        yield container_inv

        logger.info(f"teardown {container_inv}")
