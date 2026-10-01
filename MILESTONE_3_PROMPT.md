We are continuing the AstroIdentify project.

Read `CLAUDE.md` completely before changing anything. Treat it as the engineering contract.

Milestones 1 and 2 are complete and stable. Preserve their APIs and behavior unless a verified defect blocks this work.

Implement:

# Milestone 3 — Blind Astrometric Plate Solving / WCS

The objective is:

> Starting from AstroIdentify's detected stellar sources, determine the celestial sky coordinates covered by the image without being told what object the image contains.

This is the first true blind-identification step.

Do not implement catalogue lookup or object identification.

---

## Critical benchmark rule

Use:

`data/raw/M57__Ring_Nebula-eQuinox-20260925-003755.png`

as the real benchmark.

However, treat it as an UNKNOWN FIELD.

The filename contains `M57`, but that information must never enter the solver.

Do not use:

- the target name;
- known M57 RA/Dec;
- Lyra;
- coordinates from a manual lookup;
- filename parsing;
- target labels;
- any human-provided positional hint.

The benchmark should prove that the stellar geometry itself is sufficient to derive WCS.

---

## First inspect the repository and environment

Before coding:

1. inspect the existing preprocessing and detection APIs;
2. inspect the M57 detection artifacts, especially:
   - `sources.csv` / `sources.json`
   - `detection_metadata.json`
   - `detected_sources.png`
3. inspect the CLI and configuration architecture;
4. run/check the existing tests;
5. check whether local Astrometry.net is available, e.g. whether `solve-field` exists;
6. determine whether usable Astrometry.net index files are configured/installed.

Do not auto-download large index datasets.

Briefly tell me what exists, what prerequisites are available, and what you plan to add.

---

## 1. Source selection

The M57 benchmark has 643 accepted sources, including many saturated sources.

Do not simply sort all accepted sources by flux and take the brightest N.

Implement a deterministic source-selection stage for plate solving.

Prefer:

- accepted sources;
- non-edge sources;
- unsaturated sources when sufficient;
- good centroid/shape quality;
- high SNR/brightness;
- broad spatial coverage across the image.

Use a simple explainable strategy, such as quality ranking plus grid-based spatial balancing.

Keep saturated stars available as fallback because their centroids may still be useful.

Expose configuration for:

- maximum/target selected sources;
- minimum required sources;
- spatial grid/coverage policy;
- saturated-source fallback behavior.

Save:

- selected source CSV/JSON;
- a `source_selection.png` overlay showing which detections were chosen.

The next solver stage must consume this selected source set.

---

## 2. Astrometry.net source list

Prefer sending the selected Milestone 2 source positions directly to Astrometry.net rather than allowing Astrometry.net to independently redetect the raster image.

Generate a supported XYLS/FITS-table source list containing at minimum:

- x;
- y;
- flux/brightness.

Important:

- verify Astrometry.net's expected pixel-coordinate convention from the installed/documented interface;
- AstroIdentify's canonical coordinates remain 0-based image-array coordinates;
- isolate any external 0/1-based conversion in one function;
- test that conversion explicitly;
- preserve image width/height;
- make source ordering intentional and deterministic.

Do not guess coordinate conventions.

---

## 3. Local Astrometry.net backend

Implement a local `solve-field` integration.

Use a controlled subprocess call.

Requirements:

- no shell-string interpolation;
- configurable timeout;
- capture stdout/stderr;
- deterministic working directory;
- clear solved/unsolved detection;
- useful error messages;
- cleanup of temporary files unless debug retention is enabled;
- preserve a solver log artifact.

Do not silently call a public web service.

Do not auto-download index data.

If `solve-field` or indexes are missing, implement/test the integration boundary anyway, then report the exact prerequisite and setup needed.

---

## 4. Blind-first solving strategy

The first attempt must be truly blind with respect to sky position.

Allowed information:

- image width/height;
- selected x/y source positions;
- source brightness ordering;
- generic solver controls such as timeout/downstream output paths.

Do not pass RA/Dec hints.

Do not pass object names.

If a blind solve fails, a later fallback may optionally use generic image-scale/FOV bounds only if those come from telescope/camera properties rather than knowledge of the target.

If a scale-constrained fallback is used:

- record that fact;
- record the scale bounds;
- distinguish it from a fully blind solve in metadata.

Never use M57's known coordinates as a fallback.

---

## 5. WCS parsing

On successful solving, parse the resulting WCS with Astropy.

Create a structured plate-solution result containing at least:

- solved status;
- solver backend;
- solver version if available;
- centre RA (degrees);
- centre Dec (degrees);
- image corner celestial coordinates;
- pixel scale (arcsec/pixel);
- field width/height;
- orientation/rotation if derivable;
- parity/handedness if available;
- selected-source count;
- matched-source count if available;
- residual/RMS diagnostics if available;
- runtime;
- blind vs constrained mode;
- constraints used;
- warnings;
- WCS artifact path.

Use optional/null values rather than fabricating unavailable measurements.

---

## 6. Coordinate correctness

This milestone is very sensitive to origin conventions.

AstroIdentify canonical convention:

```text
x increases left -> right
y increases top -> bottom
origin is the image array
integer coordinates are pixel centres
```

Centralize and test all conversion between:

- AstroIdentify source coordinates;
- Astrometry.net source-list coordinates;
- FITS conventions;
- Astropy WCS origin settings.

Add synthetic WCS round-trip tests:

```text
pixel -> sky -> pixel
```

and verify recovery within numerical tolerance.

Do not introduce hidden flips.

