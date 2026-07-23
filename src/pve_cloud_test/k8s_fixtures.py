import logging
import tempfile

import paramiko
import pytest
import yaml
from kubernetes import client, config
from proxmoxer import ProxmoxAPI

from pve_cloud_test.cloud_fixtures import get_test_env

logger = logging.getLogger(__name__)


def get_e2e_limit_feature(get_test_env, feature_key):
    if (
        "pve_test_limit_features" in get_test_env
        and feature_key in get_test_env["pve_test_limit_features"]
    ):
        return get_test_env["pve_test_limit_features"][feature_key]

    return False


@pytest.fixture(scope="session")
def get_secondary_kubespray_inv(get_test_env):
    logger.info("create secondary kubespray")
    with tempfile.NamedTemporaryFile(
        "w", suffix=".yaml", delete=False
    ) as temp_kubespray_inv:
        yaml.dump(
            {
                "plugin": "pxc.cloud.kubespray_inv",
                "target_pve": get_test_env["pve_test_cluster_name"]
                + "."
                + get_test_env["cloud_inventory"]["pve_cloud_domain"],
                # extra / external cp for testing jump proxy conf
                "extra_control_plane_sans": [
                    f"cp-pytest-secondary.{get_test_env["kubernetes"]["deployments_domain"]}"
                ],
                "stack_name": "pytest-secondary-k8s",
                "static_includes": {
                    "dhcp_stack": "ha-dhcp."
                    + get_test_env["cloud_inventory"]["pve_cloud_domain"],
                    "proxy_stack": "ha-haproxy."
                    + get_test_env["cloud_inventory"]["pve_cloud_domain"],
                    "bind_stack": "ha-bind."
                    + get_test_env["cloud_inventory"]["pve_cloud_domain"],
                    "postgres_stack": "ha-postgres."
                    + get_test_env["cloud_inventory"]["pve_cloud_domain"],
                },
                "tcp_proxies": [],
                "external_domains": [],
                "cluster_cert_entries": [
                    {
                        "zone": get_test_env["kubernetes"]["deployments_domain"],
                        "names": [
                            "alrtmgr-secondary",
                            "vlogs-secondary",
                        ],  # route these specially to this secondary
                    }
                ],
                "qemu_global_vars": {
                    "zpool_create_force": True,  # needed for recreate since by id disk doesnt get wiped
                    "e2e_limit_containerd_downloads": get_e2e_limit_feature(
                        get_test_env, "limit_containerd_downloads"
                    ),
                },
                "qemu_base_parameters": {
                    "cpu": "host",
                    "net0": "virtio,bridge=vmbr0,firewall=1"
                    + f"{get_test_env['net0_vlan_tag_rendered'] if 'net0_vlan_tag_rendered' in get_test_env else ''}",
                    "sockets": 1,
                },
                "qemus": [
                    (
                        {
                            "k8s_roles": ["master", "worker"],
                            "disk": {
                                "size": "150G",
                                "options": {
                                    "discard": "on",
                                    "iothread": "on",
                                    "ssd": "on",
                                    "cache": "unsafe",
                                },
                                "pool": get_test_env["pve_vm_storage_id"],
                            },
                            "zpool_csi_parameters": {
                                "pool_properties": {"ashift": "12"},
                                "vdevs": [
                                    {
                                        "disks": [
                                            "/dev/disk/by-id/scsi-0QEMU_QEMU_HARDDISK_scsi1-pxzfs"
                                        ]
                                    },
                                    {
                                        "disks": [
                                            "/dev/disk/by-id/scsi-0QEMU_QEMU_HARDDISK_scsi2-pxzfs"
                                        ]
                                    },
                                ],
                            },
                            "zfs_localpv_csi_disks": (
                                [
                                    {
                                        "from_storage": {
                                            "size": "50G",
                                            "options": {
                                                "discard": "on",
                                                "iothread": "on",
                                                "ssd": "on",
                                                "cache": "unsafe",  # fastest for consumer ssds
                                            },
                                            "pool": get_test_env["pve_vm_storage_id"],
                                        }
                                    }
                                ]
                                + [
                                    {
                                        "via_passthrough": {
                                            "disk_id": get_test_env["kubernetes"][
                                                "zfs_localpv_test"
                                            ]["disk_id"],
                                            "options": {"iothread": "on"},
                                        }
                                    }
                                ]
                                if "zfs_localpv_test" in get_test_env["kubernetes"]
                                else []
                            ),
                            "parameters": {
                                "cores": 4,
                                "memory": 10240,
                            },
                        }
                        | {
                            "target_host": get_test_env["kubernetes"][
                                "zfs_localpv_test"
                            ]["target_host"]
                        }
                        if "zfs_localpv_test" in get_test_env["kubernetes"]
                        else {}
                    ),
                ],
                "target_pve_hosts": list(get_test_env["pve_test_cluster_hosts"].keys()),
                "root_ssh_pub_key": get_test_env["ssh_pub_key"],
            },
            temp_kubespray_inv,
        )

        temp_kubespray_inv.flush()

        return temp_kubespray_inv.name


