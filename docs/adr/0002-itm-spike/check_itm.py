"""Call the NTIA ITM shared library on one flat path and compare it to free-space loss.

This script exists only for the ADR 0002 spike. It is not part of the terrain
package and it does not read a DEM: the profile is a synthetic flat line at sea
level. Argument names follow include/itm.h in the NTIA repository.
"""

import ctypes
import math
import sys

LIBRARY_PATH = "/usr/local/lib/libitm.so"

FREQUENCY_MHZ = 915.0
DISTANCE_M = 10_000.0
STEP_M = 10.0
ANTENNA_HEIGHT_M = 2.0

# Climate 5 is continental temperate in the ITM climate enum.
CLIMATE_CONTINENTAL_TEMPERATE = 5
# Surface refractivity at sea level, N-units, the NTIA default.
SURFACE_REFRACTIVITY_N0 = 301.0
POLARISATION_VERTICAL = 1
# Average ground: relative permittivity 15, conductivity 0.005 S/m.
GROUND_EPSILON = 15.0
GROUND_SIGMA_S_PER_M = 0.005
# Mode of variability 12: mobile mode with location variability removed,
# the usual choice for a point-to-point link between two fixed sites.
MDVAR_POINT_TO_POINT = 12
CONFIDENCE_PCT = 50.0
RELIABILITY_PCT = 50.0


class IntermediateValues(ctypes.Structure):
    _fields_ = [
        ("theta_hzn", ctypes.c_double * 2),
        ("d_hzn__meter", ctypes.c_double * 2),
        ("h_e__meter", ctypes.c_double * 2),
        ("N_s", ctypes.c_double),
        ("delta_h__meter", ctypes.c_double),
        ("A_ref__db", ctypes.c_double),
        ("A_fs__db", ctypes.c_double),
        ("d__km", ctypes.c_double),
        ("mode", ctypes.c_int),
    ]


def free_space_loss_db(distance_m: float, frequency_mhz: float) -> float:
    """Free-space path loss, 20 log10(d_km) + 20 log10(f_MHz) + 32.45."""
    return 20 * math.log10(distance_m / 1000.0) + 20 * math.log10(frequency_mhz) + 32.45


def flat_profile(distance_m: float, step_m: float) -> ctypes.Array:
    """PFL layout from itm.h: [point count minus one, step in metres, elevations...]."""
    point_count = int(round(distance_m / step_m)) + 1
    values = [float(point_count - 1), step_m] + [0.0] * point_count
    return (ctypes.c_double * len(values))(*values)


def main() -> int:
    library = ctypes.CDLL(LIBRARY_PATH)
    library.ITM_P2P_CR_Ex.restype = ctypes.c_int
    library.ITM_P2P_CR_Ex.argtypes = [
        ctypes.c_double, ctypes.c_double, ctypes.POINTER(ctypes.c_double), ctypes.c_int,
        ctypes.c_double, ctypes.c_double, ctypes.c_int, ctypes.c_double, ctypes.c_double,
        ctypes.c_int, ctypes.c_double, ctypes.c_double,
        ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_long),
        ctypes.POINTER(IntermediateValues),
    ]

    profile = flat_profile(DISTANCE_M, STEP_M)
    loss_db = ctypes.c_double()
    warnings = ctypes.c_long()
    intermediate = IntermediateValues()
    status = library.ITM_P2P_CR_Ex(
        ANTENNA_HEIGHT_M, ANTENNA_HEIGHT_M, profile, CLIMATE_CONTINENTAL_TEMPERATE,
        SURFACE_REFRACTIVITY_N0, FREQUENCY_MHZ, POLARISATION_VERTICAL,
        GROUND_EPSILON, GROUND_SIGMA_S_PER_M, MDVAR_POINT_TO_POINT,
        CONFIDENCE_PCT, RELIABILITY_PCT,
        ctypes.byref(loss_db), ctypes.byref(warnings), ctypes.byref(intermediate),
    )

    fspl_db = free_space_loss_db(DISTANCE_M, FREQUENCY_MHZ)
    print(f"itm return code: {status} (0 is success, see ERRORS_AND_WARNINGS.md)")
    print(f"itm warnings flags: {warnings.value:#x}")
    print(f"path: {DISTANCE_M / 1000:.1f} km flat at {STEP_M:.0f} m, {len(profile) - 2} samples")
    print(f"antennas: {ANTENNA_HEIGHT_M:.1f} m above ground at both ends (assumed)")
    print(f"itm propagation mode: {intermediate.mode} (1 line of sight, 2 diffraction, 3 troposcatter)")
    print(f"itm internal free-space loss: {intermediate.A_fs__db:.2f} dB")
    print(f"itm reference attenuation: {intermediate.A_ref__db:.2f} dB")
    print(f"free-space loss, closed form: {fspl_db:.2f} dB")
    print(f"itm basic transmission loss: {loss_db.value:.2f} dB")
    print(f"itm minus free space: {loss_db.value - fspl_db:+.2f} dB")
    return 0 if status == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
