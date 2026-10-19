"""MCP Server for vcluster management."""

import base64
import json
import subprocess

import yaml
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from mcp_use import MCPServer
from pydantic import Field

server = MCPServer(
    name="vcluster-mcp",
    version="1.0.0",
    instructions="Manage vclusters: launch, list pods/services, describe resources",
)

INTEGRATION_NAMESPACE = "pve-cloud-integration"
DEFAULT_VCLUSTER_NAME = "test-vcluster"


def _get_vcluster_client(cluster_name=None):
    """Get Kubernetes client for a specific vcluster."""
    cluster_name = cluster_name or DEFAULT_VCLUSTER_NAME
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
    k8s_client = config.new_client_from_config_dict(config_dict)
    return client.CoreV1Api(k8s_client)


@server.tool()
async def launch_vcluster() -> dict:
    """Launch the default vcluster (test-vcluster) in pve-cloud-integration namespace."""
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


@server.tool()
async def delete_vcluster() -> dict:
    """Delete the default vcluster (test-vcluster) in pve-cloud-integration namespace."""
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


@server.tool()
async def list_pods(
    namespace: str | None = Field(
        default=None,
        description="Kubernetes namespace to list pods from (default: default)",
    )
) -> list:
    """List pods in the specified namespace of test-vcluster."""
    v1 = _get_vcluster_client()
    ns = namespace or "default"

    try:
        pods = v1.list_namespaced_pod(namespace=ns)
        return [
            {"name": pod.metadata.name, "status": pod.status.phase}
            for pod in pods.items
        ]
    except ApiException as e:
        return [{"error": f"API error: {e}"}]


@server.tool()
async def list_services(
    namespace: str | None = Field(
        default=None,
        description="Kubernetes namespace to list services from (default: default)",
    )
) -> list:
    """List services in the specified namespace of test-vcluster."""
    v1 = _get_vcluster_client()
    ns = namespace or "default"

    try:
        services = v1.list_namespaced_service(namespace=ns)
        return [
            {"name": svc.metadata.name, "type": svc.spec.type} for svc in services.items
        ]
    except ApiException as e:
        return [{"error": f"API error: {e}"}]


@server.tool()
async def describe_pod(
    name: str = Field(..., description="Name of the pod to describe"),
    namespace: str | None = Field(
        default=None, description="Kubernetes namespace (default: default)"
    ),
) -> dict:
    """Get detailed information about a specific pod in test-vcluster."""
    v1 = _get_vcluster_client()
    ns = namespace or "default"

    try:
        pod = v1.read_namespaced_pod(name=name, namespace=ns)
        return {
            "name": pod.metadata.name,
            "namespace": pod.metadata.namespace,
            "status": pod.status.phase,
            "node": pod.spec.node_name,
            "ip": pod.status.pod_ip,
            "containers": [c.name for c in pod.spec.containers],
            "conditions": [
                {"type": c.type, "status": c.status}
                for c in (pod.status.conditions or [])
            ],
        }
    except ApiException as e:
        return {"error": f"API error: {e}"}


@server.tool()
async def describe_service(
    name: str = Field(..., description="Name of the service to describe"),
    namespace: str | None = Field(
        default=None, description="Kubernetes namespace (default: default)"
    ),
) -> dict:
    """Get detailed information about a specific service in test-vcluster."""
    v1 = _get_vcluster_client()
    ns = namespace or "default"

    try:
        svc = v1.read_namespaced_service(name=name, namespace=ns)
        return {
            "name": svc.metadata.name,
            "namespace": svc.metadata.namespace,
            "type": svc.spec.type,
            "cluster_ip": svc.spec.cluster_ip,
            "ports": [
                {"port": p.port, "target_port": p.target_port, "protocol": p.protocol}
                for p in svc.spec.ports or []
            ],
            "selector": svc.spec.selector or {},
        }
    except ApiException as e:
        return {"error": f"API error: {e}"}


@server.tool()
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
    """Get logs from a pod in test-vcluster."""
    v1 = _get_vcluster_client()
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
                logs[c] = {"error": f"Failed to get logs: {e}"}

        return {
            "pod": name,
            "namespace": ns,
            "containers": container_list,
            "tail_lines": tail_lines,
            "logs": logs,
        }
    except ApiException as e:
        return {"error": f"API error: {e}"}


def main():
    """Run MCP server with streamable-http transport."""
    server.run(transport="streamable-http", host="0.0.0.0", port=8000)
