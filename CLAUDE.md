# CLAUDE.md — AstroIdentify

## 1. Project Overview

AstroIdentify is an extensible astronomy/computer-vision project that will eventually accept an astronomical image with little or no user-provided context, determine where in the sky the image was taken, identify catalogued objects in the field, annotate the image, and provide interpretable evidence/confidence for its identifications.

The project must be developed incrementally. Each milestone should produce a working, testable system and should not prematurely implement later milestones.

### Development roadmap

1. ✅ Image ingestion and preprocessing
2. 🚧 Astronomical source/star detection
3. ⬜ Astrometric plate solving / WCS
4. ⬜ Catalogue matching
5. ⬜ Object annotation and identification
6. ⬜ Evidence/confidence estimation
7. ⬜ Computer-vision / ML verification
8. ⬜ Solar-system object support
9. ⬜ FastAPI backend
10. ⬜ Web frontend and deployment

The current focus is **Milestone 2 only**.

---

## 2. Completed Milestones

### Milestone 1 — Image Ingestion and Preprocessing

Completed.

The existing pipeline supports:

- JPEG/JPG
- PNG
- FITS
- grayscale and RGB inputs
- float32 internal image representation
- FITS metadata preservation
- robust global background estimation
- robust global noise estimation
- per-channel diagnostics
- neighboring-pixel/difference-noise diagnostics
- percentile normalization without mandatory clipping
- preview generation
- structured metadata
- CLI usage
- automated tests

Milestone 1 behavior should remain backward-compatible unless a verified defect requires a change.

The existing preprocessing pipeline is considered stable. Do not redesign it unless a concrete bug blocks the current milestone.

---

## 3. Current Milestone: Milestone 2 — Astronomical Source / Star Detection

The objective is to take the output of the Milestone 1 preprocessing pipeline and identify reliable astronomical source/star candidates for future astrometric plate solving.

Milestone 2 should:

1. Create a consistent 2-D detection plane from grayscale or RGB inputs.
2. Estimate spatial/local background and background RMS.
3. Subtract the local background for detection purposes.
4. Detect likely stellar sources using established astronomical algorithms.
5. Measure accurate pixel centroids.
6. Measure brightness and useful source-shape information.
7. Estimate source SNR where meaningful.
8. Flag saturated sources.
9. Flag edge sources and other potentially unreliable candidates.
10. Apply conservative, explainable filtering.
11. Export machine-readable source measurements.
12. Generate diagnostic overlays showing accepted and rejected candidates.
13. Preserve exact coordinate correspondence with the original image.
14. Produce output suitable for the future plate-solving milestone.

Do **not** implement:

- astrometric plate solving;
- WCS solving;
- Gaia or SIMBAD catalogue queries;
- astronomical object identification;
- Ring Nebula / M57 recognition;
- ML or neural-network classification;
- Solar System ephemerides;
- FastAPI;
- frontend/web development;
- deployment.

Design for future milestones, but stop after source detection.

---

## 4. Engineering Principles

### Modularity

Keep astronomy logic separated by responsibility.

Prefer small modules with explicit interfaces over large scripts.

The project should continue toward a structure such as:

```text
src/astroidentify/
    preprocessing/       # Milestone 1 — stable
    detection/           # Milestone 2 — current
    astrometry/          # future
    catalogs/            # future
    annotation/          # future
    confidence/          # future
    models/              # future
```

Only `detection/` should receive substantial new functionality during Milestone 2.

### Library first, CLI second

Core functionality belongs in importable Python modules.

CLI commands should call library functions instead of containing business logic.

Bad:

```python
# CLI contains detection implementation
```

Good:

```python
result = detect_sources(preprocessing_result, config)
```

and the CLI simply exposes that functionality.

### No premature ML

Do not use neural networks where established astronomical/image-processing methods are sufficient.

Milestone 2 requires no ML.

### Preserve scientific information

Detection should operate on scientifically meaningful array data, not on cosmetically stretched previews.

A display overlay may use contrast stretching for visualization, but measurements must come from the aligned scientific data.

### Reproducibility

Processing should be deterministic unless randomness is explicitly required.

