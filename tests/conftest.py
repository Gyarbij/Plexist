import pathlib
import sys

import pytest

plexist_path = pathlib.Path(__file__).resolve().parents[1] / "plexist"
sys.path.insert(0, str(plexist_path))

from modules.base import ServiceRegistry


@pytest.fixture(autouse=True)
def reset_service_registry():
    original = dict(ServiceRegistry._providers)
    try:
        yield
    finally:
        ServiceRegistry._providers = original


@pytest.fixture(autouse=True, scope="session")
def add_plexist_to_path():
    yield
    sys.path.remove(str(plexist_path))
