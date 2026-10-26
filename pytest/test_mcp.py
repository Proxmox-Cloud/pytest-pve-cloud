import time

from fixtures import *
from kubernetes import client

import pytest
from pve_cloud_test.integration import _wait_for_pod_ready
from pve_cloud_test.vcluster_mcp import (describe_pod, get_configmap,
                                         get_pod_logs, get_services, http_get,
                                         list_configmaps, list_pods)


@pytest.mark.asyncio
async def test_list_pods(vcluster_integration_config):
    """
    Test list_pods tool against kube-system namespace in vcluster.

    Uses vcluster_integration_config fixture which injects the kubernetes
    client from integration_config into vcluster_mcp module via monkeypatching.
    """
    k8s_client, v1, namespace = vcluster_integration_config

    # Call the MCP tool
    result = await list_pods(namespace="kube-system")

    # Validate result
    assert result is not None
    assert isinstance(result, list)
    assert len(result) > 0, "kube-system namespace should have pods"

    # Validate PodMeta structure
    for pod in result:
        assert hasattr(pod, "name")
        assert hasattr(pod, "status")
        assert pod.name
        assert pod.status in ["Running", "Pending", "Succeeded", "Failed"]

    # Log some pod names for debugging
    pod_names = [p.name for p in result]
    print(f"Found {len(result)} pods in kube-system: {pod_names[:5]}...")


@pytest.mark.asyncio
async def test_get_services(vcluster_integration_config):
    """
    Test get_services tool against kube-system namespace in vcluster.
    """
    k8s_client, v1, namespace = vcluster_integration_config

    # Call the MCP tool
    result = await get_services(namespace="kube-system")

    # Validate result
    assert result is not None
    assert isinstance(result, list)
    assert len(result) > 0, "kube-system namespace should have services"

    # Validate ServiceInstance structure
    for service in result:
        assert hasattr(service, "name")
        assert hasattr(service, "namespace")
        assert hasattr(service, "type")
        assert hasattr(service, "ports")
        assert hasattr(service, "selector")
        assert service.name
        assert service.namespace == "kube-system"

    # Log some service names for debugging
    service_names = [s.name for s in result]
    print(f"Found {len(result)} services in kube-system: {service_names[:5]}...")


@pytest.mark.asyncio
async def test_list_configmaps(vcluster_integration_config):
    """
    Test list_configmaps tool against kube-system namespace in vcluster.
    """
    k8s_client, v1, namespace = vcluster_integration_config

    # Call the MCP tool
    result = await list_configmaps(namespace="kube-system")

    # Validate result
    assert result is not None
    assert isinstance(result, list)

    # Validate ConfigMapMeta structure
    for configmap in result:
        assert hasattr(configmap, "name")
        assert hasattr(configmap, "namespace")
        assert configmap.name
        assert configmap.namespace == "kube-system"

    # Log some configmap names for debugging
    configmap_names = [cm.name for cm in result]
    print(f"Found {len(result)} configmaps in kube-system: {configmap_names[:5]}...")


@pytest.mark.asyncio
async def test_get_configmap(vcluster_integration_config):
    """
    Test get_configmap tool on the first configmap returned by list_configmaps in kube-system.
    """
    k8s_client, v1, namespace = vcluster_integration_config

    # First list configmaps in kube-system
    configmaps = await list_configmaps(namespace="kube-system")
    assert (
        configmaps and len(configmaps) > 0
    ), "kube-system should have configmaps to get"

    # Get the first configmap
    first_configmap = configmaps[0]
    configmap_name = first_configmap.name

    # Call get_configmap on the first configmap
    result = await get_configmap(name=configmap_name, namespace="kube-system")

    # Validate ConfigMap structure
    assert result is not None
    assert hasattr(result, "name")
    assert hasattr(result, "namespace")
    assert hasattr(result, "data")
    assert result.name == configmap_name
    assert result.namespace == "kube-system"
    assert isinstance(result.data, dict)

    # Log configmap details for debugging
    print(
        f"Got configmap {configmap_name}: namespace={result.namespace}, data keys={list(result.data.keys())}"
    )


