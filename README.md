# AstroIdentify

AstroIdentify aims to take an astronomical image with little or no context, work out where in
the sky it points, identify catalogued objects in the field, annotate the image and explain how
confident it is in each identification.

The project is built in milestones. **Milestones 1 (preprocessing), 2 (stellar source
detection) and 3 (blind plate solving) are implemented.** With a local Astrometry.net
installation and index data, AstroIdentify can determine where an unknown image points on the
sky (a WCS) from its detected stars alone. It does **not** yet know *what* is in the image:
catalogue queries, object identification, ML verification, an API and a web frontend are
deliberately left to future milestones.

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
pip install -e ".[dev]"   # runtime: numpy, astropy, pillow, photutils, scipy; dev: pytest, ruff
```

## Usage (Milestone 1: preprocessing)

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
  more than 2× larger, a warning is emitted. The cause may be large-scale structure
  (nebulosity, gradients) *or* spatially correlated noise (upsampled, demosaiced, denoised or
  JPEG images), which this statistic underestimates. Milestone 2's local RMS map and
  empty-aperture noise factor tell the two apart.
- **Normalization.** `(x - p1) / (p99.5 - p1)` over all finite values, with one scale shared
  by all colour channels (colour ratios are kept). Values are **not clipped**, so faint signal
  below 0 and bright cores above 1 survive, and the map can be inverted exactly.
- **Preview.** Percentile (0.5–99.8) display stretch; `auto` uses asinh for FITS (usually
  linear sensor data) and linear for JPEG/PNG (usually already display-encoded).

## Known limitations (Milestone 1)

- Background and noise are single **global** values. Images dominated by nebulosity or
  gradients get an inflated `noise_sigma` (see the pixel-to-pixel noise and warning).
  Milestone 2 adds local background and RMS maps for detection.
- JPEG/PNG values are kept as stored (usually gamma-encoded, not linear light), and JPEG
  compression makes noise spatially correlated.
- Pillow reduces 16-bit **colour** PNGs to 8 bits per channel (detected and warned).
  16-bit grayscale PNG is kept at full precision.
- FITS cubes, multi-plane colour FITS and raw camera formats (CR2, NEF, ...) are not supported.
- A 24-megapixel RGB image takes about 13 s and about 1.7 GB RAM. Use `--preview-max-size`
  for smaller previews.

## Milestone 2: stellar source detection

Detection consumes the Milestone 1 `PreprocessingResult` (the image is never reloaded) and
produces a machine-readable list of stellar source candidates with accurate pixel centroids,
brightness, quality measurements, flags and explicit accept/reject decisions, plus a
diagnostic overlay. **This is source detection, not object identification.** Nothing is
named or matched to a catalogue, and extended objects such as nebulae are not recognised.

```bash
astroidentify detect data/raw/M57__Ring_Nebula-eQuinox-20260925-003755.png \
    --output outputs/m57-detection
