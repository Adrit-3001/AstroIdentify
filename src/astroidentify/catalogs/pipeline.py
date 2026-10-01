"""Milestone 4 pipeline: WCS footprint -> Gaia DR3 query -> projection -> refine -> match.

Typical use::

    inputs = load_match_inputs(image, plate_solution_json, solution_wcs, sources_json)
    result = match_catalog(inputs.sources, inputs.wcs, inputs.width, inputs.height,
                           GaiaDR3Provider(config), config, inputs.image.metadata)

The query region always comes from the solved WCS; no position or object name is used.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.wcs import WCS

from astroidentify.astrometry.wcs import load_wcs, pixel_to_sky
from astroidentify.catalogs.epoch import propagate, resolve_observation_epoch
from astroidentify.catalogs.footprint import project_to_image, query_region, validate_wcs
from astroidentify.catalogs.matching import (
    assign_one_to_one,
    candidate_edges,
    eligible_catalog_indices,
    refine_wcs,
)
from astroidentify.catalogs.types import (
    CatalogMatch,
    CatalogMatchResult,
    CatalogQueryResult,
    MatchSummary,
    ProjectedCatalog,
    QueryRegion,
)
from astroidentify.config import CatalogConfig
from astroidentify.detection.outputs import load_sources
from astroidentify.detection.types import Source
from astroidentify.exceptions import (
    CatalogError,
    InputMismatchError,
    InvalidPlateSolutionError,
    InvalidWCSError,
)
from astroidentify.preprocessing.loader import load_image
from astroidentify.serialization import sha256_file
from astroidentify.types import AstronomyImage

logger = logging.getLogger(__name__)


class CatalogProvider(Protocol):
    """Anything that can return catalogue rows for a query region (e.g. GaiaDR3Provider)."""

    def query(self, region: QueryRegion) -> CatalogQueryResult: ...


@dataclass(frozen=True, eq=False)
class MatchInputs:
    """Saved Milestone 1-3 products for one image, checked for consistency."""

    image: AstronomyImage
    sources: list[Source]
    wcs: WCS
    plate_solution: dict[str, Any]
    provenance: dict[str, Any]

    @property
    def width(self) -> int:
        return self.image.width

    @property
    def height(self) -> int:
        return self.image.height


def load_match_inputs(
    image_path: str | Path,
    plate_solution_path: str | Path,
    wcs_path: str | Path,
    sources_path: str | Path,
) -> MatchInputs:
    """Load the image, saved plate solution/WCS and saved detections; verify they agree.

    Raises:
        InvalidPlateSolutionError: Unsolved/missing/malformed plate solution or WCS.
        InputMismatchError: The products were made from a different image or size.
    """
    image = load_image(image_path)
    image_sha = sha256_file(image.source_path)
    try:
        plate = json.loads(Path(plate_solution_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InvalidPlateSolutionError(
            f"could not read plate solution {plate_solution_path}: {exc}"
        ) from exc
    if not plate.get("solved"):
        raise InvalidPlateSolutionError(
            f"plate solution {plate_solution_path} is not solved (status {plate.get('status')!r})"
        )
    plate_input = plate.get("input") or {}
    if plate_input.get("source_sha256") not in (None, image_sha):
        raise InputMismatchError("the plate solution was made from a different image")
    if (plate_input.get("width"), plate_input.get("height")) not in (
        (None, None),
        (image.width, image.height),
    ):
        raise InputMismatchError(
            f"plate solution image size {plate_input.get('width')} x {plate_input.get('height')} "
            f"differs from the image ({image.width} x {image.height})"
        )
    try:
        wcs, header = load_wcs(Path(wcs_path))
    except InvalidWCSError as exc:
        raise InvalidPlateSolutionError(str(exc)) from exc
    if (header.get("IMAGEW"), header.get("IMAGEH")) not in (
        (None, None),
        (image.width, image.height),
    ):
        raise InputMismatchError("the WCS was made for a different image size")

    sources = load_sources(sources_path)
    detection_meta_path = Path(sources_path).with_name("detection_metadata.json")
    detection_sha = None
    if detection_meta_path.is_file():
        detection_sha = json.loads(detection_meta_path.read_text(encoding="utf-8")).get(
            "source_sha256"
        )
        if detection_sha not in (None, image_sha):
            raise InputMismatchError("the detection catalogue was made from a different image")
    else:
        logger.warning(
            "no detection_metadata.json next to %s; image provenance unchecked", sources_path
        )
    provenance = {
        "image": str(image.source_path),
        "image_sha256": image_sha,
        "plate_solution": str(Path(plate_solution_path)),
        "plate_solution_mode": plate.get("mode"),
        "plate_solution_constraints": plate.get("constraints"),
        "wcs": str(Path(wcs_path)),
        "detections": str(Path(sources_path)),
        "detections_image_sha256": detection_sha,
    }
    return MatchInputs(image, sources, wcs, plate, provenance)


def match_catalog(
    sources: list[Source],
    wcs: WCS,
    width: int,
    height: int,
    provider: CatalogProvider,
    config: CatalogConfig | None = None,
    image_metadata: dict[str, Any] | None = None,
) -> CatalogMatchResult:
    """Query the catalogue for the WCS footprint and match it to all accepted detections.

    Raises:
        InvalidPlateSolutionError: The WCS fails validation.
        CatalogError: There are no accepted detections.
        CatalogQueryError (and subclasses): Provider failures.
    """
    config = config or CatalogConfig()
    geometry = validate_wcs(wcs, width, height)
    # Canonical order (by source_id) makes every step independent of the input order.
    detections = tuple(sorted((s for s in sources if s.accepted), key=lambda s: s.source_id))
    if not detections:
        raise CatalogError("no accepted detections to match")
    warnings: list[str] = []

    query = provider.query(query_region(wcs, width, height, config.query_margin_arcsec))
    rows = query.rows
    warnings += query.warnings
    if len(rows) == 0:
        warnings.append("the catalogue returned no rows for this field")

    observation, source = resolve_observation_epoch(image_metadata or {}, config.observation_epoch)
    ra, dec, epoch = propagate(rows, observation, source)
    world = SkyCoord(ra, dec, unit="deg")
    mags = rows.get("phot_g_mean_mag")
    if mags is None:
        mags = np.full(len(rows), np.nan)

    # Astrometric centroids: corrected for the saturated-core bias (Milestone 2).
    det_xy = np.array([s.astrometric_xy for s in detections], float)
    det_ids = np.array([s.source_id for s in detections], np.int64)
    det_saturated = np.array([s.saturated for s in detections], bool)

    x, y, in_image = project_to_image(wcs, ra, dec, width, height, config.edge_margin_px)
    eligible = eligible_catalog_indices(
        mags, in_image, len(detections), config.brightness_rank_factor
    )
    wcs_used, refined, refinement = wcs, False, None
    if config.refine_wcs and len(eligible):
        refinement = refine_wcs(
            wcs, det_xy, det_saturated, np.column_stack([x[eligible], y[eligible]]),
            world[eligible], geometry.pixel_scale_arcsec,
            radius_arcsec=config.registration_radius_arcsec,
            isolation_ratio=config.registration_isolation_ratio,
            sip_degree=config.refine_sip_degree, clip_sigma=config.refine_clip_sigma,
            min_pairs=config.refine_min_pairs,
        )  # fmt: skip
        if refinement.wcs is not None:
            refinement.wcs.pixel_shape = (width, height)  # the fitter infers it from the points
            wcs_used, refined = refinement.wcs, True
            x, y, in_image = project_to_image(
                wcs_used, ra, dec, width, height, config.edge_margin_px
            )
            eligible = eligible_catalog_indices(
                mags, in_image, len(detections), config.brightness_rank_factor
            )
        else:
            warnings.append(f"WCS refinement not possible ({refinement.reason}); input WCS used")

    det_ra, det_dec = pixel_to_sky(wcs_used, det_xy[:, 0], det_xy[:, 1])
    det_sky = SkyCoord(det_ra, det_dec, unit="deg")
    cat_ids = np.asarray(rows["source_id"], np.int64)
    edges = candidate_edges(det_sky, world[eligible], config.match_radius_arcsec)
    counts = np.bincount([e.detection_index for e in edges], minlength=len(detections))
    accepted = assign_one_to_one(edges, det_ids, cat_ids[eligible])

    matches = tuple(
        sorted(
            (
                _record(
                    e, detections, det_xy, eligible, rows, ra, dec, x, y, epoch.propagated, counts
                )
                for e in accepted
            ),
            key=lambda m: m.detection_source_id,
        )
    )
    matched_det = {m.detection_source_id for m in matches}
    matched_cat = {m.gaia_source_id for m in matches}
    unmatched_det = tuple(sorted(int(i) for i in det_ids if int(i) not in matched_det))
    unmatched_cat = tuple(sorted(int(i) for i in cat_ids[eligible] if int(i) not in matched_cat))
    in_image_count = int(in_image.sum())
    if not matches:
        warnings.append("no detection matched a catalogue star within the match radius")
    for message in warnings:
        logger.warning(message)

    eligible_mask = np.zeros(len(rows), bool)
    eligible_mask[eligible] = True
    summary = _summary(query, in_image_count, len(eligible), len(detections), matches,
                       len(unmatched_det), unmatched_cat, epoch.propagated)  # fmt: skip
    return CatalogMatchResult(
        query=query,
        projected=ProjectedCatalog(rows, ra, dec, x, y, in_image, epoch),
        matches=matches,
        unmatched_detection_ids=unmatched_det,
        unmatched_catalog_ids=unmatched_cat,
        summary=summary,
        match_radius_arcsec=config.match_radius_arcsec,
        match_radius_px=config.match_radius_arcsec / geometry.pixel_scale_arcsec,
        pixel_scale_arcsec=geometry.pixel_scale_arcsec,
        eligible_catalog_mask=eligible_mask,
        wcs_used=wcs_used,
        wcs_refined=refined,
        refinement=refinement,
        detections=detections,
        warnings=tuple(warnings),
    )


def _record(
    edge, detections, det_xy, eligible, rows, ra, dec, x, y, propagated, counts
) -> CatalogMatch:
    detection = detections[edge.detection_index]
    k = int(eligible[edge.catalog_index])

    def value(name: str) -> float:
        column = rows.get(name)
        return float(column[k]) if column is not None else float("nan")

    ox, oy = det_xy[edge.detection_index]
    return CatalogMatch(
        detection_source_id=int(detection.source_id),
        gaia_source_id=int(rows["source_id"][k]),
        observed_x_px=float(ox),
        observed_y_px=float(oy),
        predicted_x_px=float(x[k]),
        predicted_y_px=float(y[k]),
        residual_px=float(np.hypot(ox - x[k], oy - y[k])),
        residual_arcsec=edge.separation_arcsec,
        catalog_ra_deg=value("ra"),
        catalog_dec_deg=value("dec"),
        projected_ra_deg=float(ra[k]),
        projected_dec_deg=float(dec[k]),
        epoch_propagated=propagated,
        phot_g_mean_mag=value("phot_g_mean_mag"),
        phot_bp_mean_mag=value("phot_bp_mean_mag"),
        phot_rp_mean_mag=value("phot_rp_mean_mag"),
        parallax=value("parallax"),
        pmra=value("pmra"),
        pmdec=value("pmdec"),
        detection_flux=float(detection.flux),
        detection_snr=float(detection.snr),
        detection_saturated=bool(detection.saturated),
        detection_edge=bool(detection.edge),
        n_candidates=int(counts[edge.detection_index]),
    )


def _summary(
    query, in_image, n_eligible, n_detections, matches, n_unmatched_det, unmatched_cat, propagated
) -> MatchSummary:
    arcsec = np.array([m.residual_arcsec for m in matches], float)
    px = np.array([m.residual_px for m in matches], float)
    dx = np.array([m.observed_x_px - m.predicted_x_px for m in matches], float)
    dy = np.array([m.observed_y_px - m.predicted_y_px for m in matches], float)

    def stat(values: np.ndarray, func) -> float | None:
        return float(func(values)) if values.size else None

    def rms(values: np.ndarray) -> float:
        return np.sqrt(np.mean(values**2))

    n = len(matches)
    return MatchSummary(
        catalog=f"{query.release} ({query.table})",
        rows_returned=len(query.rows),
        rows_in_image=in_image,
        rows_eligible=n_eligible,
        detections_considered=n_detections,
        matches=n,
        detection_match_fraction=n / n_detections if n_detections else None,
        catalog_match_fraction=n / in_image if in_image else None,
        catalog_match_fraction_eligible=n / n_eligible if n_eligible else None,
        median_residual_arcsec=stat(arcsec, np.median),
        rms_residual_arcsec=stat(arcsec, rms),
        max_residual_arcsec=stat(arcsec, np.max),
        median_residual_px=stat(px, np.median),
        rms_residual_px=stat(px, rms),
        mean_offset_x_px=stat(dx, np.mean),
        mean_offset_y_px=stat(dy, np.mean),
        unmatched_detections=n_unmatched_det,
        unmatched_catalog_in_image=in_image - n,
        unmatched_catalog_eligible=len(unmatched_cat),
        ambiguous_detections=sum(1 for m in matches if m.n_candidates > 1),
        epoch_propagated=propagated,
    )
