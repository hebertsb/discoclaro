"""Pruebas del archivo de resultado del escaneo con permisos de administrador."""

import json

import pytest

from discoclaro.services.elevation_service import (
    MAX_RESULT_BYTES,
    RESULT_NAME,
    ResultError,
    read_result,
    write_result,
)


def good_result(**changes):
    result = {
        "version": 1,
        "ok": True,
        "snapshot_id": 1,
        "folders": 10,
        "files": 20,
        "skipped_links": 3,
        "nodes_saved": 4,
    }
    return {**result, **changes}


def failure_result(kind="not_a_folder", detail="C:\\"):
    return {"version": 1, "ok": False, "kind": kind, "detail": detail}


def test_a_result_survives_the_trip(tmp_path):
    write_result(tmp_path, good_result())
    assert read_result(tmp_path) == good_result()
    assert [path.name for path in tmp_path.iterdir()] == [RESULT_NAME]


def test_a_failure_survives_the_trip(tmp_path):
    write_result(tmp_path, failure_result("unexpected", "ValueError"))
    assert read_result(tmp_path) == failure_result("unexpected", "ValueError")


def test_a_new_result_replaces_an_old_one(tmp_path):
    write_result(tmp_path, good_result(files=1))
    write_result(tmp_path, good_result(files=2))
    assert read_result(tmp_path)["files"] == 2


def test_a_missing_result_is_an_error(tmp_path):
    with pytest.raises(ResultError):
        read_result(tmp_path)


def test_an_oversized_result_is_refused_without_reading_it_all(tmp_path):
    (tmp_path / RESULT_NAME).write_bytes(b" " * (MAX_RESULT_BYTES + 1))
    with pytest.raises(ResultError):
        read_result(tmp_path)


@pytest.mark.parametrize(
    "raw",
    [b"", b"no es json", b"\xff\xfe\x00", b"[]", b'"texto"', b"null", b"123"],
)
def test_text_that_is_not_a_json_object_is_refused(tmp_path, raw):
    (tmp_path / RESULT_NAME).write_bytes(raw)
    with pytest.raises(ResultError):
        read_result(tmp_path)


BAD_RESULTS = [
    good_result(version=2),
    good_result(ok="true"),
    good_result(ok=1),
    good_result(snapshot_id=0),
    good_result(snapshot_id=-1),
    good_result(folders=-1),
    good_result(files=10**12 + 1),
    good_result(skipped_links=1.5),
    good_result(nodes_saved="4"),
    good_result(files=True),
    {**good_result(), "extra": 1},
    {key: value for key, value in good_result().items() if key != "files"},
    failure_result(kind="rm -rf"),
    failure_result(kind=5),
    failure_result(detail="x" * 301),
    failure_result(detail=5),
    {**failure_result(), "extra": 1},
    {"version": 1, "ok": False, "kind": "unexpected"},
    {"version": 1},
]


@pytest.mark.parametrize("payload", BAD_RESULTS)
def test_a_result_that_does_not_follow_the_protocol_exactly_is_refused(
    tmp_path, payload
):
    (tmp_path / RESULT_NAME).write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ResultError):
        read_result(tmp_path)


def test_the_longest_allowed_detail_is_accepted(tmp_path):
    write_result(tmp_path, failure_result(detail="x" * 300))
    assert read_result(tmp_path)["detail"] == "x" * 300


def test_the_largest_allowed_count_is_accepted(tmp_path):
    write_result(tmp_path, good_result(files=10**12))
    assert read_result(tmp_path)["files"] == 10**12