```

Without `--output`, results go to `outputs/<image name>-detection/`. The summary looks like:

```text
Dimensions: 2560 x 1920 (channel_mean detection plane)
FWHM: 9.29 px (estimated)
Local background: median 9.532 (min 5.5, max 35.84)
Local RMS: median 3.784 (min 3.045, max 14.5)
Correlated-noise factor: 7.46
Candidates: 2464
Accepted: 643
Rejected: 1821 (duplicate_of_saturated_source 6, elongated_saturated_region 2, extended 1, low_snr 1787, not_star_like 189, too_close_to_edge 79, too_elongated 63)
Saturated: 129 (116 accepted)
Edge flagged: 117 (9 accepted)
...
```

### Pipeline

1. **Detection plane.** Grayscale images are used as-is; colour images use the unweighted
   mean of R, G, B, in source units (not the normalized array). Invalid pixels are filled with
   the Milestone 1 background and masked. The original arrays are not modified.
2. **Local background.** photutils `Background2D`: tiles of about `--box-size` px (default
   64, adjusted so tiles cover the image evenly), 3σ-clipped median background and
   standard-deviation RMS per tile, a 3×3 median filter over the tile grid, and
   interpolation to full resolution. This produces `background_map` and `background_rms`,
   and detection runs on `plane - background_map`. The RMS map is floored at the
   quantization noise of integer data.
3. **FWHM.** Unless `--fwhm` is given, circular Gaussians are fitted to up to 50 bright,
   unsaturated stars away from the edges, iterating until the median converges.
4. **Detection.** photutils `DAOStarFinder` (DAOFIND) with the threshold
   `--detection-sigma` × local RMS map (default 5). Its built-in shape cuts are disabled, so
   every thresholded peak becomes a candidate. DAOFIND silently drops objects it cannot fit
   (bright stars much broader than the kernel), so a supplementary peak search adds them back
   with a centre-of-mass centroid (`centroid_method = peak_com`).
5. **Saturated stars.** A pixel is saturated if any channel reaches the saturation level:
   the raster nominal maximum (e.g. 255 for 8-bit), FITS `SATURATE`, or
   `--saturation-level`. Detections on the same connected saturated core are merged: the
   brightest is repositioned to the core centroid (`centroid_method = saturated_core`) and
   the rest are rejected as duplicates. Saturated stars are flagged, never rejected for
   saturation alone.
6. **Measurements.** Aperture photometry (radius 1 FWHM) on the background-subtracted plane
   gives `flux`, `flux_err` and `snr`. `flux_err` is scaled by an empirical
   **correlated-noise factor**: the scatter of identical apertures placed on source-free sky,
   divided by the per-pixel prediction (1 for white noise). The effective FWHM is derived from
   flux/peak. DAOFIND sharpness and roundness, local background and RMS, edge distance and the
   saturated-pixel count are also recorded.
7. **Filtering** (separate and explainable; every candidate is kept with its reasons):

   | Reason | Rule (defaults) |
   | --- | --- |
   | `low_snr` | SNR < `--min-snr` (5) |
   | `too_close_to_edge` | centroid < 1 FWHM from the border (stars 1–2 FWHM away are only *flagged* `edge`) |
   | `not_star_like` | DAOFIND sharpness outside 0.2–1.0 (unsaturated only) |
   | `too_elongated` | \|roundness1\| or \|roundness2\| > 1.0 (unsaturated only) |
   | `extended` | effective FWHM > 2 × median effective FWHM of the stars (unsaturated only) |
   | `elongated_saturated_region` | saturated region ≥ 1 FWHM across with axis ratio < 0.5 |
   | `duplicate_of_saturated_source` | a second detection on an already-used saturated core |

### Detection outputs

```text
outputs/<name>-detection/
    sources.csv                  every candidate, brightest first (source_id 1 = brightest)
    sources.json                 same, plus coordinate convention and field descriptions
    detection_metadata.json      counts, SNR/FWHM statistics, background/RMS map statistics,
                                 FWHM estimate, noise factor, config, warnings
    preprocessing_metadata.json  the Milestone 1 metadata of the input (provenance)
    detected_sources.png         full-resolution overlay; legend strip appended below
    background_map.npy / .png    local background map (float32) and a visualization
    background_rms.npy / .png    local RMS map (float32) and a visualization
```

Per-source fields: `source_id, x, y, flux, flux_err, snr, peak, fwhm, sharpness, roundness1,
roundness2, local_background, local_rms, edge_distance, n_saturated_pixels, saturated, edge,
centroid_method, saturated_core_axis_ratio, saturated_core_area, duplicate_of, accepted,
rejection_reasons`. Values are in detection-plane units (source pixel values, channel mean
for colour).

Overlay markers: green circles are accepted (radius = photometry aperture), cyan circles are
accepted but edge-flagged, small red circles are rejected, and a yellow outer ring marks
saturation. The brightest accepted sources are labelled with their IDs.

**Coordinates.** `x` is the column and `y` the row of the image array: x increases left →
right, y increases top → bottom, and integer values are pixel centres (pixel `(0, 0)` spans
−0.5…0.5). Nothing is flipped, resized or cropped between measurement and overlay. For FITS
this means the overlay shows row 0 at the top, unlike the Milestone 1 preview.

### Python API

```python
from astroidentify import preprocess_image
from astroidentify.config import DetectionConfig
from astroidentify.detection import detect_sources, save_detection_outputs

