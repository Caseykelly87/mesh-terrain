"""Known-answer tests for the node pseudonym and the delivery key (ADR 0005, ing_01).

The expected digests are fixed here, not recomputed, so a change to the input
encoding or the output length fails the test. Changing either after the first
snapshot exists is a key rotation (ADR 0005, Consequences).
"""

import pytest

from ingester.keys import delivery_key, low_byte, parse_hmac_key, pseudonym

KEY = bytes(range(32))
TOPIC = "msh/US/Example/2/e/LongFast/!0a0b0c0d"
PAYLOAD = bytes.fromhex("0a0b0c0d0e0f")


def test_pseudonym_known_answer() -> None:
    # HMAC-SHA-256 over the node number as 4 bytes big-endian, as 64 lowercase hex.
    assert pseudonym(KEY, 0x0A0B0C0D) == (
        "dd53b9c9ef21397cee70016e23938eeb468575e30e791a9174faad6ddd11e9de"
    )
    assert pseudonym(KEY, 1) == "99411f24bfa9ee8e144e132c46b3b7d1f6d6bfbe2b82ab47b4963e43bfe8bdb6"


def test_pseudonym_rejects_values_outside_32_bits() -> None:
    with pytest.raises(ValueError):
        pseudonym(KEY, -1)
    with pytest.raises(ValueError):
        pseudonym(KEY, 2**32)


def test_low_byte_is_the_last_byte_of_the_node_number() -> None:
    assert low_byte(0x0A0B0C0D) == 0x0D
    assert low_byte(0x000000FF) == 255
    assert low_byte(0x00000100) == 0


def test_delivery_key_known_answer() -> None:
    # "delivery" || topic length as 4 bytes big-endian || UTF-8 topic || payload bytes.
    assert delivery_key(KEY, TOPIC, PAYLOAD) == (
        "32e57f12164904f7e08c6453889a92c457972d12dbfa9a26e31c577edf03659a"
    )
    assert delivery_key(KEY, TOPIC, b"") == (
        "37ab8842c8e644fdb9298c1c613d84d74d7340d57bee785323381df976ed97d4"
    )


def test_delivery_key_differs_from_a_pseudonym_of_the_same_bytes() -> None:
    assert delivery_key(KEY, "", (0x0A0B0C0D).to_bytes(4, "big")) != pseudonym(KEY, 0x0A0B0C0D)


def test_length_prefix_separates_topic_from_payload() -> None:
    # Without the length prefix these two deliveries would hash the same bytes.
    assert delivery_key(KEY, "msh/a", b"bc") != delivery_key(KEY, "msh/ab", b"c")


def test_stripped_and_whole_copies_give_two_keys() -> None:
    # The public broker removed the payload; the MeshSTL broker forwarded it whole.
    stripped_header = PAYLOAD[:2]
    assert delivery_key(KEY, TOPIC, stripped_header) != delivery_key(KEY, TOPIC, PAYLOAD)


def test_parse_hmac_key_accepts_32_random_bytes_written_as_64_hex_characters() -> None:
    assert parse_hmac_key(KEY.hex()) == KEY
    assert parse_hmac_key(KEY.hex().upper()) == KEY


@pytest.mark.parametrize("value", ["", "abc", "zz" * 32, "00" * 31, "00" * 33])
def test_parse_hmac_key_rejects_anything_but_64_hex_characters(value: str) -> None:
    with pytest.raises(ValueError) as caught:
        parse_hmac_key(value)
    # The error must never echo the secret.
    assert value == "" or value not in str(caught.value)
