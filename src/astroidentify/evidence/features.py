"""Loading the saved Milestone 2-5 products that evidence is extracted from.

Nothing is recomputed from the network. The inputs are the Milestone 5 object tables and
summary, the Milestone 4 Gaia match table and summary, the Milestone 3 plate solution, the
Milestone 2 detections and the original image (for extended-object measurements).

Which Gaia residuals describe the WCS that placed the objects:

* the objects used Milestone 4's ``refined_solution.wcs`` (or Milestone 4 did not refine,
  and the objects used the same ``solution.wcs``): the per-star residuals in
  ``catalog_matches.csv`` apply directly, and local residuals can be used;
* the objects used ``solution.wcs`` but Milestone 4 refined it: only the field-level
  offset of that input WCS (``input_wcs_median_offset_arcsec``) applies;
* no catalogue run: the plate solver's own match residual is the only (field-level) figure;
* an explicit WCS of unknown origin: no residual applies (astrometry "unavailable").
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from astroidentify.detection.outputs import load_sources
from astroidentify.detection.types import Source
from astroidentify.exceptions import InputMismatchError, OutputError
from astroidentify.preprocessing.loader import load_image
from astroidentify.serialization import sha256_file
from astroidentify.types import AstronomyImage

logger = logging.getLogger(__name__)

RESIDUALS_FINAL_WCS = "final_wcs"  # per-star residuals describe the WCS used
RESIDUALS_INPUT_OFFSET = "input_wcs_offset"  # only the field offset of the input WCS
RESIDUALS_PLATE_SOLVER = "plate_solver"
RESIDUALS_NONE = "none"


@dataclass(frozen=True, eq=False)
class GaiaMatches:
    """Per-star Gaia residuals (canonical pixels) of the final Milestone 4 match."""

    detection_ids: np.ndarray
    gaia_ids: np.ndarray
    x: np.ndarray
    y: np.ndarray
    residual_px: np.ndarray

    def gaia_for_detection(self) -> dict[int, int]:
        return {int(d): int(g) for d, g in zip(self.detection_ids, self.gaia_ids, strict=True)}


@dataclass(frozen=True, eq=False)
class EvidenceInputs:
    image: AstronomyImage
    plane: np.ndarray
    objects: list[dict[str, Any]]  # retained Milestone 5 objects (listing order)
    objects_summary: dict[str, Any]
    catalog_summary: dict[str, Any] | None
    gaia: GaiaMatches | None
    residual_relation: str
    plate_solution: dict[str, Any] | None
    detections: list[Source] | None
    fwhm_px: float | None
    compact_max_diameter_arcsec: float
    provenance: dict[str, Any]

    @property
    def width(self) -> int:
        return self.image.width

    @property
    def height(self) -> int:
        return self.image.height

    @property
    def pixel_scale_arcsec(self) -> float:
        return float(self.objects_summary["image"]["pixel_scale_arcsec"])

    def footprints(self) -> dict[int, list[tuple[float, float]]]:
        """Projected outlines from Milestone 5 (for drawing), keyed by ``catalogue_id``."""
        return {
            int(o["catalogue_id"]): [tuple(p) for p in o["footprint_px"]]
            for o in self.objects
            if o.get("footprint_px")
        }


def load_evidence_inputs(
    image_path: str | Path,
    objects_dir: str | Path,
    *,
    catalog_dir: str | Path | None = None,
    astrometry_dir: str | Path | None = None,
    detections: str | Path | None = None,
) -> EvidenceInputs:
    """Load and cross-check saved products (defaults come from the objects summary).

    Raises:
        OutputError: A required artifact is missing or unreadable.
        InputMismatchError: An artifact was made from a different image.
    """
    image = load_image(image_path)
    image_sha = sha256_file(image.source_path)
    objects_dir = Path(objects_dir)
    summary = _read_json(objects_dir / "identification_summary.json")
    inputs = summary.get("inputs") or {}
    _check_sha(inputs.get("image_sha256"), image_sha, "the object identification")
    if (summary["image"]["width"], summary["image"]["height"]) != (image.width, image.height):
        raise InputMismatchError("the object identification was made for another image size")
    table = _read_json(objects_dir / "catalog_objects.json")
    retained = [o for o in table["objects"] if o.get("retained")]

    catalog_summary, gaia = None, None
    catalog_dir = Path(catalog_dir) if catalog_dir else _parent(inputs.get("catalog_summary"))
    if catalog_dir is not None and (catalog_dir / "catalog_match_summary.json").is_file():
        catalog_summary = _read_json(catalog_dir / "catalog_match_summary.json")
        _check_sha(
            (catalog_summary.get("inputs") or {}).get("image_sha256"),
            image_sha,
            "the Gaia catalogue match",
        )
        gaia = _read_gaia_matches(catalog_dir / "catalog_matches.csv")

    plate = None
    astrometry_dir = (
        Path(astrometry_dir) if astrometry_dir else _parent(inputs.get("plate_solution"))
    )
    if astrometry_dir is not None and (astrometry_dir / "plate_solution.json").is_file():
        plate = _read_json(astrometry_dir / "plate_solution.json")
        _check_sha((plate.get("input") or {}).get("source_sha256"), image_sha, "the plate solution")

    relation = residual_relation(summary.get("wcs") or {}, catalog_dir, catalog_summary, plate)
    source_list, fwhm = _load_detections(detections or inputs.get("detections"), image_sha)
    plane = np.asarray(image.data, np.float32)
    if plane.ndim == 3:
        plane = plane.mean(axis=2)  # Milestone 2 detection-plane definition
    provenance = {
        "image": str(image.source_path),
        "image_sha256": image_sha,
        "objects": str(objects_dir),
        "objects_wcs": summary.get("wcs"),
        "catalog": str(catalog_dir) if catalog_summary is not None else None,
        "astrometry": str(astrometry_dir) if plate is not None else None,
        "detections": str(detections or inputs.get("detections"))
        if source_list is not None
        else None,
        "gaia_residuals_apply": relation,
    }
    compact_max = (summary.get("config") or {}).get("compact_max_diameter_arcsec", 10.0)
    return EvidenceInputs(
        image=image,
        plane=plane,
        objects=retained,
        objects_summary=summary,
        catalog_summary=catalog_summary,
        gaia=gaia,
        residual_relation=relation,
        plate_solution=plate,
        detections=source_list,
        fwhm_px=fwhm,
        compact_max_diameter_arcsec=float(compact_max),
        provenance=provenance,
    )


def residual_relation(
    wcs_info: dict[str, Any],
    catalog_dir: Path | None,
    catalog_summary: dict[str, Any] | None,
    plate: dict[str, Any] | None,
) -> str:
    """Which residual statistics describe the WCS the objects were placed with."""
    if catalog_summary is not None:
        refined = bool((catalog_summary.get("wcs_refinement") or {}).get("applied"))
        refined_sha = sha256_file(catalog_dir / "refined_solution.wcs") if refined else None
        input_wcs = (catalog_summary.get("inputs") or {}).get("wcs")
        input_sha = sha256_file(Path(input_wcs)) if input_wcs else None
        used = wcs_info.get("sha256")
        if refined and used == refined_sha:
            return RESIDUALS_FINAL_WCS
        if not refined and used is not None and used == input_sha:
            return RESIDUALS_FINAL_WCS
        if refined and used is not None and used == input_sha:
            return RESIDUALS_INPUT_OFFSET
    if plate is not None and plate.get("solved") and wcs_info.get("source") == "plate_solution":
        return RESIDUALS_PLATE_SOLVER
    return RESIDUALS_NONE


def _load_detections(path, image_sha) -> tuple[list[Source] | None, float | None]:
    if path is None:
        return None, None
    path = Path(path)
    if path.is_dir():
        path = path / "sources.json"
    if not path.is_file():
        raise OutputError(f"detection catalogue not found: {path}")
    meta_path = path.with_name("detection_metadata.json")
    fwhm = None
    if meta_path.is_file():
        meta = _read_json(meta_path)
        _check_sha(meta.get("source_sha256"), image_sha, "the detection catalogue")
        fwhm = (meta.get("fwhm") or {}).get("value")
    return [s for s in load_sources(path) if s.accepted], fwhm


def _read_gaia_matches(path: Path) -> GaiaMatches | None:
    if not path.is_file():
        return None
    try:
        rows = list(csv.DictReader(path.open(encoding="utf-8")))
    except OSError as exc:
        raise OutputError(f"could not read {path}: {exc}") from exc
    if not rows:
        return None

    def column(name, kind=float):
        return np.array([kind(r[name]) for r in rows])

    return GaiaMatches(
        detection_ids=column("detection_source_id", int),
        gaia_ids=column("gaia_source_id", int),
        x=column("observed_x_px"),
        y=column("observed_y_px"),
        residual_px=column("residual_px"),
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OutputError(f"could not read {path}: {exc}") from exc


def _check_sha(recorded: str | None, actual: str | None, what: str) -> None:
    if recorded not in (None, actual):
        raise InputMismatchError(f"{what} was made from a different image")


def _parent(path: str | None) -> Path | None:
    return Path(path).parent if path else None
