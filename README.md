# AstroIdentify

AstroIdentify aims to take an astronomical image with little or no context, work out where in
the sky it points, identify catalogued objects in the field, annotate the image and explain how
confident it is in each identification.

The project is built in milestones. **Only Milestone 1 (image ingestion and preprocessing) is
implemented.** Source/star detection, plate solving (astrometry/WCS), catalogue queries,
ML verification, an API and a web frontend are deliberately left to future milestones.

## Milestone 1: what it does

Given a `.jpg`/`.jpeg`, `.png` or `.fits` image, one command:

1. loads and validates it (clear errors for missing, unsupported or corrupt files);
2. converts it to a consistent `float32` array, `(H, W)` or `(H, W, 3)`, keeping source units;
3. preserves metadata (full FITS header, EXIF capture time/camera/GPS, PNG text chunks);
4. estimates the background level (sigma-clipped median);
5. estimates the noise (MAD-based, robust to stars);
6. normalizes the image for downstream processing (percentile-based affine map, unclipped);
7. saves the normalized array, a display preview and machine-readable metadata.

## Installation

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"      # runtime deps: numpy, astropy, pillow; dev: pytest, ruff
```

## Usage

```bash
astroidentify preprocess data/raw/m57.jpg --output outputs/m57
```

Without `--output`, results go to `outputs/<image name>/`. `python -m astroidentify ...` works too.

Example output:

```text
Input: data/raw/HorseHead.fits
Format: FITS
Dimensions: 891 x 893 (1 channel)
Background estimate: 9653
Noise sigma: 3879.97 (pixel-to-pixel: 293.54)
Normalization: percentiles 1-99.5 -> values 5226-16605
FITS HDU: 0 (PRIMARY)
Processed array: outputs/HorseHead/processed.npy
Preview: outputs/HorseHead/preview.png
Metadata: outputs/HorseHead/metadata.json
Warnings: 1 (listed in metadata.json)
```

Options:

| Option | Meaning |
| --- | --- |
| `-o, --output DIR` | Output directory (default `outputs/<image name>`) |
| `--hdu N` | FITS HDU index to use (default: first HDU with 2-D image data) |
| `--lower-percentile P` / `--upper-percentile P` | Normalization percentiles (default 1 / 99.5) |
| `--preview-stretch {auto,linear,asinh}` | Preview display stretch (default `auto`) |
| `--preview-max-size N` | Downscale the preview so its longest side is at most N px |
| `-v` / `-vv` | Info / debug logging on stderr |

Exit codes: `0` success, `1` input/processing/output error, `2` invalid usage or configuration.

### Python API

```python
from astroidentify import PreprocessingConfig, preprocess_image, save_outputs

result = preprocess_image("data/raw/m57.fits", PreprocessingConfig(fits_hdu=None))
result.image.data  # float32, source units, NaN = invalid pixel (read-only)
result.normalized  # float32, same shape, all finite (read-only)
result.valid_mask  # bool (H, W)
result.background_level, result.noise_sigma  # data units
result.normalized_background_level, result.normalized_noise_sigma  # normalized units
result.image.header  # astropy Header of the FITS HDU (for a future WCS), or None
result.image.metadata  # JSON-safe source metadata
result.diagnostics  # pixel statistics and warnings
paths = save_outputs(result, "outputs/m57")
```

## Output files

```text
outputs/<image name>/
    processed.npy     normalized float32 array, (H, W) or (H, W, 3); load with numpy.load
    preview.png       8-bit display preview (for people only, never for analysis)
    metadata.json     source info, estimates, normalization parameters, diagnostics, config
    valid_mask.npy    only if some pixels were NaN/Inf/BLANK: bool (H, W), True = valid
