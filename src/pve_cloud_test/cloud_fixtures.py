import pytest
import ansible_runner
import os
import yaml
import tempfile
import logging
from proxmoxer import ProxmoxAPI
import jsonschema

logger = logging.getLogger(__name__)


# load the test environment yaml from parameters
@pytest.fixture(scope="session")
def get_test_env(request):
  test_pve_yaml_file = os.getenv("PVE_CLOUD_TEST_CONF")
  assert test_pve_yaml_file
  
  os.environ["TF_VAR_test_pve_conf"] = test_pve_yaml_file

  assert test_pve_yaml_file is not None
  with open(test_pve_yaml_file, "r") as file:
    test_pve_conf = yaml.safe_load(file)

  # load schema and validate
  with open(os.path.dirname(os.path.realpath(__file__)) + "/test_env_schema.yaml") as file:
    test_env_schema = yaml.safe_load(file)
  
  jsonschema.validate(instance=test_pve_conf, schema=test_env_schema)

  return test_pve_conf
  

# connect proxmoxer to pve cluster
@pytest.fixture(scope="session")
def get_proxmoxer(get_test_env):
  first_test_host = get_test_env["pve_test_hosts"][next(iter(get_test_env["pve_test_hosts"]))]
  
  proxmox = ProxmoxAPI(first_test_host["ansible_host"], user="root", backend='ssh_paramiko')
  nodes = proxmox.nodes.get()

  assert nodes

  return proxmox


@pytest.fixture(scope="session")
def create_dyn_inv(get_test_env):
  with tempfile.NamedTemporaryFile('w', suffix='.yaml', delete=False) as temp_dyn_inv:
    # write the inventory that is normally filled by pvcli connect-cluster
    yaml.dump({
      get_test_env["pve_test_cloud_domain"]: {
        get_test_env["pve_test_cluster_name"] : get_test_env["pve_test_hosts"]
      }
    }, temp_dyn_inv)
    temp_dyn_inv.flush()
    logger.info(f"created dyn inv {temp_dyn_inv.name}")

    return temp_dyn_inv.name # return only works because delete=False
