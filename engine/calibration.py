"""The calibration artifact: predictions against an observations layer, never changing them.

When the config names `calibration`, a layer with `site_id`, `rx_id` and
`observed_loss_db` (basic transmission loss in dB, the same quantity the
models predict), the engine writes:

- `calibration.parquet`: one row per observation, in the layer's order, with
  the run id, the observed loss and, per model, the predicted median
  (`<model>_db_predicted`) and the residual (`<model>_residual_db`, observed
  minus predicted, so a positive residual means the model predicted less loss
  than was seen);
- `calibration.md`: per model, n, RMSE, MAE and bias (the mean residual)
  over the observations that have a prediction.

An observation with no matching pair, or whose pair has no value for a model,
has null there and is left out of that model's n. `predictions.parquet` is
read, never written.
"""

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from engine.errors import ConfigError
from terrain.path_loss import MODELS, Model

OBSERVATION_COLUMNS = ("site_id", "rx_id", "observed_loss_db")


@dataclass(frozen=True, kw_only=True)
class ModelFit:
    """How one model's predictions meet the observations.

    Attributes:
        model: The model.
        n: Observations with a prediction.
        rmse_db: Root mean square residual, dB; None when n is 0.
        mae_db: Mean absolute residual, dB; None when n is 0.
        bias_db: Mean residual (observed minus predicted), dB; None when n is 0.
    """

    model: Model
    n: int
    rmse_db: float | None
    mae_db: float | None
    bias_db: float | None


def read_observations(path: Path) -> pd.DataFrame:
    """Read and check the observations layer.

    Raises:
        ConfigError: if a column is missing, an id is not text, or a loss is
            not a finite number.
    """
    observations = pd.read_parquet(path)
    missing = [c for c in OBSERVATION_COLUMNS if c not in observations.columns]
    if missing:
        raise ConfigError(f"{path.name} misses {', '.join(missing)}")
    for column in ("site_id", "rx_id"):
        if not all(isinstance(value, str) for value in observations[column]):
            raise ConfigError(f"{path.name}: every {column} must be text")
    losses = pd.to_numeric(observations["observed_loss_db"], errors="coerce")
    if not np.isfinite(losses.to_numpy(dtype=np.float64, na_value=np.nan)).all():
        raise ConfigError(f"{path.name}: observed_loss_db must be finite numbers")
    return observations[list(OBSERVATION_COLUMNS)].reset_index(drop=True)


def calibration_table(
    observations: pd.DataFrame, predictions: pd.DataFrame, models: tuple[Model, ...], run_id: str
) -> pd.DataFrame:
    """Observed against predicted, per observation and model.

    Args:
        observations: `site_id`, `rx_id`, `observed_loss_db`.
        predictions: The run's predictions, with `<model>_db_median` columns.
        models: The run's models.
        run_id: The run's id, written on every row.

    Returns:
        One row per observation, in the observations' order.
    """
    listed = [m for m in MODELS if m in models]
    medians = predictions[["site_id", "rx_id", *(f"{m}_db_median" for m in listed)]]
    keys = observations[["site_id", "rx_id"]].astype(str)
    joined = keys.join(observations["observed_loss_db"]).merge(
        medians.astype({"site_id": str, "rx_id": str}), on=["site_id", "rx_id"], how="left"
    )
    table = pd.DataFrame({"run_id": pd.Series([run_id] * len(joined), dtype="string")})
    table = table.join(joined[["site_id", "rx_id", "observed_loss_db"]])
    for model in listed:
        predicted = joined[f"{model}_db_median"].astype("Float64")
        table[f"{model}_db_predicted"] = predicted
        table[f"{model}_residual_db"] = joined["observed_loss_db"].astype("Float64") - predicted
    return table


def model_fits(table: pd.DataFrame, models: tuple[Model, ...]) -> list[ModelFit]:
    """RMSE, MAE, bias and n per model over the observations with a prediction."""
    fits = []
    for model in (m for m in MODELS if m in models):
        residuals = table[f"{model}_residual_db"].dropna().to_numpy(dtype=np.float64)
        n = int(residuals.size)
        fits.append(
            ModelFit(
                model=model,
                n=n,
                rmse_db=math.sqrt(float(np.mean(residuals**2))) if n else None,
                mae_db=float(np.mean(np.abs(residuals))) if n else None,
                bias_db=float(np.mean(residuals)) if n else None,
            )
        )
    return fits


def calibration_markdown(run_id: str, table: pd.DataFrame, fits: list[ModelFit]) -> str:
    """The Markdown summary, labelled with the run id."""
    unmatched = int(
        table[[c for c in table.columns if c.endswith("_predicted")]].isna().all(axis=1).sum()
    )
    lines = [
        f"# Calibration: run {run_id}",
        "",
        f"Observations: {len(table)}; with no prediction from any model: {unmatched}. "
        "Residual = observed minus predicted basic transmission loss, dB. "
        "This report never changes `predictions.parquet`.",
        "",
        "| Model | n | RMSE (dB) | MAE (dB) | Bias (dB) |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines += [
        f"| {f.model} | {f.n} | {_number(f.rmse_db)} | {_number(f.mae_db)} | {_number(f.bias_db)} |"
        for f in fits
    ]
    return "\n".join(lines) + "\n"


def write_calibration(
    folder: Path, observations: pd.DataFrame, models: tuple[Model, ...], run_id: str
) -> list[Path]:
    """Write `calibration.parquet` and `calibration.md` from the run's predictions."""
    predictions = pd.read_parquet(folder / "predictions.parquet", columns=None)
    table = calibration_table(observations, predictions, models, run_id)
    parquet, markdown = folder / "calibration.parquet", folder / "calibration.md"
    table.to_parquet(parquet, index=False, compression="zstd")
    text = calibration_markdown(run_id, table, model_fits(table, models))
    markdown.write_text(text, encoding="utf-8", newline="\n")
    return [parquet, markdown]


def _number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"
