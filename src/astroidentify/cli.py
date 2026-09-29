"""Command-line interface. Thin wrapper: all processing lives in the library.

Exit codes: 0 success, 1 input/processing/output error, 2 invalid usage or configuration.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from astroidentify import __version__
from astroidentify.config import PREVIEW_STRETCHES, DetectionConfig, PreprocessingConfig
from astroidentify.detection.outputs import DetectionOutputPaths, save_detection_outputs
from astroidentify.detection.pipeline import detect_sources
from astroidentify.detection.types import DetectionResult
from astroidentify.exceptions import AstroIdentifyError, ConfigurationError
from astroidentify.logging import configure_logging
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
        "(Milestone 1: preprocessing, Milestone 2: source detection).",
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
    return parser


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
