import pytest
import yaml
from kubernetes import config, dynamic

from pve_cloud_test.integration import integration_config


# monkey patch does not work with session scoped fixtures!
@pytest.fixture
def vcluster_integration_config(integration_config, monkeypatch):
    kubeconfig_path, k8s_client, v1, namespace = integration_config

    # mock functions in vcluster_mcp.py that load / initialize kubernetes client
    # to simply use the vcluster integration client

    def mock_get_vcluster_config_dict(cluster_name=None):
        with open(kubeconfig_path, "r") as f:
            return yaml.safe_load(f)

    monkeypatch.setattr(
        "pve_cloud_test.vcluster_mcp._get_vcluster_config_dict",
        mock_get_vcluster_config_dict,
    )

    def mock_get_vcluster_client(cluster_name=None):
        return k8s_client

    monkeypatch.setattr(
        "pve_cloud_test.vcluster_mcp._get_vcluster_client", mock_get_vcluster_client
    )

    def mock_get_vcluster_dynamic_client(cluster_name=None):
        client_obj = config.new_client_from_config(config_file=kubeconfig_path)
        return dynamic.DynamicClient(client_obj)

    monkeypatch.setattr(
        "pve_cloud_test.vcluster_mcp._get_vcluster_dynamic_client",
        mock_get_vcluster_dynamic_client,
    )

    return k8s_client, v1, namespace
