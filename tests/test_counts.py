"""Counting of internet copies and dropped deliveries, and broker sightings (ing_01, ing_03)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from ingester.counts import CopyCounts, DroppedCounts, hour_of
from ingester.sightings import Broker, SightingIndex

GATEWAY = "a" * 64
TOPIC = "msh/US/Example/2/e/LongFast/" + GATEWAY
AT = datetime(2026, 10, 22, 16, 59, 59, 999999, tzinfo=UTC)


def test_hour_of_floors_to_the_utc_hour() -> None:
    assert hour_of(AT) == datetime(2026, 10, 22, 16, tzinfo=UTC)
    central = AT.astimezone(timezone(timedelta(hours=-5)))
    assert hour_of(central) == datetime(2026, 10, 22, 16, tzinfo=UTC)


def test_hour_of_rejects_a_naive_time() -> None:
    with pytest.raises(ValueError):
        hour_of(datetime(2026, 10, 22, 16))


def test_copy_counts_group_by_hour_topic_and_gateway() -> None:
    counts = CopyCounts()
    counts.add(AT, TOPIC, GATEWAY, 0xAA)
    counts.add(AT - timedelta(minutes=30), TOPIC, GATEWAY, 0xAA)
    counts.add(AT + timedelta(seconds=1), TOPIC, GATEWAY, 0xAA)
    rows = counts.rows()
    assert [(row.hour.hour, row.copy_count) for row in rows] == [(16, 2), (17, 1)]
    assert rows[0].topic == TOPIC
    assert rows[0].gateway_pseudonym == GATEWAY
    assert rows[0].gateway_low_byte == 0xAA
    assert counts.total() == 3


def test_copy_counts_merge_sums_per_key() -> None:
    first, second = CopyCounts(), CopyCounts()
    first.add(AT, TOPIC, GATEWAY, 0xAA)
    second.add(AT, TOPIC, GATEWAY, 0xAA)
    second.add(AT, TOPIC, "b" * 64, 0xBB)
    first.merge(second)
    assert sorted(row.copy_count for row in first.rows()) == [1, 2]
    assert first.total() == 3


@pytest.mark.parametrize(
    ("topic_class", "reason"),
    [
        ("json", "json"),
        ("stat", "stat"),
        ("e", "unparsed"),
        ("map", "unparsed"),
        ("other", "other"),
    ],
)
def test_dropped_counts_accept_each_reason_for_its_topic_class(
    topic_class: str, reason: str
) -> None:
    counts = DroppedCounts()
    counts.add(AT, topic_class, reason)
    counts.add(AT, topic_class, reason)
    [row] = counts.rows()
    assert (row.topic_class, row.reason, row.delivery_count) == (topic_class, reason, 2)


@pytest.mark.parametrize(
    ("topic_class", "reason"),
    [("e", "json"), ("json", "unparsed"), ("e", "malformed"), ("e", "stripped"), ("stat", "other")],
)
def test_dropped_counts_reject_other_reasons(topic_class: str, reason: str) -> None:
    # malformed and stripped are stored decode statuses, never counted reasons (ing_03).
    with pytest.raises(ValueError):
        DroppedCounts().add(AT, topic_class, reason)


def test_dropped_counts_merge_and_total() -> None:
    first, second = DroppedCounts(), DroppedCounts()
    first.add(AT, "json", "json")
    second.add(AT, "json", "json")
    second.add(AT, "stat", "stat")
    first.merge(second)
    assert first.total() == 3
    assert len(first.rows()) == 2


def test_a_delivery_seen_on_both_brokers_is_one_entry_with_both_flags() -> None:
    index = SightingIndex()
    assert index.add("k" * 64, Broker.PUBLIC, AT) is True
    assert index.add("k" * 64, Broker.MESHSTL, AT + timedelta(seconds=2)) is False
    assert len(index) == 1
    sighting = index.get("k" * 64)
    assert (sighting.seen_public, sighting.seen_meshstl) == (True, True)
    # The earliest sighting fixes received_at and so the hour the row lands in.
    assert sighting.received_at == AT


def test_the_earlier_sighting_wins_whichever_broker_is_read_first() -> None:
    index = SightingIndex()
    index.add("k" * 64, Broker.MESHSTL, AT)
    index.add("k" * 64, Broker.PUBLIC, AT - timedelta(seconds=5))
    assert index.get("k" * 64).received_at == AT - timedelta(seconds=5)


def test_two_keys_from_two_brokers_stay_two_entries() -> None:
    # A delivery the public broker stripped and MeshSTL forwarded whole has two keys.
    index = SightingIndex()
    index.add("s" * 64, Broker.PUBLIC, AT)
    index.add("w" * 64, Broker.MESHSTL, AT)
    assert len(index) == 2
    assert (index.get("s" * 64).seen_public, index.get("s" * 64).seen_meshstl) == (True, False)
    assert (index.get("w" * 64).seen_public, index.get("w" * 64).seen_meshstl) == (False, True)