@pytest.fixture(scope="session")
def get_kubespray_inv(get_test_env):
    with tempfile.NamedTemporaryFile(
        "w", suffix=".yaml", delete=False
    ) as temp_kubespray_inv:
        yaml.dump(
            {
                "plugin": "pxc.cloud.kubespray_inv",
                "target_pve": get_test_env["pve_test_cluster_name"]
                + "."
                + get_test_env["cloud_inventory"]["pve_cloud_domain"],
                "extra_control_plane_sans": [
                    f"cp-pytest.{get_test_env["kubernetes"]["deployments_domain"]}"
                ],
                "stack_name": "pytest-k8s",
                "static_includes": {
                    "dhcp_stack": "ha-dhcp."
                    + get_test_env["cloud_inventory"]["pve_cloud_domain"],
                    "proxy_stack": "ha-haproxy."
                    + get_test_env["cloud_inventory"]["pve_cloud_domain"],
                    "bind_stack": "ha-bind."
                    + get_test_env["cloud_inventory"]["pve_cloud_domain"],
                    "postgres_stack": "ha-postgres."
                    + get_test_env["cloud_inventory"]["pve_cloud_domain"],
                },
                "tcp_proxies": [
                    {
                        "proxy_name": "postgres-test",
                        "haproxy_port": 6432,
                        "node_port": 30432,
                    },
                    {
                        "proxy_name": "graphite-exporter",
                        "haproxy_port": 9109,
                        "node_port": 30109,
                    },
                ],
                "external_domains": [
                    {
                        "zone": get_test_env["kubernetes"]["deployments_domain"],
                        "names": ["external-example", "test-dns-delete"],
                    }
                ],
                "cluster_cert_entries": [
                    {
                        "zone": get_test_env["kubernetes"]["deployments_domain"],
                        "authoritative_zone": True,
                        "names": ["*"],
                    }
                ],
                "ceph_csi_sc_pools": [
                    {
                        "name": get_test_env["ceph_csi_storage_pool"],
                        "default": True,
                        "mount_options": ["discard", "barrier=0"],
                    }
                ],
                "qemu_global_vars": {
                    "e2e_limit_containerd_downloads": get_e2e_limit_feature(
                        get_test_env, "limit_containerd_downloads"
                    )
                },
                "qemu_base_parameters": (
                    {
                        "cpu": "host",
                        "net0": "virtio,bridge=vmbr0,firewall=1"
                        + f"{get_test_env['net0_vlan_tag_rendered'] if 'net0_vlan_tag_rendered' in get_test_env else ''}",
                        "sockets": 1,
                    }
                    | (
                        {
                            "net1": f"virtio,bridge={get_test_env['pve_ceph_frontend_dhcp_iface']},firewall=1"
                        }
                        if "pve_ceph_frontend_dhcp_iface" in get_test_env
                        else {}
                    )
                ),
                "qemus": [
                    {
                        "k8s_roles": ["master"],
                        "disk": {
                            "size": "50G",
                            "options": {
                                "discard": "on",
                                "iothread": "on",
                                "ssd": "on",
                                "cache": "unsafe",
                            },
                            "pool": get_test_env["pve_vm_storage_id"],
                        },
                        "parameters": {
                            "cores": 4,
                            "memory": 4096,
                        },
                    },
                    {
                        "k8s_roles": ["worker"],
                        "disk": {
                            "size": "100G",
                            "options": {
                                "discard": "on",
                                "iothread": "on",
                                "ssd": "on",
                                "cache": "unsafe",
                            },
                            "pool": get_test_env["pve_vm_storage_id"],
                        },
                        "parameters": {
                            "cores": 4,
                            "memory": 8192,
                        },
                    },
                ],
                "target_pve_hosts": list(get_test_env["pve_test_cluster_hosts"].keys()),
                "root_ssh_pub_key": get_test_env["ssh_pub_key"],
            },
            temp_kubespray_inv,
        )
        temp_kubespray_inv.flush()

        return temp_kubespray_inv.name


