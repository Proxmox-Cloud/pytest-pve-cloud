import base64
import json
import logging
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone

import requests
import yaml
from kubernetes import client, config, dynamic
from kubernetes.client.exceptions import ApiException
from mcp_use import MCPServer
from pydantic import BaseModel, Field

from pve_cloud_test.integration import (DEFAULT_VCLUSTER_NAME,
                                        INTEGRATION_NAMESPACE,
                                        forward_ports_local_addr)

logger = logging.getLogger(__name__)

mcp = MCPServer(
    name="vcluster-mcp",
    version="1.0.0",
    instructions=(
        "Manage dedicated vcluster for Proxmox Cloud ingration testing: launch/destroy vcluster, "
        "list pods/services, describe resources, get logs and run http requests. This is your only way "
        "of accessing kubernetes, NEVER use kubectl directly!"
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


def _get_vcluster_dynamic_client(cluster_name=None):
    """Get a DynamicClient for generic Kubernetes resource access."""
    config_dict = _get_vcluster_config_dict(cluster_name)
    client_obj = config.new_client_from_config_dict(config_dict)
    return dynamic.DynamicClient(client_obj.api_client)


class GenericStatusResponse(BaseModel):
    status: str = Field(description="Tool call result status.")
    message: str = Field(description="Tool call result full message.")


@mcp.tool()
async def launch_vcluster() -> GenericStatusResponse:
    """Launch the default vcluster (test-vcluster) in the pve-cloud-integration host namespace."""
    result = subprocess.run(
        ["vcluster", "list", "--output", "json"],
        capture_output=True,
        text=True,
    )

    existing = json.loads(result.stdout) or []
    for cluster in existing:
        if cluster["Name"] == DEFAULT_VCLUSTER_NAME:
            return GenericStatusResponse(
                status="exists",
                message=f"vcluster {DEFAULT_VCLUSTER_NAME} already exists",
            )

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
        return GenericStatusResponse(status="error", message=result.stderr)

    return GenericStatusResponse(
        status="created",
        message=f"vcluster {DEFAULT_VCLUSTER_NAME} created successfully",
    )


# todo: we need to implement some sort of ready check after the launch functionality!


@mcp.tool()
async def delete_vcluster() -> GenericStatusResponse:
    """Delete the default vcluster (test-vcluster) in the pve-cloud-integration host namespace. Use this for cleanup or for resetting a stuck testing environment."""
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
        return GenericStatusResponse(status="error", message=result.stderr)

    return GenericStatusResponse(
        status="deleted",
        message=f"vcluster {DEFAULT_VCLUSTER_NAME} deleted successfully",
    )


class PodMeta(BaseModel):
    name: str = Field(description="Pod name.")
    status: str = Field(description="Pod general status phase.")


@mcp.tool()
async def list_pods(
    namespace: str = Field(
        description="Kubernetes namespace to list pods from.",
    )
) -> list[PodMeta]:
    """
    List pods in the specified namespace of test-vcluster.

    Use this as a fundamental tool to explore the workloads within a given namespace.
    Usually this tool call is follwed by a call to the describe_pod tool, to find out details about a specific pod,
    such as the owners, port mappings, runtime status, etc...
    """
    logger.info(f"Listing pods in namespace: {namespace}")
    k8s_client = _get_vcluster_client()
    v1 = client.CoreV1Api(k8s_client)

    pods = v1.list_namespaced_pod(namespace=namespace)
    return [
        PodMeta(name=pod.metadata.name, status=pod.status.phase) for pod in pods.items
    ]


class ServicePort(BaseModel):
    port: int = Field(description="Service port.")
    target_port: int | str | None = Field(
        default=None, description="Target port on the pods."
    )
    protocol: str = Field(default="TCP", description="Protocol (TCP/UDP).")


class ServiceInstance(BaseModel):
    name: str = Field(description="Service name.")
    namespace: str = Field(description="Service namespace.")
    type: str = Field(description="Service type.")
    ports: list[ServicePort] = Field(default=[], description="Service ports.")
    selector: dict = Field(default={}, description="Pod selector.")


@mcp.tool()
async def get_services(
    namespace: str = Field(
        description="Kubernetes namespace to get services from.",
    )
) -> list[ServiceInstance]:
    """
    Get all services and their full definitions in the specified namespace of test-vcluster.

    Prefer this as a way to find targets for http_get and http_post tool calls, as services usually provide
    a less convoluted way to access apis compared to pods.
    """
    logger.info(f"Listing services in namespace: {namespace}")
    k8s_client = _get_vcluster_client()
    v1 = client.CoreV1Api(k8s_client)

    services = v1.list_namespaced_service(namespace=namespace)
    return [
        ServiceInstance(
            name=svc.metadata.name,
            namespace=svc.metadata.namespace,
            type=svc.spec.type,
            ports=[
                ServicePort(
                    port=p.port,
                    target_port=p.target_port,
                    protocol=p.protocol,
                )
                for p in svc.spec.ports or []
            ],
            selector=svc.spec.selector or {},
        )
        for svc in services.items
    ]


class ContainerPort(BaseModel):
    container_port: int = Field(description="Container port.")
    name: str | None = Field(default=None, description="Port name.")
    protocol: str = Field(default="TCP", description="Protocol (TCP/UDP).")


class Container(BaseModel):
    name: str = Field(description="Container name.")
    image: str = Field(description="Container image.")
    ports: list[ContainerPort] = Field(default=[], description="Container ports.")


class ContainerState(BaseModel):
    waiting: dict | None = Field(
        default=None, description="Waiting state with reason and message."
    )
    running: dict | None = Field(default=None, description="Running state.")
    terminated: dict | None = Field(default=None, description="Terminated state.")


class ContainerStatus(BaseModel):
    name: str = Field(description="Container name.")
    state: ContainerState | None = Field(
        default=None, description="Current container state."
    )
    last_state: ContainerState | None = Field(
        default=None, description="Last container state."
    )
    ready: bool = Field(default=False, description="Container ready status.")
    restart_count: int = Field(default=0, description="Number of restarts.")
    image: str = Field(description="Container image.")
    container_id: str | None = Field(default=None, description="Container ID.")
    started: bool | None = Field(
        default=None, description="Whether container has started."
    )


class Condition(BaseModel):
    type: str = Field(description="Condition type.")
    status: str = Field(description="Condition status.")


class OwnerRef(BaseModel):
    kind: str = Field(description="Owner kind.")
    name: str = Field(description="Owner name.")
    api_version: str = Field(description="API version.")


class Pod(BaseModel):
    name: str = Field(description="Pod name.")
    namespace: str = Field(description="Pod namespace.")
    status: str = Field(description="Pod status phase.")
    labels: dict = Field(default={}, description="Pod labels.")
    containers: list[Container] = Field(default=[], description="Pod containers.")
    container_statuses: list[ContainerStatus] = Field(
        default=[], description="Container statuses."
    )
    conditions: list[Condition] = Field(default=[], description="Pod conditions.")
    owner_chain: list[OwnerRef] | None = Field(default=None, description="Owner chain.")


def _get_owner_chain(dyn_client, obj, ns) -> list[OwnerRef] | None:
    """Traverse controller chain (controller: true) up to top-level owner."""
    chain = []
    owner_refs = obj.metadata.owner_references or []

    for ref in owner_refs:
        # Only follow controller: true references
        if not ref.controller:
            continue

        # Generic resource lookup
        gvr = dyn_client.resources.get(api_version=ref.api_version, kind=ref.kind)
        parent_obj = gvr.get(name=ref.name, namespace=ns)

        chain.append(
            OwnerRef(kind=ref.kind, name=ref.name, api_version=ref.api_version)
        )

        # Recurse to find parent controllers
        parent_chain = _get_owner_chain(dyn_client, parent_obj, ns)
        chain.extend(parent_chain or [])

    return chain if chain else None


@mcp.tool()
async def describe_pod(
    name: str = Field(..., description="Name of the pod to describe."),
    namespace: str | None = Field(
        default=None, description="Kubernetes namespace of the pod."
    ),
) -> Pod:
    """
    Get detailed information about a specific pod in test-vcluster.

    This includes but is not limited to: Pod status, owner chain (ReplicaSet, Deployment, StatefulSet, Job, ...),
    ports and pod labels.
    """
    logger.info(f"Describing pod: {name} in namespace: {namespace}")
    k8s_client = _get_vcluster_client()
    dyn_client = _get_vcluster_dynamic_client()
    v1 = client.CoreV1Api(k8s_client)

    pod = v1.read_namespaced_pod(name=name, namespace=namespace)
    owner_chain = _get_owner_chain(dyn_client, pod, namespace)
    return Pod(
        name=pod.metadata.name,
        namespace=pod.metadata.namespace,
        status=pod.status.phase,
        labels=pod.metadata.labels or {},
        containers=[
            Container(
                name=c.name,
                image=c.image,
                ports=[
                    ContainerPort(
                        container_port=p.container_port,
                        name=p.name,
                        protocol=p.protocol,
                    )
                    for p in c.ports or []
                ],
            )
            for c in pod.spec.containers
        ],
        container_statuses=[
            ContainerStatus(
                name=s.name,
                state=(
                    ContainerState(
                        waiting=s.state.waiting.to_dict() if s.state.waiting else None,
                        running=s.state.running.to_dict() if s.state.running else None,
                        terminated=s.state.terminated.to_dict() if s.state.terminated else None,
                    )
                    if s.state
                    else None
                ),
                last_state=(
                    ContainerState(
                        waiting=s.last_state.waiting.to_dict() if s.last_state.waiting else None,
                        running=s.last_state.running.to_dict() if s.last_state.running else None,
                        terminated=s.last_state.terminated.to_dict() if s.last_state.terminated else None,
                    )
                    if s.last_state
                    else None
                ),
                ready=s.ready,
                restart_count=s.restart_count,
                image=s.image,
                container_id=s.container_id,
                started=s.started,
            )
            for s in (pod.status.container_statuses or [])
        ],
        conditions=[
            Condition(type=c.type, status=c.status)
            for c in (pod.status.conditions or [])
        ],
        owner_chain=owner_chain,
    )


class RestartStatusMessage(BaseModel):
    status: str = Field(description="Restart result status.")
    kind: str | None = Field(default=None, description="Resource type.")
    name: str | None = Field(default=None, description="Resource name.")
    namespace: str | None = Field(default=None, description="Resource namespace.")
    message: str | None = Field(default=None, description="Error or status message.")


@mcp.tool()
async def restart_workload(
    name: str = Field(..., description="Name of the deployment or statefulset."),
    namespace: str = Field(description="Kubernetes namespace of the workload."),
    kind: str = Field(
        default="deployment",
        description="Resource type: 'deployment' or 'statefulset'",
    ),
) -> RestartStatusMessage:
    """
    Restart a Deployment or StatefulSet the same way `kubectl rollout restart` does. Use describe_pod
    mcp tool call to find the owner and the owner type.

    Useful for reloading pods deployed by unclean Helm charts.
    """
    logger.info(f"Restarting {kind} {name} in namespace: {namespace}")
    k8s_client = _get_vcluster_client()
    apps_v1 = client.AppsV1Api(k8s_client)

    timestamp = datetime.now(timezone.utc).isoformat()

    if kind.lower() == "deployment":
        deployment = apps_v1.read_namespaced_deployment(name=name, namespace=namespace)
        if deployment.spec.template.metadata.annotations is None:
            deployment.spec.template.metadata.annotations = {}
        deployment.spec.template.metadata.annotations[
            "kubectl.kubernetes.io/restartedAt"
        ] = timestamp
        result = apps_v1.patch_namespaced_deployment(
            name=name, namespace=namespace, body=deployment
        )
        return RestartStatusMessage(
            status="success",
            kind="deployment",
            name=result.metadata.name,
            namespace=result.metadata.namespace,
            restarted_at=timestamp,
            generation=result.metadata.generation,
        )
    elif kind.lower() == "statefulset":
        statefulset = apps_v1.read_namespaced_stateful_set(
            name=name, namespace=namespace
        )
        if statefulset.spec.template.metadata.annotations is None:
            statefulset.spec.template.metadata.annotations = {}
        statefulset.spec.template.metadata.annotations[
            "kubectl.kubernetes.io/restartedAt"
        ] = timestamp
        result = apps_v1.patch_namespaced_stateful_set(
            name=name, namespace=namespace, body=statefulset
        )
        return RestartStatusMessage(
            status="success",
            kind="statefulset",
            name=result.metadata.name,
            namespace=result.metadata.namespace,
        )
    else:
        return RestartStatusMessage(
            status="error",
            message=f"Unsupported kind: {kind}. Use 'deployment' or 'statefulset'.",
        )


class PodLogsResponse(BaseModel):
    pod: str = Field(description="Pod name.")
    namespace: str = Field(description="Pod namespace.")
    containers: list[str] = Field(description="Container names.")
    tail_lines: int = Field(description="Number of log lines requested.")
    logs: dict[str, list[str]] = Field(description="Log content per container.")


@mcp.tool()
async def get_pod_logs(
    name: str = Field(..., description="Pod name"),
    namespace: str = Field(description="Kubernetes namespace of the pod."),
    container: str | None = Field(
        default=None, description="Container name (default: all containers)."
    ),
    tail_lines: int = Field(
        default=1000, description="Number of lines from the end (default: 1000)."
    ),
) -> PodLogsResponse:
    """Get logs from a pod in test-vcluster. This should be your primary way of evaluating the state of a deployment."""
    logger.info(f"Getting logs from pod: {name}, namespace: {namespace}")
    k8s_client = _get_vcluster_client()
    v1 = client.CoreV1Api(k8s_client)

    pod = v1.read_namespaced_pod(name=name, namespace=namespace)
    containers = [c.name for c in pod.spec.containers]

    if container:
        container_list = [container]
    else:
        container_list = containers

    logs: dict[str, list[str]] = {}
    for c in container_list:
        try:
            log_content = v1.read_namespaced_pod_log(
                name=name,
                namespace=namespace,
                container=c,
                tail_lines=tail_lines,
                timestamps=True,
            )
            logs[c] = log_content.split("\n") if log_content else []
        except ApiException as e:
            logger.error(f"Failed to get logs for container {c}: {e}")
            logs[c] = [f"Failed to get logs: {e}"]

    return PodLogsResponse(
        pod=name,
        namespace=namespace,
        containers=container_list,
        tail_lines=tail_lines,
        logs=logs,
    )


class ConfigMapMeta(BaseModel):
    name: str = Field(description="ConfigMap name.")
    namespace: str = Field(description="ConfigMap namespace.")


class ConfigMap(BaseModel):
    name: str = Field(description="ConfigMap name.")
    namespace: str = Field(description="ConfigMap namespace.")
    data: dict = Field(default={}, description="ConfigMap data keys and values.")


@mcp.tool()
async def list_configmaps(
    namespace: str = Field(
        description="Kubernetes namespace to list configmaps in.",
    )
) -> list[ConfigMapMeta]:
    """
    List configmaps in the specified namespace of test-vcluster.

    Use this to discover / explore configuration data available in a given namespace.
    """
    logger.info(f"Listing configmaps in namespace: {namespace}")
    k8s_client = _get_vcluster_client()
    v1 = client.CoreV1Api(k8s_client)

    configmaps = v1.list_namespaced_config_map(namespace=namespace)
    return [
        ConfigMapMeta(name=cm.metadata.name, namespace=cm.metadata.namespace)
        for cm in configmaps.items
    ]


@mcp.tool()
async def get_configmap(
    name: str = Field(..., description="Name of the configmap."),
    namespace: str = Field(description="Kubernetes namespace of the configmap."),
) -> ConfigMap:
    """
    Get a specific configmap in test-vcluster.

    Returns the configmap name, namespace, and all of its data.
    """
    logger.info(f"Getting configmap: {name} in namespace: {namespace}")
    k8s_client = _get_vcluster_client()
    v1 = client.CoreV1Api(k8s_client)

    configmap = v1.read_namespaced_config_map(name=name, namespace=namespace)
    return ConfigMap(
        name=configmap.metadata.name,
        namespace=configmap.metadata.namespace,
        data=configmap.data or {},
    )


@contextmanager
def _vcluster_port_forward(
    target_name: str,
    target_type: str,
    target_port: int,
    namespace: str,
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


class HttpResponse(BaseModel):
    status_code: int | None = Field(default=None, description="HTTP status code.")
    headers: dict = Field(default={}, description="Response headers.")
    body: dict | str | None = Field(default=None, description="Response body.")


@mcp.tool()
async def http_get(
    target_name: str = Field(..., description="Pod or service name."),
    target_type: str = Field(..., description="Target type: 'pod' or 'service'."),
    target_port: int = Field(..., description="Target port on pod/service."),
    namespace: str = Field(..., description="Kubernetes namespace of pod/service."),
    path: str = Field(default="/", description="HTTP path to request."),
) -> HttpResponse:
    """
    Perform HTTP GET request against a pod or service in the vcluster.

    Use this to find and analyze endpoints prior to writing integration tests.
    """

    logger.info(f"HTTP GET: {target_type}/{target_name}:{target_port}{path}")
    with _vcluster_port_forward(
        target_name=target_name,
        target_type=target_type,
        target_port=target_port,
        namespace=namespace,
    ) as loopback_addr:
        url = f"http://{loopback_addr}:8080{path}"

        response = requests.get(url, timeout=30)

        # try parsing the body as json, otherwise return plain text
        try:
            body = response.json()
        except ValueError:
            body = response.text

        return HttpResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=body,
        )


@mcp.tool()
async def http_post(
    target_name: str = Field(..., description="Pod or service name."),
    target_type: str = Field(..., description="Target type: 'pod' or 'service'."),
    target_port: int = Field(
        ..., description="Target port for the requset on pod/service."
    ),
    namespace: str = Field(..., description="Kubernetes namespace of pod/service."),
    path: str = Field(default="/", description="HTTP path to request."),
    body: str = Field(..., description="Request body (JSON or plain text)."),
    content_type: str = Field(
        default="application/json", description="Content-Type header."
    ),
) -> HttpResponse:
    """
    Perform HTTP POST request against a pod or service in the vcluster.

    Use this to find and analyze endpoints before writing implementation tests.
    """

    logger.info(f"HTTP POST: {target_type}/{target_name}:{target_port}{path}")
    with _vcluster_port_forward(
        target_name=target_name,
        target_type=target_type,
        target_port=target_port,
        namespace=namespace,
    ) as loopback_addr:
        url = f"http://{loopback_addr}:8080{path}"

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

        return HttpResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=response_body,
        )


def main():
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)