Configuration values must be explicit and testable.

### Type safety

Use Python type hints throughout public interfaces.

Prefer `pathlib.Path` over raw path strings internally.

### Error handling

Fail with clear domain-specific errors.

Examples:

- invalid detection-plane dimensions
- unsupported source array shape
- local background estimation failure
- invalid detection configuration
- output path failure

Do not silently swallow errors.

### Logging

Use Python's `logging` module.

Library code must not print directly to stdout.

CLI code may display concise user-facing summaries.

### Avoid unnecessary abstraction

Build interfaces that support future milestones, but do not construct unused plugin systems, dependency-injection frameworks, databases, cloud services, web infrastructure, or ML frameworks.

---

## 5. Python and Dependencies

Target modern Python, preferably Python 3.11+.

Current scientific dependencies may include:

- numpy
- astropy
- Pillow
- photutils
- matplotlib where justified

Development dependencies include:

- pytest
- ruff

Do not introduce PyTorch, TensorFlow, scikit-learn, web frameworks, databases, or frontend dependencies during Milestone 2.

Do not add OpenCV unless there is a concrete technical reason.

---

## 6. Milestone 1 Input Contract

Milestone 2 must consume the existing Milestone 1 preprocessing result instead of independently reloading or reinterpreting source files.

The preprocessing result already provides:

- original image metadata
- original dimensions
- float32 scientific array
- normalized array
- valid-pixel mask information
- background diagnostics
- per-channel diagnostics
- source metadata
- normalization parameters

Milestone 2 should build on this output cleanly.

Avoid duplicate image loading or duplicate preprocessing logic.

---

## 7. Detection Plane

JPEG/PNG inputs may be RGB while FITS images may be grayscale.

Create a clearly documented 2-D `detection_plane`.

For grayscale:

```text
2-D scientific image
    ↓
detection plane
```

For RGB:

```text
RGB scientific image
    ↓
documented channel combination
    ↓
2-D detection plane
```

A channel mean or justified luminance-style combination is acceptable initially.

Requirements:

- preserve the original RGB data;
- do not mutate Milestone 1 arrays;
- isolate detection-plane generation in its own module/function;
- document the exact combination rule;
- keep the implementation easy to replace later;
- output a finite 2-D floating-point array.

Do not implement complex colour science unless necessary.

---

## 8. Local Background and Noise Estimation

Milestone 1 intentionally computes global diagnostics, but real telescope images can contain gradients, dense stars, nebulosity, and spatially varying background.

Milestone 2 must introduce spatial/local background estimation.

`photutils.background.Background2D` is a strong default candidate.

A reasonable implementation may use:

- `Background2D`
- `SigmaClip`
- `MedianBackground`
- a robust RMS estimator

The result should conceptually expose:

```python
background_map
background_rms_map
background_subtracted
```

Requirements:

- configurable box/tile size;
- sensible defaults;
- configurable interpolation/filter behavior where justified;
- finite outputs;
- graceful handling of small images;
- no hard-coded tuning specific to a single benchmark image.

The local background model should be visualizable for debugging.

Do not use the Milestone 1 global scalar background/noise as the sole detection threshold.

---

## 9. Source Detection

Use a mature astronomical source-detection technique.

`photutils.detection.DAOStarFinder` is an appropriate default unless there is a concrete reason to choose another method.

Detection should operate on the local-background-subtracted detection plane.

Expose important detector parameters through configuration, including at minimum concepts equivalent to:

- detection sigma
- expected FWHM

Choose sensible defaults, but do not bury unexplained magic numbers in implementation code.

The detector should aim to:

- recover faint but usable stars;
- avoid large numbers of noise detections;
- tolerate some elongation and non-ideal source shapes;
- work on real consumer-telescope imagery;
- produce coordinates suitable for future plate solving.

---

## 10. Source Measurement Contract

Each detected candidate should expose stable machine-readable measurements such as:

- source ID
- x centroid
- y centroid
- peak value
- flux or comparable brightness measure
- SNR where meaningful
- FWHM estimate where available
- sharpness
- roundness / ellipticity where available
- local background
- local noise
- saturation flag
- edge flag
- accepted/rejected state
- rejection reasons

