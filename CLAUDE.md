# CLAUDE.md — AstroIdentify

## 1. Project Overview

AstroIdentify is an extensible astronomy/computer-vision project that accepts an astronomical image with little or no user-provided context, determines where the image points on the sky, associates detected image sources with astronomical catalogue entries, and later identifies/annotates astronomical objects with interpretable evidence.

Development is milestone-based. Every milestone must leave the repository in a working, tested state. Do not prematurely implement later milestones.

### Development roadmap

1. ✅ Image ingestion and preprocessing
2. ✅ Astronomical source/star detection
3. ✅ Astrometric plate solving / WCS
4. ✅ Gaia DR3 catalogue matching
4.1. 🚧 Saturated-star centroid calibration
5. ⬜ Object annotation and identification
6. ⬜ Evidence/confidence estimation
7. ⬜ Computer-vision / ML verification
8. ⬜ Solar-system object support
9. ⬜ FastAPI backend
10. ⬜ Web frontend and deployment

The current focus is **Milestone 4.1 only**.

Do not begin Milestone 5 until Milestone 4.1 is complete.

---

## Add this completed Milestone 4 section

### Milestone 4 — Gaia DR3 Catalogue Matching

Completed and stable, subject only to the Milestone 4.1 upstream centroid correction.

The catalogue pipeline now:

- accepts the solved WCS from Milestone 3;
- derives a query region from the image footprint;
- queries Gaia DR3 locally through the implemented TAP client;
- filters returned catalogue sources to the actual image footprint;
- optionally propagates proper motion when an observation epoch is available;
- refines the astrometric solution against Gaia using isolated unsaturated stars;
- matches Gaia stars to accepted image detections one-to-one;
- records angular and pixel residuals;
- writes structured catalogue-query, match and refinement artifacts;
- generates a catalogue-match overlay;
- keeps the original blind `solution.wcs` untouched and stores the catalogue-refined solution separately as `refined_solution.wcs`;
- does not use target names, filename semantics or manually supplied target coordinates.

The real Unistellar benchmark produced:

- 643 accepted detections;
- 614 one-to-one Gaia DR3 matches;
- 95.5% of accepted detections matched;
- median residual about 0.668 arcsec;
- RMS residual about 0.982 arcsec;
- maximum accepted residual about 2.966 arcsec;
- a live Gaia query returning 22,477 rows;
- 13,170 Gaia sources projected inside the image;
- 1,929 catalogue stars eligible for matching.

Milestone 4 does **not** identify named astronomical objects.

Do not redesign the Milestone 4 query or matching architecture unless a verified defect requires it.

---

## Add this section after the completed milestones

# Milestone 4.1 — Saturated-Star Centroid Calibration

## Purpose

Milestone 4.1 is a focused corrective sub-milestone.

The purpose is to improve the astrometric centroid of bright saturated stars produced by Milestone 2 so that:

1. Milestone 3's blind plate solution is not systematically shifted by biased saturated-star centroids;
2. bright Gaia stars can be matched more reliably;
3. Milestone 4's Gaia WCS refinement becomes a small astrometric refinement rather than compensation for a several-arcsecond upstream systematic offset.

This is **not** a redesign of Milestone 2.

This is **not** object identification.

This is **not** Milestone 5.

---

## Observed issue

The current benchmark shows a real upstream astrometric defect:

- Milestone 3's WCS is fitted primarily from bright saturated stars;
- the saturated stars in the Unistellar image have large/clipped, asymmetric or comet-shaped cores;
- the current saturated-core centroid can differ from the astrometric centre by several pixels;
- this creates an approximately several-arcsecond systematic offset for ordinary unsaturated stars;
- Gaia refinement in Milestone 4 removes that shift successfully;
- the original blind WCS remains scientifically valid as a plate solution, but its astrometric zero point is unnecessarily biased by saturated-star centroiding.

The evidence indicates centroid bias changes with saturated-core morphology/size.

Do not treat this as a WCS projection, coordinate-origin, Gaia-catalogue, filename, index-coverage or plate-solver problem unless new evidence contradicts the previous diagnostics.

