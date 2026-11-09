"""Topic classification, the landed topic and modem preset parsing (ADR 0005 items 4, 5, 8)."""

import re

import pytest

from ingester.keys import pseudonym
from ingester.topics import landed_topic, modem_preset, topic_class, topic_root

KEY = bytes(range(32))
NODE_PATTERN = re.compile(r"![0-9a-fA-F]{8}")


@pytest.mark.parametrize(
    ("topic", "expected"),
    [
        ("msh/US/2/e/LongFast/!0a0b0c0d", "e"),
        ("msh/US/Example/2/e/MediumFast/!0a0b0c0d", "e"),
        ("msh/US/Example/2/c/LongFast/!0a0b0c0d", "e"),
        ("msh/US/2/e/LongFast", "e"),
        ("msh/US/Example/Town/2/map/", "map"),
        ("msh/US/2/json/LongFast/!0a0b0c0d", "json"),
        ("msh/US/2/stat/!0a0b0c0d", "stat"),
        ("msh/US/Example/Example", "other"),
        ("msh/US", "other"),
        ("", "other"),
    ],
)
def test_topic_class(topic: str, expected: str) -> None:
    assert topic_class(topic) == expected


def test_topic_root_is_the_segment_after_msh_us() -> None:
    assert topic_root("msh/US/MeshSTL/2/e/LongFast/!0a0b0c0d") == "MeshSTL"
    assert topic_root("msh/US/2/e/LongFast/!0a0b0c0d") == "2"
    assert topic_root("msh/US") == ""


def test_landed_topic_replaces_the_gateway_segment_with_its_pseudonym() -> None:
    landed = landed_topic("msh/US/MeshSTL/2/e/LongFast/!0a0b0c0d", KEY)
    assert landed == "msh/US/MeshSTL/2/e/LongFast/" + pseudonym(KEY, 0x0A0B0C0D)


def test_landed_topic_leaves_no_node_id_anywhere() -> None:
    topic = "msh/US/!0A0B0C0E/2/e/LongFast/!0a0b0c0d"
    landed = landed_topic(topic, KEY)
    assert NODE_PATTERN.search(landed) is None
    assert pseudonym(KEY, 0x0A0B0C0E) in landed


def test_landed_topic_without_a_gateway_segment_is_unchanged() -> None:
    assert landed_topic("msh/US/2/e/LongFast", KEY) == "msh/US/2/e/LongFast"
    assert landed_topic("msh/US/Example/2/map/", KEY) == "msh/US/Example/2/map/"


@pytest.mark.parametrize(
    ("topic", "expected"),
    [
        ("msh/US/2/e/LongFast/!0a0b0c0d", "LongFast"),
        ("msh/US/Example/2/e/MediumSlow/!0a0b0c0d", "MediumSlow"),
        ("msh/US/Example/2/c/ShortTurbo/!0a0b0c0d", "ShortTurbo"),
        ("msh/US/2/e/LongFast", "LongFast"),
        ("msh/US/2/e/PKI/!0a0b0c0d", None),
        ("msh/US/2/e/InventedChannel/!0a0b0c0d", None),
        ("msh/US/2/e/longfast/!0a0b0c0d", None),
        ("msh/US/Example/2/map/", None),
        ("msh/US/2/e", None),
    ],
)
def test_modem_preset_from_the_channel_segment(topic: str, expected: str | None) -> None:
    assert modem_preset(topic) == expected
