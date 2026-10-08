# AstroIdentify

AstroIdentify aims to take an astronomical image with little or no context, work out where in
the sky it points, identify catalogued objects in the field, annotate the image and explain how
confident it is in each identification.

The project is built in milestones:

| Milestone | Status |
| --- | --- |
| 1. Image ingestion and preprocessing | complete |
| 2. Stellar source detection | complete |
| 3. Blind plate solving (WCS) | complete |
| 4. Catalogue matching (Gaia DR3) | complete |
| 4.1 Saturated-star astrometric centroids | complete |
| 5. Object annotation and identification (SIMBAD) | complete |
| 6. Evidence and confidence assessment | complete |
| 6.1 Object inspection / field visualization | complete |
| 7+. ML verification, Solar System, API, frontend | not started |

With a local Astrometry.net installation, AstroIdentify determines where an unknown image
points on the sky from its detected stars alone. It then associates the detected point sources
with Gaia DR3 stars, and lists and annotates the catalogued non-stellar objects (nebulae,
galaxies, clusters, ...) that the WCS places in the image. It reports catalogue presence and raw
image measurements, and grades each identification with a transparent, rule-based support
level (not a probability).

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
   saturation alone. The plateau centroid is biased on asymmetric PSFs; step 8 corrects it.
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

8. **Astrometric centroids** (Milestone 4.1). Every source also gets an astrometric centroid,
   `astrometric_x/astrometric_y`, which plate solving and catalogue matching use. It equals
   `x/y` for everything except saturated cores, which are corrected by the image's own
   isophote calibration (see [Milestone 4.1](#milestone-41-saturated-star-astrometric-centroids)).
   `x/y` stay the detection centroid used for photometry.

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
rejection_reasons, astrometric_x, astrometric_y, astrometric_method,
astrometric_correction_px`. Values are in detection-plane units (source pixel values, channel mean
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
detection.xy_flux()                # (N, 3) astrometric x, y and flux: plate-solver input
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
- The index stars are the saturated ones, so the solution depends on saturated-star centroids.
  Since Milestone 4.1 the source list uses the isophote-calibrated astrometric centroid. On the
  benchmark this lowered the solver's residual from 1.22″ to 0.66″ median and the WCS offset
  for typical (unsaturated) stars from about 4.2″ to 1.6″. Most of the remaining offset is a
  translation of about 1.6 px at the image centre (down from 4.7 px). This is consistent with
  the residual bias of the largest saturated cores, which the calibration does not fully remove.
- A fully blind search over all installed index scales is slow when the field's index stars
  are not in the list; camera-derived `--scale-low/--scale-high` bounds speed solving up a lot.
- Debugging tools for this milestone live in `scripts/m57_astrometry_diagnostics.py`
  (non-production; its known-position runs use a diagnostic-only oracle).
- Object identification is **not** implemented: the WCS says where the image points, not what
  is in it.


## Milestone 4: catalogue matching (Gaia DR3)

Catalogue matching associates the accepted Milestone 2 detections of a plate-solved image with
**Gaia DR3** stars. The result is a set of point-source correspondences with measured
residuals, for example "these 614 detections are these Gaia DR3 sources, with a median offset
of 0.67″". It is **not** object identification: nothing is named, and no target is inferred.
That is the next milestone.

```bash
astroidentify catalog-match data/raw/M57__Ring_Nebula-eQuinox-20260925-003755.png \
    --plate-solution outputs/m57-astrometry/plate_solution.json \
    --wcs outputs/m57-astrometry/solution.wcs \
    --detections outputs/m57-detection/sources.json \
    --output outputs/m57-catalog
```

It consumes the saved Milestone 2/3 products (no re-detection or re-solving). It checks that
the image, detections and plate solution share the same image SHA-256 and size, and that the
plate solution is solved and its WCS is valid. Benchmark result (live Gaia query):

```text
Catalogue: Gaia DR3 (live, 69.4 s)
Query cone: centre (283.38646, +33.02704) deg, radius 23.36'
Rows returned: 22477
Rows in image: 13170 (1929 eligible by brightness)
Accepted detections: 643
Input WCS check: median offset 4.14" over 473 registration pairs; refined WCS used
Matches: 614 (95.5% of detections, 31.8% of eligible catalogue stars)
Median residual: 0.668" (0.78 px)
RMS residual: 0.982" (1.15 px); max 2.966"
Unmatched: 29 detections, 1315 eligible catalogue stars
Epoch propagation: no (no reliable observation timestamp)
```

### Method

1. **Query region from the WCS.** A cone centred on the WCS image centre whose radius reaches
   the farthest image corner, plus a 30″ margin. This safely covers any rotated or flipped
   footprint without RA wrap problems. Rows are then filtered *exactly*: each is projected
   through the WCS (Astropy, `origin=0`, canonical pixels; the Astrometry.net `+1` applies only
   to its source lists) and kept only if it falls inside the image and projects back
   consistently.
2. **Gaia DR3 provider.** Standard IVOA TAP synchronous ADQL query to the ESA Gaia archive
   (`gaiadr3.gaia_source`), parsed with Astropy's VOTable reader; no extra client dependency
   and no scraping. Columns: `source_id, ra, dec, ra_error, dec_error, phot_g/bp/rp_mean_mag,
   pmra, pmdec, parallax, ref_epoch`. There is no `TOP N`; `MAXREC` is an explicit
   `--row-limit` (200,000), and reaching it (TAP `OVERFLOW`) is an error, never a silently
   incomplete field. Missing values stay null.
3. **Epoch.** Gaia positions are at J2016.0. Proper motion is applied (Astropy
   `apply_space_motion`) only when a reliable timestamp exists: `--observation-date`, FITS
   `DATE-OBS`/`MJD-OBS`, or EXIF `DateTimeOriginal`. Filenames are never parsed. The benchmark
   PNG has no usable timestamp, so catalogue-epoch positions are used and recorded as such.
4. **Eligibility.** Only the brightest `3 × N_detections` in-image Gaia stars are eligible. A
   catalogue to G≈21 mostly adds faint stars the image cannot show, which can only create chance
   coincidences. On the benchmark this cut the estimated chance matches (by shifting the
   catalogue 60″) from 66 to 8 while losing 3 genuine matches.
5. **WCS refinement.** Mutually nearest, isolated, unsaturated detection/Gaia pairs within 12″
   (through the input WCS) are used to refit a TAN WCS with Astropy `fit_wcs_from_points`, with
   4σ clipping. Why: a plate solution fitted to a few bright, saturated stars can carry a
   zero-point bias (about 4″ on the benchmark; see Milestone 3 limitations). The input WCS is
   never modified. The refined one is written as `refined_solution.wcs`, and the input-WCS
   offset is reported. Distortion terms gave no held-out improvement on the benchmark, so the
   default is plain TAN (`--no-refine` disables refinement).
6. **One-to-one matching** within `--match-radius` (default **3″**, about 3.5 px at
   0.857″/px). Candidate pairs are processed in ascending angular separation, ties broken by
   Gaia `source_id` then detection `source_id`; a pair is accepted if neither side is taken.
   Deterministic, one-to-one, closer pairs win conflicts. The radius is about 3× the refined
   median residual. Widening to 4″ adds about as many chance matches as real ones.

All accepted Milestone 2 detections are used (643 on the benchmark), not just the 100 plate-
solving stars, and every match keeps both the detection `source_id` and the Gaia `source_id`.

### Outputs

```text
outputs/<name>-catalog/
    catalog_query.json          service, release, cone, exact ADQL, columns, row limit,
                                truncation, live/cache origin, query time, epoch handling
    gaia_sources.csv            every returned row + projected x/y, in_image, eligible,
                                matched_detection_source_id
    catalog_matches.csv         one row per match: both IDs, observed/predicted x/y, residual
                                px/arcsec, catalogue and projected RA/Dec, G/BP/RP, parallax,
                                pmra/pmdec, detection flux/SNR/saturated/edge, candidate count
    catalog_match_summary.json  input provenance (paths, SHA-256, plate-solution mode), matching
                                configuration, metrics, WCS-refinement diagnostics, unmatched
                                detection IDs, warnings
    refined_solution.wcs        catalogue-refined WCS (if refinement ran)
    catalog_overlay.png         coordinate-exact overlay: green = matches, cyan = eligible Gaia
                                positions, grey = unmatched detections, orange = residual
                                vectors magnified 20×, G-magnitude labels on the brightest
```

Metrics: rows returned / in image / eligible, detections considered, matches, detection and
catalogue match fractions, median/RMS/max residual (arcsec and px), mean offset, unmatched
counts, detections with more than one candidate, and whether proper motion was applied. No
confidence or identification score is computed.

### Network and cache

A live query needs network access to `gea.esac.esa.int` (about 70 s for the benchmark field).
Responses are cached in `outputs/.catalog-cache/` (`--cache-dir`, `--no-cache`). A cache entry
stores the raw VOTable plus its exact query identity (service, table, ADQL including centre,
radius and columns, and row limit). It is reused only when the identity matches exactly, and
the output records `origin: cache` with the original query time. `--refresh-cache` forces a
live query. Timeouts (`--timeout`), HTTP/service errors, malformed responses and truncation are
distinct errors. "No matches" is a valid result, not an error.

### Known limitations (Milestone 4)

- **The very brightest saturated stars.** Before Milestone 4.1, stars brighter than G≈11 had
  core centroids biased by ~4–5 px (22 of 29 unmatched detections). With the astrometric
  centroid, 111 of 116 accepted saturated stars match (5 unmatched). Cores larger than the
  calibrated isophote range keep the uncorrected centroid.
- Without an observation timestamp, positions are at the Gaia epoch (J2016). High-proper-motion
  stars (above ~0.2″/yr) can then miss the radius after a decade. `--observation-date` enables
  propagation.
- Small residual distortion remains at the image corners (≤ 0.4 px mean per region) with the
  plain-TAN refined WCS.
- Gaia DR3 only; other catalogues would be new providers behind the same interface.
- Named-object identification is Milestone 5 (below), not part of catalogue matching.

## Milestone 4.1: saturated-star astrometric centroids

**Problem.** A saturated star's clipped plateau is the PSF's isophote at the saturation level.
Its centroid equals the star's position only for a point-symmetric PSF. On the benchmark
telescope's comet-shaped PSF (tail toward the upper left), the plateau centroid moves toward
the tail as the star gets brighter: about 0.5 px for 30–100 px cores and up to 5–7 px for the
largest ones. Milestone 3 anchors its WCS on these stars, so it inherited the bias.

**Method** (`detection/astrometric_centroid.py`; image data only, no catalogue at run time):

1. Stack up to 150 bright, isolated, unsaturated, non-edge DAOFIND stars. Each is aligned on
   its DAOFIND centroid (the astrometric reference for all other sources) and normalised by
   flux, then the per-pixel median is taken: the image's own PSF.
2. For 80 isophote levels (0.95 → 0.01 of the peak), record the isophote's area and its
   centroid offset from the PSF centre. Stop once an isophote touches the stack border.
3. For a saturated core of area *A*: `astrometric = core centroid − offset(A)`, interpolated
   and anchored so the smallest (peak) isophote has zero correction.
4. Fallback: no calibration (fewer than 10 suitable stars) or a core larger than the largest
   calibrated isophote → the core centroid is kept, as `astrometric_method =
   saturated_core_fallback`. Nothing is extrapolated.

Matching isophotes by *area* makes this independent of any monotonic tone curve, so it works on
stretched 8-bit images. No thresholds are tuned to a target, and Gaia is not used.

**Data model.** `Source.x/y` (detection centroid, photometry), `centroid_method` and
`source_id` are unchanged. New fields: `astrometric_x`, `astrometric_y`, `astrometric_method`
(`detection` | `isophote_calibrated` | `saturated_core_fallback`) and
`astrometric_correction_px`. Plate-solver selection and XYLS export (`SelectedSource.x/y`, with
an `astrometric_method` column), `DetectionResult.xy_flux()` and Gaia matching use the
astrometric centroid. The calibration curve is recorded under `astrometric_centroid` in
`detection_metadata.json`. `DetectionConfig.astrometric_centroid=False` turns it off. Files
written before 4.1 load with the astrometric centroid equal to `x/y`.

**Evaluation** (offline, `scripts/saturated_centroid_diagnostics.py`). The reference is Gaia
DR3 projected through the Milestone 4 refined WCS, which is fitted to unsaturated stars only.
110 saturated stars have an unambiguous Gaia counterpart. They were split by `source_id`
parity: parameters were chosen on the even IDs and are reported on the 54 odd (held-out) IDs.

| Method | median px | RMS px | p90 px | median dx, dy px | failures |
| --- | --- | --- | --- | --- | --- |
| current plateau centroid | 1.17 | 2.73 | 4.45 | −0.44, −0.43 | 0% |
| unsaturated-wing centre of light | 4.11 | 5.12 | 7.45 | −2.08, −2.65 | 0% |
| masked-core 2-D Gaussian fit | 3.63 | 4.39 | 6.82 | −1.59, −2.51 | 7% |
| wing point-symmetry | 1.28 | 3.05 | 5.12 | −0.54, −0.66 | 87% |
| saturation-depth-weighted core | 1.10 | 2.93 | 4.75 | −0.55, −0.40 | 0% |
| **isophote-calibrated core (production)** | **0.82** | **1.34** | **2.28** | −0.19, +0.08 | 0% |

Wing-based methods do worse because the comet tail dominates the unsaturated wings.

| Held-out core size | n | current median / RMS | isophote median / RMS |
| --- | --- | --- | --- |
| 1–30 px | 21 | 0.46 / 0.93 | 0.50 / 0.98 |
| 30–100 px | 16 | 0.85 / 1.03 | 0.77 / 0.78 |
| ≥ 100 px | 17 | 4.28 / 4.64 | 2.02 / 1.98 |

Unsaturated sources are bit-identical: 0.0 px change on all 472 Gaia-associated ones.

**Effect on Milestones 3 and 4** (benchmark, fully blind, no scale bounds):

| | before | after |
| --- | --- | --- |
| solver residual (median / RMS) | 1.22″ / 2.01″ | 0.66″ / 0.87″ |
| blind WCS vs Gaia-refined WCS, 519 unsaturated stars (median) | 4.89 px (4.19″) | 1.89 px (1.62″) |
| M4 input-WCS median offset | 4.14″ | 1.84″ |
| Gaia matches (of 643) | 614 | 630 |
| saturated matched / unmatched (of 116) | 95 / 21 | 111 / 5 |
| brightest 100 detections matched | 79 | 95 |
| M4 median / RMS residual | 0.668″ / 0.982″ | 0.674″ / 0.977″ |

**Limitations.**

- The calibration assumes the PSF shape (and so the isophote offsets) is constant across the
  field. Field-dependent coma would need a spatially varying calibration, which was not
  attempted.
- Cores larger than the stack's largest isophote (5 FWHM half-width) fall back (1 star on the
  benchmark).
- Small cores (< 30 px) see no improvement: their bias is already below the centroid noise.
- The plateau is defined on the raw channels and the stack on the channel-mean plane. This
  assumes a colour-independent PSF shape.

## Milestone 5: object annotation and identification (SIMBAD)

**Purpose.** List every catalogued named object that the solved WCS places in the image,
keep the scientifically interesting non-stellar ones, record objective image evidence for
each, and draw them on the image.

Here an identification means: *the WCS places this catalogued object at this position in the
image*. Image evidence is recorded separately, and nothing is turned into a confidence or
probability (that is Milestone 6).

The only path to an identification is: image → WCS → WCS-derived sky region → catalogue query
→ pixel projection → filtering/association → annotation. No object name, target coordinate or
filename is ever an input, and the CLI has no such option.

```bash
astroidentify identify data/raw/M57__Ring_Nebula-eQuinox-20260925-003755.png \
    --astrometry outputs/m57-astrometry-centroid-fixed \
    --catalog outputs/m57-catalog-centroid-fixed \
    --output outputs/m57-objects
```

The command reuses the saved Milestone 2–4 products and does not re-run Gaia.

| Option | Effect |
| --- | --- |
| `--wcs FILE` | use this WCS file explicitly |
| `--detections FILE` | Milestone 2 `sources.json` (default: the one the catalogue run used) |
| `--cache-dir DIR` | response cache (default `outputs/.catalog-cache`) |
| `--no-cache` | always query live, store nothing |
| `--offline` | use only the cache; an uncached field is an error, never an empty result |
| `--refresh` | query live and rewrite the cache entry |
| `--max-objects N` | cap on objects drawn (default 40) |
| `--max-labels N` | cap on labels (default 25) |
| `--timeout S` | network time limit |
| `--row-limit N` | maximum rows; reaching it is an error |

**WCS choice.** In order:

1. an explicit `--wcs`;
2. Milestone 4's Gaia-refined `refined_solution.wcs`;
3. Milestone 3's `solution.wcs`.

Neither WCS file is ever modified. The summary records the path, its SHA-256, whether it is
refined, and which artifact it came from. Every saved product that records an image hash
must match the input image.

**Catalogue: SIMBAD (requires network or a cache).**
- **Interface.** CDS SIMBAD TAP (`/sync`, ADQL, VOTable), parsed with Astropy. No scraping
  and no extra dependency.
- **Tables.** One query joins:
  - `basic`: identity, type, position, `galdim_*` angular size, morphology, redshift and
    reference count;
  - `ids`: all identifiers;
  - `otypedef`: type description and hierarchy;
  - `flux`: B and V magnitudes.
- **Query geometry.**
  - A cone from the WCS centre to the farthest image corner, plus 30″.
  - Also any object within a wider circle (cone + 1°) whose catalogued semi-major axis reaches
    into the cone. This means a large object centred outside the frame is still found.
  - Every row is then projected through the WCS (Astropy `origin=0`, canonical pixels; the
    Astrometry.net `+1` does not apply here) and filtered exactly against the image.
- **Completeness.** No type filter is applied at query time: all rows are kept, with the reason
  for any exclusion. `MAXREC` is an explicit row limit, and reaching it is an error. Very deep
  survey fields can hit the limit, so raise `--row-limit` there.
- **Cache.** Shared with Milestone 4. The key is a hash of service, exact ADQL and row limit,
  so another field's response is never reused. Responses that fail to parse are not cached.
  The raw response is also written to the output directory.

**Object-type policy** (`objects/filtering.py`).
- **How categories are assigned.** Categories follow SIMBAD's own type hierarchy
  (`otypedef.path`), plus explicit overrides; planetary nebulae, for example, sit under evolved
  stars in SIMBAD.
- **Retained by default:**
  - planetary nebulae;
  - supernova remnants;
  - nebulae (everything under ISM: H II regions, reflection/dark/diffuse nebulae, clouds);
  - star clusters and associations;
  - galaxies (including AGN and quasars);
  - galaxy pairs, groups and clusters.
- **Excluded, but kept in the tables:**
  - stars, which are Milestone 4's business;
  - moving groups and streams;
  - parts of galaxies;
  - radio, IR, X-ray and UV sources and other non-object types;
  - unknown types.
- SIMBAD "candidate" types (`?`) are retained and flagged.

**Display names** (`objects/naming.py`). Identifiers are normalised by collapsing whitespace.
The preference is Messier → NGC → IC → other common catalogues (Sharpless, Collinder,
Melotte, Trumpler, Berkeley, King, Stock, Abell PN/clusters, Barnard, vdB, LBN, LDN, UGC, MCG,
PGC, PN G) → SIMBAD `main_id`. SIMBAD `NAME` identifiers are reported as `common_names` and
shown in parentheses on the overlay, but are never the primary label. All aliases and the
raw `main_id`/SIMBAD `oid` are kept.

**Angular extent** (`objects/extent.py`).
- **Units.** SIMBAD `galdim_majaxis`/`galdim_minaxis` are full axes in arcmin, and
  `galdim_angle` is the position angle east of north.
- **Drawing.** The ellipse is built on the sky (Astropy `directional_offset_by`) and projected
  through the WCS, so rotation and parity come from the WCS alone.
- **Reliability.**
  - All three values known → ellipse.
  - Only a major axis (or no angle for an elongated object) → circle of the major axis, a
    conservative superset.
  - No size → position marker only. No size is ever invented.
- **Field status.** An object whose centre is outside the frame but whose footprint overlaps it
  is in the field (`extent_overlaps_image`), with the fraction of its footprint inside the image.

**Image association** (`objects/association.py`). The two are recorded separately from
catalogue presence.
- **Compact objects** (no size, or ≤ 10″): the nearest accepted Milestone 2 detection within
  3″, using its astrometric centroid; ties go to the lower `source_id`. Otherwise
  association `none`.
- **Extended objects:**
  - the number of accepted detections inside the footprint;
  - a brightness contrast `(median inside − median of a 1.5–2.5× annulus) / (1.4826 × MAD of
    the annulus)` on the original pixels (channel mean).

  These are raw measurements, not probabilities. `catalogued_in_field` objects can have
  association `none`.

**Outputs** (`outputs/<name>-objects/`):

```text
object_query.json            service, cone + outer radius, ADQL, columns, row limit,
                             live/cache, cache key/path, timestamps, row count
simbad_response.vot          raw SIMBAD response (offline replay/provenance)
catalog_objects.csv / .json  every returned row: identity, aliases, type/category, RA/Dec,
                             projected x/y, field status, extent, magnitudes, redshift,
                             status/exclusion reason (+ projected outlines in JSON)
object_associations.csv      image evidence for retained objects
identification_summary.json  inputs/hashes, WCS used, query, counts by type/category,
                             retained objects, policy, warnings, artifacts
object_overlay.png           annotated image, same size as the input
```

There is no primary-object selection. Retained objects are listed in a deterministic
presentation order: designation rank, then catalogued size, then reference count.

**Benchmark.** With the refined WCS and a live query:
- **Query results.** 206 rows. 131 lie in the field and 21 are retained (19 galaxies, 1
  planetary nebula, 1 star cluster).
- **Central feature.** The extended object near the image centre is catalogued as **M 57**
  (NGC 6720, a planetary nebula, SIMBAD common name "Ring Nebula"). It projects to (1258.8,
  937.1) px, about 30 px from the image centre. The light centroid of the feature measured
  afterwards is 2.6 px from that position, and its ring profile matches the catalogued 1.15′
  diameter (40 px radius). Its brightness contrast is 37.5 σ.
- **Other objects.** These include IC 1296 (a galaxy about 4′ west) and several small LEDA/2MASX
  galaxies. One is a 2° open cluster (PHOC 41) whose centre lies outside the frame and whose
  footprint crosses it.

**Limitations.**
- SIMBAD completeness and size information vary. Many small galaxies have sizes but no
  position angle (drawn as circles), and some have none.
- Very large catalogued footprints (e.g. a 2° cluster) are reported faithfully even though
  they are not visually distinct in a 36′ field.
- The brightness contrast is a simple local statistic. Stars, gradients or a footprint larger
  than the image can affect it, and it is absent when the annulus falls outside the image.
- Magnitudes are SIMBAD's catalogue values (for some nebulae, those of a central star), not
  image photometry.
- Positions are at SIMBAD's epoch (no proper motion; negligible for extended objects).
- Overlay label placement is greedy. In crowded areas some labels are skipped; every object
  is still in the tables.
- No confidence calibration, ML verification or Solar System objects; these are later
  milestones.

## Milestone 6: evidence assessment (support levels)

**Purpose.** For every object Milestone 5 retained, turn the existing measurements into an
explicit, deterministic answer to one question: how well do they support "this catalogued
object is at this place in the image"? No network access is needed. Objects excluded by
the Milestone 5 type policy (for example ordinary stars such as Vega) are not assessed and
stay excluded.

> **Support levels are not probabilities.** They are ordinal, rule-based summaries of
> explicit evidence. No numeric score is produced (`evidence_score` is always null) because
> nothing has been calibrated against ground truth.

| Level | Meaning |
| --- | --- |
| `strong` | strong image evidence, a precise (Gaia-verified) WCS, a confirmed catalogue type, nothing truncated, no competing catalogue object |
| `moderate` | clear image evidence, limited by one documented factor |
| `weak` | some image evidence, or evidence limited by a serious factor |
| `catalogue-only` | the catalogue and WCS place the object here, but the image adds no support (not visible, or not measurable). This is not contradiction |
| `insufficient` | abstention: no astrometric statistics apply, the object is almost entirely off-frame, the ambiguity is severe, or there is no support under a poorly verified WCS |

```bash
astroidentify assess data/raw/M57__Ring_Nebula-eQuinox-20260925-003755.png \
    --objects outputs/m57-objects --output outputs/m57-evidence
```

`--catalog`, `--astrometry` and `--detections` default to the products recorded by the
objects run. Every product's image hash must match the input image.

**Evidence groups** (`src/astroidentify/evidence/`). Each group is used once.

1. **Astrometry** (`astrometry.py`).
   - **Field grade.** precise: ≥ 20 Gaia matches with median residual ≤ 1.5 px. adequate:
     ≥ 6 matches with median ≤ 3 px. Anything worse is poor.
   - **Which residuals apply.** Only residual statistics that describe the WCS actually used
     are applied:
     - Gaia per-star residuals for the refined WCS;
     - the input-WCS offset if the objects used the blind WCS;
     - the plate solver's own residual (at most adequate) if there is no Gaia run;
     - otherwise none, and every object abstains.
   - **Positional scale r50.** This is the median radial residual of matched Gaia stars, so
     half of all true star–detection pairs lie closer than r50. It is local (the 15 nearest
     matches, if they lie within a quarter of the image diagonal), otherwise field-level.
2. **Compact image evidence** (`compact.py`; objects without a size or ≤ 10″).
   - **Normalized offset.** `n = separation / r50` to the nearest accepted detection,
     measured from its astrometric centroid. For Rayleigh-distributed errors n ≤ 1.5 holds
     for 79 % of true pairs ("close") and n ≤ 3 for 99.8 % ("consistent"). 3 < n ≤ 5 is
     "poor"; beyond 5 there is no association.
   - **Chance coincidence.** The expected number of unrelated detections inside the 3 r50
     region (detection density × area). It must be ≤ 0.05 for strong and ≤ 0.3 for
     moderate. A poorer WCS means a larger r50 and a higher chance expectation, so the same
     separation earns less support.
   - **Source-quality caps** (moderate): SNR < 10, an edge flag, or a saturated detection
     without a calibrated centroid.
3. **Extended image evidence** (`extended.py`). No point-source match is required, and
   there is no segmentation or ML.
   - **Visibility class:**
     - mostly visible: ≥ 50 % of the footprint is in the image;
     - truncated: caps at moderate;
     - larger than frame: caps at moderate;
     - mostly outside (centre off-frame and < 10 % visible): abstain.
   - **Contrast.** `(median inside − median of a 1.5–2.5× annulus) / (1.4826 MAD)`, in
     per-pixel noise units. It is gated by the significance of the median excess:

     | Grade | contrast | significance z |
     | --- | --- | --- |
     | strong | ≥ 5 | ≥ 10 |
     | moderate | ≥ 2 | ≥ 5 |
     | weak | ≥ 0.5 | ≥ 3 |

   - **Structure offset.** The light centroid of pixels > 2σ above the annulus is compared
     with the centroid of the in-image part of the footprint, as a fraction of the radius.
     Foreground stars are masked, except a source at the catalogue centre (a nucleus or
     central star). An offset > 0.25 R caps at moderate; > 0.5 R caps at weak.
   - **Placement.** `r50 / radius` > 0.2 caps at moderate; > 0.5 caps at weak.
4. **Catalogue** evidence: a SIMBAD candidate type caps at moderate.
5. **Ambiguity** (`ambiguity.py`).
   - **What competes.** Compact objects whose catalogue positions lie within 3 r50 of each
     other, and extended footprints of comparable size (radius ratio ≤ 3) with one centre
     inside the other.
   - **Indistinguishable** competitors are compact objects within 1.5 r50, or extended
     objects with nearly coincident centres and similar sizes.
   - **Severity caps:** minor → moderate, major → weak, ≥ 3 indistinguishable → abstain.
   - **Nested objects** are recorded as context, not competition (`CONTAINS_SUBSTRUCTURE` /
     `WITHIN_LARGER_OBJECT`). An H II region inside a galaxy does not compete with it.
   - No winner is chosen.

**Aggregation** (`scoring.py`). Image grade → base level, then every applicable cap lowers
it to the weakest level. Caps only ever lower a level, so a smaller offset, a higher contrast,
a better WCS or less ambiguity never reduce support (all tested).

**Not scored, to avoid double counting or priors:**
- Gaia RMS (correlated with the median used);
- the plate-solver residual when Gaia applies;
- the Milestone 5 point-match radius (replaced by the normalized offset);
- detections inside extended footprints (dominated by foreground stars);
- a detection's own Gaia match (it cannot tell a compact galaxy from a star);
- designation rank, reference counts and other popularity priors.

**Missing data.** Missing size, epoch, detections, local residuals, contrast or structure
position stays `null` with a `MISSING_*`/`*_UNAVAILABLE` code, listed in `missing`. It is
never converted into negative evidence. For example, a missing epoch is reported, not
penalized.

**Outputs** (`outputs/<name>-evidence/`):

```text
object_evidence.csv     one row per assessed object: level, grades, key raw/normalized features,
                        ambiguity, caps, missing fields, reason codes, explanation
object_evidence.json    full versioned records (evidence_version 6.0) incl. provenance
evidence_summary.json   counts by level/path/ambiguity, field astrometry, reason-code glossary,
                        config, warnings
evidence_overlay.png    retained objects coloured by support level (image dimensions unchanged)
```

**Benchmark** (M57 field):
- **Counts:** 21 objects assessed: 1 strong, 1 moderate, 3 weak, 15 catalogue-only, 1
  insufficient.
- **The central planetary nebula (M 57) is `strong`.** It rests on a precise WCS (630 Gaia
  matches, local r50 0.92 px), a footprint contrast of 37.5σ (z ≈ 1900), 100 % of the
  extent visible, structure centred to 0.02 radius, and no competitors.
- **IC 1296 is `moderate`** (contrast 2.8σ, centred).
- **Faint LEDA/2MASX/WISE galaxies** are `catalogue-only`.
- **The 2° cluster** whose centre lies off-frame abstains.

**Limitations.**
- **Thresholds.** They are generic and documented in `EvidenceConfig`, but they are not
  calibrated against labelled data, so levels are not probabilities.
- **Objects larger than the frame** (e.g. a large galaxy filling the image) have no
  surrounding annulus. Their contrast is unavailable and they cannot rise above
  `catalogue-only` on image grounds.
- **Contrast** is a simple local statistic. Gradients, scattered light and crowded star
  fields can affect it.
- **Positional scale.** r50 uses Gaia stars and therefore assumes catalogue positions of
  compact objects are as accurate as Gaia's. SIMBAD coordinate errors are not available
  and are not invented.
- **Ambiguity** uses catalogue geometry only. Two catalogue entries for the same physical
  object are reported as competitors.
- **Not in scope here:** ML/CV verification (Milestone 7).

## Milestone 6.1: object inspection (display only)

**Purpose.** Diffuse objects can be hard to see in a raw frame even when they are correctly
identified. `inspect-object` takes one object that Milestone 5 already identified and draws
a stretched view showing exactly where it lies. It does not change any scientific result.

```bash
astroidentify inspect-object data/raw/test_field_03.png \
    --objects outputs/test-field-03-objects \
    --catalog outputs/test-field-03-catalog \
    --object "NGC 7000" \
    --output outputs/test-field-03-ngc7000
```

| Option | Effect |
| --- | --- |
| `--object NAME` | **post-identification selector only** (see below) |
| `--stretch none\|percentile\|asinh` | display stretch (default `asinh`) |
| `--low-percentile P` | black point (default 10) |
| `--high-percentile P` | white point (default 99.9) |
| `--softening A` | asinh softening (default 0.05; smaller lifts faint structure more) |
| `--reference-stars N` | brightest Gaia stars drawn as landmarks (default 15; 0 = none) |
| `--label-reference-stars` | also label unnamed landmarks with their G magnitude |

**Selection.**
- **What is searched.** `--object` is matched only against saved Milestone 5 rows, in tiers:
  display name, then SIMBAD main ID, then aliases (including common names such as
  "North America Nebula").
- **How matching works.**
  - It is exact apart from case and repeated spaces, with no fuzzy matching.
  - Several objects matching in the same tier is an error that lists them.
- **Excluded rows.**
  - Identified (retained) objects are searched first.
  - Only if none matches can an explicit request select a row that Milestone 5 placed in the
    image but excluded by its type policy (`in_field_type_excluded`, e.g. a named star such
    as `--object "Albireo"`).
  - The summary records the row's Milestone 5 status and exclusion reason, and that it was
    explicitly selected. The image carries a note to the same effect.
  - Milestone 5 filtering and every normal overlay stay unchanged, and rows outside the
    image cannot be selected.
- **Isolation.** The name never reaches detection, plate solving, catalogue queries or
  evidence grading.

**Display-only stretch** (`inspection/stretch.py`).
- **What it touches.** The scientific pipeline never imports this module, which a test
  enforces. Output PNGs have the image's exact dimensions, with no resampling or warping.
  The zoom view only enlarges by an integer factor (pixel replication).
- **Modes.**
  - `none`: 8-bit data shown exactly as stored.
  - `percentile`: linear between two percentiles of all channels.
  - `asinh`: Lupton-style and colour-preserving. Luminance is stretched and every channel is
    scaled by the same factor, so star colours are kept.
- **Recording.** Levels and parameters are written to `inspection_summary.json`.

**What is drawn.**
- **The object.** The catalogue centre is projected through the same WCS Milestone 5 used
  (canonical 0-based pixels, no `+1`) and drawn as a magenta open crosshair.
- **The label.** If SIMBAD lists a common name (`NAME` identifier), the label shows it first,
  with the catalogue designation underneath: the display name, plus the main ID if different.
  When there are several common names, mixed-case ones win over all-capitals abbreviations,
  then the most complete (longest), then SIMBAD's order. Without a common name the label is
  the designation over the SIMBAD type. This is presentation only: the stored identity, type
  and naming are unchanged, and the type stays in `inspection_summary.json`.
- **Map aids.** A scale bar, and north/east arrows computed from the WCS (never assumed).
- **Two views.** A full-frame view and a zoom.
- **Extent:**
  - **Catalogued ellipse inside the frame:** the projected outline is drawn, and the zoom
    covers it with a 1.5× margin.
  - **Ellipse crossing the frame edge:** only the visible arcs are drawn, and the summary
    records that the object extends beyond the image.
  - **Ellipse containing the whole frame:** nothing is drawn, so no misleading boundary
    appears inside the image.
  - **No catalogued size:** only the centre marker. The image and summary say explicitly that
    no boundary is claimed (`extent_available: false`). The zoom is then a fixed contextual
    crop, labelled as not a measured object boundary.
- **Reference stars.**
  - **Which stars.** The brightest Gaia DR3 stars (by G) from Milestone 4's
    `gaia_sources.csv`, projected through the same WCS. They are landmarks, not evidence.
    The zoom only uses stars at most 3 mag fainter than the full view's faintest landmark.
  - **Names.** Only names already in the saved SIMBAD star rows are used (common name,
    Bayer/Flamsteed, variable-star name, HD or HIP). Gaia positions are moved back to
    SIMBAD's J2000 epoch with their proper motions for this lookup only.

**Outputs:** `inspection_full.png`, `inspection_zoom.png` and `inspection_summary.json`. The
summary records:
- input image hash;
- the selected object and how it was selected;
- aliases, type, RA/Dec and projected x/y (re-projection checked);
- WCS path and SHA;
- extent metadata and visible fraction;
- stretch parameters and levels;
- reference stars;
- crop bounds and zoom mapping;
- warnings.

Input artifacts are never modified, and writing into an input directory is refused.

**Example: the North America Nebula field.**
- **The row.** NGC 7000 is the SIMBAD row Milestone 5 identified (typed `Cl*`, "Cluster of
  Stars", via its Bermuda Cluster designation). Its aliases include SH 2-117, LBN 373 and
  the common name "North America Nebula".
- **No size.** The row carries no angular size, so the view shows the exact catalogue
  centre and states that no boundary is known from this row.
- **Field coverage.** This 36′ × 27′ frame lies well inside the nebula's roughly 2° region,
  so its glow appears as broad background structure, not as a bounded shape.

**Limitations.**
- **No invented outlines.** Catalogue extents are drawn only when SIMBAD provides them, and
  many large diffuse nebulae have no reliable size or orientation there.
- **Only a visual aid.** A stretch cannot separate faint nebulosity from gradients or
  background, and 8-bit quantization limits how much faint signal can be lifted.
- **Landmark names.** These depend on which star rows the Milestone 5 SIMBAD query returned.

## Development

```bash
pytest                       # run the test suite (no Astrometry.net needed)
ruff check . && ruff format --check .

# Optional: end-to-end test against a real Astrometry.net (builds a synthetic index itself)
ASTROIDENTIFY_SOLVE_FIELD=$(which solve-field) \
ASTROIDENTIFY_BUILD_INDEX=$(which build-astrometry-index) pytest -m integration

# Optional: live Gaia DR3 query (network)
ASTROIDENTIFY_LIVE_GAIA=1 pytest tests/catalogs/test_live_gaia.py

# Optional: live SIMBAD query (network)
ASTROIDENTIFY_LIVE_SIMBAD=1 pytest tests/objects/test_live_simbad.py
```

Tests generate synthetic star fields with fixed seeds, so they need no data files. Put your
own images in `data/raw/` (git-ignored); generated results go in `outputs/` (git-ignored).

```text
src/astroidentify/
    config.py          Preprocessing/Detection/Astrometry/CatalogConfig (all tunable values)
    exceptions.py      domain-specific errors
    types.py           AstronomyImage, PreprocessingResult, BackgroundEstimate, ...
    logging.py         CLI logging setup (library code only logs, never prints)
    serialization.py   shared JSON/array/output-directory helpers
    cli.py             `astroidentify preprocess | detect | solve | catalog-match ...`
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
    catalogs/
        footprint.py   WCS validation, query cone, projection + exact in-image filter
        gaia.py        Gaia DR3 TAP provider (ADQL, VOTable parsing, truncation, cache)
        epoch.py       observation epoch from metadata; proper-motion propagation
        matching.py    eligibility, WCS refinement, deterministic one-to-one assignment
        pipeline.py    load_match_inputs() / match_catalog()
        overlay.py     catalog_overlay.png
        outputs.py     artifacts and JSON/CSV serialization
        types.py       QueryRegion, CatalogQueryResult, CatalogMatch, MatchSummary, ...
```

## Roadmap

1. **Image ingestion and preprocessing** (done)
2. **Source/star detection** (done)
3. **Astrometric plate solving / WCS** (done; needs local Astrometry.net + index data)
4. **Catalogue matching** (done: Gaia DR3; 4.1 saturated-star astrometric centroids)
5. **Annotation and identification** (done: SIMBAD)
6. **Evidence/confidence estimation** (done: rule-based support levels, not probabilities)
7. CV/ML verification
8. Solar-system objects
9. FastAPI backend
10. Web frontend and deployment