detection = detect_sources(preprocess_image("data/raw/field.png"), DetectionConfig())
detection.accepted_sources         # tuple[Source, ...], brightest first
detection.brightest(50)            # 50 brightest accepted sources
detection.xy_flux()                # (N, 3) array of x, y, flux: input for a future plate solver
detection.background.background   # local background map; .rms, .subtracted
save_detection_outputs(detection, "outputs/field-detection")
```

### Important parameters

| Option / config field | Default | Effect |
| --- | --- | --- |
| `--detection-sigma` / `detection_sigma` | 5 | candidate threshold in local RMS units |
| `--fwhm` / `fwhm` | estimated | stellar FWHM in px (kernel, apertures, edge margins) |
| `--min-snr` / `min_snr` | 5 | acceptance threshold on the (correlated-noise corrected) SNR |
| `--box-size` / `background_box_size` | 64 | background tile size: larger than stars, smaller than gradients |
| `--saturation-level` / `saturation_level` | from metadata | saturation threshold |
| `aperture_radius_fwhm` | 1.0 | photometry aperture radius |
| `max_fwhm_ratio` | 2.0 | `extended` rule |
| `correct_correlated_noise` | true | apply the empty-aperture noise factor |

All values live in `DetectionConfig` (`src/astroidentify/config.py`) with documentation.

### Known limitations (Milestone 2)

- **SNR is background-limited.** Source photon noise is excluded (gain unknown), and SNR is a
  ranking statistic, not a calibrated uncertainty. On heavily processed images the
  correlated-noise factor is large (7.5 on the M57 benchmark), so many faint smudges are
  rejected as `low_snr`. Lower `--min-snr` if you want more faint candidates.
- **Tile-grid background.** Edge tiles compress strong gradients (3×3 grid filter), and a
  strong gradient inside a tile inflates that tile's RMS. Extended objects of about tile size
  (like the Ring Nebula) are partly absorbed into the background map, and its cubic
  interpolation leaves a shallow undershoot (about 1σ) around them.
- **Effective FWHM is a flux/peak width.** It is not a per-source fit and is inflated for
  saturated stars.
- **Extended structure can still produce candidates.** Detections on nebulae are filtered
  only by general rules (width, shape, saturated-core shape). No object is recognised or
  special-cased.
- **Blends.** Close doubles within 2.5 FWHM are reported as one source.


## Milestone 3: blind plate solving (WCS)

**Plate solving** finds the mapping between image pixels and sky coordinates (a *World
Coordinate System*, WCS) by recognising the geometric pattern of the detected stars. AstroIdentify
solves **blind**: the solver receives only star pixel positions, their brightness ranking and the
image size. No RA/Dec hint, object name, filename or target label is ever passed; the command
builder refuses position flags. The result says where the image points, not what is in it.

```bash
astroidentify solve data/raw/M57__Ring_Nebula-eQuinox-20260925-003755.png \
    --output outputs/m57-astrometry