---

## Scope

Milestone 4.1 may modify only what is required for saturated-star centroid measurement and its direct consumers.

Likely affected areas:

```text
src/astroidentify/detection/
src/astroidentify/astrometry/
tests/detection/
tests/astrometry/
tests/catalogs/
```

Milestone 1 image ingestion should remain unchanged.

Milestone 4 catalogue querying/matching should remain architecturally unchanged unless a small compatibility adjustment is required.

Do not implement named-object catalogues, annotation, confidence scoring, ML, Solar System support, APIs or frontend work.

---

## Scientific requirement

For saturated stars, the centroid estimator should estimate the star's **astrometric centre**, not merely the geometric centre of the clipped plateau.

Candidate approaches may include, but are not limited to:

- centroiding on unsaturated wings only;
- masking saturated pixels and fitting the surrounding PSF;
- robust 2-D Gaussian fitting outside the saturated core;
- robust Moffat-like fitting where justified;
- symmetry-based wing centroiding;
- radial/annular weighting around the saturated region;
- another simple deterministic method supported by evidence.

Do not select a method because it looks sophisticated.

Select it because it improves held-out astrometric residuals without degrading unsaturated-source behaviour.

Do not add a neural network.

---

## Preserve canonical coordinates

AstroIdentify's canonical pixel convention remains:

```text
x increases left -> right
y increases top -> bottom
0-based pixel centres
integer coordinates refer to pixel centres
```

Do not introduce flips or origin changes.

If a new centroid is added, it must be expressed in this same canonical system.

---

## Source model

Preserve the existing detection identity and provenance.

If practical, retain both:

- the original measured/core centroid;
- the astrometry-preferred centroid.

Do not silently overwrite useful diagnostics if preserving both is inexpensive.

A useful model may conceptually expose:

```text
x
y
astrometric_x
astrometric_y
centroid_method
centroid_quality
```

The exact API should fit the existing codebase.

For unsaturated stars, the astrometric centroid should remain equivalent to the existing trusted centroid unless evidence supports a change.

---

## Calibration and evaluation

Gaia may be used as a scientific reference for **evaluation and calibration experiments**, but production Milestone 2 detection must not require network access or Gaia.

Do not tune and evaluate on the exact same stars without a held-out test.

Use a deterministic split or another defensible held-out procedure.

At minimum compare:

1. current centroid vs Gaia;
2. proposed saturated centroid vs Gaia;
3. unsaturated-star residual before vs after;
4. Milestone 3 blind WCS before vs after;
5. Milestone 4 residual before vs after refinement.

The final production centroid algorithm must operate from the image and local detection data alone.

---

## Required benchmark measurements

For the M57 benchmark, record before/after values for:

- number of saturated accepted detections;
- number for which a refined saturated centroid can be computed;
- saturated-star median Gaia residual;
- saturated-star RMS Gaia residual;
- unsaturated-star median Gaia residual;
- unsaturated-star RMS Gaia residual;
- Milestone 3 blind WCS centre shift relative to the Gaia-refined reference;
- Milestone 3 plate-solver match count;
- Milestone 3 plate-solver residuals;
- Milestone 4 match count;
- Milestone 4 pre-refinement residual;
- Milestone 4 post-refinement residual.

Do not claim improvement from one metric alone.

---

## Acceptance criteria

Milestone 4.1 is complete only if all of the following are true:

1. the selected centroid method is target-agnostic and deterministic;
2. saturated-star astrometric residual improves materially on held-out data;
3. unsaturated-star astrometry does not materially regress;
4. Milestone 3 still solves fully blind with no target name or sky-position hint;
5. the Milestone 3 systematic astrometric offset is substantially reduced;
6. Milestone 4 still produces a valid Gaia match set;
7. `solution.wcs` remains the independent plate-solver output;
8. `refined_solution.wcs` remains a separate Gaia-refined product;
9. ordinary tests do not require internet access;
10. all existing tests remain green;
11. new regression tests cover the discovered saturated-centroid bias;
12. ruff lint and format checks pass.

