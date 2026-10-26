import pytest

from pve_cloud_test.vcluster_mcp import list_pods
from fixtures import *


@pytest.mark.asyncio
async def test_list_pods_kube_system(vcluster_integration_config):
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
