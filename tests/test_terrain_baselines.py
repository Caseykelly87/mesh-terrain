"""Free-space loss and Okumura-Hata against closed forms and a published value."""

import math

import pytest

from terrain.baselines import fspl_db, hata_db, hata_variant


def test_fspl_matches_the_closed_form_at_10_km_and_915_mhz() -> None:
    # 32.4478 dB is 20 log10(4 pi 1e9 / c) for distance in km and frequency in MHz.
    closed_form = 32.4478 + 20.0 * math.log10(915.0) + 20.0 * math.log10(10.0)

    loss = fspl_db(10_000.0, 915.0)

    assert loss == pytest.approx(closed_form, abs=0.01)
    assert loss == pytest.approx(111.7, abs=0.05)


def test_fspl_grows_6_db_per_doubling_of_distance() -> None:
    assert fspl_db(2_000.0, 906.875) - fspl_db(1_000.0, 906.875) == pytest.approx(
        20.0 * math.log10(2.0)
    )


@pytest.mark.parametrize(
    ("distance_m", "frequency_mhz"), [(0.0, 915.0), (-1.0, 915.0), (10.0, 0.0)]
)
def test_fspl_rejects_non_positive_inputs(distance_m: float, frequency_mhz: float) -> None:
    with pytest.raises(ValueError):
        fspl_db(distance_m, frequency_mhz)


def test_hata_urban_matches_the_published_example() -> None:
    # RF Wireless World, Hata model path loss calculator: 900 MHz, base station
    # 100 m, mobile 2 m, 4 km gives a(hm) = 1.29 dB and 137.05 dB.
    assert hata_db(4_000.0, 900.0, 100.0, 2.0, "urban") == pytest.approx(137.05, abs=0.1)


def test_hata_suburban_applies_hatas_correction() -> None:
    urban = hata_db(4_000.0, 900.0, 100.0, 2.0, "urban")
    correction = 2.0 * math.log10(900.0 / 28.0) ** 2 + 5.4

    suburban = hata_db(4_000.0, 900.0, 100.0, 2.0, "suburban")

    assert suburban == pytest.approx(urban - correction)
    assert correction == pytest.approx(9.94, abs=0.01)


def test_hata_takes_the_higher_antenna_as_the_base_station() -> None:
    assert hata_db(4_000.0, 900.0, 2.0, 100.0, "urban") == hata_db(
        4_000.0, 900.0, 100.0, 2.0, "urban"
    )


@pytest.mark.parametrize("frequency_mhz", [149.0, 1501.0])
def test_hata_refuses_frequencies_outside_150_to_1500_mhz(frequency_mhz: float) -> None:
    with pytest.raises(ValueError, match="150 to 1500"):
        hata_db(4_000.0, frequency_mhz, 30.0, 2.0, "urban")


def test_hata_refuses_zero_distance() -> None:
    with pytest.raises(ValueError):
        hata_db(0.0, 915.0, 30.0, 2.0, "urban")


def test_hata_refuses_an_unknown_variant() -> None:
    with pytest.raises(ValueError, match="variant"):
        hata_db(4_000.0, 915.0, 30.0, 2.0, "rural")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("tx_share", "rx_share", "expected"),
    [(0.30, 0.10, "urban"), (0.25, 0.10, "suburban"), (0.0, 0.0, "suburban"), (1.0, 0.0, "urban")],
)
def test_variant_is_urban_when_the_mean_share_reaches_0_20(
    tx_share: float, rx_share: float, expected: str
) -> None:
    assert hata_variant(tx_share, rx_share) == expected


@pytest.mark.parametrize(("tx_share", "rx_share"), [(-0.1, 0.2), (0.2, 1.1), (math.nan, 0.2)])
def test_variant_refuses_shares_outside_0_to_1(tx_share: float, rx_share: float) -> None:
    with pytest.raises(ValueError):
        hata_variant(tx_share, rx_share)