If no simple local centroid method improves the held-out residual reliably, document that result and preserve the current implementation rather than overfitting the benchmark.

---

## Milestone 4.1 Definition of Done

Before Milestone 5 begins:

- the saturated-centroid problem has been measured;
- at least several candidate methods have been compared;
- the chosen method is evidence-backed;
- production code has been changed only where justified;
- the blind M57 plate solve has been rerun;
- the Gaia catalogue matching benchmark has been rerun;
- before/after metrics are recorded;
- tests and lint are clean;
- no named-object identification has been implemented.

Stop there.

The next milestone will be Milestone 5 — Object Annotation and Identification.

---

## 2. Completed Milestones

### Milestone 1 — Image Ingestion and Preprocessing

Completed and stable.

The preprocessing pipeline supports:

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

The detection pipeline provides:

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

The real Unistellar benchmark produced:

- 2,464 candidates
- 643 accepted sources
- 1,821 rejected candidates
- 129 saturated candidates
- 116 accepted saturated sources
- median accepted SNR ≈ 15.6
- estimated field FWHM ≈ 9.29 px
- local background median ≈ 9.53
- local RMS median ≈ 3.78

Milestone 2 behavior is a stable dependency.

### Milestone 3 — Blind Astrometric Plate Solving / WCS

Completed and stable.

The production pipeline now:

- selects deterministic, spatially distributed plate-solving sources;
- uses a brightness-pooled source policy that permits useful saturated stars;
- writes an Astrometry.net-compatible XYLS source list;
- calls local Astrometry.net through a controlled subprocess boundary;
- distinguishes solved, unsolved, timeout, solver error, invalid WCS, skipped, and exhausted-budget states;
- uses bounded per-attempt and total solve budgets;
- parses a valid WCS;
- exposes centre, corners, pixel scale, FOV, orientation and canonical parity;
- parses match/correspondence residuals;
- writes structured plate-solution metadata;
- generates source-selection and WCS overlays;
- keeps the normal test suite independent of local index files/network access.

#### Milestone 3 root-cause lesson

The initial plate-solving failures were caused by **source selection**, not by image scale, coordinate convention, index coverage, or unmodelled distortion.

In the real Unistellar frame, the Astrometry.net index stars are among the brightest image detections, and those stars are saturated. The old tier-by-tier policy filled the source list with unsaturated stars before saturated sources could be considered. That produced almost no overlap with the solver's indexed bright-star geometry.

The corrected production policy pools allowed non-edge tiers and ranks them by brightness while preserving spatial distribution. Saturation is a quality flag, not an automatic reason to remove an otherwise useful astrometric centroid.

Preserve this behavior unless new evidence demonstrates a better general policy.

#### Verified real benchmark

Benchmark image:

```text
data/raw/M57__Ring_Nebula-eQuinox-20260925-003755.png
```

The production solve succeeds **fully blind**:

- no object-name hint;
- no RA/Dec hint;
- no filename-derived position;
- no scale constraint;
- first attempt (`bright`) succeeds.

Measured benchmark values:

```text
centre RA              283.386455998 deg
centre Dec             +33.0270439152 deg
pixel scale            0.856952 arcsec/pixel
field width            36.5701 arcmin
field height           27.4170 arcmin
up position angle      33.6336 deg E of N
canonical parity       normal
selected sources       100
selected saturated     95
selected preferred     5
occupied grid cells    15 / 15
matched stars          25
median residual        1.22 arcsec
RMS residual           2.01 arcsec
max residual           4.18 arcsec
solver attempt runtime ~2.73 s
```

The canonical AstroIdentify pixel convention remains:

```text
x increases left -> right
y increases top -> bottom
0-based pixel centres
```

The verified Astrometry.net source-list conversion is centralized as:

```text
solver coordinate = canonical coordinate + 1
```

with no image flip.

#### Raw backend diagnostics vs canonical geometry

Astrometry.net's raw textual rotation/parity diagnostics use backend-specific conventions and can differ by a 180-degree representation or parity label from AstroIdentify's canonical WCS-derived geometry.

