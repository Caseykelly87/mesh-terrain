"""Counting of internet copies, dropped deliveries and deliveries per hour and broker (ing_03)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from ingester.counts import BrokerHourCounts, CopyCounts, DroppedCounts, hour_of
from ingester.sightings import Broker

GATEWAY = "a" * 64
TOPIC = "msh/US/Example/2/e/LongFast/" + GATEWAY
AT = datetime(2026, 10, 22, 16, 59, 59, 999999, tzinfo=UTC)
HOUR = datetime(2026, 10, 22, 16, tzinfo=UTC)


def test_hour_of_floors_to_the_utc_hour() -> None:
    assert hour_of(AT) == HOUR
    central = AT.astimezone(timezone(timedelta(hours=-5)))
    assert hour_of(central) == HOUR


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


def test_copy_counts_key_on_the_contract_tuple_and_carry_the_low_byte() -> None:
    # The key is (hour, topic, gateway_pseudonym); a pseudonym has one low byte.
    counts = CopyCounts()
    counts.add(AT, TOPIC, GATEWAY, 0xAA)
    with pytest.raises(ValueError):
        counts.add(AT, TOPIC, GATEWAY, 0xAB)
    assert counts.by_key() == {(HOUR, TOPIC, GATEWAY): 1}


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
    assert first.by_key() == {(HOUR, "json", "json"): 2, (HOUR, "stat", "stat"): 1}


def test_broker_hour_counts_balance_received_against_its_parts() -> None:
    # ing_03: received = stored + counted + both merged counts, per hour and broker.
    counts = BrokerHourCounts()
    for outcome in ("stored", "stored", "counted", "merged_repeat"):
        counts.add(AT, Broker.PUBLIC, outcome)
    counts.add(AT, Broker.MESHSTL, "merged_cross_broker")
    assert counts.rows() == [
        {
            "hour": "2026-10-22T16",
            "broker": "meshstl",
            "received": 1,
            "stored": 0,
            "counted": 0,
            "merged_cross_broker": 1,
            "merged_repeat": 0,
        },
        {
            "hour": "2026-10-22T16",
            "broker": "public",
            "received": 4,
            "stored": 2,
            "counted": 1,
            "merged_cross_broker": 0,
            "merged_repeat": 1,
        },
    ]


def test_broker_hour_counts_reject_an_unknown_outcome() -> None:
    with pytest.raises(ValueError):
        BrokerHourCounts().add(AT, Broker.PUBLIC, "dropped")


def test_broker_hour_counts_merge() -> None:
    first, second = BrokerHourCounts(), BrokerHourCounts()
    first.add(AT, Broker.PUBLIC, "stored")
    second.add(AT, Broker.PUBLIC, "stored")
    first.merge(second)
    assert first.rows()[0]["stored"] == 2
