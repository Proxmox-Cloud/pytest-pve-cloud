import base64
import json
import logging
import subprocess
import tempfile
from contextlib import contextmanager

import requests
import yaml
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from mcp_use import MCPServer
from pydantic import Field

from pve_cloud_test.integration import (DEFAULT_VCLUSTER_NAME,
                                        INTEGRATION_NAMESPACE,
                                        forward_ports_local_addr)

logger = logging.getLogger(__name__)

mcp = MCPServer(
    name="vcluster-mcp",
    version="1.0.0",
    instructions=(
        "Manage vclusters: launch/destroy vcluster, list pods/services, describe rebsources, get logs and run http requests. "
        "This mcp server is the only way to interact with the kubernetes vcluster, as it and its pods are in a restricted network."
    ),
)


def _get_vcluster_config_dict(cluster_name=None):
    cluster_name = cluster_name or DEFAULT_VCLUSTER_NAME
    logger.info(f"Loading vcluster config for: {cluster_name}")

    config.load_incluster_config()
    v1 = client.CoreV1Api()

    with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace", "r") as f:
        namespace = f.read().strip()
    secret_name = f"vc-{cluster_name}"
    secret = v1.read_namespaced_secret(name=secret_name, namespace=namespace)

    config_dict = yaml.safe_load(base64.b64decode(secret.data["config"]))
    config_dict["clusters"][0]["cluster"][
        "server"
    ] = f"https://{cluster_name}.{INTEGRATION_NAMESPACE}"

    logger.info(
        f"Vcluster config loaded successfully, server: {config_dict['clusters'][0]['cluster']['server']}"
    )

    return config_dict


def _get_vcluster_client(cluster_name=None):
    config_dict = _get_vcluster_config_dict(cluster_name)
    return config.new_client_from_config_dict(config_dict)