For application logic, use the normalized/canonical geometry produced by AstroIdentify's WCS module. Preserve raw backend diagnostics only as provenance. Do not compare raw backend parity strings directly with canonical `geometry.parity` as though they use the same convention.

#### Important Milestone 4 input rule

Milestone 3 selected only 100 sources for efficient plate solving. **Milestone 4 catalogue matching must not restrict itself to those 100 solver sources.**

Use the full accepted Milestone 2 detection catalogue (643 accepted sources in the benchmark) as the image-source set to be matched against catalogue objects.

---

## 3. Current Milestone: Milestone 4 — Catalogue Matching

### Objective

Given:

1. an image with a valid Milestone 3 WCS solution; and
2. the accepted Milestone 2 source catalogue;

retrieve astronomical catalogue entries covering the solved image footprint and associate catalogue stars with detected image sources in a deterministic, measurable, explainable way.

Milestone 4 answers:

> Which catalogue stars correspond to the point sources already detected in this solved field?

It does **not** yet answer:

> What is the primary astronomical object in the image?

That is Milestone 5.

---

## 4. Milestone 4 Scope Boundary

### Implement now

- WCS-derived sky footprint/query region;
- a clean catalogue-provider boundary;
- Gaia DR3 stellar queries;
- projection of catalogue sky coordinates into image pixel coordinates;
- local filtering to the actual image footprint;
- deterministic one-to-one crossmatching between Gaia sources and accepted Milestone 2 detections;
- match residuals in pixels and arcseconds;
- structured catalogue-source and match outputs;
- a catalogue-match diagnostic overlay;
- clear network/cache/error behavior;
- offline unit tests with synthetic/fixture catalogue rows;
- a separate opt-in live Gaia integration test/benchmark;
- documentation.

### Do not implement yet

- selecting or naming the image's "main target";
- identifying M57 from its appearance or location;
- SIMBAD object-name lookup as a production identification path;
- Messier/NGC/IC target selection;
- deep-sky object annotation;
- object labels on the public image overlay beyond Gaia source identifiers/debug labels;
- object confidence scoring;
- calibrated evidence/confidence;
- ML/neural networks/embeddings;
- Solar-System ephemerides;
- FastAPI;
- frontend/web work;
- deployment.

Stop after verified catalogue-source matching.

---

## 5. Engineering Principles

### Library first, CLI second

Core catalogue logic belongs in importable modules. CLI commands must call library APIs rather than contain query/matching algorithms.

Conceptually:

```python
catalogue = query_catalogue(wcs_solution, config)
matches = match_catalogue_to_detections(
    catalogue,
    detection_result,
    wcs_solution,
    config,
)
```

### Preserve completed contracts

Milestones 1–3 are stable dependencies.

Do not:

- redetect stars;
- recompute preprocessing independently;
- reimplement WCS math manually where Astropy already provides it;
- alter the plate-solving source selection merely to improve catalogue matching.

### Deterministic matching

Given the same:

- detections;
- WCS;
- catalogue rows;
- configuration;

the crossmatch result and ordering must be deterministic.

### Explainability

Every accepted catalogue match must be traceable to:

- an AstroIdentify detection/source ID;
- a Gaia `source_id`;
- catalogue RA/Dec;
- predicted image x/y from WCS;
- observed detection x/y;
- pixel residual;
- angular residual;
- relevant catalogue photometry/astrometry.

Do not output a synthetic "confidence percentage" in this milestone.

### Fail explicitly

Network failure, malformed catalogue responses, missing WCS, invalid WCS, and matching failures must be explicit states/errors. Do not silently fabricate or reuse stale unrelated catalogue data.

---

## 6. Recommended Package Structure

Continue toward a structure such as:

```text
src/astroidentify/
    preprocessing/       # Milestone 1 — stable
    detection/           # Milestone 2 — stable
    astrometry/          # Milestone 3 — stable
    catalogs/            # Milestone 4 — current
        __init__.py
        types.py
        footprint.py
        gaia.py
        epoch.py          # only if needed/justified
        matching.py
        diagnostics.py
        outputs.py
        pipeline.py
    annotation/          # future
    confidence/          # future
    models/              # future
```

