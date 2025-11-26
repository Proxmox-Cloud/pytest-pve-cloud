import pytest
import yaml
from proxmoxer import ProxmoxAPI
import paramiko
import base64
import os
import re
import random
import string
from pve_cloud_test.terraform import apply, destroy
from kubernetes import client, config
import tempfile
import logging
import redis
from pve_cloud_test.cloud_fixtures import *

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def set_k8s_auth(get_test_env):
  logger.info("setting k8s auth os variables for tf")
  first_test_host = get_test_env["pve_test_hosts"][next(iter(get_test_env["pve_test_hosts"]))]
  
  # assumes loaded ssh key like all playbooks
  proxmox = ProxmoxAPI(first_test_host["ansible_host"], user="root", backend='ssh_paramiko')

  # find k8s master
  master_qemu = None
  host_node = None
  for node in proxmox.nodes.get():
    for qemu in proxmox.nodes(node["node"]).qemu.get():
      if "tags" in qemu and "pytest-k8s." + get_test_env["pve_test_cloud_domain"] in qemu["tags"] and "master" in qemu["tags"]:
        master_qemu = qemu
        host_node = node["node"]
        break

  assert master_qemu
  assert host_node
  logger.info(master_qemu)

  ifaces = proxmox.nodes(host_node).qemu(master_qemu["vmid"]).agent("network-get-interfaces").get()

  master_ipv4 = None

  for iface in ifaces["result"]:
    if iface["name"] == "lo":
      continue # skip the first loopback device

    # after that comes the primary interface
    for ip_address in iface["ip-addresses"]:
      if ip_address["ip-address-type"] == "ipv4":
        master_ipv4 = ip_address["ip-address"]
        break
    
    assert master_ipv4

    break

  # now we can use that address to connect via ssh
  ssh = paramiko.SSHClient()
  ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
  ssh.connect(master_ipv4, username="admin")

  # since we need root we cant use sftp and root via ssh is disabled
  _, stdout, _ = ssh.exec_command("sudo cat /etc/kubernetes/admin.conf")

  kubeconfig = stdout.read().decode('utf-8').replace("https://127.0.0.1:6443", f"https://{master_ipv4}:6443")
  assert kubeconfig

  # variables that terraform applies in test will use
  os.environ["TF_VAR_master_kubeconfig_b64"] = base64.b64encode(kubeconfig.encode('utf-8')).decode('utf-8')
  os.environ["TF_VAR_master_ip"] = master_ipv4
  os.environ["TF_VAR_pve_ansible_host"] = first_test_host["ansible_host"]

  return kubeconfig


@pytest.fixture(scope="session")
def set_pve_cloud_auth(request, get_test_env):
  logger.info("setting pve cloud auth env variables for tf")
  first_test_host = get_test_env["pve_test_hosts"][next(iter(get_test_env["pve_test_hosts"]))]

  ssh = paramiko.SSHClient()
  ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
  ssh.connect(first_test_host["ansible_host"], username="root")

  _, stdout, _ = ssh.exec_command("sudo cat /etc/pve/cloud/secrets/patroni.pass")
  patroni_pass = stdout.read().decode('utf-8')

  pg_conn_str = f"postgres://postgres:{patroni_pass}@{get_test_env["pve_test_cloud_inv_cluster"]["pve_haproxy_floating_ip_internal"]}:5000/tf_states?sslmode=disable"
  pg_conn_str_orm = f"postgresql+psycopg2://postgres:{patroni_pass}@{get_test_env["pve_test_cloud_inv_cluster"]["pve_haproxy_floating_ip_internal"]}:5000/pve_cloud?sslmode=disable"

  # variables that terraform applies in test will use
  os.environ["PG_CONN_STR"] = pg_conn_str
  os.environ["TF_VAR_pve_cloud_pg_cstr"] = pg_conn_str_orm
  os.environ["TF_VAR_pve_ansible_host"] = first_test_host["ansible_host"]
  
  # fetch bind update key for ingress dns validation
  _, stdout, _ = ssh.exec_command("sudo cat /etc/pve/cloud/secrets/internal.key")
  bind_key_file = stdout.read().decode('utf-8')

  bind_internal_key = re.search(r'secret\s+"([^"]+)";', bind_key_file).group(1)

  return { "bind_internal_key": bind_internal_key }
  

@pytest.fixture(scope="session")
def get_k8s_api_v1(set_k8s_auth):
  kubeconfig = set_k8s_auth

  # auth kubernetes api
  with tempfile.NamedTemporaryFile(mode='w', delete=False) as temp_file:
    temp_file.write(kubeconfig)
    temp_file.flush()
    config.load_kube_config(config_file=temp_file.name)

  v1 = client.CoreV1Api()

  return v1