@mcp.tool()
async def launch_vcluster() -> dict:
    """Launch the default vcluster (test-vcluster) in the pve-cloud-integration namespace."""
    result = subprocess.run(
        ["vcluster", "list", "--output", "json"],
        capture_output=True,
        text=True,
    )

    existing = json.loads(result.stdout) or []
    for cluster in existing:
        if cluster["Name"] == DEFAULT_VCLUSTER_NAME:
            return {
                "status": "exists",
                "message": f"vcluster {DEFAULT_VCLUSTER_NAME} already exists",
            }

    result = subprocess.run(
        [
            "vcluster",
            "create",
            DEFAULT_VCLUSTER_NAME,
            "--connect=false",
            "--namespace",
            INTEGRATION_NAMESPACE,
            "-f",
            "/app/vcluster.yaml",
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return {"status": "error", "message": result.stderr}

    return {
        "status": "created",
        "message": f"vcluster {DEFAULT_VCLUSTER_NAME} created successfully",
    }


# todo: we need to implement some sort of ready check after the launch functionality!


@mcp.tool()
async def delete_vcluster() -> dict:
    """Delete the default vcluster (test-vcluster) in the pve-cloud-integration namespace."""
    result = subprocess.run(
        [
            "vcluster",
            "delete",
            DEFAULT_VCLUSTER_NAME,
            "--namespace",
            INTEGRATION_NAMESPACE,
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return {"status": "error", "message": result.stderr}

    return {
        "status": "deleted",
        "message": f"vcluster {DEFAULT_VCLUSTER_NAME} deleted successfully",
    }


@mcp.tool()
async def list_pods(
    namespace: str | None = Field(
        default=None,
        description="Kubernetes namespace to list pods from (default: default)",
    )
) -> list:
    """
    List pods in the specified namespace of test-vcluster.

    Use this as a fundamental tool to explore the workloads within a given namespace,
    usually follwed by a call to the describe_pod tool, to find out details about the pod.
    """
    logger.info(f"Listing pods in namespace: {namespace or 'default'}")
    k8s_client = _get_vcluster_client()
    v1 = client.CoreV1Api(k8s_client)
    ns = namespace or "default"

    try:
        pods = v1.list_namespaced_pod(namespace=ns)
        return [
            {"name": pod.metadata.name, "status": pod.status.phase}
            for pod in pods.items
        ]
    except ApiException as e:
        logger.error(f"API error listing pods: {e}")
        return [{"error": f"API error: {e}"}]


@mcp.tool()
async def get_services(
    namespace: str | None = Field(
        default=None,
        description="Kubernetes namespace to get services from (default: default)",
    )
) -> list:
    """
    Get all services and their full definitions in the specified namespace of test-vcluster.

    Prefer this as a way to find targets for http_get and http_post tool calls, as services usually provide
    a less convoluted way to access apis compared to pods.
    """
    logger.info(f"Listing services in namespace: {namespace or 'default'}")
    k8s_client = _get_vcluster_client()
    v1 = client.CoreV1Api(k8s_client)
    ns = namespace or "default"

    try:
        services = v1.list_namespaced_service(namespace=ns)
        return [
            {
                "name": svc.metadata.name,
                "namespace": svc.metadata.namespace,
                "type": svc.spec.type,
                "ports": [
                    {
                        "port": p.port,
                        "target_port": p.target_port,
                        "protocol": p.protocol,
                    }
                    for p in svc.spec.ports or []
                ],
                "selector": svc.spec.selector or {},
            }
            for svc in services.items
        ]
    except ApiException as e:
        logger.error(f"API error listing services: {e}")
        return [{"error": f"API error: {e}"}]


def _get_owner_chain(core_v1, apps_v1, obj, ns):
    """Traverse owner references up the hierarchy (e.g., pod => replicaset => deployment)."""
    chain = []
    owner_refs = obj.metadata.owner_references or []

    for ref in owner_refs:
        kind = ref.kind
        name = ref.name
        api_group = ref.api_version

        try:
            if kind == "ReplicaSet":
                rs = apps_v1.read_namespaced_replica_set(name=name, namespace=ns)
                owner_chain = {"kind": kind, "name": name, "api_version": api_group}
                if rs.metadata.owner_references:
                    owner_chain["owner"] = _get_owner_chain(core_v1, apps_v1, rs, ns)
                chain.append(owner_chain)
            elif kind == "Deployment":
                chain.append({"kind": kind, "name": name, "api_version": api_group})
            elif kind == "StatefulSet":
                sts = apps_v1.read_namespaced_stateful_set(name=name, namespace=ns)
                owner_chain = {"kind": kind, "name": name, "api_version": api_group}
                if sts.metadata.owner_references:
                    owner_chain["owner"] = _get_owner_chain(core_v1, apps_v1, sts, ns)
                chain.append(owner_chain)
            elif kind == "Job":
                chain.append({"kind": kind, "name": name, "api_version": api_group})
            else:
                chain.append({"kind": kind, "name": name, "api_version": api_group})
        except ApiException:
            chain.append(
                {
                    "kind": kind,
                    "name": name,
                    "api_version": api_group,
                    "error": "not found",
                }
            )

    return chain if chain else None


@mcp.tool()
async def describe_pod(
    name: str = Field(..., description="Name of the pod to describe"),
    namespace: str | None = Field(
        default=None, description="Kubernetes namespace (default: default)"
    ),
) -> dict:
    """Get detailed information about a specific pod in test-vcluster."""
    logger.info(f"Describing pod: {name} in namespace: {namespace or 'default'}")
    k8s_client = _get_vcluster_client()
    v1 = client.CoreV1Api(k8s_client)
    apps_v1 = client.AppsV1Api(k8s_client)
    ns = namespace or "default"

    try:
        pod = v1.read_namespaced_pod(name=name, namespace=ns)
        owner_chain = _get_owner_chain(v1, apps_v1, pod, ns)
        return {
            "name": pod.metadata.name,
            "namespace": pod.metadata.namespace,
            "status": pod.status.phase,
            "labels": pod.metadata.labels or {},
            "containers": [
                {
                    "name": c.name,
                    "image": c.image,
                    "ports": [
                        {
                            "container_port": p.container_port,
                            "name": p.name,
                            "protocol": p.protocol,
                        }
                        for p in c.ports or []
                    ],
                }
                for c in pod.spec.containers
            ],
            "conditions": [
                {"type": c.type, "status": c.status}
                for c in (pod.status.conditions or [])
            ],
            "owner_chain": owner_chain,
        }
    except ApiException as e:
        logger.error(f"API error describing pod {name}: {e}")
        return {"error": f"API error: {e}"}


@mcp.tool()
async def get_pod_logs(
    name: str = Field(..., description="Pod name"),
    namespace: str | None = Field(
        default=None, description="Kubernetes namespace (default: default)"
    ),
    container: str | None = Field(
        default=None, description="Container name (default: all containers)"
    ),
    tail_lines: int = Field(
        default=1000, description="Number of lines from the end (default: 1000)"
    ),
) -> dict:
    """Get logs from a pod in test-vcluster. This should be your primary way of evaluating the state of a deployment."""
    logger.info(f"Getting logs from pod: {name}, namespace: {namespace or 'default'}")
    k8s_client = _get_vcluster_client()
    v1 = client.CoreV1Api(k8s_client)
    ns = namespace or "default"

    try:
        pod = v1.read_namespaced_pod(name=name, namespace=ns)
        containers = [c.name for c in pod.spec.containers]

        if container:
            container_list = [container]
        else:
            container_list = containers

        logs = {}
        for c in container_list:
            try:
                log_content = v1.read_namespaced_pod_log(
                    name=name,
                    namespace=ns,
                    container=c,
                    tail_lines=tail_lines,
                    timestamps=True,
                )
                logs[c] = log_content.split("\n") if log_content else []
            except ApiException as e:
                logger.error(f"Failed to get logs for container {c}: {e}")
                logs[c] = {"error": f"Failed to get logs: {e}"}

        return {
            "pod": name,
            "namespace": ns,
            "containers": container_list,
            "tail_lines": tail_lines,
            "logs": logs,
        }
    except ApiException as e:
        logger.error(f"API error getting pod logs: {e}")
        return {"error": f"API error: {e}"}


@mcp.tool()
async def list_configmaps(
    namespace: str | None = Field(
        default=None,
        description="Kubernetes namespace to list configmaps from (default: default)",
    )
) -> list:
    """
    List configmaps in the specified namespace of test-vcluster.

    Use this to discover configuration data available in a given namespace.
    """
    logger.info(f"Listing configmaps in namespace: {namespace or 'default'}")
    k8s_client = _get_vcluster_client()
    v1 = client.CoreV1Api(k8s_client)
    ns = namespace or "default"

    try:
        configmaps = v1.list_namespaced_config_map(namespace=ns)
        return [
            {"name": cm.metadata.name, "namespace": cm.metadata.namespace}
            for cm in configmaps.items
        ]
    except ApiException as e:
        logger.error(f"API error listing configmaps: {e}")
        return [{"error": f"API error: {e}"}]


@mcp.tool()
async def get_configmap(
    name: str = Field(..., description="Name of the configmap"),
    namespace: str | None = Field(
        default=None, description="Kubernetes namespace (default: default)"
    ),
) -> dict:
    """
    Get a specific configmap in test-vcluster.

    Returns the configmap name, namespace, and its data keys.
    """
    logger.info(f"Getting configmap: {name} in namespace: {namespace or 'default'}")
    k8s_client = _get_vcluster_client()
    v1 = client.CoreV1Api(k8s_client)
    ns = namespace or "default"

    try:
        configmap = v1.read_namespaced_config_map(name=name, namespace=ns)
        return {
            "name": configmap.metadata.name,
            "namespace": configmap.metadata.namespace,
            "data": configmap.data or {},
        }
    except ApiException as e:
        logger.error(f"API error getting configmap {name}: {e}")
        return {"error": f"API error: {e}"}


@contextmanager
def _vcluster_port_forward(
    target_name: str,
    target_type: str,
    target_port: int,
    namespace: str = "default",
):
    logger.info(
        f"Starting port-forward: {target_type}/{target_name}:{target_port} -> 8080"
    )
    config_dict = _get_vcluster_config_dict()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml") as f:
        yaml.dump(config_dict, f)
        kubeconfig_path = f.name

        with forward_ports_local_addr(
            target_name=target_name,
            namespace=namespace,
            ports=[f"8080:{target_port}"],
            target_type=target_type,
            kubeconfig_path=kubeconfig_path,
        ) as loopback_addr:
            yield loopback_addr


@mcp.tool()
async def http_get(
    target_name: str = Field(..., description="Pod or service name"),
    target_type: str = Field(..., description="Target type: 'pod' or 'service'"),
    target_port: int = Field(..., description="Target port on pod/service"),
    namespace: str = Field(default="default", description="Kubernetes namespace"),
    path: str = Field(default="/", description="HTTP path to request"),
) -> dict:
    """
    Perform HTTP GET request against a pod or service in the vcluster.

    Use this to find and test endpoints prior to creating asserting requests in
    integration tests.
    """

    logger.info(f"HTTP GET: {target_type}/{target_name}:{target_port}{path}")
    with _vcluster_port_forward(
        target_name=target_name,
        target_type=target_type,
        target_port=target_port,
        namespace=namespace,
    ) as loopback_addr:
        url = f"http://{loopback_addr}:8080{path}"

        try:
            response = requests.get(url, timeout=30)

            # try parsing the body as json, otherwise return plain text
            try:
                body = response.json()
            except ValueError:
                body = response.text

            return {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": body,
            }
        except requests.RequestException as e:
            logger.error(f"Request failed: {e}")
            return {
                "error": str(e),
            }


@mcp.tool()
async def http_post(
    target_name: str = Field(..., description="Pod or service name"),
    target_type: str = Field(..., description="Target type: 'pod' or 'service'"),
    target_port: int = Field(..., description="Target port on pod/service"),
    namespace: str = Field(default="default", description="Kubernetes namespace"),
    path: str = Field(default="/", description="HTTP path to request"),
    body: str = Field(..., description="Request body (JSON or plain text)"),
    content_type: str = Field(
        default="application/json", description="Content-Type header"
    ),
) -> dict:
    """
    Perform HTTP POST request against a pod or service in the vcluster.

    Use this to find and test endpoints prior to creating asserting requests in
    integration tests.
    """

    logger.info(f"HTTP POST: {target_type}/{target_name}:{target_port}{path}")
    with _vcluster_port_forward(
        target_name=target_name,
        target_type=target_type,
        target_port=target_port,
        namespace=namespace,
    ) as loopback_addr:
        url = f"http://{loopback_addr}:8080{path}"

        try:
            response = requests.post(
                url,
                data=body,
                headers={"Content-Type": content_type},
                timeout=30,
            )

            # dynamic body parsing
            try:
                response_body = response.json()
            except ValueError:
                response_body = response.text

            return {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": response_body,
            }
        except requests.RequestException as e:
            logger.error(f"Request failed: {e}")
            return {
                "error": str(e),
            }


def main():
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)
