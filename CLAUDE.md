# CLAUDE.md — AstroIdentify

## 1. Project Overview

AstroIdentify is an extensible astronomy/computer-vision project that will eventually accept an astronomical image with little or no user-provided context, determine where in the sky the image was taken, identify catalogued objects in the field, annotate the image, and provide interpretable evidence/confidence for its identifications.

Development is milestone-based. Each milestone must leave the repository in a working, tested state and must not prematurely implement later milestones.

### Development roadmap

1. ✅ Image ingestion and preprocessing
2. ✅ Astronomical source/star detection
3. 🚧 Astrometric plate solving / WCS
4. ⬜ Catalogue matching
5. ⬜ Object annotation and identification
6. ⬜ Evidence/confidence estimation
7. ⬜ Computer-vision / ML verification
8. ⬜ Solar-system object support
9. ⬜ FastAPI backend
10. ⬜ Web frontend and deployment

The current focus is **Milestone 3 only**.

---

## 2. Completed Milestones

### Milestone 1 — Image Ingestion and Preprocessing

Completed and stable.

The existing pipeline supports:

- JPEG/JPG
- PNG
- FITS
- grayscale and RGB inputs
- float32 scientific image representation
- FITS metadata preservation
- global and per-channel background/noise diagnostics
- neighboring-pixel/difference-noise diagnostics
- percentile normalization without mandatory clipping
- preview generation
- structured metadata
- CLI usage
- automated tests

Do not redesign Milestone 1 unless a verified defect blocks current work.

### Milestone 2 — Astronomical Source / Star Detection

Completed and stable.

The existing detection pipeline provides:

- 2-D detection-plane construction
- spatial background and RMS estimation
- DAOStarFinder-based stellar candidate detection
- FWHM estimation
- supplementary recovery of broad/bright sources
- source centroid and brightness measurements
- correlated-noise-aware SNR diagnostics
- saturation handling and saturated-core merging
- edge flags
- explainable filtering with rejection reasons
- machine-readable source CSV/JSON
- coordinate-exact detection overlay
- local background/RMS artifacts
- CLI usage
- automated tests

The real Unistellar M57 benchmark produced:

- 2,464 candidates
- 643 accepted sources
- 1,821 rejected candidates
- 129 saturated candidates
- 116 accepted saturated sources
- median accepted SNR ≈ 15.6
- estimated field FWHM ≈ 9.29 px
- local background median ≈ 9.53
- local RMS median ≈ 3.78

Milestone 2 behavior should remain backward-compatible unless a verified defect requires change.

---

## 3. Current Milestone: Milestone 3 — Blind Astrometric Plate Solving / WCS

The objective is to determine **where an arbitrary astronomical image is on the celestial sphere** using the stellar source positions produced by Milestone 2.

The system must not be told the target name or expected coordinates.

For the benchmark image, the filename contains `M57`; this must never be used as a hint. Treat the image as an unknown star field.

Milestone 3 should:

1. Select a high-quality, spatially distributed subset of accepted stellar detections.
2. Convert that subset into a plate-solver-compatible source list.
3. Integrate a mature astrometric solver, preferably local Astrometry.net `solve-field`.
4. Produce a valid WCS solution when solving succeeds.
5. Parse and expose:
   - image centre RA/Dec;
   - celestial coordinates of image corners;
   - pixel scale;
   - field of view;
   - orientation/rotation;
   - parity if available;
   - solver runtime and status;
   - match/residual quality information where available.
6. Preserve exact pixel-coordinate conventions from Milestone 2.
7. Save the WCS and structured plate-solution metadata.
8. Generate visual diagnostics that demonstrate the WCS is attached to the image.
9. Fail clearly when the solver, required index data, or a valid solution is unavailable.
10. Keep plate-solving logic isolated so another backend could be added later without rewriting the rest of the pipeline.

Do **not** implement:

- Gaia queries;
- SIMBAD queries;
- object-name lookup;
- identification of M57 or any other object;
- catalogue annotation;
- object confidence scoring;
- ML or neural networks;
- Solar System ephemerides;
- FastAPI;
- frontend/web work;
- deployment.

