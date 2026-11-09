"""Topic classification and the topic as it lands (ADR 0005 items 4, 5, 7 and 8).

A Meshtastic topic is `msh/US/<root segments>/2/<class>/<channel>/!<gateway id>`.
The class segment is `e` (or the older `c`) for a ServiceEnvelope, `json`,
`map` or `stat`; anything else is `other`. The landed topic has every node id
replaced by its pseudonym. The modem preset is the channel segment when it is a
known preset name, since only default channels are named after their preset.
"""

import re

from ingester.keys import pseudonym

CLASS_SEGMENTS = {"e": "e", "c": "e", "json": "json", "map": "map", "stat": "stat"}
MODEM_PRESETS = frozenset(
    {
        "LongFast",
        "LongModerate",
        "LongSlow",
        "LongTurbo",
        "MediumFast",
        "MediumSlow",
        "ShortFast",
        "ShortSlow",
        "ShortTurbo",
        "VeryLongSlow",
    }
)
NODE_ID_PATTERN = re.compile(r"!([0-9a-fA-F]{8})")


def _class_index(parts: list[str]) -> int | None:
    for index in range(2, len(parts)):
        if parts[index] in CLASS_SEGMENTS:
            return index
    return None


def topic_class(topic: str) -> str:
    """One of e, json, map, stat or other, from the first class segment after msh/US."""
    parts = topic.split("/")
    index = _class_index(parts)
    return "other" if index is None else CLASS_SEGMENTS[parts[index]]


def topic_root(topic: str) -> str:
    """The segment after msh/US: a community or state name, or 2 when none is set."""
    parts = topic.split("/")
    return parts[2] if len(parts) > 2 else ""


def landed_topic(topic: str, key: bytes) -> str:
    """The topic with every `!xxxxxxxx` node id replaced by its pseudonym.

    Args:
        topic: The topic as received.
        key: The HMAC key from keys.parse_hmac_key.

    Returns:
        The topic to store; it contains no node id in any segment.
    """
    return NODE_ID_PATTERN.sub(lambda match: pseudonym(key, int(match.group(1), 16)), topic)


def modem_preset(topic: str) -> str | None:
    """The preset named by the channel segment of an e or json topic, or None.

    Returns None for custom channel names, the PKI channel and topics with no
    channel segment; analytics-eng decides how a null preset is treated (ADR 0005 item 8).
    """
    parts = topic.split("/")
    index = _class_index(parts)
    if index is None or CLASS_SEGMENTS[parts[index]] not in {"e", "json"}:
        return None
    if index + 1 >= len(parts):
        return None
    channel = parts[index + 1]
    return channel if channel in MODEM_PRESETS else None
