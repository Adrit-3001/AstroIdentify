# CLAUDE.md — AstroIdentify

## Project overview
AstroIdentify accepts an astronomical image with little or no user-provided context, determines where it points on the sky, matches detections to catalogues, identifies catalogued objects, annotates the image, and provides interpretable evidence for those identifications.

Development is milestone-based. Each milestone must leave the repository working, tested, reproducible, and scientifically auditable.

## Roadmap
1. ✅ Image ingestion and preprocessing
2. ✅ Astronomical source/star detection
3. ✅ Blind astrometric plate solving / WCS
4. ✅ Gaia DR3 catalogue matching
4.1. ✅ Saturated-star astrometric centroid calibration
5. ✅ Object annotation and identification
6. 🚧 Evidence and confidence estimation
7. ⬜ Computer-vision / ML verification
8. ⬜ Solar-system support
9. ⬜ FastAPI backend
10. ⬜ Web frontend and deployment

**Current focus: Milestone 6 only. Do not begin Milestone 7.**

## Stable completed milestones

### Milestone 1 — Preprocessing
Stable JPEG/PNG/FITS ingestion, scientific image representation, metadata preservation, background/noise diagnostics, normalization, previews, CLI, and tests.

### Milestone 2 — Detection
Stable spatial background/RMS estimation, stellar detection, source measurements, saturation handling, filtering, astrometric centroids, overlays, and structured source artifacts.

Benchmark:
- 2,464 candidates
- 643 accepted detections
- 129 saturated candidates
- 116 accepted saturated detections

### Milestone 3 — Blind WCS
Stable local Astrometry.net integration.

Permanent rules:
- no target-name hint;
- no filename-derived sky information;
- no production RA/Dec hint;
- `solution.wcs` remains the independent blind plate solution;
- canonical pixels are 0-based, x left→right, y top→bottom;
- only the Astrometry.net XYLS boundary uses the verified `+1` conversion.

### Milestone 4 — Gaia DR3 matching
Stable Gaia footprint query, projection, proper-motion support when epoch exists, deterministic one-to-one matching, residuals, caching, overlays, and a separate Gaia-refined WCS.

Preserve:
- `solution.wcs` = independent blind WCS
- `refined_solution.wcs` = Gaia-refined WCS

After Milestone 4.1, the benchmark has approximately:
- 630/643 accepted detections matched;
- 111/116 saturated detections matched;
- ~0.67 arcsec median final Gaia residual;
- ~0.98 arcsec RMS final Gaia residual.

### Milestone 4.1 — Saturated-star astrometric centroid calibration
Stable image-only, target-agnostic saturated-star correction.

Requirements that remain permanent:
- unsaturated positions unchanged;
- no Gaia/network runtime dependency;
- deterministic fallback;
- no benchmark-specific tuning;
- no extrapolation outside calibrated support.

### Milestone 5 — Object annotation and identification
Completed and stable.

Milestone 5:
- prefers `refined_solution.wcs`, falling back to `solution.wcs`;
- derives SIMBAD queries only from WCS/image geometry;
- uses an official structured SIMBAD interface;
- caches/replays results;
- projects named objects into image pixels;
- preserves IDs, aliases, types, sizes, and orientation metadata where available;
- filters ordinary stars from the default overlay;
- associates compact/extended objects with image evidence when practical;
- writes structured object artifacts and `object_overlay.png`;
- never uses target names, filename semantics, or hard-coded target coordinates.

Benchmark validation:
- the central nebula was identified as M 57 / NGC 6720 using only WCS-derived catalogue lookup;
- the overlay aligned correctly;
- additional galaxies and catalogue objects were also placed correctly.

A separate Vega-field test confirmed:
- SIMBAD returned Vega;
- Vega projected correctly into the image;
- it was intentionally excluded because the default Milestone 5 object policy suppresses category `star`.

This is accepted behavior.

### Deferred object-filter configuration
A later user-facing option may allow choosing which object categories to retain or suppress, including named/notable stars. Do not implement this now unless a later milestone naturally requires it.

# Milestone 6 — Evidence and Confidence Estimation

## Objective
Convert existing objective evidence from Milestones 2–5 into an interpretable, deterministic assessment of how strongly each reported identification is supported.

Core principle:

> Confidence must come from explicit evidence, not target-name priors, benchmark knowledge, or arbitrary model intuition.

The system must be allowed to report:
- `strong`
- `moderate`
- `weak`
- `catalogue-only`
- `insufficient`

or equivalent carefully documented levels.

## No fake probabilities
Do not expose an uncalibrated percentage such as `93% confidence`.

If a numeric value is implemented, call it an **evidence score**, not a probability, unless genuine empirical probability calibration is performed.

Document prominently:

> Evidence scores are not calibrated probabilities.

## Evidence classes

### Astrometric evidence
May include:
- WCS source;
- Gaia match count/fraction;
- Gaia median/RMS residual;
- local Gaia residuals when robustly available;
- plate-solver residual;
- catalogue-to-image association separation.

### Catalogue evidence
May include:
- canonical identifier;
- aliases;
- type;
- coordinates;
- angular extent;
- metadata completeness;
- number of competing nearby candidates.

Catalogue presence alone is not proof of visual detection.

### Compact-object image evidence
May include:
- nearest accepted detection;
- pixel/angular separation;
- normalized separation relative to astrometric quality;
- source quality;
- saturation/edge state;
- centroid method.

### Extended-object image evidence
May include:
- footprint overlap;
- fraction of expected extent visible;
- local brightness/background contrast;
- detections inside the expected footprint;
- position of visible structure relative to catalogue centre;
- catalogue extent versus measured structure when already available.