Stop after a verified WCS solution.

---

## 4. Engineering Principles

### Library first, CLI second

Core functionality belongs in importable Python modules.

CLI commands must call library APIs rather than contain plate-solving logic.

Conceptually:

```python
selection = select_plate_sources(detection_result, config)
solution = solve_astrometry(selection, image_shape, config)
```

### Preserve existing contracts

Milestones 1 and 2 are stable dependencies.

Do not duplicate preprocessing or detection.

Do not independently redetect stars inside the astrometry module unless an explicitly documented fallback is later approved.

### Blind solving first

The primary benchmark must not use:

- object names;
- known target coordinates;
- coordinates inferred from filenames;
- manually entered M57 coordinates;
- target labels;
- a human-selected “this is Lyra/M57” hint.

Generic solver configuration such as image dimensions and source flux ranking is allowed.

If an image-scale/FOV bound is later used as a fallback, it must be generic telescope/camera information rather than target information, and the metadata must record that the solve was scale-constrained rather than fully blind.

### Reproducibility

Source selection, solver command construction, and metadata generation must be deterministic for the same input/configuration.

### Type safety

Use typed public interfaces and dataclasses where appropriate.

### Error handling

Use clear domain-specific failures for:

- no accepted sources;
- too few usable sources;
- plate solver executable unavailable;
- Astrometry.net index data unavailable/misconfigured;
- solver timeout;
- solver process failure;
- solver returns unsolved;
- malformed/missing WCS output;
- invalid WCS transform.

Do not silently fall back to fabricated coordinates.

### Logging

Library code uses logging, not direct printing.

The CLI may print concise user-facing status.

### Avoid unnecessary abstraction

A small backend boundary around the external plate solver is appropriate.

Do not create a general plugin framework, web service layer, database, distributed job queue, or ML infrastructure.

---

## 5. Recommended Package Structure

Continue toward a structure such as:

```text
src/astroidentify/
    preprocessing/       # Milestone 1 — stable
    detection/           # Milestone 2 — stable
    astrometry/          # Milestone 3 — current
        __init__.py
        selection.py
        xylist.py
        solver.py
        wcs.py
        diagnostics.py
        outputs.py
        pipeline.py
        types.py
    catalogs/            # future
    annotation/          # future
    confidence/          # future
    models/              # future
```

Adjust this only where the existing architecture justifies a cleaner organization.

---

## 6. Plate-Solving Backend

Prefer local **Astrometry.net** `solve-field`.

Why:

- it is a mature blind astrometric solver;
- it can solve from source lists rather than requiring AstroIdentify to re-detect stars;
- it produces standard WCS output;
- it fits the project's goal of explainable astronomy tooling.

The implementation should call the local solver through a controlled subprocess boundary.

Requirements:

- detect whether `solve-field` is available;
- construct arguments safely without shell-string interpolation;
- enforce a configurable timeout;
- capture stdout/stderr;
- preserve useful solver logs in diagnostics;
- use a temporary/work directory rather than cluttering the repository;
- clean temporary files unless configured to keep them for debugging;
- detect solved/unsolved status from actual solver outputs, not from string guessing alone.

Do not auto-download large Astrometry.net index datasets without explicit user action.

If index files are missing, fail clearly and document exactly what the user must install/configure.

Do not silently switch to a public web API.

---

## 7. Source Selection for Plate Solving

Do not blindly send the brightest N accepted detections.

The M57 benchmark contains many saturated stars and some edge/distorted sources.

Create a deterministic plate-solving selection policy that prefers:

- accepted sources;
- non-edge sources;
- unsaturated sources when sufficient;
- reasonable source shape;
- reliable centroid;
- high SNR/brightness;
- broad spatial coverage across the image.

Saturated sources may remain available as fallback geometry because their centroids can still be useful.

The selection algorithm should avoid concentrating all selected stars in the brightest/densest portion of the frame.

A grid-based or other simple spatial-balancing strategy is acceptable.

Expose useful configuration such as:

- target/max number of selected sources;
- grid dimensions or coverage policy;
- whether saturated sources are allowed as fallback;
- minimum selected-source count.

