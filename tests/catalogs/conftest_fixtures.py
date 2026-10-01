from __future__ import annotations

import json
from pathlib import Path

import pytest
from astropy.io import fits

from astroidentify import preprocess_image
from astroidentify.config import DetectionConfig
from astroidentify.detection import detect_sources, save_detection_outputs
from astroidentify.serialization import sha256_file
from tests.astrometry.fixtures import make_truth_wcs


@pytest.fixture
def saved_products(tmp_path: Path) -> dict[str, Path]:
    """Image + Milestone 2 sources + a solved plate solution/WCS, as saved on disk."""
    from tests.test_cli import _star_field_png

    image = _star_field_png(tmp_path / "field.png")
    detection = detect_sources(preprocess_image(image), DetectionConfig(fwhm=4.0))
    det_paths = save_detection_outputs(detection, tmp_path / "det")
    wcs = make_truth_wcs(200, 160, centre=(120.5, -12.25), scale_arcsec=1.0, rotation_deg=20.0)
    header = wcs.to_header()
    header["IMAGEW"], header["IMAGEH"] = 200, 160
    fits.PrimaryHDU(header=header).writeto(tmp_path / "solution.wcs")
    plate = {
        "solved": True, "status": "solved", "mode": "blind",
        "constraints": {"position_hint": None},
        "input": {"source_sha256": sha256_file(image), "width": 200, "height": 160},
    }  # fmt: skip
    (tmp_path / "plate_solution.json").write_text(json.dumps(plate))
    return {
        "image": image, "plate": tmp_path / "plate_solution.json",
        "wcs": tmp_path / "solution.wcs", "sources": det_paths.sources_json,
        "detection": detection, "wcs_obj": wcs,
    }  # fmt: skip