Do not invent measurements that are not scientifically meaningful.

Future code should be able to obtain a brightness-ranked source list without reprocessing the image.

---

## 11. Coordinate Convention

Coordinate consistency is critical for future WCS and annotation work.

Use and document:

```text
origin: image array
x increases left → right
y increases top → bottom
row zero is displayed at top
```

No hidden image flipping, resizing, or cropping may occur between measurement and annotation unless an explicit tested transform is stored.

The diagnostic overlay must remain pixel-aligned with source coordinates.

---

## 12. Saturated and Edge Sources

Bright stars may contain saturated pixels.

Saturated stars should:

- be detected when possible;
- be flagged as saturated;
- not automatically be discarded solely because of saturation.

Use source metadata / nominal maximum where available rather than assuming every image saturates at 255.

Edge sources should also be flagged when their measurements may be unreliable.

Filtering should remain conservative because plate solving benefits from retaining many genuine stars.

---

## 13. Accepted vs Rejected Candidates

Filtering must be a distinct, explainable step.

Each rejected source should retain one or more reasons such as:

```json
{
  "accepted": false,
  "rejection_reasons": [
    "low_snr",
    "too_close_to_edge"
  ]
}
```

Do not make filtering opaque.

Initially prefer conservative thresholds over aggressive pruning.

The goal is not a perfectly clean scientific source catalogue. The goal is a robust set of positional stellar candidates for future plate solving.

---

## 14. Benchmark Image

Use the real Unistellar observation already present in the repository as the primary Milestone 2 integration benchmark:

```text
data/raw/M57__Ring_Nebula-eQuinox-20260925-003755.png
```

Its Milestone 1 metadata showed approximately:

```text
Dimensions: 2560 × 1920
Format: PNG
Channels: 3
Background level: 9.333
Global noise sigma: 3.954
Difference/pixel-to-pixel noise sigma: 1.048
R background: 8
G background: 8
B background: 12
Background rejected fraction: ~7.3%
Fraction at minimum: ~2.35%
Fraction at maximum: ~0.12%
```

Important diagnostic:

```text
global noise sigma is much larger than pixel-to-pixel noise,
indicating large-scale background structure
```

This benchmark demonstrates why local background estimation is required.

Do not optimize specifically for this image.

Do not identify or special-case M57.

The central nebula is not the detection target. The surrounding stellar field is.

---

## 15. Output Artifacts

A successful detection run should produce machine-readable source results and visual diagnostics.

A reasonable output structure is:

```text
outputs/<name>/
    sources.csv
    sources.json
    detection_metadata.json
    detected_sources.png
    background_map.npy
    background_rms.npy
```

The exact structure may evolve if the existing artifact system suggests a cleaner design.

Do not duplicate large arrays unnecessarily.

The detection metadata should include:

- detector configuration;
- number of raw candidates;
- number accepted;
- number rejected;
- number saturated;
- number edge-flagged;
- summary SNR statistics;
- summary FWHM statistics where meaningful;
- background-map statistics;
- RMS-map statistics;
- warnings.

---

## 16. Diagnostic Overlay

Generate an image overlay aligned exactly with the source image.

It should visually distinguish:

- accepted sources;
- rejected candidates;
- saturated sources;
- optionally edge-flagged sources;
- optionally source IDs.

The overlay is a debugging and evaluation artifact.

It should make obvious whether:

- real stars are being found;
- noise is being falsely accepted;
- saturated stars are handled;
- edge/distorted stars are retained appropriately;
- extended objects are causing inappropriate detections.

Do not resize/crop in a way that breaks coordinates unless an explicit transform is stored and tested.

---

## 17. CLI

Extend the CLI with a detection command conceptually similar to:

```bash
astroidentify detect \
    data/raw/M57__Ring_Nebula-eQuinox-20260925-003755.png \
    --output outputs/m57-detection
```

It may internally invoke preprocessing if that fits the current architecture.

The command should print a concise summary such as:

```text
Input: ...
Candidates: ...
Accepted: ...
Rejected: ...
Saturated: ...
Edge flagged: ...
Median SNR: ...
Median FWHM: ...
Overlay: ...
Sources: ...
```