Persist the selected source set as an artifact for debugging.

---

## 8. Astrometry.net Source List / XYLS Contract

Prefer passing Milestone 2 detections to Astrometry.net as a source list rather than asking Astrometry.net to independently detect sources from the raster image.

Create an XYLS/FITS-table writer or another officially supported source-list representation.

The source list should contain at minimum:

- x coordinate;
- y coordinate;
- brightness/flux suitable for ranking.

Important:

- verify Astrometry.net's coordinate convention from the installed/documented interface;
- isolate any 0-based ↔ 1-based conversion in exactly one well-tested location;
- store AstroIdentify's canonical coordinates unchanged in its own artifacts;
- preserve image width and height;
- verify that the output source ordering is the intended brightness/quality ordering.

Do not guess the external solver's coordinate convention.

---

## 9. WCS Result Contract

A successful solution should expose a typed result containing at least:

- solved: bool
- solver backend/name/version if available
- centre RA in degrees
- centre Dec in degrees
- image corner celestial coordinates
- pixel scale in arcsec/pixel
- field width/height in degrees or arcminutes
- orientation/rotation in degrees
- parity/handedness if available
- WCS header / serialized WCS artifact path
- selected-source count
- matched-source count if available
- residual/RMS quality information if available
- runtime
- whether solving was fully blind or constrained
- any scale/position constraints used
- warnings

If a value cannot be reliably derived, use `null`/optional fields rather than inventing it.

---

## 10. WCS Coordinate Convention

AstroIdentify's canonical pixel convention remains:

```text
x increases left → right
y increases top → bottom
origin corresponds to the image array
integer pixel coordinates refer to pixel centres
```

Astropy WCS APIs may use an explicit origin parameter.

All conversions between AstroIdentify pixel coordinates, FITS conventions, Astropy WCS conventions, and Astrometry.net inputs must be centralized and tested.

Do not allow hidden flips or off-by-one shifts.

---

## 11. WCS Diagnostics

Generate diagnostics sufficient to inspect whether the solution is plausible without doing catalogue identification.

Useful diagnostics may include:

- a coordinate-grid overlay on the original image;
- labelled RA/Dec grid lines;
- image centre marker and centre coordinates;
- corner coordinates;
- selected plate-solving sources;
- matched plate-solving sources if Astrometry.net correspondence output is available.

The diagnostic overlay must preserve pixel alignment.

Do not annotate object names.

---

## 12. Match / Residual Quality

If Astrometry.net can emit correspondence/match information for the solved field, parse it.

Prefer objective diagnostics such as:

- number of matched stars;
- median or RMS positional residual;
- maximum residual;
- fraction of selected stars participating in the match, where meaningful.

Do not invent a percentage “confidence” in this milestone.

A later milestone will handle calibrated confidence/evidence.

For now report raw solution-quality measurements.

---

## 13. Solver Fallback Strategy

The default attempt should be blind and source-list based.

A reasonable deterministic fallback sequence may be:

1. preferred unsaturated/non-edge spatially distributed source subset;
2. larger source subset;
3. allow suitable saturated sources;
4. optional generic image-scale/FOV bounds if configured from telescope/camera information.

Do not use target coordinates or object identity as fallback hints.

Record every attempted configuration and which attempt solved the field.

Avoid uncontrolled repeated solver calls.

---

## 14. Benchmark Image

Use the existing real Unistellar image as the primary integration benchmark:

```text
data/raw/M57__Ring_Nebula-eQuinox-20260925-003755.png
```

Its filename reveals the target, but the solver pipeline must ignore that semantic information.

The authoritative Milestone 2 outputs should be used, including the accepted source catalogue and detection metadata.

For the blind benchmark:

- no RA/Dec hint;
- no object-name hint;
- no manually supplied M57 location;
- no filename parsing.

The success condition is a valid WCS solution produced from image geometry/source detections.

Do not begin catalogue matching after the solve.

---

## 15. Output Artifacts

A successful astrometry run should produce artifacts similar to:

```text
outputs/<name>-astrometry/
    selected_sources.csv
    selected_sources.json
    source_selection.png
    solution.wcs
    plate_solution.json
    wcs_overlay.png
    solver.log
    correspondences.csv      # if available
```