Use the existing repository architecture if a smaller/cleaner layout fits better. Do not create unnecessary layers.

---

## 7. Inputs and Contracts

Milestone 4 should consume existing scientific products rather than reconstruct them.

Required logical inputs:

- valid WCS / Milestone 3 plate solution;
- image dimensions;
- accepted Milestone 2 detections;
- original image or coordinate-exact preview for diagnostics.

### WCS requirement

Catalogue matching must refuse to proceed if:

- the plate solution is unsolved;
- the WCS artifact is missing;
- the WCS is malformed;
- pixel/world round-trip validation fails beyond a clearly justified numerical tolerance.

### Detection-source requirement

Use all accepted detections by default, not only Milestone 3 plate-solving sources.

Keep the original detection source IDs intact so later milestones can trace every catalogue association back to the original measured source.

---

## 8. Sky Footprint and Query Region

The image can be rotated relative to RA/Dec, so do not treat width/height as a simple axis-aligned RA/Dec rectangle.

Derive the actual celestial footprint from the WCS.

For the Gaia network query, a robust simple approach is:

1. compute the WCS centre;
2. compute the celestial coordinates of all image corners;
3. choose a cone radius that reaches the farthest corner, optionally with a small documented margin;
4. query Gaia within that cone;
5. project returned Gaia rows into image pixels;
6. keep only rows whose projected coordinates fall within the actual image bounds (plus any explicitly configured matching margin).

This avoids fragile RA wrap/polar polygon logic while still producing an exact local image-footprint filter.

If implementing an ADQL polygon query instead, handle RA wrap and projection edge cases correctly and test them.

Persist enough query geometry for reproducibility.

---

## 9. Gaia DR3 Provider

Use **Gaia DR3** as the first production stellar catalogue.

The provider boundary must be replaceable later without changing matching logic.

Use a mature client such as `astroquery.gaia` or a clean TAP/ADQL wrapper already appropriate to the project's dependencies.

Do not scrape webpages.

### Minimum useful Gaia fields

Retrieve fields needed for matching/provenance and future stellar evidence, for example:

- `source_id`
- `ra`
- `dec`
- `ra_error`
- `dec_error`
- `phot_g_mean_mag`
- `phot_bp_mean_mag`
- `phot_rp_mean_mag`
- `pmra`
- `pmdec`
- `parallax`

Additional compact quality fields may be included when clearly useful, but do not turn Milestone 4 into a stellar-classification project.

Preserve null/missing catalogue values as null rather than inventing replacements.

### Query limits

The benchmark field is small. Query only required columns and only the footprint-covering region.

Avoid arbitrary low `TOP N` limits that can silently truncate a dense field.

If a safety row limit is needed, make it explicit in configuration/output and treat truncation as a warning/error state rather than silently assuming completeness.

---

## 10. Observation Epoch and Proper Motion

Gaia coordinates have a catalogue reference epoch, while telescope images may be observed years later.

Do not parse observation time from the semantic filename.

Use epoch propagation only when a reliable observation timestamp is available from supported image metadata/FITS headers or explicit non-target user input.

If a reliable observation epoch is available and the necessary Gaia astrometric values are present, propagation with Astropy is appropriate.

If the observation epoch is unavailable:

- match using catalogue coordinates at their catalogue epoch;
- record that proper-motion propagation was not applied;
- document this as a limitation, especially for high-proper-motion stars.

Do not fabricate an observation epoch.

---

## 11. Projection into Image Coordinates

Use the solved Astropy WCS to transform Gaia sky coordinates into AstroIdentify canonical pixel coordinates.

Do not manually derive a tangent-plane transform.

Keep conventions explicit:

```text
AstroIdentify x: left -> right
AstroIdentify y: top -> bottom
Astropy pixel origin: explicitly specified, normally origin=0
```