```

Key `metadata.json` fields: `source`, `source_sha256`, `format`, `width`, `height`,
`channels`, `original_shape`, `background_level`, `noise_sigma`, `background` (method,
per-channel estimates), `normalization` (percentiles, the exact `lower_value`/`upper_value`
used, and the normalized background/noise), `diagnostics` (min/max, invalid pixel count,
clipping/saturation fractions, pixel-to-pixel noise, `warnings`), `source_metadata` (full
FITS header cards, EXIF, and so on), `config`, and `schema_version`.

To convert normalized values back to data units:
`data = normalized * (upper_value - lower_value) + lower_value`.

## Supported formats

| Format | Extensions | Notes |
| --- | --- | --- |
| JPEG | `.jpg`, `.jpeg` | Grayscale or RGB; EXIF orientation applied (lossless) |
| PNG | `.png` | 8/16-bit grayscale, RGB; alpha dropped, palette converted to RGB |
| FITS | `.fits`, `.fit`, `.fts` (optionally `.gz`) | 2-D images (singleton axes dropped) |

File content decides the format: a PNG saved as `.jpg` loads as PNG, with a warning.

## Processing decisions

- **Array layout.** Arrays are indexed `[y, x]` or `[y, x, channel]`. FITS data is never
  flipped, so a WCS from the header stays valid. FITS row 0 is displayed at the *bottom*
  (as in DS9), so FITS previews are flipped for display only.
- **FITS HDU selection.** Use `--hdu` if given. Otherwise take the first HDU in file order
  (primary first) whose data is 2-D after dropping length-1 axes. Cubes and multi-plane
  colour FITS are rejected with a clear error instead of being guessed at.
- **Invalid pixels.** NaN, ±Inf and FITS `BLANK` become NaN in `image.data`. They are
  excluded from all statistics and filled with the normalized background level in
  `processed.npy`, so they don't look like sources. `valid_mask` records where they were.
- **Background and noise.** Iterative 3σ clipping (median centre, MAD width) removes stars,
  then background = median and noise = 1.4826 × MAD of the remaining pixels. Colour images
  are measured on the channel mean, with per-channel values in the metadata. If the MAD is 0
  (heavily quantized or black-clipped backgrounds), a clipped standard deviation is used
  instead, with a warning.
- **Pixel-to-pixel noise.** Also reported: the MAD of differences between neighbouring
  pixels, divided by √2. Smooth structure cancels in these differences. If the global noise is
  more than 2× larger, the image has large-scale structure (nebulosity, gradients) and a
  warning is emitted.
- **Normalization.** `(x - p1) / (p99.5 - p1)` over all finite values, with one scale shared
  by all colour channels (colour ratios are kept). Values are **not clipped**, so faint signal
  below 0 and bright cores above 1 survive, and the map can be inverted exactly.
- **Preview.** Percentile (0.5–99.8) display stretch; `auto` uses asinh for FITS (usually
  linear sensor data) and linear for JPEG/PNG (usually already display-encoded).

## Known limitations (Milestone 1)

- Background and noise are single **global** values. Images dominated by nebulosity or
  gradients get an inflated `noise_sigma` (see the pixel-to-pixel noise and warning). A tiled
  background map belongs to a later milestone.
- JPEG/PNG values are kept as stored (usually gamma-encoded, not linear light), and JPEG
  compression makes noise spatially correlated.
- Pillow reduces 16-bit **colour** PNGs to 8 bits per channel (detected and warned).
  16-bit grayscale PNG is kept at full precision.
- FITS cubes, multi-plane colour FITS and raw camera formats (CR2, NEF, ...) are not supported.
- A 24-megapixel RGB image takes about 13 s and about 1.7 GB RAM. Use `--preview-max-size`
  for smaller previews.

## Development

```bash
pytest                       # run the test suite
ruff check . && ruff format --check .
```

Tests generate synthetic star fields with fixed seeds, so they need no data files. Put your
own images in `data/raw/` (git-ignored); generated results go in `outputs/` (git-ignored).

```text
src/astroidentify/
    config.py          PreprocessingConfig (all tunable values, validated)
    exceptions.py      domain-specific errors
    types.py           AstronomyImage, PreprocessingResult, BackgroundEstimate, ...
    logging.py         CLI logging setup (library code only logs, never prints)
    cli.py             `astroidentify preprocess ...`
    preprocessing/
        loader.py      JPEG/PNG/FITS -> AstronomyImage (the only format-specific code)
        background.py  background level, noise, pixel-to-pixel noise
        normalize.py   percentile normalization
        preview.py     display-only preview rendering
        pipeline.py    preprocess_image() / preprocess()
        outputs.py     artifact writing and metadata.json serialization
```

## Roadmap

1. **Image ingestion and preprocessing** (done)
2. Source/star detection
3. Astrometric plate solving / WCS
4. Catalogue matching (Gaia, SIMBAD)
5. Annotation and identification
6. Evidence/confidence estimation
7. CV/ML verification
8. Solar-system objects
9. FastAPI backend
10. Web frontend and deployment