The exact structure may differ if the existing artifact system suggests something cleaner.

Do not expose temporary Astrometry.net implementation files as public API unless useful for debugging.

---

## 16. CLI

Add a command conceptually similar to:

```bash
astroidentify solve \
    data/raw/M57__Ring_Nebula-eQuinox-20260925-003755.png \
    --output outputs/m57-astrometry
```

It may run preprocessing and detection internally if needed by the current CLI architecture, but it must reuse those library pipelines and contracts.

The CLI should print a concise result such as:

```text
Solved: yes
Backend: Astrometry.net
Selected sources: ...
Matched sources: ...
Centre RA: ...
Centre Dec: ...
Pixel scale: ... arcsec/pixel
Field of view: ... × ...
Orientation: ...
Residual: ...
Runtime: ...
WCS: ...
Overlay: ...
```

Do not print object names.

---

## 17. Tests

All Milestone 1 and Milestone 2 tests must continue to pass.

Add deterministic Milestone 3 tests.

### Source selection

Test:

- only accepted sources are used by default;
- edge sources are deprioritized/excluded as configured;
- unsaturated sources are preferred;
- fallback can include saturated sources;
- selection is brightness/quality aware;
- selection is spatially distributed;
- selection is deterministic;
- insufficient-source behavior is clear.

### XYLS/source-list export

Test:

- correct image dimensions;
- correct x/y columns;
- correct brightness ordering;
- external coordinate-convention conversion;
- no mutation of canonical source coordinates.

### Solver subprocess boundary

Do not require real Astrometry.net indexes for the normal unit-test suite.

Use a fake/stub executable or mocked subprocess results to test:

- command construction;
- timeout;
- process failure;
- unsolved result;
- solved result;
- missing output files;
- log capture.

### WCS parsing

Use synthetic known WCS headers to test:

- centre world coordinates;
- corner coordinates;
- pixel scale;
- FOV;
- orientation where derivable;
- pixel → sky → pixel round trips;
- explicit origin behavior;
- absence/malformed WCS errors.

### Outputs

Test:

- plate-solution JSON serialization;
- selected-source exports;
- overlay coordinate alignment;
- solver-log persistence.

### CLI

Test success and failure behavior without depending on a network service.

---

## 18. Integration Test with Real Astrometry.net

The ordinary unit-test suite must not require external index data.

However, Milestone 3 is not complete until a separate real integration run is attempted on the M57 benchmark using an actual Astrometry.net installation.

Before the integration run:

1. check whether `solve-field` is installed;
2. identify whether usable index data is configured;
3. if either is missing, report the exact missing prerequisite and installation/configuration steps;
4. do not claim Milestone 3 is end-to-end complete until the real solve succeeds.

If prerequisites are available, perform the blind solve and inspect the resulting WCS diagnostics.

---

## 19. Documentation

Update the README with:

- Milestone 3 purpose;
- Astrometry.net prerequisite;
- index-data requirement;
- exact solver command;
- explanation of blind solving;
- explanation of source selection;
- output artifacts;
- WCS fields;
- failure modes;
- statement that object identification/catalogue lookup is intentionally not implemented yet.

Do not claim AstroIdentify knows what object is in the image.

At this stage it only knows where the image points on the sky.

---

## 20. Coding Style

- Write readable Python over clever Python.
- Keep functions focused.
- Prefer descriptive names.
- Avoid giant classes.
- Avoid deep inheritance.
- Document non-obvious astrometric/FITS coordinate decisions.
- Keep configuration centralized.
- Use comments to explain *why*.
- Never hide coordinate-origin conversion in unrelated code.

---

## 21. How to Work on Tasks

When implementing Milestone 3:

1. Inspect the existing repository and current Milestone 1/2 APIs.
2. Inspect the real M57 detection artifacts.
3. Check local Astrometry.net availability and configuration.
4. Design the smallest clean astrometry package.
5. Implement and test source selection.
6. Implement and test source-list export.
7. Implement the local solver boundary.
8. Implement WCS parsing and diagnostics.
9. Run the full existing test suite.
10. Run lint/format checks.
11. Attempt the real blind M57 integration solve.
12. Inspect the WCS diagnostic output.
13. Report prerequisites accurately if the real solve cannot run.
14. Stop before catalogue lookup or object identification.

