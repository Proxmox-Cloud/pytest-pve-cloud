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
        "--skip-fixture-tags",
        type=str,
        default=None,
        help="Runs only fixtures with the specified tags (comma seperated list). Works for fixtures annotated with special cloud_fixture from cloud_fixtures.py",
    )
    parser.addoption(
        "--runner-tags",
        type=str,
        default=None,
        help="Runs only fixtures with the specified tags (comma seperated list). Works for fixtures annotated with special cloud_fixture from cloud_fixtures.py",
    )
    parser.addoption(
        "--skip-runner-tags",
        type=str,
        default=None,
        help="Runs only fixtures with the specified tags (comma seperated list). Works for fixtures annotated with special cloud_fixture from cloud_fixtures.py",
    )
    parser.addoption("--ansible-verbosity", type=int, choices=[1, 2, 3], default=0)
