import pytest

# custom yaml env that defines the testing pve cloud environment
def pytest_addoption(parser):
    parser.addoption(
        "--skip-cleanup",
        action="store_true",
        default=False,
        help="Skips the fixture cleanup part, faster for consequtive runs / tdd.",
    )
    parser.addoption(
        "--skip-fixture-init",
        action="store_true",
        default=False,
        help="Skips the initialization part of fixtures. Target run only test on consequtive runs.",
    )
    parser.addoption(
        "--ansible-verbosity",
        type=int,
        choices=[1,2,3],
        default=0,
        help="Increase verbosity level (-v, -vv, -vvv)"
    )
    parser.addoption(
        "--skip-apply",
        action="store_true",
        default=False,
        help="Skips the terraform apply part, helps with faster writing tests.",
    )
    parser.addoption(
        "--tf-upgrade",
        action="store_true",
        default=False,
        help="Runs init --upgrade instead of just init for terraform scenarios.",
    )