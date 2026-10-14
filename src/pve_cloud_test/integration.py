import logging
import os
import socket
import subprocess
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

import pytest
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

logger = logging.getLogger(__name__)

INTEGRATION_KUBECONFIG_KEY = "PXC_INTEGRATION_KUBECONFIG"


def _init_integration_test_dir(v1, namespace):
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


@pytest.fixture(scope="session")
def integration_config():
    if os.getenv(INTEGRATION_KUBECONFIG_KEY):
        # load kubeconfig from path
        config.load_kube_config(config_file=os.getenv(INTEGRATION_KUBECONFIG_KEY))

        integration_namespace = "pve-cloud-integration"
        v1 = client.CoreV1Api()
        try:
            v1.read_namespace(name=integration_namespace)
        except ApiException as e:
            if e.status == 404:
                v1.create_namespace(
                    client.V1Namespace(
                        metadata=client.V1ObjectMeta(name=integration_namespace)
                    )
                )
            else:
                raise

        _init_integration_test_dir(v1, integration_namespace)

        return integration_namespace, v1
    else:
        # use existing namespace (agentic k8s solution)
        config.load_incluster_config()
        v1 = client.CoreV1Api()
        with open("/run/secrets/kubernetes.io/serviceaccount/namespace", "r") as f:
            namespace = f.read().strip()

            _init_integration_test_dir(v1, namespace)

            return namespace, v1


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
def _forward_ports_local_addr(pod_name, namespace, ports=None):
    # first find free loopback addr
    loopback_addr = None
    for i in range(2, 100):
        addr = f"127.0.0.{i}"
        try:
            logger.info(f"trying {addr}")
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((addr, 2222))

            loopback_addr = addr
            break
        except OSError as e:
            logger.info(e)
            continue

    if not loopback_addr:
        raise RuntimeError("Could not get loopback addr for forwarding!")

    fwd_cmd = [
        "kubectl",
        "--kubeconfig",
        os.getenv(INTEGRATION_KUBECONFIG_KEY),
        "port-forward",
        f"pod/{pod_name}",
        "-n",
        namespace,
        "--address",
        loopback_addr,
        "2222:22",  # use port 2222 on host as not to require root
    ]

    if ports:
        fwd_cmd += ports

    logger.info(fwd_cmd)

    proc = subprocess.Popen(
        fwd_cmd,
        # stdout=subprocess.DEVNULL,
        # stderr=subprocess.DEVNULL
    )

    time.sleep(3)  # short sleep for network

    try:
        yield loopback_addr
    finally:
        proc.terminate()
        proc.wait()


@contextmanager
def _launch_pod(v1, namespace, pod, hostname):
    v1.create_namespaced_pod(namespace=namespace, body=pod)

    pod = _wait_for_pod_ready(v1, namespace, hostname)

    yield pod.status.pod_ip

    v1.delete_namespaced_pod(name=hostname, namespace=namespace)


@contextmanager
def launch_containers(namespace, v1, containers):
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], text=True
    ).strip()

    container_inv = {}

    with ExitStack() as container_exit:
        for hostname, container_def in containers.items():
            # right now images have to be publicly available
            # the also need an active openssh server
            # also their default entrypoint needs to be a wait forever
            if "image_kind" in container_def:
                image_kind = container_def["image_kind"]
                image = f"{os.getenv("PXC_INTEGRATION_REGISTRY")}/{image_kind}:{branch}"
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
            if os.getenv(INTEGRATION_KUBECONFIG_KEY):
                pod_addr = container_exit.enter_context(
                    _forward_ports_local_addr(
                        hostname, namespace, container_def.get("ports")
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
                raise Exception("Not implemented!")

        logger.info(container_inv)

        yield container_inv
