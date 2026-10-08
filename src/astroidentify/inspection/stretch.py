"""Display-only intensity stretches for inspection images.

These functions return new ``uint8`` arrays of exactly the input's height and width. They
never resample, warp, crop or flip, and nothing in the scientific pipeline imports this
module.

* ``none``: a linear map of the data's nominal range. 8-bit rasters are shown exactly as
  stored; other data maps its min..max.
* ``percentile``: a linear map between the ``low``/``high`` percentiles of all channels
  together (preserving colour balance), clipped.
* ``asinh`` (default): colour-preserving arcsinh stretch (Lupton et al. 2004). Data are
  normalized between the ``low``/``high`` percentiles, the luminance ``L`` (channel mean) is
  mapped through ``asinh(L / softening) / asinh(1 / softening)``, and every channel is
  scaled by the same factor, so hue is kept. A smaller ``softening`` lifts faint, diffuse
  structure more strongly. Pixels whose brightest channel would exceed 1 are scaled down
  as a whole (hue kept).

All modes are deterministic functions of the data and the recorded parameters.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

STRETCHES = ("none", "percentile", "asinh")


@dataclass(frozen=True)
class StretchParameters:
    mode: str = "asinh"
    # Black point at a low sky quantile hides pixel noise below the background level; the
    # white point keeps bright star cores; softening 0.05 lifts faint diffuse signal.
    low_percentile: float = 10.0
    high_percentile: float = 99.9
    softening: float = 0.05  # asinh only

    def __post_init__(self) -> None:
        if self.mode not in STRETCHES:
            raise ValueError(f"stretch must be one of {STRETCHES}, got {self.mode!r}")
        if not 0 <= self.low_percentile < self.high_percentile <= 100:
            raise ValueError("percentiles must satisfy 0 <= low < high <= 100")
        if not self.softening > 0:
            raise ValueError("softening must be positive")


def stretch(data: np.ndarray, params: StretchParameters, nominal_max: float | None = None):
    """Return ``(uint8 array, record)``; ``record`` holds the parameters and levels used."""
    values = np.asarray(data, dtype=np.float64)
    finite = values[np.isfinite(values)]
    record = {**asdict(params)}
    if params.mode == "none":
        low, high = (0.0, float(nominal_max)) if nominal_max else _range(finite, 0, 100)
        scaled = (values - low) / (high - low)
        record.update(
            levels={
                "black": low,
                "white": high,
                "source": "nominal range" if nominal_max else "data min/max",
            }
        )
        return _to_uint8(np.clip(scaled, 0, 1)), _without_unused(record, params.mode)

    low, high = _range(finite, params.low_percentile, params.high_percentile)
    scaled = np.clip((values - low) / (high - low), 0, None)
    record.update(levels={"black": low, "white": high, "source": "percentiles of all channels"})
    if params.mode == "percentile":
        return _to_uint8(np.clip(scaled, 0, 1)), _without_unused(record, params.mode)

    luminance = scaled.mean(axis=2) if scaled.ndim == 3 else scaled
    a = params.softening
    with np.errstate(divide="ignore", invalid="ignore"):
        gain = np.where(
            luminance > 0, np.arcsinh(luminance / a) / (np.arcsinh(1 / a) * luminance), 0
        )
    out = scaled * (gain[..., None] if scaled.ndim == 3 else gain)
    if out.ndim == 3:
        peak = out.max(axis=2, keepdims=True)
        out = np.where(peak > 1, out / np.maximum(peak, 1e-12), out)  # keep hue when clipping
    return _to_uint8(np.clip(out, 0, 1)), record


def _range(finite: np.ndarray, low: float, high: float) -> tuple[float, float]:
    if finite.size == 0:
        return 0.0, 1.0
    lo, hi = (float(v) for v in np.percentile(finite, [low, high]))
    if not hi > lo:
        hi = lo + 1.0
    return lo, hi


def _to_uint8(unit: np.ndarray) -> np.ndarray:
    return np.round(np.nan_to_num(unit, nan=0.0) * 255.0).astype(np.uint8)


def _without_unused(record: dict, mode: str) -> dict:
    record = dict(record)
    record.pop("softening", None)
    if mode == "none":
        record.pop("low_percentile", None)
        record.pop("high_percentile", None)
    return record