Catalogue rows whose projected coordinates are non-finite or clearly outside the image should not enter the matching assignment.

Persist predicted catalogue x/y for debugging.

---

## 12. Crossmatching Strategy

The problem is a one-to-one association between:

- projected Gaia catalogue positions; and
- accepted Milestone 2 detections.

### Candidate generation

Generate candidate pairs only within a configurable maximum separation.

Prefer expressing the main tolerance in **arcseconds** and derive the corresponding pixel tolerance from the local/representative WCS scale. Pixel residuals should still be reported.

The initial tolerance must be scientifically defensible relative to:

- the Milestone 3 WCS residuals;
- source-centroid uncertainty;
- possible proper-motion omission;
- the benchmark's ≈0.857 arcsec/pixel scale.

Do not choose a huge radius merely to maximize match count.

### One-to-one assignment

A detection must not match multiple Gaia sources, and one Gaia source must not match multiple detections.

Use a deterministic assignment strategy such as:

- globally minimizing separation for valid candidate edges; or
- deterministic greedy assignment sorted by separation with stable tie-breaking,

provided tests demonstrate correct one-to-one behavior.

Do not independently call nearest-neighbor in both directions and retain conflicting duplicates.

### Matching output

For every match record at least:

- detection/source ID;
- Gaia `source_id`;
- observed detection x/y;
- predicted Gaia x/y;
- pixel separation;
- angular separation;
- Gaia RA/Dec used for the projection;
- whether proper-motion propagation was applied;
- Gaia G/BP/RP magnitudes when available;
- parallax/pmra/pmdec when available.

Keep unmatched detections and unmatched in-frame Gaia sources countable and exportable/inspectable.

---

## 13. Match Quality Metrics

Milestone 4 should report objective matching diagnostics, not a confidence score.

Useful summary values include:

- number of catalogue rows returned by the remote query;
- number projected inside the image;
- number of accepted detections considered;
- number of one-to-one matches;
- fraction of detections matched;
- fraction of in-frame Gaia rows matched;
- median angular residual;
- RMS angular residual;
- maximum angular residual;
- median pixel residual;
- RMS pixel residual;
- number of unmatched detections;
- number of unmatched in-frame Gaia rows;
- whether epoch propagation was applied;
- catalogue release/provider.

Do not interpret these as a probability that the image is M57 or any other target.

---

## 14. Catalogue Diagnostic Overlay

Generate a coordinate-exact overlay suitable for visual inspection.

Recommended visual semantics:

- accepted image detections: subtle existing markers;
- projected Gaia positions: a distinct marker style;
- confirmed one-to-one matches: a clear combined marker and/or short connecting residual vector;
- optional limited labels for a small number of bright/debug sources.

Do not clutter the image with hundreds of full Gaia source IDs.

Do not add deep-sky object names or target labels in Milestone 4.

The overlay must preserve exact image-coordinate alignment.

---

## 15. Output Artifacts

A successful Milestone 4 run should produce artifacts similar to:

```text
outputs/<name>-catalog/
    catalog_query.json
    gaia_sources.csv
    gaia_sources.json              # optional if CSV + summary is sufficient
    catalog_matches.csv
    catalog_matches.json           # optional if CSV + summary is sufficient
    catalog_match_summary.json
    catalog_overlay.png
```

Re-use existing project artifact conventions where possible.

### `catalog_query.json`

Record enough provenance to reproduce/understand the query:

- provider;
- release/table;
- centre;
- query radius/footprint representation;
- selected columns;
- row count;
- timestamp of query;
- cache status;
- any row limit;
- observation epoch used, if any.

### `catalog_match_summary.json`

Record:

- input WCS/plate-solution provenance;
- detection catalogue provenance;
- matching tolerance/configuration;
- summary metrics;
- warnings;
- artifact paths.

Do not duplicate the full plate solution unnecessarily.

---

## 16. Network and Cache Behavior

The production Gaia provider requires external catalogue access unless a valid cached response is available.

Requirements:

