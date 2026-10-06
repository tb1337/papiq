import papiq
from papiq.core import ports


def test_version_is_set() -> None:
    assert papiq.__version__


def test_ports_are_exported() -> None:
    assert all(hasattr(ports, name) for name in ports.__all__)