---

## 7. Astrometry quality diagnostics

If Astrometry.net can output matched/correspondence data, request and parse it.

Where available, report:

- matched star count;
- median positional residual;
- RMS residual;
- maximum residual;
- match fraction where meaningful.

Do not produce a fake confidence percentage.

This milestone should expose raw astrometric evidence.

---

## 8. Diagnostic visualization

Generate a WCS diagnostic overlay aligned with the original image.

Useful content:

- selected plate-solving stars;
- RA/Dec coordinate grid;
- image-centre marker;
- centre coordinates;
- optionally matched-solver stars if correspondence output is available.

Do not annotate M57 or any object name.

The goal is to visually demonstrate:

> the image now has a celestial coordinate system.

---

## 9. Outputs

Produce a clean artifact set similar to:

```text
outputs/m57-astrometry/
    selected_sources.csv
    selected_sources.json
    source_selection.png
    solution.wcs
    plate_solution.json
    wcs_overlay.png
    solver.log
    correspondences.csv
```

`correspondences.csv` is optional if the installed Astrometry.net outputs do not expose usable correspondence data.

Keep temporary solver files out of the main output unless they are intentionally useful.

---

## 10. CLI

Add a command conceptually like:

```bash
astroidentify solve \
    data/raw/M57__Ring_Nebula-eQuinox-20260925-003755.png \
    --output outputs/m57-astrometry
```

It may invoke the existing preprocessing/detection pipelines internally, but must reuse their library APIs rather than duplicate logic.

The CLI summary should contain something like:

```text
Solved: yes/no
Backend: Astrometry.net
Selected sources: ...
Matched sources: ...
Centre RA: ...
Centre Dec: ...
Pixel scale: ... arcsec/pixel
Field: ... × ...
Orientation: ...
Residual: ...
Runtime: ...
Mode: blind / scale-constrained
WCS: ...
Overlay: ...
```

Do not output object names.

---

## 11. Tests

Preserve every Milestone 1 and Milestone 2 test.

Add tests for:

### Source selection

- accepted-only behavior;
- edge deprioritization;
- unsaturated preference;
- saturated fallback;
- deterministic ranking;
- spatial distribution;
- too-few-sources behavior.

### XYLS/source-list writer

- image width/height;
- x/y fields;
- flux ordering;
- external coordinate-origin conversion;
- canonical coordinates remain unchanged.

### Solver boundary

The normal unit tests must NOT require real Astrometry.net index files.

Use stubs/mocks/fake solver outputs to test:

- command construction;
- binary missing;
- timeout;
- non-zero exit;
- unsolved output;
- malformed/missing WCS output;
- successful solve;
- log persistence.

### WCS parser

Use synthetic WCS headers to test:

- centre coordinates;
- corners;
- pixel scale;
- field size;
- orientation where possible;
- pixel/sky round trip;
- explicit origin handling.

### Outputs/CLI

Test JSON serialization, overlays, selected source exports, and user-facing errors.

---

## 12. Real integration run

After unit tests and lint pass, perform a REAL local Astrometry.net run on the M57 benchmark if prerequisites are installed.

This is mandatory for full Milestone 3 completion.

Before solving, confirm:

- `solve-field` executable exists;
- usable Astrometry.net index files are configured.

If they are missing:

- do not fake success;
- do not silently use the web;
- give me exact install/configuration steps;
- report the milestone as `integration-blocked`.

If available:

1. run the source-selection stage;
2. perform a blind source-list solve;
3. parse WCS;
4. inspect the WCS overlay;
5. inspect match/residual diagnostics if available;
6. confirm no target-name/coordinate hint was used.

---

## 13. README

Update README with:

- what plate solving means;
- Astrometry.net dependency;
- index-data requirement;
- setup instructions;
- `astroidentify solve` command;
- blind-solving behavior;
- source-selection behavior;
- output files;
- failure modes;
- explanation that catalogue/object identification is still a future milestone.

Do not claim the program identifies M57 yet.

If the solve succeeds, the correct claim is:

> AstroIdentify can determine the celestial coordinates of an unknown astronomical image.

---

## Definition of Done

Milestone 3 is complete only when:

1. all previous tests remain green;
2. new Milestone 3 tests pass;
3. lint/format checks pass;
4. source selection is quality-aware and spatially distributed;
5. solver-compatible source-list export is verified;
6. local Astrometry.net integration works;
7. WCS parsing works;
8. origin/convention tests pass;
9. astrometric outputs and diagnostics are generated;
10. missing solver/index prerequisites fail clearly;
11. a real blind solve of the M57 benchmark succeeds;
12. no target name or target coordinate was supplied;
13. no Gaia/SIMBAD/object identification was implemented.

If the integration is blocked only by local solver/index prerequisites, stop and tell me exactly what to install rather than moving ahead.

Do not begin Milestone 4.

---

## Final response

When finished, report:

1. files created/modified;
2. architecture;
3. source-selection strategy;
4. Astrometry.net availability/version/index status;
5. exact benchmark command;
6. selected source count and selection statistics;
7. whether the solve was fully blind or constrained;
8. solved centre RA/Dec;
9. pixel scale;
10. field of view;
11. orientation/parity if available;
12. matched source count and residual metrics if available;
13. runtime;
14. tests/lint results;
15. visual observations from `source_selection.png` and `wcs_overlay.png`;
16. known limitations;
17. exact paths to generated artifacts.

If the real solve could not run, clearly state `integration-blocked` and give the prerequisite setup commands.

Stop after Milestone 3.