Do not claim a real solve succeeded unless it actually did.

---

## 22. Current Definition of Done

Milestone 3 is complete when:

1. all Milestone 1 and 2 tests still pass;
2. quality-ranked spatially distributed source selection is implemented;
3. selected sources can be exported in a verified solver-compatible format;
4. Astrometry.net integration is implemented through a controlled local subprocess;
5. missing solver/index prerequisites fail clearly;
6. solved/unsolved/timeout/process-failure states are handled;
7. WCS output is parsed into structured metadata;
8. pixel/world coordinate conventions are tested;
9. centre coordinates, corners, pixel scale, FOV, and orientation are reported where derivable;
10. match/residual diagnostics are parsed when available;
11. selected-source and WCS diagnostic overlays are generated;
12. the normal test suite does not require network access or Astrometry.net index data;
13. a real blind integration solve on the Unistellar benchmark succeeds;
14. the benchmark solve uses no target name or target-coordinate hint;
15. lint/format checks pass;
16. no catalogue matching, object identification, ML, or web functionality has been implemented.

If the only blocker is missing local Astrometry.net/index prerequisites, implement and test the software boundary but explicitly report Milestone 3 as **integration-blocked**, not complete.

Stop there.

The next milestone will use the WCS to query astronomical catalogues and determine what known objects are present in the field.

Milestone 3 Status
Milestone 3 remains in progress. The software boundary, WCS parser, source-list writer, source-selection logic, CLI, and tests exist, but the real Unistellar benchmark has not yet produced a valid WCS solution. Do not begin Milestone 4.
The current task is a focused Milestone 3 integration-debugging subphase, not a new milestone.
Observed Real-Benchmark Failures
Benchmark image:
data/raw/M57__Ring_Nebula-eQuinox-20260925-003755.png
Observed results so far:
1. AstroIdentify XYLS, 100 preferred sources, Tycho-2 only, fully blind: timed out at 600 s.
2. AstroIdentify XYLS after installing 2MASS 06, 07 and 08-19, fully blind: timed out at 600 s. Logs confirm 2MASS indexes were searched.
3. Direct AstroIdentify XYLS solve with camera scale 0.5–1.2 arcsec/pixel and no position hint: did not solve.
4. Direct raw-PNG solve with Astrometry.net native extraction and camera scale 0.5–1.2 arcsec/pixel: simplexy found 16,479 sources and still did not solve within the test budget.
5. Direct AstroIdentify XYLS solve with camera scale 0.75–0.95 arcsec/pixel: processed the full 100-source input and did not solve. A weak false hypothesis near 0.937 arcsec/pixel was rejected; no WCS was written.
These failures mean the problem must now be diagnosed systematically rather than by installing random additional indexes or simply extending blind timeouts.
Temporary Diagnostic Exception: Known Sky Position
The production AstroIdentify solver must remain blind to sky position.
However, during this debugging subphase only, a known approximate sky position may be supplied to raw solve-field diagnostic commands or isolated diagnostic scripts to determine why the production blind solve fails.
For the M57 benchmark, use only as a diagnostic oracle:
RA ≈ 283.396 degrees
Dec ≈ +33.029 degrees
Requirements:
- do not add this position to default configuration;
- do not parse it from the filename;
- do not store it as production benchmark input;
- do not expose a target-name-based solve path in the product;
- do not call a position-constrained diagnostic a Milestone 3 success;
- final Milestone 3 completion still requires solving without a sky-position hint.
Interpretation:
known-position solve succeeds
    -> image/index/source geometry can be solved;
       investigate blind search, ranking, source subset, scale or timeout strategy

known-position solve fails
    -> investigate source coordinates, image transformation/distortion,
       index compatibility, source extraction or scale assumptions
