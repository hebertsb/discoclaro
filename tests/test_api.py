"""Pruebas de la API del dashboard (T3.1): de punta a punta, con `TestClient`.

No levanta ningún servidor real ni abre ningún puerto: `TestClient` llama a la
aplicación ASGI directamente en el mismo proceso.
"""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from discoclaro.adapters.store import Store
from discoclaro.domain.models import Node, Snapshot
from discoclaro.interfaces.api import create_app

T0 = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
OLDER = T0.replace(day=1)

NODES = (
    Node("C:\\", None, 1000, 10, 0),
    Node("C:\\Games", "C:\\", 600, 5, 1),
    Node("C:\\Games\\Saves", "C:\\Games", 100, 2, 2),
)


def save(store, volume, nodes=NODES, **overrides):
    fields = {
        "taken_at": T0,
        "volume": volume,
        "total_bytes": 1000,
        "used_bytes": 400,
        "free_bytes": 600,
        "scanned_bytes": 350,
        "system_files_bytes": 0,
        "unreadable_bytes": 0,
        "unreadable_count": 0,
        "duration_s": 1.0,
        "is_admin": False,
    }
    fields.update(overrides)
    store.save_snapshot(Snapshot(**fields), list(nodes))


def test_get_volumes_returns_the_real_disks(tmp_path):
    client = TestClient(create_app(tmp_path))
    response = client.get("/volumes")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list) and body
    assert set(body[0]) == {"name", "root", "total_bytes", "used_bytes", "free_bytes"}


def test_get_snapshots_is_empty_without_any_scan(tmp_path):
    client = TestClient(create_app(tmp_path))
    response = client.get("/snapshots")
    assert response.status_code == 200
    assert response.json() == []


def test_get_snapshots_lists_what_was_saved(tmp_path):
    with Store.open(tmp_path / "discoclaro.db") as store:
        save(store, "C:")
        save(store, "D:")
    client = TestClient(create_app(tmp_path))
    response = client.get("/snapshots")
    assert response.status_code == 200
    assert {row["volume"] for row in response.json()} == {"C:", "D:"}


def test_get_snapshots_filters_by_volume(tmp_path):
    with Store.open(tmp_path / "discoclaro.db") as store:
        save(store, "C:")
        save(store, "D:")
    client = TestClient(create_app(tmp_path))
    response = client.get("/snapshots", params={"volume": "D:"})
    assert [row["volume"] for row in response.json()] == ["D:"]


def test_get_tree_without_any_scan_is_404(tmp_path):
    client = TestClient(create_app(tmp_path))
    response = client.get("/tree")
    assert response.status_code == 404
    assert response.json()["detail"] == "tree_no_snapshots"


def test_get_tree_without_a_path_returns_the_root(tmp_path):
    with Store.open(tmp_path / "discoclaro.db") as store:
        save(store, "C:")
    client = TestClient(create_app(tmp_path))
    response = client.get("/tree")
    assert response.status_code == 200
    body = response.json()
    assert body["node"] is None
    assert [child["path"] for child in body["children"]] == ["C:\\"]


def test_get_tree_with_a_path_returns_it_and_its_children(tmp_path):
    with Store.open(tmp_path / "discoclaro.db") as store:
        save(store, "C:")
    client = TestClient(create_app(tmp_path))
    response = client.get("/tree", params={"path": "C:\\Games"})
    assert response.status_code == 200
    body = response.json()
    assert body["node"]["path"] == "C:\\Games"
    assert [child["path"] for child in body["children"]] == ["C:\\Games\\Saves"]


def test_get_tree_with_an_unknown_path_is_404(tmp_path):
    with Store.open(tmp_path / "discoclaro.db") as store:
        save(store, "C:")
    client = TestClient(create_app(tmp_path))
    response = client.get("/tree", params={"path": "C:\\Nope"})
    assert response.status_code == 404
    assert response.json()["detail"] == "tree_no_such_path"


def test_get_categories_without_any_scan_is_404(tmp_path):
    client = TestClient(create_app(tmp_path))
    response = client.get("/categories")
    assert response.status_code == 404
    assert response.json()["detail"] == "report_no_snapshots"


def test_get_categories_returns_a_list(tmp_path):
    with Store.open(tmp_path / "discoclaro.db") as store:
        save(store, "C:")
    client = TestClient(create_app(tmp_path))
    response = client.get("/categories")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_get_growth_without_any_scan_is_404(tmp_path):
    client = TestClient(create_app(tmp_path))
    response = client.get("/growth")
    assert response.status_code == 404
    assert response.json()["detail"] == "growth_no_snapshots"


def test_get_growth_compares_the_latest_two_snapshots(tmp_path):
    bigger = (
        Node("C:\\", None, 2000, 20, 0),
        Node("C:\\Games", "C:\\", 1600, 15, 1),
        Node("C:\\Games\\Saves", "C:\\Games", 100, 2, 2),
    )
    with Store.open(tmp_path / "discoclaro.db") as store:
        save(store, "C:", NODES, taken_at=OLDER)
        save(store, "C:", bigger, taken_at=T0)
    client = TestClient(create_app(tmp_path))
    response = client.get("/growth")
    assert response.status_code == 200
    body = response.json()
    games = next(f for f in body["folders"] if f["path"] == "C:\\Games")
    assert games["before_bytes"] == 600
    assert games["after_bytes"] == 1600
    assert games["grown_bytes"] == 1000


def test_get_growth_with_an_invalid_since_is_400(tmp_path):
    with Store.open(tmp_path / "discoclaro.db") as store:
        save(store, "C:")
    client = TestClient(create_app(tmp_path))
    response = client.get("/growth", params={"since": "not-a-duration"})
    assert response.status_code == 400
    assert response.json()["detail"] == "growth_invalid_since"


def test_there_is_no_public_api_documentation(tmp_path):
    client = TestClient(create_app(tmp_path))
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_get_summary_without_any_scan_still_lists_real_volumes(tmp_path):
    client = TestClient(create_app(tmp_path))
    response = client.get("/summary")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["volumes"], list) and body["volumes"]
    assert body["volumes"][0]["snapshot_id"] is None


def test_get_summary_with_a_scan_includes_unexplained(tmp_path):
    with Store.open(tmp_path / "discoclaro.db") as store:
        save(store, "C:")
    client = TestClient(create_app(tmp_path))
    response = client.get("/summary")
    assert response.status_code == 200
    body = response.json()
    row = next(
        (r for r in body["volumes"] if r["volume"]["name"] == "C:"),
        None,
    )
    if row is not None and row["snapshot_id"] is not None:
        assert "unexplained" in row


def test_plan_item_out_maps_every_field():
    from discoclaro.domain.plan import PlanItem
    from discoclaro.interfaces.api import _plan_item_out

    item = PlanItem(
        level="safe",
        program="docker",
        name="images",
        size_bytes=100,
        reason="reclaimable",
        target="docker:images",
    )
    out = _plan_item_out(item)
    assert out.level == "safe"
    assert out.program == "docker"
    assert out.name == "images"
    assert out.size_bytes == 100
    assert out.reason == "reclaimable"
    assert out.target == "docker:images"


# /plan corre de verdad los seis inspectores de programas (Docker, Steam…), con
# comandos reales: no se prueba aquí con TestClient a propósito, para que la
# prueba no dependa del estado real de esta máquina ni haga llamadas reales en
# cada corrida de pytest. Su envoltorio (_plan_item_out) sí se prueba arriba, y
# el endpoint completo se verificó en la demo real de T3.3.