- network access is isolated behind the provider boundary;
- normal unit tests never require network access;
- network timeout is configurable;
- service errors are clear;
- cache use is explicit in metadata;
- cache keys include enough query identity to prevent accidental reuse for another field/configuration;
- stale or incompatible cache entries are not silently substituted.

A small on-disk cache is acceptable if it remains simple and deterministic.

Do not build a database or distributed service.

---

## 17. CLI

Add a Milestone 4 command consistent with the existing CLI style.

A conceptual interface is:

```bash
astroidentify catalog-match \
    data/raw/M57__Ring_Nebula-eQuinox-20260925-003755.png \
    --plate-solution outputs/m57-astrometry/plate_solution.json \
    --wcs outputs/m57-astrometry/solution.wcs \
    --output outputs/m57-catalog
```

The exact command name/options may follow the existing CLI architecture.

Prefer reusing existing saved Milestone 2/3 products where the architecture permits rather than unnecessarily re-solving the image.

The CLI should print a concise result such as:

```text
Catalogue: Gaia DR3
Rows queried: ...
Rows in image: ...
Accepted detections: ...
Matched: ...
Median residual: ... arcsec (... px)
RMS residual: ... arcsec
Epoch propagation: yes/no
Matches: .../catalog_matches.csv
Overlay: .../catalog_overlay.png
Summary: .../catalog_match_summary.json
```

Do not print an object-identification verdict.

---

## 18. Error Handling

Use domain-specific failures or clear structured statuses for:

- missing/unsolved plate solution;
- missing WCS artifact;
- malformed WCS;
- invalid image footprint;
- Gaia client unavailable;
- network timeout;
- remote catalogue service error;
- malformed/empty response;
- configured row limit exceeded/truncated result;
- no catalogue rows in field;
- no accepted detections;
- no valid projected catalogue rows;
- no matches within tolerance;
- output write failure.

"No matches" is a valid scientific outcome and should be distinguishable from a network/query failure.

---

## 19. Tests

All Milestone 1–3 tests must continue to pass.

### Footprint tests

Test:

- centre/corner extraction from known synthetic WCS;
- farthest-corner query radius;
- rotated WCS;
- near-RA-wrap behavior;
- projection-based in-image filtering.

### Gaia provider tests

Do not use live Gaia for the normal unit suite.

Mock/provider-fixture tests should cover:

- ADQL/query construction or client arguments;
- required columns;
- null fields;
- empty response;
- timeout/service failure;
- cache hit/miss;
- truncation/row-limit behavior if implemented.

### Projection tests

Using synthetic WCS and known sky positions, test:

- RA/Dec -> canonical x/y;
- origin behavior;
- rotated images;
- out-of-image filtering;
- no hidden flip.

### Matching tests

Use synthetic detections/catalogue rows to test:

- exact match;
- small residual match;
- outside-tolerance rejection;
- one catalogue source near two detections;
- two catalogue sources near one detection;
- deterministic tie handling;
- one-to-one uniqueness;
- unmatched detections;
- unmatched Gaia rows;
- stable output ordering.

### Epoch tests

If epoch propagation is implemented, test:

- no timestamp -> no propagation;
- reliable timestamp -> propagation;
- missing proper motion -> safe handling;
- high-proper-motion synthetic source behaves as expected.

### Outputs/overlay tests

Test:

- structured summary serialization;
- match CSV columns/provenance;
- catalogue query provenance;
- overlay coordinate alignment;
- artifact naming.

### CLI tests

Test success and failure behavior with a fake catalogue provider and no network dependency.

---

## 20. Real Gaia Integration Benchmark

Milestone 4 is not complete until a separate real catalogue query/crossmatch is attempted using the solved Unistellar benchmark.

Use:

```text
data/raw/M57__Ring_Nebula-eQuinox-20260925-003755.png
outputs/m57-astrometry/plate_solution.json
outputs/m57-astrometry/solution.wcs
```

Use the full accepted Milestone 2 source catalogue.

The benchmark may query Gaia DR3 over the network.

If the catalogue service is unavailable, implement/test the provider boundary and report the live benchmark as integration-blocked. Do not fabricate results.

