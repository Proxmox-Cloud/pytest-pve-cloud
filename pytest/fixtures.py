import pytest
from pve_cloud_test.integration import integration_config


@pytest.fixture
def vcluster_integration_config(integration_config, monkeypatch):
    """
    Fixture that injects the kubernetes client from integration_config
    into vcluster_mcp module via monkeypatching.

    Returns the k8s_client, v1, and namespace for direct access if needed.
    """
    kubeconfig_path, k8s_client, v1, namespace = integration_config

    # Patch _get_vcluster_client to return our fixture's client
    def mock_get_vcluster_client(cluster_name=None):
        return k8s_client

    monkeypatch.setattr(
        "pve_cloud_test.vcluster_mcp._get_vcluster_client",
        mock_get_vcluster_client
    )

    return k8s_client, v1, namespace
