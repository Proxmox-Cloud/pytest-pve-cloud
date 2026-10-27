import logging
import time

from fixtures import *
from kubernetes import client

import pytest
from pve_cloud_test.integration import _wait_for_pod_ready
from pve_cloud_test.vcluster_mcp import (describe_pod, get_configmap,
                                         get_pod_logs, get_services, http_get,
                                         list_configmaps, list_pods)

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_list_pods(vcluster_integration_config):
    k8s_client, v1, namespace = vcluster_integration_config

    result = await list_pods(namespace="kube-system")

    assert result is not None
    assert len(result) > 0, "kube-system namespace should have pods"

    pod_names = [p.name for p in result]
    logger.info(f"Found {len(result)} pods in kube-system: {pod_names[:5]}...")


@pytest.mark.asyncio
async def test_get_services(vcluster_integration_config):
    k8s_client, v1, namespace = vcluster_integration_config

    result = await get_services(namespace="kube-system")

    assert result is not None
    assert len(result) > 0, "kube-system namespace should have services"

    service_names = [s.name for s in result]
    logger.info(f"Found {len(result)} services in kube-system: {service_names[:5]}...")


@pytest.mark.asyncio
async def test_list_configmaps(vcluster_integration_config):
    k8s_client, v1, namespace = vcluster_integration_config

    result = await list_configmaps(namespace="kube-system")

    assert result is not None

    configmap_names = [cm.name for cm in result]
    logger.info(
        f"Found {len(result)} configmaps in kube-system: {configmap_names[:5]}..."
    )


@pytest.mark.asyncio
async def test_get_configmap(vcluster_integration_config):
    k8s_client, v1, namespace = vcluster_integration_config

    configmaps = await list_configmaps(namespace="kube-system")
    assert (
        configmaps and len(configmaps) > 0
    ), "kube-system should have configmaps to get"

    # Get the first configmap
    first_configmap = configmaps[0]
    configmap_name = first_configmap.name

    result = await get_configmap(name=configmap_name, namespace="kube-system")

    assert result is not None
    assert result.name == configmap_name

    # Log configmap details for debugging
    logger.info(
        f"Got configmap {configmap_name}: namespace={result.namespace}, data keys={list(result.data.keys())}"
    )


@pytest.mark.asyncio
async def test_get_pod_logs(vcluster_integration_config):
    k8s_client, v1, namespace = vcluster_integration_config

    pods = await list_pods(namespace="kube-system")
    assert pods and len(pods) > 0, "kube-system should have pods to get logs from"

    # Get the first pod
    first_pod = pods[0]
    pod_name = first_pod.name

    result = await get_pod_logs(
        name=pod_name, namespace="kube-system", container=None, tail_lines=1000
    )

    assert result is not None
    assert result.pod == pod_name

    for container_name, log_lines in result.logs.items():
        assert isinstance(log_lines, list)
        logger.info(f"Container {container_name}: {len(log_lines)} log lines")


@pytest.mark.asyncio
async def test_describe_pod(
    vcluster_integration_config,
):
    k8s_client, v1, namespace = vcluster_integration_config

    pods = await list_pods(namespace="kube-system")
    assert pods and len(pods) > 0, "kube-system should have pods to describe"

    # Get the first pod
    first_pod = pods[0]
    pod_name = first_pod.name

    result = await describe_pod(name=pod_name, namespace="kube-system")

    # Validate Pod structure
    assert result is not None

    assert result.name == pod_name

    logger.info(
        f"Described pod {pod_name}: status={result.status}, containers={len(result.containers)}"
    )

    if result.owner_chain:
        logger.info(f"Owner chain: {[o.name for o in result.owner_chain]}")


@pytest.mark.asyncio
async def test_http_get_nginx_pod(vcluster_integration_config):
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

    v1.create_namespaced_pod(namespace=default_namespace, body=pod)

    try:
        ready_pod = _wait_for_pod_ready(v1, default_namespace, pod_name, timeout=120)
        assert ready_pod is not None, f"Pod {pod_name} did not become ready in time"

        # http get with retry, nginx pod ready != nginx ready
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
                    logger.info(
                        f"HTTP GET attempt {attempt + 1} failed: {e}. Retrying in 5s..."
                    )
                    time.sleep(5)

        assert (
            result is not None
        ), f"HTTP GET failed after {max_retries} attempts: {last_error}"

        assert result.status_code == 200
        assert result.body is not None

        logger.info(
            f"HTTP GET successful: status_code={result.status_code}, body length={len(result.body) if result.body else 0}"
        )

    finally:
        try:
            v1.delete_namespaced_pod(name=pod_name, namespace=default_namespace)
        except Exception as e:
            logger.error(f"Warning: Failed to cleanup pod {pod_name}: {e}")
