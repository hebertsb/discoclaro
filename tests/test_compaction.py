""" "Liberado pero no recuperado" (T2C.2): pura."""

import pytest

from discoclaro.domain.compaction import CompactionGap, needs_compaction


def test_a_compaction_gap_needs_a_path():
    with pytest.raises(ValueError):
        CompactionGap(path="", file_bytes=10, used_bytes=5)


def test_a_compaction_gap_cannot_have_negative_sizes():
    with pytest.raises(ValueError):
        CompactionGap(path="x.vhdx", file_bytes=-1, used_bytes=0)
    with pytest.raises(ValueError):
        CompactionGap(path="x.vhdx", file_bytes=0, used_bytes=-1)


def test_gap_bytes_is_the_difference():
    gap = CompactionGap(path="x.vhdx", file_bytes=100, used_bytes=30)
    assert gap.gap_bytes == 70


def test_gap_bytes_is_never_negative():
    gap = CompactionGap(path="x.vhdx", file_bytes=30, used_bytes=100)
    assert gap.gap_bytes == 0


def test_needs_compaction_is_false_under_the_default_threshold():
    gap = CompactionGap(path="x.vhdx", file_bytes=4 * 1024**3, used_bytes=0)
    assert needs_compaction(gap) is False


def test_needs_compaction_is_true_at_the_default_threshold():
    gap = CompactionGap(path="x.vhdx", file_bytes=5 * 1024**3, used_bytes=0)
    assert needs_compaction(gap) is True


def test_needs_compaction_accepts_a_custom_threshold():
    gap = CompactionGap(path="x.vhdx", file_bytes=100, used_bytes=0)
    assert needs_compaction(gap, min_gap_bytes=50) is True
    assert needs_compaction(gap, min_gap_bytes=200) is False