@pytest.mark.asyncio
async def test_get_pod_logs(vcluster_integration_config):
    """
    Test get_pod_logs tool on the first pod returned by list_pods in kube-system.
    """
    k8s_client, v1, namespace = vcluster_integration_config

    # First list pods in kube-system
    pods = await list_pods(namespace="kube-system")
    assert pods and len(pods) > 0, "kube-system should have pods to get logs from"

    # Get the first pod
    first_pod = pods[0]
    pod_name = first_pod.name

    # Call get_pod_logs on the first pod
    result = await get_pod_logs(
        name=pod_name, namespace="kube-system", container=None, tail_lines=1000
    )

    # Validate PodLogsResponse structure
    assert result is not None
    assert hasattr(result, "pod")
    assert hasattr(result, "namespace")
    assert hasattr(result, "containers")
    assert hasattr(result, "tail_lines")
    assert hasattr(result, "logs")
    assert result.pod == pod_name
    assert result.namespace == "kube-system"
    assert len(result.containers) > 0
    assert isinstance(result.logs, dict)

    # Validate that logs contain lines for each container
    for container_name, log_lines in result.logs.items():
        assert isinstance(log_lines, list)
        print(f"Container {container_name}: {len(log_lines)} log lines")

    print(f"Got logs from pod {pod_name}: containers={result.containers}")


@pytest.mark.asyncio
async def test_describe_pod(
    vcluster_integration_config,
):
    """
    Test describe_pod tool on the first pod returned by list_pods on kube-system.
    """
    k8s_client, v1, namespace = vcluster_integration_config

    # First list pods in kube-system
    pods = await list_pods(namespace="kube-system")
    assert pods and len(pods) > 0, "kube-system should have pods to describe"

    # Get the first pod
    first_pod = pods[0]
    pod_name = first_pod.name

    # Call describe_pod on the first pod
    result = await describe_pod(name=pod_name, namespace="kube-system")

    # Validate Pod structure
    assert result is not None
    assert hasattr(result, "name")
    assert hasattr(result, "namespace")
    assert hasattr(result, "status")
    assert hasattr(result, "labels")
    assert hasattr(result, "containers")
    assert hasattr(result, "container_statuses")
    assert hasattr(result, "conditions")
    assert hasattr(result, "owner_chain")

    assert result.name == pod_name
    assert result.namespace == "kube-system"
    assert result.status in ["Running", "Pending", "Succeeded", "Failed"]

    # Log pod details for debugging
    print(
        f"Described pod {pod_name}: status={result.status}, containers={len(result.containers)}"
    )
    if result.owner_chain:
        print(f"Owner chain: {[o.name for o in result.owner_chain]}")


@pytest.mark.asyncio
async def test_http_get_nginx_pod(vcluster_integration_config):
    """
    Test http_get tool by deploying an nginx pod in the default namespace.
    """
    k8s_client, v1, namespace = vcluster_integration_config

    pod_name = "test-nginx-pod"
    default_namespace = "default"

    # Create nginx pod spec
    pod = client.V1Pod(
        api_version="v1",
        kind="Pod",
        metadata=client.V1ObjectMeta(name=pod_name, namespace=default_namespace),
        spec=client.V1PodSpec(
            containers=[
                client.V1Container(
                    name="nginx",
                    image="nginx:alpine3.24-slim",
                    image_pull_policy="Always",
                    ports=[client.V1ContainerPort(container_port=80, protocol="TCP")],
                )
            ],
            restart_policy="Always",
        ),
    )

    # Deploy the pod
    v1.create_namespaced_pod(namespace=default_namespace, body=pod)

    try:
        # Wait for pod to be ready
        ready_pod = _wait_for_pod_ready(v1, default_namespace, pod_name, timeout=120)
        assert ready_pod is not None, f"Pod {pod_name} did not become ready in time"

        # Call http_get MCP tool against the nginx pod with retry
        max_retries = 3
        last_error = None
        result = None

        for attempt in range(max_retries):
            try:
                result = await http_get(
                    target_name=pod_name,
                    target_type="pod",
                    target_port=80,
                    namespace=default_namespace,
                    path="/",
                )
                break
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    print(
                        f"HTTP GET attempt {attempt + 1} failed: {e}. Retrying in 5s..."
                    )
                    time.sleep(5)

        assert (
            result is not None
        ), f"HTTP GET failed after {max_retries} attempts: {last_error}"

        # Validate response
        assert result is not None
        assert result.status_code == 200
        assert result.body is not None
        assert "<html>" in result.body or "<!DOCTYPE html>" in result.body

        print(
            f"HTTP GET successful: status_code={result.status_code}, body length={len(result.body) if result.body else 0}"
        )

    finally:
        # Cleanup: delete the pod
        try:
            v1.delete_namespaced_pod(name=pod_name, namespace=default_namespace)
            print(f"Deleted pod {pod_name} from namespace {default_namespace}")
        except Exception as e:
            print(f"Warning: Failed to cleanup pod {pod_name}: {e}")