Debugging Rules
- Do not make broad code changes until a controlled experiment identifies the failure mode.
- Change one variable at a time.
- Do not hard-code M57-specific behavior.
- Keep diagnostic target knowledge outside the production solver path.
- Do not call an online plate-solving API unless explicitly requested by the user.
Required Diagnostic Matrix
A. Verify environment
Record:
- solve-field path/version;
- Astrometry.net config used;
- installed index families/scales;
- exact indexes attempted.
Do not add more index packages unless evidence shows a missing scale.
B. Known-position raster diagnostic
Run Astrometry.net directly on the original PNG using the diagnostic-only RA/Dec, a generous radius, and a broad plausible camera scale such as 0.3–2.5 arcsec/pixel.
Answer:
- does the raster solve when the sky region is known?
- if yes, what is the actual pixel scale, FOV, orientation, parity and residual quality?
- does the solved scale agree with prior assumptions?
C. Known-position AstroIdentify XYLS diagnostics
Using the same diagnostic position, compare at minimum:
- current spatially balanced 100;
- top 100 accepted by brightness without spatial balancing;
- top 200 accepted;
- top 400 accepted where feasible;
- a variant that includes suitable saturated stars;
- high-SNR ordering if materially different from flux ordering.
Preserve exact source IDs and XYLS for each run.
D. Coordinate-convention diagnostic
If the raster solves but XYLS fails, explicitly verify the real-binary coordinate transform.
AstroIdentify canonical coordinates are:
x increases left -> right
y increases top -> bottom
0-based pixel centres
Do not assume the existing +1 mapping is correct merely because synthetic tests passed. Verify X origin, Y origin, Y direction, image-height conversion and parity/reflection with controlled real-binary diagnostics. Temporary flipped XYLS variants are allowed for diagnosis only.
E. Source-ranking diagnostic
Compare:
- current spatially balanced order;
- pure flux order;
- high-SNR order;
- inclusion/exclusion of saturated stars;
- larger source counts.
Revise production selection only if evidence shows a general advantage.
F. Scale diagnostic
If any position-constrained diagnostic solves, use the WCS-derived scale as the authoritative empirical scale for this processed export. Record pixel scale and FOV, and compare against 0.5–1.2 and 0.75–0.95.
G. Distortion/processed-image diagnostic
If raster and XYLS behave differently, investigate resampling, stacking, digital upscaling, nonlinear warping, edge-dependent centroid shifts and relevant Astrometry.net SIP/tweak behavior. Do not add distortion fitting without evidence.
Solver Scheduling Requirement
The current attempt strategy must not allow attempt 1 to consume the entire pipeline budget and prevent later attempts from running.
After root cause is understood, implement:
- separate per-attempt and total solve budgets;
- timeout of one attempt proceeds to the next appropriate fallback;
- scale-constrained attempt runs early when the user explicitly supplies scale bounds;
- metadata records each attempt, status and runtime;
- timeout, normal unsolved, solver error and solved are distinct states.
Production Success Rule
Diagnostic solves using known RA/Dec do not count as Milestone 3 completion.
Milestone 3 is complete only when the M57 benchmark produces a valid WCS with:
- no target-name hint;
- no RA/Dec hint;
- no filename-derived position;
- no M57-specific behavior.
A generic camera-derived scale constraint is allowed and must be recorded. Final mode may be fully blind or position-blind, camera-scale-constrained.
Diagnostic Artifacts
Keep diagnostics separate, for example:
outputs/m57-astrometry-diagnostics/
    run_manifest.json
    raster_known_position/
    xyls_current_known_position/
    xyls_flux100_known_position/
    xyls_flux200_known_position/
    xyls_flux400_known_position/
    xyls_with_saturated_known_position/
Each run records exact args, source policy/count, scale bounds, position-hint usage, timeout, result, runtime, WCS fields and log path.
Debugging Definition of Done
This subphase is complete when:
1. the failure mode is identified with evidence;
2. necessary general-purpose fixes are made only after diagnosis;
3. regression tests cover the discovered issue;
4. all previous tests remain green;
5. lint/format checks pass;
6. the real benchmark is retried without a sky-position hint;
7. a valid WCS is produced, or the remaining blocker is documented precisely.
Do not begin catalogue matching until this is resolved.