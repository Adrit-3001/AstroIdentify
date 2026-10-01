"""Command-line interface. Thin wrapper: all processing lives in the library.

Exit codes: 0 success, 1 input/processing/output error, 2 invalid usage or configuration.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from astroidentify import __version__
from astroidentify.astrometry.outputs import AstrometryOutputPaths, save_astrometry_outputs
from astroidentify.astrometry.pipeline import plate_solve
from astroidentify.astrometry.types import PlateSolution
from astroidentify.astrometry.wcs import format_dec, format_ra
from astroidentify.catalogs.gaia import GaiaDR3Provider
from astroidentify.catalogs.outputs import CatalogOutputPaths, save_catalog_outputs
from astroidentify.catalogs.pipeline import load_match_inputs, match_catalog
from astroidentify.catalogs.types import CatalogMatchResult
from astroidentify.config import (
    PREVIEW_STRETCHES,
    AstrometryConfig,
    CatalogConfig,
    DetectionConfig,
    ObjectConfig,
    PreprocessingConfig,
)
from astroidentify.detection.outputs import DetectionOutputPaths, save_detection_outputs
from astroidentify.detection.pipeline import detect_sources
from astroidentify.detection.types import DetectionResult
from astroidentify.exceptions import AstroIdentifyError, ConfigurationError
from astroidentify.logging import configure_logging
from astroidentify.objects import (
    ObjectOutputPaths,
    SimbadProvider,
    identify_objects,
    load_identify_inputs,
    save_object_outputs,
)
from astroidentify.objects.overlay import OverlayReport
from astroidentify.objects.types import IdentificationResult
from astroidentify.preprocessing.loader import SUPPORTED_EXTENSIONS
from astroidentify.preprocessing.outputs import OutputPaths, default_output_dir, save_outputs
from astroidentify.preprocessing.pipeline import preprocess_image
from astroidentify.types import PreprocessingResult

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="astroidentify",
        description="AstroIdentify astronomical image analysis "
        "(Milestone 1: preprocessing, Milestone 2: source detection, "
        "Milestone 3: blind plate solving, Milestone 4: catalogue matching, "
        "Milestone 5: object identification).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    defaults = PreprocessingConfig()
    pre = commands.add_parser(
        "preprocess",
        help="load, validate and normalize an image; write array, preview and metadata",
        description=f"Supported inputs: {', '.join(SUPPORTED_EXTENSIONS)}",
    )
    pre.add_argument("input", type=Path, help="image file (JPEG, PNG or FITS)")
    pre.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output directory (default: outputs/<image name>)",
    )
    pre.add_argument(
        "--hdu",
        type=int,
        default=None,
        help="FITS HDU index to use (default: first HDU with 2-D image data)",
    )
    pre.add_argument(
        "--lower-percentile",
        type=float,
        default=defaults.normalization_lower_percentile,
        help="normalization percentile mapped to 0 (default: %(default)s)",
    )
    pre.add_argument(
        "--upper-percentile",
        type=float,
        default=defaults.normalization_upper_percentile,
        help="normalization percentile mapped to 1 (default: %(default)s)",
    )
    pre.add_argument(
        "--preview-stretch",
        choices=PREVIEW_STRETCHES,
        default=defaults.preview_stretch,
        help="preview display stretch (default: %(default)s = asinh for FITS, linear otherwise)",
    )
    pre.add_argument(
        "--preview-max-size",
        type=int,
        default=defaults.preview_max_dimension,
        help="downscale the preview so its longest side is at most N pixels",
    )
    _add_verbose(pre)

    detection_defaults = DetectionConfig()
    det = commands.add_parser(
        "detect",
        help="preprocess an image and detect stellar source candidates",
        description="Detect, measure and filter stellar source candidates. This finds "
        "point sources; it does not identify objects.",
    )
    det.add_argument("input", type=Path, help="image file (JPEG, PNG or FITS)")
    det.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output directory (default: outputs/<image name>-detection)",
    )
    det.add_argument("--hdu", type=int, default=None, help="FITS HDU index to use")
    det.add_argument(
        "--fwhm",
        type=float,
        default=detection_defaults.fwhm,
        help="stellar FWHM in pixels (default: estimated from the image)",
    )
    det.add_argument(
        "--detection-sigma",
        type=float,
        default=detection_defaults.detection_sigma,
        help="detection threshold in units of the local background RMS (default: %(default)s)",
    )
    det.add_argument(
        "--min-snr",
        type=float,
        default=detection_defaults.min_snr,
        help="minimum aperture SNR for accepted sources (default: %(default)s)",
    )
    det.add_argument(
        "--box-size",
        type=int,
        default=detection_defaults.background_box_size,
        help="local background tile size in pixels (default: %(default)s)",
    )
    det.add_argument(
        "--saturation-level",
        type=float,
        default=None,
        help="pixel value treated as saturated (default: from image metadata)",
    )
    _add_verbose(det)

    astro_defaults = AstrometryConfig()
    solve = commands.add_parser(
        "solve",
        help="blindly plate-solve an image (WCS) from its detected stars",
        description="Preprocess, detect stars and blindly determine where the image points "
        "on the sky with a local Astrometry.net installation. No sky position or object name "
        "is used. This does not identify objects.",
    )
    solve.add_argument("input", type=Path, help="image file (JPEG, PNG or FITS)")
    solve.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output directory (default: outputs/<image name>-astrometry)",
    )
    solve.add_argument("--hdu", type=int, default=None, help="FITS HDU index to use")
    solve.add_argument(
        "--max-sources",
        type=int,
        default=astro_defaults.max_sources,
        help="sources in the first selection (default: %(default)s)",
    )
    solve.add_argument(
        "--min-sources",
        type=int,
        default=astro_defaults.min_sources,
        help="minimum usable sources (default: %(default)s)",
    )
    solve.add_argument(
        "--grid",
        type=_grid_shape,
        default=None,
        metavar="COLSxROWS",
        help="spatial balancing grid, e.g. 5x3 (default: about 16 cells, image aspect)",
    )
    solve.add_argument(
        "--exclude-saturated",
        action="store_true",
        help="never send saturated sources to the solver (default: rank them by brightness "
        "with the others, since index stars are the brightest stars)",
    )
    solve.add_argument("--solve-field", help="path to solve-field (default: search PATH)")
    solve.add_argument("--astrometry-config", help="Astrometry.net engine config file")
    solve.add_argument(
        "--index-dir",
        action="append",
        default=[],
        help="directory of Astrometry.net index files (repeatable; overrides the config)",
    )
    solve.add_argument(
        "--timeout",
        type=float,
        default=astro_defaults.timeout_seconds,
        help="wall-clock limit per solver attempt in seconds (default: %(default)s)",
    )
    solve.add_argument(
        "--total-timeout",
        type=float,
        default=astro_defaults.total_timeout_seconds,
        help="wall-clock budget for all attempts together in seconds (default: %(default)s)",
    )
    solve.add_argument(
        "--cpulimit",
        type=float,
        default=astro_defaults.cpulimit_seconds,
        help="solver CPU-time limit in seconds (default: %(default)s)",
    )
    solve.add_argument(
        "--scale-low",
        type=float,
        default=None,
        help="camera-derived lower pixel scale (arcsec/px); runs a scale-constrained attempt first",
    )
    solve.add_argument(
        "--scale-high",
        type=float,
        default=None,
        help="camera-derived upper pixel scale (arcsec/px); runs a scale-constrained attempt first",
    )
    solve.add_argument("--keep-temp", action="store_true", help="keep the solver working directory")
    _add_verbose(solve)

    catalog_defaults = CatalogConfig()
    cat = commands.add_parser(
        "catalog-match",
        help="match a solved image's detections to Gaia DR3 stars",
        description="Query Gaia DR3 for the solved WCS footprint and associate catalogue stars "
        "one-to-one with the accepted detections. Uses saved Milestone 2/3 products. This is "
        "point-source correspondence, not object identification.",
    )
    cat.add_argument("input", type=Path, help="the image the products were made from")
    cat.add_argument(
        "--plate-solution", type=Path, required=True, help="plate_solution.json (solved)"
    )
    cat.add_argument("--wcs", type=Path, help="solution.wcs (default: next to --plate-solution)")
    cat.add_argument("--detections", type=Path, required=True, help="Milestone 2 sources.json")
    cat.add_argument(
        "-o", "--output", type=Path, help="output directory (default: outputs/<image name>-catalog)"
    )
    cat.add_argument(
        "--match-radius",
        type=float,
        default=catalog_defaults.match_radius_arcsec,
        help="maximum match separation in arcsec (default: %(default)s)",
    )
    cat.add_argument(
        "--brightness-factor",
        type=float,
        default=catalog_defaults.brightness_rank_factor,
        help="eligible catalogue stars = brightest FACTOR x N_detections in the image; 0 = all "
        "(default: %(default)s)",
    )
    cat.add_argument(
        "--no-refine", action="store_true", help="match with the input WCS without refinement"
    )
    cat.add_argument(
        "--observation-date",
        help="observation date/time (ISO 8601) for proper motion when the image has none",
    )
    cat.add_argument(
        "--cache-dir",
        default="outputs/.catalog-cache",
        help="catalogue response cache directory (default: %(default)s)",
    )
    cat.add_argument("--no-cache", action="store_true", help="always query live, store nothing")
    cat.add_argument(
        "--refresh-cache", action="store_true", help="query live and overwrite any cache entry"
    )
    cat.add_argument(
        "--timeout",
        type=float,
        default=catalog_defaults.network_timeout_seconds,
        help="network time limit in seconds (default: %(default)s)",
    )
    cat.add_argument(
        "--row-limit",
        type=int,
        default=catalog_defaults.row_limit,
        help="maximum catalogue rows; reaching it is an error (default: %(default)s)",
    )
    _add_verbose(cat)

    object_defaults = ObjectConfig()
    ident = commands.add_parser(
        "identify",
        help="identify catalogued named objects in a solved image (SIMBAD)",
        description="Query SIMBAD for the solved WCS footprint, place every catalogued object "
        "on the image, keep non-stellar object types and annotate them. Uses saved Milestone "
        "3/4 products. Reports catalogue presence and raw image evidence; no confidence.",
    )
    ident.add_argument("input", type=Path, help="the image the products were made from")
    ident.add_argument(
        "--astrometry", type=Path, help="Milestone 3 output directory (plate_solution.json)"
    )
    ident.add_argument(
        "--catalog",
        type=Path,
        help="Milestone 4 output directory; its refined_solution.wcs is preferred",
    )
    ident.add_argument("--wcs", type=Path, help="explicit WCS file (overrides the defaults)")
    ident.add_argument(
        "--detections",
        type=Path,
        help="Milestone 2 sources.json (default: the one recorded by the catalogue run)",
    )
    ident.add_argument(
        "-o", "--output", type=Path, help="output directory (default: outputs/<image name>-objects)"
    )
    ident.add_argument(
        "--cache-dir",
        default="outputs/.catalog-cache",
        help="catalogue response cache directory (default: %(default)s)",
    )
    ident.add_argument("--no-cache", action="store_true", help="always query live, store nothing")
    ident.add_argument(
        "--offline", action="store_true", help="use only the cache; fail if the field is not cached"
    )
    ident.add_argument("--refresh", action="store_true", help="query live and overwrite the cache")
    ident.add_argument(
        "--timeout",
        type=float,
        default=object_defaults.network_timeout_seconds,
        help="network time limit in seconds (default: %(default)s)",
    )
    ident.add_argument(
        "--row-limit",
        type=int,
        default=object_defaults.row_limit,
        help="maximum catalogue rows; reaching it is an error (default: %(default)s)",
    )
    ident.add_argument(
        "--max-objects",
        type=int,
        default=object_defaults.overlay_max_objects,
        help="objects drawn on the overlay (default: %(default)s)",
    )
    ident.add_argument(
        "--max-labels",
        type=int,
        default=object_defaults.overlay_max_labels,
        help="objects labelled on the overlay (default: %(default)s)",
    )
    _add_verbose(ident)
    return parser


def _grid_shape(text: str) -> tuple[int, int]:
    try:
        columns, rows = (int(v) for v in text.lower().split("x"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected COLSxROWS, e.g. 5x3, got {text!r}") from exc
    return columns, rows


def _add_verbose(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="show more log output (-v info, -vv debug)",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    args = build_parser().parse_args(argv)
    configure_logging(args.verbose)
    if args.command == "detect":
        return _run_detect(args)
    if args.command == "solve":
        return _run_solve(args)
    if args.command == "catalog-match":
        return _run_catalog_match(args)
    if args.command == "identify":
        return _run_identify(args)
    return _run_preprocess(args)


def _error(exc: Exception) -> None:
    print(f"astroidentify: error: {exc}", file=sys.stderr)


def _run_preprocess(args: argparse.Namespace) -> int:
    try:
        config = PreprocessingConfig(
            fits_hdu=args.hdu,
            normalization_lower_percentile=args.lower_percentile,
            normalization_upper_percentile=args.upper_percentile,
            preview_stretch=args.preview_stretch,
            preview_max_dimension=args.preview_max_size,
        )
    except ConfigurationError as exc:
        _error(exc)
        return EXIT_USAGE

    output_dir = args.output if args.output is not None else default_output_dir(args.input)
    try:
        result = preprocess_image(args.input, config)
        paths = save_outputs(result, output_dir)
    except AstroIdentifyError as exc:
        _error(exc)
        return EXIT_ERROR

    print(format_summary(args.input, result, paths))
    return EXIT_OK


def _run_detect(args: argparse.Namespace) -> int:
    try:
        preprocessing_config = PreprocessingConfig(fits_hdu=args.hdu)
        detection_config = DetectionConfig(
            fwhm=args.fwhm,
            detection_sigma=args.detection_sigma,
            min_snr=args.min_snr,
            background_box_size=args.box_size,
            saturation_level=args.saturation_level,
        )
    except ConfigurationError as exc:
        _error(exc)
        return EXIT_USAGE

    output_dir = args.output
    if output_dir is None:
        base = default_output_dir(args.input)
        output_dir = base.with_name(f"{base.name}-detection")
    try:
        preprocessing = preprocess_image(args.input, preprocessing_config)
        result = detect_sources(preprocessing, detection_config)
        paths = save_detection_outputs(result, output_dir)
    except AstroIdentifyError as exc:
        _error(exc)
        return EXIT_ERROR

    print(format_detection_summary(args.input, result, paths))
    return EXIT_OK


def _run_solve(args: argparse.Namespace) -> int:
    try:
        preprocessing_config = PreprocessingConfig(fits_hdu=args.hdu)
        config = AstrometryConfig(
            max_sources=args.max_sources,
            expanded_max_sources=max(args.max_sources, AstrometryConfig().expanded_max_sources),
            min_sources=args.min_sources,
            grid_shape=args.grid,
            include_saturated=not args.exclude_saturated,
            solve_field_path=args.solve_field,
            astrometry_config=args.astrometry_config,
            index_dirs=tuple(args.index_dir),
            timeout_seconds=args.timeout,
            total_timeout_seconds=args.total_timeout,
            cpulimit_seconds=args.cpulimit,
            scale_low_arcsec=args.scale_low,
            scale_high_arcsec=args.scale_high,
            keep_temp=args.keep_temp,
        )
    except ConfigurationError as exc:
        _error(exc)
        return EXIT_USAGE

    output_dir = args.output
    if output_dir is None:
        base = default_output_dir(args.input)
        output_dir = base.with_name(f"{base.name}-astrometry")
    try:
        detection = detect_sources(preprocess_image(args.input, preprocessing_config))
        solution = plate_solve(detection, config, work_dir=Path(output_dir) / "solver_work")
        paths = save_astrometry_outputs(solution, detection, output_dir, config)
    except AstroIdentifyError as exc:
        _error(exc)
        return EXIT_ERROR

    print(format_solve_summary(solution, paths))
    if not solution.solved:
        _error(f"plate solving {solution.status.replace('_', ' ')}: {solution.error}")
        return EXIT_ERROR
    return EXIT_OK


def _run_catalog_match(args: argparse.Namespace) -> int:
    try:
        config = CatalogConfig(
            match_radius_arcsec=args.match_radius,
            brightness_rank_factor=args.brightness_factor or None,
            refine_wcs=not args.no_refine,
            observation_epoch=args.observation_date,
            cache_dir=None if args.no_cache else args.cache_dir,
            refresh_cache=args.refresh_cache,
            network_timeout_seconds=args.timeout,
            row_limit=args.row_limit,
        )
    except ConfigurationError as exc:
        _error(exc)
        return EXIT_USAGE

    wcs_path = args.wcs or args.plate_solution.with_name("solution.wcs")
    output_dir = args.output
    if output_dir is None:
        base = default_output_dir(args.input)
        output_dir = base.with_name(f"{base.name}-catalog")
    try:
        inputs = load_match_inputs(args.input, args.plate_solution, wcs_path, args.detections)
        result = match_catalog(
            inputs.sources, inputs.wcs, inputs.width, inputs.height,
            GaiaDR3Provider(config), config, inputs.image.metadata,
        )  # fmt: skip
        paths = save_catalog_outputs(result, inputs.image, output_dir, config, inputs.provenance)
    except ConfigurationError as exc:
        _error(exc)
        return EXIT_USAGE
    except AstroIdentifyError as exc:
        _error(exc)
        return EXIT_ERROR
    print(format_catalog_summary(result, paths))
    return EXIT_OK


def _run_identify(args: argparse.Namespace) -> int:
    try:
        if args.no_cache and args.offline:
            raise ConfigurationError("--offline needs the cache; do not combine with --no-cache")
        config = ObjectConfig(
            cache_dir=None if args.no_cache else args.cache_dir,
            refresh_cache=args.refresh,
            offline=args.offline,
            network_timeout_seconds=args.timeout,
            row_limit=args.row_limit,
            overlay_max_objects=args.max_objects,
            overlay_max_labels=args.max_labels,
        )
    except ConfigurationError as exc:
        _error(exc)
        return EXIT_USAGE

    output_dir = args.output
    if output_dir is None:
        base = default_output_dir(args.input)
        output_dir = base.with_name(f"{base.name}-objects")
    try:
        inputs = load_identify_inputs(
            args.input,
            astrometry_dir=args.astrometry,
            catalog_dir=args.catalog,
            wcs_path=args.wcs,
            detections_path=args.detections,
        )
        result = identify_objects(
            inputs.wcs, inputs.width, inputs.height, SimbadProvider(config), config,
            inputs.detections, inputs.plane, inputs.wcs_choice,
        )  # fmt: skip
        paths, report = save_object_outputs(
            result, inputs.image, output_dir, config, inputs.provenance
        )
    except ConfigurationError as exc:
        _error(exc)
        return EXIT_USAGE
    except AstroIdentifyError as exc:
        _error(exc)
        return EXIT_ERROR
    print(format_identify_summary(result, paths, report))
    return EXIT_OK


def format_identify_summary(
    result: IdentificationResult, paths: ObjectOutputPaths, report: OverlayReport
) -> str:
    """Concise identification summary: catalogue facts and raw evidence, no confidence."""
    q = result.query
    wcs = result.wcs
    lines = [
        f"Catalogue: {q.service} ("
        + (
            f"cache, queried {q.queried_at}"
            if q.origin == "cache"
            else f"live, {q.query_seconds:.1f} s"
        )
        + ")",
        f"WCS: {wcs.path} ({wcs.source})" if wcs else "WCS: supplied",
        f"Query cone: centre ({q.region.centre.ra_deg:.5f}, {q.region.centre.dec_deg:+.5f}) deg, "
        f"radius {q.region.radius_deg * 60:.2f}' "
        f"(extended objects to {q.outer_radius_deg:.2f} deg)",
        f"Rows returned: {len(q.rows)}",
        f"In field: {len(result.in_field)} (catalogued objects of any type)",
        f"Retained (non-stellar types): {len(result.retained)}; drawn {len(report.drawn)}, "
        f"labelled {len(report.labelled)}",
    ]
    for obj in result.retained[:10]:
        e = obj.extent
        size = (
            f"{e.major_arcmin:.2g}'x{e.minor_arcmin or e.major_arcmin:.2g}'"
            if e.has_size
            else "size n/a"
        )
        evidence = obj.association.kind if obj.association else "n/a"
        lines.append(
            f"  {obj.display_name} [{obj.main_id}] {obj.object_type_description or obj.object_type}"
            f" at ({obj.projected_x:.1f}, {obj.projected_y:.1f}) px, {size}, evidence: {evidence}"
        )
    if len(result.retained) > 10:
        lines.append(f"  ... {len(result.retained) - 10} more in {paths.objects_csv.name}")
    lines += [
        f"Objects: {paths.objects_csv}",
        f"Associations: {paths.associations}",
        f"Overlay: {paths.overlay}",
        f"Summary: {paths.summary}",
    ]
    if result.warnings:
        lines.append(f"Warnings: {'; '.join(result.warnings)}")
    return "\n".join(lines)


def format_catalog_summary(result: CatalogMatchResult, paths: CatalogOutputPaths) -> str:
    """Concise catalogue-match summary (no identification verdict)."""
    s = result.summary
    q = result.query
    refinement = result.refinement
    lines = [
        f"Catalogue: {q.release} ({q.origin}"
        + (f", queried {q.queried_at}" if q.origin == "cache" else f", {q.query_seconds:.1f} s")
        + ")",
        f"Query cone: centre ({q.region.centre.ra_deg:.5f}, {q.region.centre.dec_deg:+.5f}) deg, "
        f"radius {q.region.radius_deg * 60:.2f}'",
        f"Rows returned: {s.rows_returned}",
        f"Rows in image: {s.rows_in_image} ({s.rows_eligible} eligible by brightness)",
        f"Accepted detections: {s.detections_considered}",
    ]
    if refinement is not None and refinement.input_median_offset_arcsec is not None:
        lines.append(
            f'Input WCS check: median offset {refinement.input_median_offset_arcsec:.2f}" over '
            f"{refinement.n_pairs} registration pairs; "
            + ("refined WCS used" if result.wcs_refined else "input WCS used")
        )
    if s.matches:
        lines += [
            f"Matches: {s.matches} ({s.detection_match_fraction:.1%} of detections, "
            f"{s.catalog_match_fraction_eligible:.1%} of eligible catalogue stars)",
            f'Median residual: {s.median_residual_arcsec:.3f}" ({s.median_residual_px:.2f} px)',
            f'RMS residual: {s.rms_residual_arcsec:.3f}" ({s.rms_residual_px:.2f} px); '
            f'max {s.max_residual_arcsec:.3f}"',
        ]
    else:
        lines.append("Matches: 0 (no detection within the match radius of a catalogue star)")
    lines += [
        f"Unmatched: {s.unmatched_detections} detections, "
        f"{s.unmatched_catalog_eligible} eligible catalogue stars",
        f"Epoch propagation: {'yes' if s.epoch_propagated else 'no'}"
        + ("" if s.epoch_propagated else " (no reliable observation timestamp)"),
        f"Matches: {paths.matches}",
        f"Overlay: {paths.overlay}",
        f"Summary: {paths.summary}",
    ]
    if result.warnings:
        lines.append(f"Warnings: {len(result.warnings)} (see summary)")
    return "\n".join(lines)


def format_solve_summary(solution: PlateSolution, paths: AstrometryOutputPaths) -> str:
    """Concise plate-solving summary (never includes object names)."""
    selection = solution.selection
    stats = solution.match_statistics
    lines = [
        f"Solved: {'yes' if solution.solved else 'no'} ({solution.status})",
        f"Backend: {solution.backend}"
        + (f" {solution.backend_version}" if solution.backend_version else ""),
        f"Selected sources: {len(selection) if selection is not None else 0}",
    ]
    attempts = ", ".join(f"{a.number}:{a.name}={_attempt_state(a)}" for a in solution.attempts)
    lines.append(f"Attempts: {attempts or 'none (solver not run)'}")
    if solution.solved and solution.geometry is not None:
        g = solution.geometry
        lines += [
            f"Matched sources: {stats.n_matched if stats else 'n/a'}",
            f"Centre RA: {g.centre.ra_deg:.6f} deg ({format_ra(g.centre.ra_deg)})",
            f"Centre Dec: {g.centre.dec_deg:+.6f} deg ({format_dec(g.centre.dec_deg)})",
            f"Pixel scale: {g.pixel_scale_arcsec:.4f} arcsec/pixel",
            f"Field: {g.field_width_deg * 60:.2f}' x {g.field_height_deg * 60:.2f}'",
            f"Orientation: up is {g.up_position_angle_deg:.2f} deg E of N; parity {g.parity}",
        ]
        if stats is not None and stats.median_residual_arcsec is not None:
            lines.append(
                f'Residual: median {stats.median_residual_arcsec:.3f}", '
                f'RMS {stats.rms_residual_arcsec:.3f}", max {stats.max_residual_arcsec:.3f}"'
            )
        else:
            lines.append("Residual: n/a (no correspondence output)")
    lines += [
        f"Runtime: {solution.runtime_seconds:.1f} s",
        f"Mode: {solution.mode or 'n/a'}",
        f"WCS: {paths.wcs or 'not written (unsolved)'}",
        f"Overlay: {paths.wcs_overlay or paths.selection_overlay}",
        f"Solution: {paths.plate_solution}",
        f"Solver log: {paths.solver_log}",
    ]
    return "\n".join(lines)


def _attempt_state(attempt) -> str:
    runtime = f" {attempt.runtime_seconds:.1f}s" if attempt.runtime_seconds is not None else ""
    return f"{attempt.status}{runtime}"


def format_detection_summary(
    input_path: Path, result: DetectionResult, paths: DetectionOutputPaths
) -> str:
    """Concise summary of a detection run (no per-source output)."""
    image = result.preprocessing.image
    d = result.diagnostics
    median_fwhm = d["median_fwhm_accepted"]
    median_snr = d["median_snr_accepted"]
    reasons = ", ".join(f"{k} {v}" for k, v in d["rejection_reason_counts"].items()) or "none"
    lines = [
        f"Input: {input_path}",
        f"Dimensions: {image.width} x {image.height} ({result.plane.method} detection plane)",
        f"FWHM: {result.fwhm.value:.3g} px ({result.fwhm.method})",
        f"Local background: median {d['background_map']['median']:.4g} "
        f"(min {d['background_map']['min']:.4g}, max {d['background_map']['max']:.4g})",
        f"Local RMS: median {d['background_rms_map']['median']:.4g} "
        f"(min {d['background_rms_map']['min']:.4g}, max {d['background_rms_map']['max']:.4g})",
        f"Correlated-noise factor: {d['noise_correlation_factor']:.3g}",
        f"Candidates: {d['n_candidates']}",
        f"Accepted: {d['n_accepted']}",
        f"Rejected: {d['n_rejected']} ({reasons})",
        f"Saturated: {d['n_saturated']} ({d['n_saturated_accepted']} accepted)",
        f"Edge flagged: {d['n_edge_flagged']} ({d['n_edge_flagged_accepted']} accepted)",
        _astrometric_line(d.get("astrometric_centroid")),
        f"Median SNR (accepted): {median_snr:.3g}" if median_snr is not None else "Median SNR: n/a",
        f"Median FWHM (accepted, effective): {median_fwhm:.3g} px"
        if median_fwhm is not None
        else "Median FWHM: n/a",
        f"Overlay: {paths.overlay}",
        f"Sources: {paths.sources_csv} , {paths.sources_json}",
        f"Metadata: {paths.metadata}",
        f"Background maps: {paths.background_map} , {paths.background_rms}",
    ]
    if result.warnings:
        lines.append(f"Warnings: {len(result.warnings)} (listed in detection_metadata.json)")
    return "\n".join(lines)


def format_summary(input_path: Path, result: PreprocessingResult, paths: OutputPaths) -> str:
    """Build the concise, human-readable summary printed after a successful run."""
    image = result.image
    diagnostics = result.diagnostics
    lines = [
        f"Input: {input_path}",
        f"Format: {image.format.value}",
        f"Dimensions: {image.width} x {image.height} "
        f"({image.channels} channel{'s' if image.channels > 1 else ''})",
        f"Background estimate: {result.background_level:.6g}",
        f"Noise sigma: {result.noise_sigma:.6g} "
        f"(pixel-to-pixel: {diagnostics['difference_noise_sigma']:.6g})",
        f"Normalization: percentiles {result.normalization.lower_percentile:g}-"
        f"{result.normalization.upper_percentile:g} -> values "
        f"{result.normalization.lower_value:.6g}-{result.normalization.upper_value:.6g}",
    ]
    if diagnostics["n_invalid_pixels"]:
        lines.append(f"Invalid pixels: {diagnostics['n_invalid_pixels']} (NaN/Inf/BLANK)")
    if "fits" in image.metadata:
        fits_meta = image.metadata["fits"]
        lines.append(f"FITS HDU: {fits_meta['hdu_index']} ({fits_meta['hdu_name'] or 'unnamed'})")
    lines += [
        f"Processed array: {paths.processed}",
        f"Preview: {paths.preview}",
        f"Metadata: {paths.metadata}",
    ]
    if paths.mask is not None:
        lines.append(f"Valid-pixel mask: {paths.mask}")
    if result.warnings:
        lines.append(f"Warnings: {len(result.warnings)} (listed in metadata.json)")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())


def _astrometric_line(summary: dict | None) -> str:
    """One-line summary of the saturated-star astrometric centroids."""
    if not summary or not summary.get("enabled"):
        return "Astrometric centroids: detection centroids (calibration disabled)"
    counts = summary["accepted_by_method"]
    line = (
        f"Astrometric centroids: {counts['isophote_calibrated']} saturated cores calibrated, "
        f"{counts['saturated_core_fallback']} fallback"
    )
    if summary.get("median_correction_px") is not None:
        line += f" (median shift {summary['median_correction_px']:.2f} px)"
    return line
