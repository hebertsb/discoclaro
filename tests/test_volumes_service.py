"""Pruebas del servicio de volúmenes (T3.1)."""

from discoclaro.domain.models import Volume
from discoclaro.services.volumes_service import run_volumes


def test_run_volumes_returns_what_the_injected_lister_gives():
    fake = [Volume("C:", "C:\\", 1000, 400, 600)]
    assert run_volumes(volumes=lambda: fake) == fake


def test_run_volumes_uses_the_real_lister_by_default():
    volumes = run_volumes()
    assert all(isinstance(volume, Volume) for volume in volumes)
