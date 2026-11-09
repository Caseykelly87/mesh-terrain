"""Broker flags and merged deliveries (ing_01, ing_03)."""

import pytest

from ingester.keys import delivery_key
from ingester.sightings import Broker, SightingIndex

KEY = bytes(range(32))
TOPIC = "msh/US/Example/2/e/LongFast/!0a0b0c0d"
PAYLOAD = bytes.fromhex("0a0b0c0d0e0f")


def test_identical_bytes_from_both_brokers_land_as_one_row_with_both_flags() -> None:
    # ing_01: the broker is not in the key, so both copies reach one row.
    index = SightingIndex()
    assert index.add(delivery_key(KEY, TOPIC, PAYLOAD), Broker.PUBLIC) == "stored"
    outcome = index.add(delivery_key(KEY, TOPIC, PAYLOAD), Broker.MESHSTL)
    assert outcome == "merged_cross_broker"
    assert len(index) == 1
    assert index.flags(delivery_key(KEY, TOPIC, PAYLOAD)) == (True, True)


def test_the_same_broker_sending_the_same_bytes_again_is_a_repeat() -> None:
    index = SightingIndex()
    index.add(delivery_key(KEY, TOPIC, PAYLOAD), Broker.MESHSTL)
    assert index.add(delivery_key(KEY, TOPIC, PAYLOAD), Broker.MESHSTL) == "merged_repeat"
    assert index.flags(delivery_key(KEY, TOPIC, PAYLOAD)) == (False, True)


def test_a_repeat_after_a_cross_broker_merge_is_still_a_repeat() -> None:
    index = SightingIndex()
    key = delivery_key(KEY, TOPIC, PAYLOAD)
    outcomes = [index.add(key, broker) for broker in (Broker.PUBLIC, Broker.MESHSTL, Broker.PUBLIC)]
    assert outcomes == ["stored", "merged_cross_broker", "merged_repeat"]


def test_stripped_on_public_and_whole_on_meshstl_are_two_rows() -> None:
    index = SightingIndex()
    stripped = delivery_key(KEY, TOPIC, PAYLOAD[:2])
    whole = delivery_key(KEY, TOPIC, PAYLOAD)
    assert index.add(stripped, Broker.PUBLIC) == "stored"
    assert index.add(whole, Broker.MESHSTL) == "stored"
    assert index.flags(stripped) == (True, False)
    assert index.flags(whole) == (False, True)


def test_flags_of_an_unknown_key_raise() -> None:
    with pytest.raises(KeyError):
        SightingIndex().flags("0" * 64)


def test_keys_must_be_delivery_keys() -> None:
    with pytest.raises(ValueError):
        SightingIndex().add("not hex", Broker.PUBLIC)