Do not introduce segmentation or ML in Milestone 6.

### Data-quality evidence
May include:
- missing epoch;
- missing angular size;
- saturation;
- edge truncation;
- missing association;
- catalogue/query warnings;
- object mostly outside frame.

Missing information must remain unknown/missing, not silently become contradictory evidence.

## Avoid double counting
Many metrics are correlated. Do not simply sum every available number.

Group evidence conceptually, for example:
- astrometric support;
- catalogue support;
- image support;
- data-quality penalties;
- ambiguity penalty.

Document correlated/redundant features.

## Local astrometric uncertainty
Where practical, compare association separation against expected WCS/centroid uncertainty.

Conceptually:

`normalized_offset = association_separation / representative_astrometric_uncertainty`

Prefer local Gaia residuals where robust. Fall back to a documented field-level value. Never fabricate unavailable catalogue uncertainties.

## Compact versus extended evidence
Compact and extended objects must use different evidence paths.

A compact catalogue object without a corresponding image source should not receive strong image-confirmation evidence.

An extended nebula/galaxy should not require a Milestone 2 point-source detection.

## Ambiguity
Record ambiguity when:
- multiple catalogue objects fall within the same positional uncertainty region;
- extended footprints overlap;
- one image feature plausibly corresponds to multiple catalogue entries.

Do not force a winner when evidence cannot distinguish candidates.

## Evidence data model
Create versioned typed records. Conceptually:

```text
ObjectEvidence
  object identity/type
  evidence version
  astrometry features
  catalogue features
  image-association features
  ambiguity
  data-quality flags
  missing fields
  reason codes
  support level
  optional non-probabilistic evidence score
  explanation
```

The same inputs + config must reproduce the same result.

## Explanations
Every assessment must expose machine-readable reason codes plus a short human-readable explanation.

Possible generic reason codes:
- `ASTROMETRY_PRECISE`
- `POSITION_MATCH_CLOSE`
- `EXTENDED_CONTRAST_STRONG`
- `EXTENT_MOSTLY_VISIBLE`
- `NO_IMAGE_ASSOCIATION`
- `OBJECT_TRUNCATED`
- `AMBIGUOUS_CANDIDATES`
- `MISSING_SIZE`
- `MISSING_EPOCH`

Do not hard-code object-specific explanations.

## Suggested package
Prefer:

```text
src/astroidentify/evidence/
    __init__.py
    types.py
    features.py
    astrometry.py
    compact.py
    extended.py
    ambiguity.py
    scoring.py
    explanations.py
    outputs.py
    overlay.py
    pipeline.py
```

Adapt if the repository suggests a cleaner organization.

No ML infrastructure.

## CLI
Add a command conceptually like:

```bash
astroidentify assess   data/raw/example.png   --objects outputs/example-objects   --catalog outputs/example-catalog   --astrometry outputs/example-astrometry   --detections outputs/example-detection   --output outputs/example-evidence
```

Reuse saved artifacts. No network should be required if earlier milestone outputs exist.

## Outputs
Produce conceptually:

```text
outputs/<name>-evidence/
    object_evidence.csv
    object_evidence.json
    evidence_summary.json
    evidence_overlay.png
```

Each object record should include:
- identity/type;
- raw and normalized features;
- missing-data flags;
- ambiguity;
- support level;
- optional evidence score;
- reason codes;
- explanation;
- provenance.

## Evaluation
Create synthetic/offline cases for:
- exact compact match;
- close compact match;
- poor compact match;
- catalogue-only compact object;
- strong extended object;
- weak extended object;
- edge-truncated object;
- ambiguous candidates;
- missing extent;
- missing epoch;
- saturated compact source;
- intentionally bad WCS/association.

Expected support levels must be defined and tested.

## M57 benchmark
Run Milestone 6 on the M57 object outputs.

The central nebula should receive strong evidence only if the generic rules support it.

Do not hard-code M57.

Report which evidence components drive the result.

## Vega regression
Do not change Milestone 5's star-filter policy.

The Vega test already proved Vega was queried and projected correctly but intentionally filtered from the default object set. Milestone 6 must not change that behavior.

## Tests
All previous tests must remain green.

Offline tests must cover:
- complete and missing evidence;
- compact and extended paths;
- normalized positional evidence;
- support-level mapping;
- catalogue-only and insufficient outcomes;
- ambiguity;
- deterministic repeated results;
- monotonic behavior where scientifically expected;
- schema/serialization;
- overlay alignment;
- CLI success/failure.

Normal tests must not use the network.

## Documentation
README must explain:
- evidence groups;
- support levels;
- abstention;
- evidence score versus probability;
- CLI usage;
- outputs;
- limitations.

## Definition of done
Milestone 6 is complete only when:
1. all previous tests remain green;
2. evidence extraction is deterministic and target-agnostic;
3. compact and extended objects use suitable evidence paths;
4. astrometric quality affects positional evidence;
5. missing data is explicit;
6. ambiguity is represented;
7. support levels/scores are explainable;
8. no uncalibrated probability percentage is presented;
9. an `insufficient`/abstain outcome exists;
10. structured evidence artifacts are written;
11. an evidence overlay is generated;
12. normal tests require no network;
13. M57 benchmark evidence is inspected;
14. Vega behavior is unchanged;
15. ruff lint/format checks pass;
16. no ML/CV verification, Solar-System support, backend, or frontend work is implemented.

Stop there.

The next milestone is **Milestone 7 — Computer-Vision / ML Verification**.
