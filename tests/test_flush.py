"""Flush timing, stop rules and merge checks of a capture run on a fake clock (ADR 0005)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ingester.flush import (
    FlushPolicy,
    MergeError,
    check_merged_counts,
    check_merged_keys,
    folder_bytes,
    parse_end_at,
    stop_reason,
)

END_AT = datetime(2026, 1, 2, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.now_s = 1000.0

    def __call__(self) -> float:
        return self.now_s


def test_flush_is_due_after_the_interval() -> None:
    clock = FakeClock()
    policy = FlushPolicy(interval_s=300.0, max_records=5000, clock=clock)
    assert not policy.due(pending=1)
    clock.now_s += 299.9
    assert not policy.due(pending=1)
    clock.now_s += 0.1
    assert policy.due(pending=1)


def test_flush_is_due_at_the_record_cap_before_the_interval() -> None:
    policy = FlushPolicy(interval_s=300.0, max_records=5000, clock=FakeClock())
    assert not policy.due(pending=4999)
    assert policy.due(pending=5000)


def test_an_empty_buffer_is_never_due() -> None:
    clock = FakeClock()
    policy = FlushPolicy(interval_s=300.0, max_records=5000, clock=clock)
    clock.now_s += 3600
    assert not policy.due(pending=0)


def test_flushed_restarts_the_interval() -> None:
    clock = FakeClock()
    policy = FlushPolicy(interval_s=300.0, max_records=5000, clock=clock)
    clock.now_s += 300
    assert policy.due(pending=1)
    policy.flushed()
    assert not policy.due(pending=1)
    clock.now_s += 300
    assert policy.due(pending=1)


def test_flush_policy_rejects_nonsense_settings() -> None:
    with pytest.raises(ValueError):
        FlushPolicy(interval_s=0.0, max_records=1, clock=FakeClock())
    with pytest.raises(ValueError):
        FlushPolicy(interval_s=1.0, max_records=0, clock=FakeClock())


def test_stop_reason_end_at_wins_over_bytes() -> None:
    assert stop_reason(END_AT - timedelta(seconds=1), END_AT, 10, 100) is None
    assert stop_reason(END_AT, END_AT, 10, 100) == "end_at"
    assert stop_reason(END_AT, END_AT, 100, 100) == "end_at"
    assert stop_reason(END_AT - timedelta(hours=1), END_AT, 100, 100) == "max_raw_bytes"


def test_parse_end_at_needs_an_offset() -> None:
    assert parse_end_at("2026-01-02T00:00:00+00:00") == END_AT
    assert parse_end_at("2026-01-01T19:00:00-05:00") == END_AT
    with pytest.raises(ValueError):
        parse_end_at("2026-01-02T00:00:00")


def test_folder_bytes_counts_every_file_below_the_folder(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "one.parquet").write_bytes(b"12345")
    (tmp_path / "two.parquet").write_bytes(b"123")
    assert folder_bytes(tmp_path) == 8


def test_merged_keys_must_equal_the_flush_keys() -> None:
    check_merged_keys([["k1", "k2"], ["k3"]], ["k3", "k1", "k2"])
    with pytest.raises(MergeError):
        check_merged_keys([["k1", "k2"], ["k3"]], ["k1", "k2"])
    with pytest.raises(MergeError):
        check_merged_keys([["k1"]], ["k1", "k1"])


def test_merged_counts_must_match_the_flush_counts_per_key() -> None:
    first = {("2026-01-01T07", "json", "json"): 3}
    second = {("2026-01-01T07", "json", "json"): 1, ("2026-01-01T07", "stat", "stat"): 2}
    merged = {("2026-01-01T07", "json", "json"): 4, ("2026-01-01T07", "stat", "stat"): 2}
    check_merged_counts([first, second], merged)
    # Same grand total, different keys: a total-only check would pass this.
    with pytest.raises(MergeError):
        check_merged_counts([first, second], {("2026-01-01T07", "json", "json"): 6})
    with pytest.raises(MergeError):
        check_merged_counts([first], {("2026-01-01T07", "json", "json"): 3, ("x", "y", "z"): 0})
