"""Keyed hashes that replace node ids and identify deliveries (ADR 0005 item 3, ing_01).

Every node id, whether gateway, sender or neighbour, passes through one HMAC
under NODE_ID_HMAC_KEY, so a node has the same pseudonym in every role. The
delivery key uses the same secret with a "delivery" prefix and a length-prefixed
topic. The encodings here are fixed by known-answer tests: changing either one
after a snapshot exists is a key rotation. This module never logs and never
puts the key or an input in an exception message.
"""

import hashlib
import hmac

KEY_BYTES = 32
DELIVERY_PREFIX = b"delivery"
_NODE_ID_MAX = 2**32 - 1


def parse_hmac_key(value: str) -> bytes:
    """Decode NODE_ID_HMAC_KEY from hex.

    Args:
        value: The key as hex, at least 64 characters (32 bytes), any case.

    Returns:
        The key bytes.

    Raises:
        ValueError: if the value is not hex or decodes to fewer than 32 bytes.
            The message never contains the value.
    """
    try:
        key = bytes.fromhex(value)
    except ValueError:
        raise ValueError("NODE_ID_HMAC_KEY is not hex") from None
    if len(key) < KEY_BYTES:
        raise ValueError(f"NODE_ID_HMAC_KEY must be at least {KEY_BYTES} bytes as hex")
    return key


def pseudonym(key: bytes, node_id: int) -> str:
    """Pseudonym of a node: HMAC-SHA-256 of the node number as 4 bytes big-endian.

    Args:
        key: The HMAC key from parse_hmac_key.
        node_id: The node number, 0 to 2**32 - 1.

    Returns:
        64 lowercase hex characters.

    Raises:
        ValueError: if node_id is outside 32 bits.
    """
    if not 0 <= node_id <= _NODE_ID_MAX:
        raise ValueError("node id outside 32 bits")
    return hmac.new(key, node_id.to_bytes(4, "big"), hashlib.sha256).hexdigest()


def low_byte(node_id: int) -> int:
    """The node's low id byte, the value firmware puts in relay_node (0 to 255)."""
    return node_id & 0xFF


def delivery_key(key: bytes, topic: str, payload: bytes) -> str:
    """Key of one delivery: HMAC of "delivery", the topic length, the topic and the payload.

    Args:
        key: The HMAC key from parse_hmac_key.
        topic: The topic as received, with the real gateway id; encoded as UTF-8.
        payload: The MQTT payload bytes as received.

    Returns:
        64 lowercase hex characters. The length is 4 bytes big-endian, so two
        different (topic, payload) pairs never hash the same byte string.
    """
    topic_bytes = topic.encode("utf-8")
    message = DELIVERY_PREFIX + len(topic_bytes).to_bytes(4, "big") + topic_bytes + payload
    return hmac.new(key, message, hashlib.sha256).hexdigest()