```

The command reuses the Milestone 1 and 2 pipelines (preprocess, then detect), then selects
sources, writes the source list, runs `solve-field` and parses the WCS. The real Unistellar
benchmark solves **fully blind** (no position, no scale hint; the filename is never read):

```text
Solved: yes (solved)
Backend: Astrometry.net 0.93
Selected sources: 100
Attempts: 1:bright=solved 2.7s
Matched sources: 25
Centre RA: 283.386456 deg (18h53m32.75s)
Centre Dec: +33.027044 deg (+33d01m37.4s)
Pixel scale: 0.8570 arcsec/pixel
Field: 36.57' x 27.42'
Orientation: up is 33.63 deg E of N; parity normal
Residual: median 1.220", RMS 2.007", max 4.181"
Runtime: 2.7 s
Mode: blind
```

AstroIdentify can determine the celestial coordinates of an unknown astronomical image. It
still does **not** know what objects are in it: catalogue lookup and identification are later
milestones.

### Prerequisite: Astrometry.net and index data

AstroIdentify calls a **local** `solve-field` through a controlled subprocess. It never uses the
astrometry.net web service and never downloads index data by itself. Two things are needed:

1. **The solver:** `sudo apt install astrometry.net` (Debian/Ubuntu; other platforms: build from
   https://github.com/dstndstn/astrometry.net).
2. **Index files** matching your field of view. Index files are pre-computed star-pattern
   catalogues; each covers a range of pattern ("quad") sizes, which should span roughly 10–100%
   of the image width. For typical small-telescope fields of about 20′–60′:

   ```bash
   sudo apt install astrometry-data-tycho2        # bright stars, all scales, ~284 MB
   sudo apt install astrometry-data-2mass-06 astrometry-data-2mass-07 \
                    astrometry-data-2mass-08-19   # deeper, quads >= 16', ~624 MB (downloaded at install)
   ```

   Narrower fields need smaller-scale sets (e.g. `astrometry-data-2mass-05`, 11′–16′, 629 MB).
   The packages install into `/usr/share/astrometry`, which `/etc/astrometry.cfg` already lists.

Index files can also live anywhere: `--index-dir DIR` (repeatable) makes AstroIdentify generate
a solver config listing only those directories, and `--astrometry-config FILE` uses your own
config. If the solver or index files are missing, `solve` still writes the source selection,
then exits with status 1 and names the missing prerequisite.

### Source selection

The solver gets a deterministic, quality-aware and spatially balanced subset of the accepted
Milestone 2 sources, not simply the brightest N:

- **Tiers**: `preferred` (unsaturated, not edge-flagged, DAOFIND centroid), `secondary`
  (unsaturated, peak-search centroid), `saturated` (core-centroided) and `edge`.
- **Tier modes**: `pooled` (allowed tiers compete on brightness) or `sequential` (better tiers
  used up first).
- **Why saturated stars come first by default.** Astrometry.net index files hold the
  *brightest* stars of each sky region, and blind matching pairs the brightest field stars
  with them. In processed consumer images those stars are saturated. On the M57 benchmark
  all 25 index stars in the field were among the 37 brightest detections and all saturated.
  An unsaturated-only list did not solve even when the sky position was given; the pooled
  list solved blind in under 3 s. Saturated-core centroids are good to about 1–2 px, which is
  enough for matching.
- **Brightness** order: flux, then SNR, then position (for ties).
- **Spatial balancing:** a grid of about 16 cells shaped to the image aspect (5×3 for 4:3
  images). Sources are taken round-robin, the brightest remaining per cell per round, so the
  list cannot collapse onto the brightest or densest part of the frame.
- The list is sent **sorted by flux** (rank 1 = brightest), as blind solvers expect.

### Attempt sequence and scheduling (bounded, deterministic)

0. `scale_bounds` *(only if `--scale-low/--scale-high` are given, and then first)*: the
   `bright` set with that pixel-scale range (arcsec/pixel, from your telescope/camera, never
   from the target); recorded as `scale-constrained`. Blind attempts follow if it fails;
1. `bright`: unsaturated + saturated non-edge sources, pooled by brightness, grid-balanced (100);
2. `unsaturated`: unsaturated non-edge sources only (100), for images whose saturated
   centroids are unreliable;
3. `expanded`: all tiers including edge sources, pooled (200).

Each attempt gets at most `--timeout` seconds (default 300) and the whole sequence at most
`--total-timeout` (default 900). A timeout or solver error ends only that attempt; the next one
still runs while budget remains. Attempts with an identical source set are skipped. Each
attempt records its own `status` (`solved`, `unsolved`, `timeout`, `solver_error`,
`invalid_wcs`, `skipped`, `budget_exhausted`), runtime, timeout and exact command in
`plate_solution.json`, and its full output in `solver.log`. `--exclude-saturated` keeps
saturated stars out of every attempt.

### Coordinate conventions

- Canonical AstroIdentify coordinates are unchanged: 0-based array x/y, pixel centres at
  integers, row 0 at the top.
- Astrometry.net source lists use FITS 1-based coordinates. The conversion
  (`solver = canonical + 1`) lives in exactly one function
  (`astroidentify.astrometry.xylist`). It was verified against Astrometry.net 0.93 itself: its
  own extractor reports a star at array column 30, row 20 as (31, 21), and solved synthetic
  fields map canonical coordinates to the true sky positions with 0.000″ error.
- WCS files use FITS 1-based `CRPIX`. AstroIdentify always calls Astropy with `origin=0` and
  canonical coordinates (`astrometry.wcs.pixel_to_sky/sky_to_pixel`, including SIP
  distortion), so no manual ±1 appears anywhere else.
- `parity` is `normal` when the image, displayed with row 0 at the top, shows the sky as seen
  from the ground (east 90° counter-clockwise from north), else `mirrored`.
  `up_position_angle_deg` is the position angle (east of north) of the displayed up direction.
  The solver's own values (whose "up" is FITS +y, i.e. down the display) are kept verbatim
  under `solver_report`.

### Outputs

```text
outputs/<name>-astrometry/
    selected_sources.csv / .json   the solver source set (canonical coordinates, rank, tier, grid cell)
    source_selection.png           selected sources by tier over all accepted detections, plus the grid
    plate_solution.json            status, mode, constraints, centre, corners, scale, field size,
                                   orientation, parity, match statistics, attempts, runtime
    solver.log                     exact commands, exit status and solver output of every attempt
    solution.wcs                   FITS WCS header (TAN-SIP), only if solved
    wcs_overlay.png                RA/Dec grid, centre marker and coordinates, selected and
                                   solver-matched stars, only if solved
    correspondences.csv            solver-matched stars: pixel/sky positions and residuals
