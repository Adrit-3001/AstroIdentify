We are continuing the AstroIdentify project.

Read `CLAUDE.md` completely before changing anything. Treat it as the project's engineering contract.

Milestone 1 is complete and stable. Preserve all existing preprocessing behavior and tests unless a verified defect blocks the current milestone.

We are now implementing:

# Milestone 2 — Astronomical Source / Star Detection

The sole objective is:

> Given the output of the existing preprocessing pipeline, produce a reliable machine-readable set of stellar source candidates with accurate pixel coordinates and useful quality measurements, plus diagnostic visualizations showing what was detected and rejected.

Do not implement:

- astrometric plate solving;
- WCS solving;
- Gaia or SIMBAD queries;
- astronomical object identification;
- M57 recognition;
- ML or neural networks;
- Solar System ephemerides;
- FastAPI;
- frontend code;
- web deployment.

Stop after source detection.

---

## First: inspect the current repository

Before coding:

1. inspect the existing project structure;
2. inspect the current preprocessing API and dataclasses;
3. inspect the CLI architecture;
4. inspect existing tests;
5. inspect the real benchmark image:
   `data/raw/M57__Ring_Nebula-eQuinox-20260925-003755.png`
6. inspect the Milestone 1 outputs for that image if present, especially:
   - `metadata.json`
   - `processed.npy`
   - preview image

Use the real metadata file in the repository as the authoritative result rather than relying on copied text if it exists.

Briefly state what exists and what you plan to add.

---

## Important benchmark facts

The real M57 Unistellar image is:

- 2560 × 1920
- RGB
- 8-bit PNG
- processed internally as float32

Milestone 1 measured approximately:

- background level: 9.333
- global noise sigma: 3.954
- neighboring-pixel/difference noise sigma: 1.048
- red background: 8
- green background: 8
- blue background: 12
- background rejection fraction: ~7.3%
- fraction at minimum: ~2.35%
- fraction at maximum: ~0.12%

Important warning:

`global noise sigma is much larger than the pixel-to-pixel noise, indicating large-scale background structure`

Therefore:

> Do NOT use the Milestone 1 global scalar background/noise as the sole detection threshold.

Milestone 2 must introduce local/spatial background estimation.

---

# 1. Architecture

Extend the existing package cleanly.

A reasonable structure is:

```text
src/astroidentify/
    detection/
        __init__.py
        plane.py
        background.py
        detector.py
        measurements.py
        filtering.py
        overlay.py
        pipeline.py
```

You may adjust this if the existing codebase suggests something cleaner.

Core detection logic belongs in importable library modules.

The CLI should call library functions rather than contain detection logic.

The main public interface should conceptually resemble:

```python
result = detect_sources(preprocessing_result, config)
```

The detector must consume the existing Milestone 1 preprocessing result instead of independently reloading and preprocessing the image.

---

# 2. Detection Plane

Create a documented 2-D detection plane.

For grayscale input:

```text
2-D image -> detection plane
```

For RGB input:

```text
RGB image
    ↓
documented channel combination
    ↓
2-D detection plane
```

A channel mean or justified luminance-style combination is acceptable initially.

Requirements:

- preserve original RGB data;
- do not mutate preprocessing arrays;
- isolate this transformation cleanly;
- output a finite 2-D float array;
- document the exact conversion;
- keep the implementation easy to replace later.

Do not modify the Milestone 1 normalization pipeline merely to create the detection plane.

---

# 3. Local Background and RMS

Implement spatial/local background estimation.

`photutils.background.Background2D` is the preferred starting point unless there is a concrete reason not to use it.

A reasonable implementation may use:

- `Background2D`
- `SigmaClip`
- `MedianBackground`
- robust RMS estimation

The output should conceptually expose:

```python
background_map
background_rms_map
background_subtracted
```

Requirements:

- configurable box/tile size;
- configurable filter/interpolation behavior where justified;
- sensible defaults;
- finite outputs;
- graceful handling of small images;
- no hard-coded tuning specific to M57.

The background map and RMS map should be saved or otherwise inspectable for debugging.

---

# 4. Stellar Candidate Detection

Use a mature astronomical detector.

`photutils.detection.DAOStarFinder` is a good default.

If another algorithm is chosen, explain why.

Detection must operate on the locally background-subtracted detection plane.

Expose important parameters through config, including at least concepts equivalent to:

- detection sigma
- expected FWHM

Choose sensible defaults.

Do not bury unexplained magic numbers.

The detector should aim to:

- recover faint usable stars;
- avoid large numbers of obvious noise detections;
- tolerate some elongation/distortion;
- handle processed consumer-telescope images;
- provide coordinates suitable for future plate solving.

---

# 5. Source Measurements

For each candidate, export at least:

```text
source_id
x
y
peak
flux or comparable brightness
snr
```

Where meaningful, also expose:

```text
fwhm
sharpness
roundness / ellipticity
local background
local noise
saturation flag
edge flag
accepted/rejected state
rejection reasons
```

Do not invent measurements that are not actually supported by the detector or image data.

Coordinate convention must remain:

```text
x increases left -> right
y increases top -> bottom
origin corresponds to the image array
row zero displayed at top
```

This convention must be documented and tested.

---

# 6. Saturation

The M57 PNG contains some pixels at the source maximum of 255.

Bright saturated stars should:

- be detected when possible;
- be flagged as saturated;
- not be automatically discarded solely because they are saturated.

Use source metadata / nominal maximum where available instead of assuming 255 for all formats.

---

# 7. Shape Robustness

The benchmark image includes:

- slightly elongated stars;
- edge distortion;
- processed source shapes;
- saturated stellar cores.

Do not assume every star is a perfect circular Gaussian.

Shape measurements may inform quality filtering, but do not make filtering so aggressive that real stars near the field edges disappear.

---

# 8. Filtering

Filtering must be a separate, explainable step.

Keep rejection reasons.

Example:

```json
{
  "source_id": 42,
  "accepted": false,
  "rejection_reasons": [
    "low_snr",
    "too_close_to_edge"
  ]
}
```

Initially prefer conservative filtering.

For future plate solving, retaining some imperfect genuine stars is usually better than eliminating half the field.

---

# 9. M57 Is Not the Detection Target

The central Ring Nebula is visible in the benchmark image.

Do not:

- identify it;
- label it M57;
- add special-case logic for it;
- optimize specifically to make it disappear or appear.

If it is detected as a source candidate, record that honestly.

Our objective is the surrounding stellar field.

The future plate solver should determine the sky location without being told the target.

---

# 10. Outputs

Extend the CLI with a command conceptually like:

```bash
astroidentify detect \
    data/raw/M57__Ring_Nebula-eQuinox-20260925-003755.png \
    --output outputs/m57-detection
```

It may invoke preprocessing internally if that fits the existing architecture.

Produce machine-readable and diagnostic artifacts similar to:

```text
outputs/m57-detection/
    sources.csv
    sources.json
    detection_metadata.json
    detected_sources.png
    background_map.npy
    background_rms.npy
```

A slightly different structure is acceptable if it fits the existing artifact system better.

Do not duplicate large arrays unnecessarily.

---

# 11. Diagnostic Overlay

Generate a visual overlay aligned exactly with the original image coordinate system.

It should distinguish:

- accepted sources;
- rejected candidates;
- saturated sources;
- optionally edge-flagged sources;
- optionally source IDs.

The overlay must allow visual inspection of whether:

- obvious stars were found;
- noise was incorrectly accepted;
- saturated stars were retained;
- edge/distorted stars were handled reasonably;
- the central nebula caused problematic detections.

Do not resize/crop in a way that breaks coordinates unless an explicit tested transform is stored.

---

# 12. Detection Metadata

Generate summary diagnostics including:

```text
raw candidate count
accepted count
rejected count
saturated count
edge-flagged count
median SNR
median FWHM if meaningful
background-map min/median/max
RMS-map min/median/max
detector configuration
warnings
```

Do not force the system toward a predetermined source count.

We do not yet know the ground-truth number of stars in this image.

---

# 13. Testing

Preserve every Milestone 1 test.

Add deterministic Milestone 2 tests for:

## Detection plane

- grayscale input
- RGB input
- correct shape
- finite output
- no source-array mutation

## Background model

Use synthetic images with:

- flat constant background;
- known gradient;
- known noise;
- injected bright stars.

Verify the recovered background is reasonably close to the known synthetic truth.

## Source detection

Use synthetic star fields with known coordinates.

At minimum test:

- isolated Gaussian stars;
- stars of different brightness;
- noisy background;
- near-edge stars;
- saturated star;
- slightly elongated star if practical.

Verify recovered coordinates within a reasonable pixel tolerance.

## Filtering

Test:

- low-SNR candidate;
- edge candidate;
- saturation flag;
- rejection reasons.

## Outputs

Test:

- JSON serialization;
- CSV output;
- diagnostic overlay;
- coordinate preservation.

## CLI

Test successful runs and expected failure cases.

Avoid brittle exact-count assertions across dependency versions unless justified.

---

# 14. Real M57 Evaluation

After unit tests pass, run the complete detection pipeline on:

`data/raw/M57__Ring_Nebula-eQuinox-20260925-003755.png`

Inspect the resulting overlay yourself.

Do not only report command success.

Explicitly comment on:

- whether obvious stars are being detected;
- whether large amounts of background noise are falsely detected;
- whether saturated stars are handled;
- whether edge/distorted stars remain usable;
- whether the Ring Nebula causes false stellar detections;
- whether the local background model appears sensible.

If the overlay reveals obvious problems, adjust general-purpose configuration/logic and re-run.

Do not overfit to M57.

---

# 15. Plate-Solving Compatibility

The next milestone will consume stellar geometry and brightness.

Design accepted source output so future code can easily do:

```python
plate_solver_input = [
    (source.x, source.y, source.flux)
    for source in detection_result.accepted_sources
]
```

Provide a straightforward way to retrieve sources sorted by brightness.

Do not implement plate solving yet.

---

# 16. Documentation

Update the README with:

- Milestone 2 purpose;
- detection command;
- output artifact descriptions;
- explanation of local background estimation;
- important tuning parameters;
- explanation that source detection is not yet object identification;
- known limitations.

Do not claim AstroIdentify can identify celestial objects yet.

---

# 17. Definition of Done

Milestone 2 is complete when:

1. all existing Milestone 1 tests still pass;
2. new Milestone 2 tests pass;
3. lint/format checks pass;
4. RGB and grayscale images produce a documented 2-D detection plane;
5. local background and RMS maps are produced;
6. stellar source candidates are detected;
7. source coordinates and brightness measurements are exported;
8. saturated and edge sources are flagged;
9. filtering decisions are explainable;
10. an aligned diagnostic overlay is generated;
11. the real M57 image has been run end-to-end;
12. the overlay has been visually inspected;
13. no plate solving, catalogue lookup, object recognition, ML, or web functionality has been implemented.

Stop after Milestone 2.

I will inspect the source overlay and detection metadata before we proceed to astrometric plate solving.

---

# Final response

When finished, report:

1. files created/modified;
2. architecture and detection algorithm used;
3. new dependencies;
4. exact command used on the M57 image;
5. raw candidate / accepted / rejected / saturated / edge counts;
6. local background and noise statistics;
7. test and lint results;
8. observations from visually inspecting the M57 overlay;
9. known limitations;
10. exact paths of:
   - detected source overlay;
   - source CSV/JSON;
   - detection metadata;
   - background/RMS artifacts.

Do not begin Milestone 3.
