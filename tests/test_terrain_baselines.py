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


# Okumura-Hata worked by hand from the published formula.
#
# Source: M. Hata, "Empirical formula for propagation loss in land mobile radio
# services", IEEE Trans. Veh. Technol., vol. VT-29, no. 3, pp. 317-325, Aug.
# 1980, doi:10.1109/T-VT.1980.23859. The paper itself was not read (the unit
# downloads nothing), so no equation number is cited. The form below is as the
# Wikipedia article "Hata model" (read 2026-11-15) gives it, citing that paper:
#
#   urban     LU  = 69.55 + 26.16 log f - 13.82 log hB - CH + (44.9 - 6.55 log hB) log d
#   small or medium city
#             CH  = 0.8 + (1.1 log f - 0.7) hM - 1.56 log f
#   suburban  LSU = LU - 2 (log(f / 28))^2 - 5.4
#
# with f in MHz, hB and hM in metres, d in km and log the base 10 logarithm.
#
# Case 1, inside every validity range: f = 900, hB = 100, hM = 2, d = 4.
#   log f = 2.95424, log hB = 2.00000, log d = 0.60206
#   26.16 log f                   = 77.283
#   13.82 log hB                  = 27.640
#   CH = 0.8 + (3.24967 - 0.7) x 2 - 4.60862 = 1.291
#   (44.9 - 13.100) log d = 31.800 x 0.60206 = 19.146
#   LU  = 69.55 + 77.283 - 27.640 - 1.291 + 19.146 = 137.048 dB
#   log(f / 28) = 1.50708; 2 x 1.50708^2 + 5.4 = 9.943
#   LSU = 137.048 - 9.943 = 127.105 dB
#
# Case 2, at our 915 MHz with a base inside the 30 m floor: f = 915, hB = 30,
# hM = 2, d = 5.
#   log f = 2.96142, log hB = 1.47712, log d = 0.69897
#   26.16 log f                   = 77.471
#   13.82 log hB                  = 20.414
#   CH = 0.8 + (3.25756 - 0.7) x 2 - 4.61982 = 1.295
#   (44.9 - 9.675) log d = 35.225 x 0.69897 = 24.621
#   LU  = 69.55 + 77.471 - 20.414 - 1.295 + 24.621 = 149.933 dB
#   log(f / 28) = 1.51426; 2 x 1.51426^2 + 5.4 = 9.986
#   LSU = 149.933 - 9.986 = 139.947 dB
HATA_CASES = [
    # distance_m, frequency_mhz, base_m, mobile_m, urban_db, suburban_db
    (4_000.0, 900.0, 100.0, 2.0, 69.55 + 77.283 - 27.640 - 1.291 + 19.146, 137.048 - 9.943),
    (5_000.0, 915.0, 30.0, 2.0, 69.55 + 77.471 - 20.414 - 1.295 + 24.621, 149.933 - 9.986),
]


@pytest.mark.parametrize(
    ("distance_m", "frequency_mhz", "base_m", "mobile_m", "urban_db", "suburban_db"),
    HATA_CASES,
)
def test_hata_matches_the_hand_worked_formula(
    distance_m: float,
    frequency_mhz: float,
    base_m: float,
    mobile_m: float,
    urban_db: float,
    suburban_db: float,
) -> None:
    urban = hata_db(distance_m, frequency_mhz, base_m, mobile_m, "urban")
    suburban = hata_db(distance_m, frequency_mhz, base_m, mobile_m, "suburban")

    assert urban == pytest.approx(urban_db, abs=0.01)
    assert suburban == pytest.approx(suburban_db, abs=0.01)


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