### Benchmark validation

Inspect:

- catalogue query region;
- number of Gaia rows projected into the actual image;
- match count;
- residual distribution;
- catalogue overlay;
- whether residuals show a systematic positional offset or rotation;
- whether bright matched stars visually land on the corresponding image detections.

A valid result should show a physically plausible cluster of low-residual matches. Do not require every image detection to have a Gaia counterpart.

---

## 21. Scientific Interpretation Rules

Milestone 4 catalogue matching is evidence about point-source correspondence, not object identity.

Do not write claims such as:

```text
"This image is M57 because Gaia matched 300 stars."
```

A correct Milestone 4 claim is closer to:

```text
"The solved WCS projects Gaia DR3 sources onto the image, and N catalogue stars were associated one-to-one with detected point sources with a median residual of X arcsec."
```

The later object-identification milestone will use WCS/catalogue context to identify named astronomical objects.

---

## 22. Documentation

Update README documentation with:

- Milestone 4 purpose;
- Gaia DR3 dependency/provider;
- whether network access is required;
- catalogue query geometry;
- matching strategy;
- matching tolerance;
- output fields/artifacts;
- cache behavior;
- live benchmark command;
- known limitations;
- explicit statement that named-object identification is intentionally not implemented yet.

Also update the roadmap/status so Milestone 3 is marked complete and Milestone 4 is current.

---

## 23. Coding Style

- Write readable Python over clever Python.
- Use typed public interfaces and dataclasses where useful.
- Keep functions focused.
- Prefer descriptive names.
- Avoid giant classes and deep inheritance.
- Keep catalogue-network code isolated from matching logic.
- Keep WCS transformations centralized and explicit.
- Preserve source IDs/provenance.
- Use logging in library code.
- Do not silently swallow network or scientific-data errors.
- Use comments to explain scientific/coordinate decisions, not obvious syntax.

---

## 24. How to Work on Milestone 4

1. Read this file fully.
2. Inspect repository architecture and existing M1–M3 APIs/artifacts.
3. Run the full existing test suite and Ruff checks before changes.
4. Record `git status` and existing uncommitted changes.
5. Design the smallest clean catalogue-provider + matching boundary.
6. Implement WCS footprint/query geometry.
7. Implement Gaia DR3 provider with mockable network behavior.
8. Implement world->pixel projection and exact in-image filtering.
9. Implement deterministic one-to-one matching.
10. Implement match metrics and structured outputs.
11. Implement diagnostic overlay.
12. Add offline unit tests.
13. Run full tests and Ruff.
14. Attempt the live Gaia benchmark on the solved Unistellar field.
15. Inspect the overlay and residuals.
16. Update README/status.
17. Stop before named-object identification.

Do not commit unless the user explicitly asks you to commit.

---

## 25. Milestone 4 Definition of Done

Milestone 4 is complete when:

1. all Milestone 1–3 tests still pass;
2. a solved WCS can be converted into a reproducible sky query region;
3. a Gaia DR3 provider is implemented behind a clean boundary;
4. normal tests do not require the network;
5. Gaia rows can be projected into canonical image pixels correctly;
6. rows outside the actual image footprint are filtered correctly;
7. the full accepted Milestone 2 source catalogue is used for matching;
8. one-to-one matching is deterministic and tested;
9. every match preserves detection ID + Gaia `source_id` provenance;
10. pixel and angular residuals are reported;
11. unmatched detections and unmatched in-frame Gaia rows are measurable;
12. catalogue query and match outputs are machine-readable;
13. a coordinate-exact catalogue overlay is generated;
14. network/cache/query failures are explicit;
15. tests and Ruff checks pass;
16. a real Gaia DR3 integration query/crossmatch is attempted on the solved Unistellar benchmark;
17. the benchmark result is visually inspected and residuals are plausible;
18. no named-object identification, deep-sky target selection, confidence scoring, ML, Solar-System support, API, or frontend work has been started.

Stop there.

The next milestone will use the WCS and catalogue context to identify and annotate named astronomical objects in the field.