```

Match evidence is raw: matched-star count, median, RMS and maximum residual (arcsec and px),
the match fraction, and the solver's own log-odds and verification counts. No percentage
"confidence" is computed. Temporary solver files are removed unless `--keep-temp` is given.

### Failure modes

| Status | Meaning | Exit code |
| --- | --- | --- |
| `solved` | valid WCS | 0 |
| `unsolved` | the attempts ran normally and none matched (try more index scales or scale bounds) | 1 |
| `prerequisites_missing` | `solve-field` or index files not found (message says which) | 1 |
| `timeout` | at least one attempt hit its time limit and none solved (more time may help) | 1 |
| `solver_error` | every attempt that ran failed inside `solve-field` (see `solver.log`) | 1 |
| `invalid_wcs` | the solver claimed success but its WCS is missing or malformed | 1 |

The overall status is the most informative attempt outcome (`invalid_wcs`, then `timeout`, then
`unsolved`, then `solver_error`); per-attempt statuses are always recorded.

Too few usable sources (fewer than `--min-sources`, default 10) is reported as an error before
any solver run. Invalid options exit with status 2.

### Python API

```python
from astroidentify import preprocess_image
from astroidentify.astrometry import plate_solve, save_astrometry_outputs
from astroidentify.config import AstrometryConfig
from astroidentify.detection import detect_sources