def get_kubeconfig(get_test_env, pve_host, stack_name):
    # assumes loaded ssh key like all playbooks
    proxmox = ProxmoxAPI(pve_host, user="root", backend="ssh_paramiko")

    # find k8s master
    master_qemu = None
    host_node = None
    for node in proxmox.nodes.get():
        for qemu in proxmox.nodes(node["node"]).qemu.get():
            if (
                "tags" in qemu
                and stack_name
                + "."
                + get_test_env["cloud_inventory"]["pve_cloud_domain"]
                in qemu["tags"]
                and "master" in qemu["tags"]
            ):
                master_qemu = qemu
                host_node = node["node"]
                break

    assert master_qemu
    assert host_node
    logger.info(master_qemu)

    ifaces = (
        proxmox.nodes(host_node)
        .qemu(master_qemu["vmid"])
        .agent("network-get-interfaces")
        .get()
    )

    master_ipv4 = None

    for iface in ifaces["result"]:
        if iface["name"] == "lo":
            continue  # skip the first loopback device

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

    kubeconfig = (
        stdout.read()
        .decode("utf-8")
        .replace("https://127.0.0.1:6443", f"https://{master_ipv4}:6443")
    )
    assert kubeconfig

    return kubeconfig


@pytest.fixture(scope="session")
def get_primary_kubeconfig(get_test_env):
    test_host = get_test_env["pve_test_cluster_hosts"][
        next(iter(get_test_env["pve_test_cluster_hosts"]))
    ]
    return get_kubeconfig(get_test_env, test_host["ansible_host"], "pytest-k8s")


@pytest.fixture(scope="session")
def get_secondary_kubeconfig(get_test_env):
    test_host = get_test_env["pve_test_cluster_hosts"][
        next(iter(get_test_env["pve_test_cluster_hosts"]))
    ]
    return get_kubeconfig(
        get_test_env, test_host["ansible_host"], "pytest-secondary-k8s"
    )


@pytest.fixture(scope="session")
def get_k8s_api_v1_batch(get_primary_kubeconfig):
    kubeconfig = get_primary_kubeconfig

    # auth kubernetes api
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as temp_file:
        temp_file.write(kubeconfig)
        temp_file.flush()
        config.load_kube_config(config_file=temp_file.name)

    v1 = client.BatchV1Api()

    return v1


@pytest.fixture(scope="session")
def get_k8s_api_v1(get_primary_kubeconfig):
    kubeconfig = get_primary_kubeconfig

    # auth kubernetes api
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as temp_file:
        temp_file.write(kubeconfig)
        temp_file.flush()
        config.load_kube_config(config_file=temp_file.name)

    v1 = client.CoreV1Api()

    return v1


@pytest.fixture(scope="session")
def get_k8s_secondary_api_v1_batch(get_secondary_kubeconfig):
    kubeconfig = get_secondary_kubeconfig

    # auth kubernetes api
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as temp_file:
        temp_file.write(kubeconfig)
        temp_file.flush()
        config.load_kube_config(config_file=temp_file.name)

    v1 = client.BatchV1Api()

    return v1


@pytest.fixture(scope="session")
def get_k8s_secondary_api_v1(get_secondary_kubeconfig):
    kubeconfig = get_secondary_kubeconfig

    # auth kubernetes api
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as temp_file:
        temp_file.write(kubeconfig)
        temp_file.flush()
        config.load_kube_config(config_file=temp_file.name)

    v1 = client.CoreV1Api()

    return v1