Do not dump full source arrays to stdout.

---

## 18. Tests

Milestone 2 is not complete without automated tests.

All Milestone 1 tests must continue to pass.

Add deterministic tests for:

### Detection plane

- grayscale input
- RGB input
- correct 2-D shape
- finite output
- no mutation of source arrays

### Background model

Use synthetic images with:

- flat constant background
- known gradient
- known noise
- injected bright stars

Verify the recovered background is reasonably close to synthetic truth.

### Detection

Use synthetic star fields with known positions.

Test:

- isolated Gaussian stars
- stars of different brightness
- noisy background
- stars near edges
- saturated star
- slightly elongated star where practical

Verify coordinate recovery within a reasonable tolerance.

### Filtering

Test:

- low-SNR candidate
- edge candidate
- saturation flag
- accepted/rejected reasons

### Outputs

Test:

- JSON serialization
- CSV output
- diagnostic overlay
- coordinate preservation

### CLI

Test success and expected failure behavior.

Do not write brittle tests that depend on exact source counts across dependency versions unless scientifically justified.

---

## 19. Real-Image Evaluation Requirement

After unit tests pass, run the full Milestone 2 pipeline on the M57 Unistellar image.

Inspect the overlay visually.

Do not merely report that the command completed.

Explicitly evaluate:

- whether obvious stars are detected;
- whether large amounts of background noise are falsely detected;
- whether saturated stars are handled;
- whether edge/distorted stars remain useful;
- whether the central Ring Nebula creates false stellar detections;
- whether the local background map appears sensible.

If the overlay reveals obvious problems, adjust general-purpose detection logic/configuration and re-run.

Do not overfit to the M57 image.

---

## 20. Future Plate-Solving Compatibility

The next milestone will consume source geometry and brightness.

Future code should be able to do something equivalent to:

```python
sources = detection_result.accepted_sources

plate_solver_input = [
    (source.x, source.y, source.flux)
    for source in sources
]
```

Coordinate quality and brightness ranking are more important during Milestone 2 than detailed astrophysical classification.

Do **not** implement plate solving yet.

---

## 21. Coding Style

- Write readable Python over clever Python.
- Keep functions focused.
- Prefer descriptive names.
- Avoid giant classes.
- Avoid deep inheritance.
- Document non-obvious astronomy/math decisions.
- Keep configuration centralized.
- Keep constants out of implementation code where they may need tuning.
- Avoid duplicate conversion/detection logic.
- Use comments to explain *why*, not restate *what* the code says.

---

## 22. How to Work on Tasks

When implementing a task:

1. Inspect the existing repository first.
2. Preserve functioning Milestone 1 code unless a verified defect requires change.
3. State assumptions when an implementation choice materially affects behavior.
4. Implement the smallest coherent solution.
5. Add/update tests with the implementation.
6. Run relevant tests.
7. Fix regressions before stopping.
8. Update README when user-facing usage changes.
9. Run the real M57 benchmark.
10. Inspect the resulting diagnostic overlay.
11. Summarize exactly what changed and any remaining limitations.

Do not claim tests passed unless they were actually run successfully.

---

## 23. Current Definition of Done

Milestone 2 is complete when:

1. all Milestone 1 tests still pass;
2. grayscale and RGB inputs produce a documented 2-D detection plane;
3. a spatial background map is calculated;
4. a spatial background RMS/noise map is calculated;
5. stellar source candidates are detected;
6. accurate source centroids are exported;
7. useful brightness measurements are exported;
8. saturated sources are flagged;
9. edge/unreliable sources can be flagged;
10. filtering decisions are explainable;
11. accepted and rejected sources are exported in machine-readable form;
12. an aligned diagnostic source overlay is generated;
13. synthetic source-detection tests pass;
14. the real Unistellar M57 image has been processed and visually inspected;
15. lint/format checks pass;
16. no plate solving, catalogue matching, object recognition, ML, or web functionality has been implemented.

Stop there.

The next milestone will use the detected stellar coordinates for astrometric plate solving.