detection = detect_sources(preprocess_image("data/raw/field.png"))
solution = plate_solve(detection, AstrometryConfig())
solution.raise_for_status()                      # domain exception unless solved
solution.geometry.centre                         # SkyPosition(ra_deg, dec_deg)
solution.wcs                                     # astropy.wcs.WCS (use origin=0)
save_astrometry_outputs(solution, detection, "outputs/field-astrometry")
```

### Known limitations (Milestone 3)

- A local Astrometry.net installation and index data are required; nothing is downloaded
  automatically.
- The solve can only succeed at scales covered by the installed index files.
- Correspondences, residuals and verification counts come from Astrometry.net's own match
  (`--corr`/`--match`). Independent catalogue cross-matching is a later milestone.
- Distortion is modelled by the solver's SIP polynomial (default order 2). On the benchmark
  the SIP correction is about 1.7″ at the corners and residuals show no strong field
  dependence, so no extra distortion model is used.
- Residuals (median about 1.2″) are dominated by saturated-core centroid scatter, because the
  index stars are the saturated ones.
- A fully blind search over all installed index scales is slow when the field's index stars
  are not in the list; camera-derived `--scale-low/--scale-high` bounds speed solving up a lot.
- Debugging tools for this milestone live in `scripts/m57_astrometry_diagnostics.py`
  (non-production; its known-position runs use a diagnostic-only oracle).
- Object identification is **not** implemented: the WCS says where the image points, not what
  is in it.


## Development

```bash
pytest                       # run the test suite (no Astrometry.net needed)
ruff check . && ruff format --check .

# Optional: end-to-end test against a real Astrometry.net (builds a synthetic index itself)
ASTROIDENTIFY_SOLVE_FIELD=$(which solve-field) \
ASTROIDENTIFY_BUILD_INDEX=$(which build-astrometry-index) pytest -m integration
```

Tests generate synthetic star fields with fixed seeds, so they need no data files. Put your
own images in `data/raw/` (git-ignored); generated results go in `outputs/` (git-ignored).

```text
src/astroidentify/
    config.py          Preprocessing/Detection/AstrometryConfig (all tunable values, validated)
    exceptions.py      domain-specific errors
    types.py           AstronomyImage, PreprocessingResult, BackgroundEstimate, ...
    logging.py         CLI logging setup (library code only logs, never prints)
    serialization.py   shared JSON/array/output-directory helpers
    cli.py             `astroidentify preprocess | detect | solve ...`
    preprocessing/
        loader.py      JPEG/PNG/FITS -> AstronomyImage (the only format-specific code)
        background.py  background level, noise, pixel-to-pixel noise
        normalize.py   percentile normalization
        preview.py     display-only preview rendering
        pipeline.py    preprocess_image() / preprocess()
        outputs.py     artifact writing and metadata.json serialization
    detection/
        plane.py       detection plane (grayscale or channel mean)
        background.py  local background and RMS maps (photutils Background2D)
        detector.py    DAOFIND candidates, supplementary peaks, FWHM estimation
        saturation.py  saturation level/mask and saturated-core consolidation
        measurements.py aperture photometry, SNR, correlated-noise factor, flags
        filtering.py   rejection rules and reason codes
        overlay.py     diagnostic overlay and map visualizations
        outputs.py     sources.csv/json, detection_metadata.json, maps
        pipeline.py    detect_sources() / detect_image()
        types.py       Source, DetectionResult, coordinate convention
    astrometry/
        selection.py   quality tiers + grid-balanced source selection
        xylist.py      XYLS writer and the single canonical <-> solver pixel conversion
        solver.py      local solve-field boundary (prerequisites, command, subprocess)
        wcs.py         WCS loading, pixel <-> sky (origin 0), centre/corners/scale/orientation
        diagnostics.py correspondences, residuals, solver match statistics
        overlay.py     source_selection.png and wcs_overlay.png
        outputs.py     artifacts and plate_solution.json
        pipeline.py    plate_solve(): deterministic attempt sequence
        types.py       SourceSelection, PlateSolution, WcsGeometry, ...
```

## Roadmap

1. **Image ingestion and preprocessing** (done)
2. **Source/star detection** (done)
3. **Astrometric plate solving / WCS** (implemented; needs local Astrometry.net + index data)
4. Catalogue matching (Gaia, SIMBAD)
5. Annotation and identification
6. Evidence/confidence estimation
7. CV/ML verification
8. Solar-system objects
9. FastAPI backend
10. Web frontend and deployment
