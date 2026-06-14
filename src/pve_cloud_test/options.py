import pytest


# custom yaml env that defines the testing pve cloud environment
def pytest_addoption(parser):
    parser.addoption(
        "--skip-cleanup",
        action="store_true",
        default=False,
        help="Skips the fixture cleanup part and also test cleanups that implement this flag. Setting this keeps the infra state and allows for hands on development.",
    )
    parser.addoption(
        "--skip-fixtures",
        action="store_true",
        default=False,
        help="Skips fixtures alltogether. Target run only test on consequtive runs. This will also skip the cleanup of the fixture.",
    )
    # only avaible in pxc_cloud collection (decorator is defined there)
    parser.addoption(
        "--fixture-tags",
        type=str,
        default=None,
        help="Runs only fixtures with the specified tags (comma seperated list). Works for fixtures annotated with special cloud_fixture from cloud_fixtures.py",
    )
    parser.addoption(
        "--skip-kubespray",
        action="store_true",
        default=False,
        help="Skips the kubespray playbook part when syncing test kubespray clusters. This saves a lot of time in total.",
    )
    parser.addoption("--ansible-verbosity", type=int, choices=[1, 2, 3], default=0)
