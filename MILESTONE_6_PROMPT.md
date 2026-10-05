# Milestone 6 Prompt — Evidence and Confidence Estimation

Read `CLAUDE.md` completely before changing code.

Milestones 1–5 are complete. This task is **Milestone 6 only**.

Goal:

> Convert AstroIdentify's existing astrometric, catalogue, association, and image-structure measurements into transparent, deterministic evidence assessments for each identified object.

Do not begin ML/CV verification, Solar-System support, FastAPI, frontend work, or deployment.

Do not commit unless explicitly asked.

## Phase 1 — Baseline
Before modifying production code:
1. run the full current test suite;
2. run `ruff check`;
3. run `ruff format --check`;
4. record `git status`;
5. inspect M3–M5 artifact schemas;
6. inspect Gaia residual metadata;
7. inspect compact and extended object-association records;
8. inspect `identification_summary.json`, `catalog_objects.*`, and `object_associations.csv`.

Report exact baseline counts.

## Phase 2 — Evidence inventory
List all candidate features already available.

At minimum consider:

### Astrometry
- WCS source: blind/refined;
- Gaia match count/fraction;
- Gaia median/RMS residual;
- local Gaia residuals if robustly derivable;
- plate-solver residual;
- association angular/pixel separation.

### Catalogue
- ID, aliases, type;
- coordinates;
- angular size/orientation;
- metadata completeness.

### Compact association
- associated source exists;
- separation;
- source quality;
- saturation;
- edge flag;
- astrometric centroid method.

### Extended association
- footprint overlap;
- fraction visible;
- brightness/background contrast;
- detections in footprint;
- centre/structure offset;
- existing radial/shape measurements.

### Data quality
- missing epoch;
- missing extent;
- truncation;
- warnings;
- missing association.

Document which candidate features are correlated. Do not simply sum all values.

## Phase 3 — Data model
Create a versioned typed evidence schema.

Conceptually:

```text
ObjectEvidence
  object_id
  display_name
  object_type
  evidence_version
  astrometry
  catalogue
  image_association
  data_quality
  ambiguity
  support_level
  evidence_score optional
  reason_codes
  explanation
```

Use optional/null values for unavailable evidence.

## Phase 4 — Confidence semantics
Do NOT implement fake probability percentages.

Use support levels such as:

```text
strong
moderate
weak
catalogue-only
insufficient
```

An optional numeric score is allowed only if clearly named **evidence score** and documented as non-probabilistic.

## Phase 5 — Positional evidence
Raw separation alone is insufficient.

Where possible compute a normalized separation relative to astrometric uncertainty.

Prefer local nearby Gaia residuals if stable; otherwise use a documented field-level residual.

Test that the same absolute separation receives different positional support under high-quality versus poor-quality WCS.

Do not fabricate missing uncertainty values.

## Phase 6 — Compact-object path
For compact objects, consider:
- source association existence;
- angular/pixel separation;
- normalized separation;
- source quality;
- saturation/edge state;
- centroid method;
- Gaia correspondence when already available.

Distinguish `catalogue-only` from actual contradictory evidence.

## Phase 7 — Extended-object path
For extended objects, consider:
- centre inside image;
- footprint intersection;
- fraction visible;
- local contrast;
- detections within footprint;
- centre-to-visible-structure offset;
- catalogue size versus observed structure when already measurable.

Do not require a point-source match.

Do not introduce segmentation or ML.

## Phase 8 — Missing evidence
Missing values must not silently become zero/negative evidence.

Handle explicitly:
- missing size;
- missing epoch;
- no image association;
- object largely off-frame;
- no local Gaia residual;
- saturation;
- query/catalogue warnings.

Emit missing-data reason codes.

## Phase 9 — Ambiguity
Detect plausible competing candidates.

Examples:
- multiple compact catalogue objects inside one uncertainty region;
- overlapping extended footprints;
- multiple retained objects near the same image feature.

Record competitors.

Ambiguity should reduce support or trigger abstention where justified.

Do not force a unique winner.

## Phase 10 — Aggregation
Build a small deterministic aggregation system.

Prefer conceptual groups:

```text
astrometric support
catalogue support
image support
data-quality penalties
ambiguity penalty
```

Avoid double counting.

A few documented weights are acceptable, or use a clear rule-based system.

If weights/thresholds are used:
- expose them in config;
- justify them;
- do not tune to M57 alone.

Require minimum conditions for `strong`.

## Phase 11 — Abstention
Implement explicit `insufficient`/abstention behavior.

Examples:
- invalid prerequisites;
- no meaningful association evidence;
- severe ambiguity;
- object almost entirely outside the image;
- required evidence unavailable.

## Phase 12 — Explanations
Generate machine-readable reason codes and a short explanation.

Examples:
- `ASTROMETRY_PRECISE`
- `POSITION_MATCH_CLOSE`
- `EXTENDED_CONTRAST_STRONG`
- `EXTENT_MOSTLY_VISIBLE`
- `NO_IMAGE_ASSOCIATION`
- `OBJECT_TRUNCATED`
- `AMBIGUOUS_CANDIDATES`
- `MISSING_SIZE`
- `MISSING_EPOCH`

No object-specific hard-coding.

## Phase 13 — Package
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

Adapt to current architecture if needed.

No ML libraries.

## Phase 14 — CLI
Add an `assess` command consistent with the current CLI.

Conceptually:

```bash
astroidentify assess   data/raw/example.png   --objects outputs/example-objects   --catalog outputs/example-catalog   --astrometry outputs/example-astrometry   --detections outputs/example-detection   --output outputs/example-evidence
```

Reuse existing saved artifacts. It should not require a fresh network request.

Print:
- objects assessed;
- counts by support level;
- warnings;
- output paths.

## Phase 15 — Outputs
Produce:

```text
outputs/<name>-evidence/
    object_evidence.csv
    object_evidence.json
    evidence_summary.json
    evidence_overlay.png
```

Each record should preserve:
- identity/type;
- raw features;
- normalized features;
- missing-data flags;
- ambiguity;
- support level;
- optional evidence score;
- reason codes;
- explanation;
- provenance.

## Phase 16 — Offline validation fixtures
Create synthetic cases for:
- perfect compact identification;
- close compact identification;
- poor compact association;
- catalogue-only compact object;
- strong extended object;
- weak extended object;
- truncated extended object;
- ambiguous pair;
- missing angular extent;
- missing epoch;
- saturated compact source;
- intentionally bad-WCS case.

Define expected support levels before looking at M57 results.

## Phase 17 — Tests
Add offline tests covering:
- feature extraction;
- normalized positional evidence;
- compact/extended paths;
- missing data;
- ambiguity;
- support-level mapping;
- deterministic results;
- JSON/CSV schema;
- reason codes;
- overlay coordinates;
- CLI success/failure.

Add monotonicity tests where sensible:
- smaller normalized positional offset must not reduce positional support;
- stronger extended contrast must not reduce image support;
- more ambiguity must not increase final support.

Normal tests must not access the network.

## Phase 18 — M57 benchmark
Run the evidence pipeline using existing M57 outputs.

Do not hard-code M57.

Report the central object's:
- support level;
- evidence score if implemented;
- astrometric evidence;
- catalogue evidence;
- image evidence;
- ambiguity;
- reason codes;
- explanation.

The result may be `strong` only if generic rules justify it.

## Phase 19 — Vega regression
Do not change Milestone 5's star-filter behavior.

Vega was correctly queried/projected but intentionally excluded as category `star`.

Milestone 6 must leave that unchanged.

Use retained non-stellar objects in the Vega field only as optional regression cases.

## Phase 20 — README
Document:
- what evidence means;
- support levels;
- abstention;
- score vs probability;
- CLI;
- artifacts;
- limitations.

## Required final report

### A. Baseline
- test count;
- lint/format;
- git status.

### B. Evidence inventory
- used features;
- excluded/redundant features;
- correlation/double-counting decisions.

### C. Data model
- schema/version;
- support levels;
- reason codes.

### D. Aggregation
- exact rules/formula;
- thresholds;
- config;
- missing-data behavior;
- ambiguity behavior.

### E. Synthetic validation
Table:

```text
case | expected level | actual level | key evidence
```

### F. M57 result
Report:
- object identity;
- support level;
- evidence score if any;
- astrometric evidence;
- catalogue evidence;
- image evidence;
- ambiguity;
- reason codes;
- explanation.

### G. Vega regression
Confirm previous behavior is unchanged.

### H. Tests
Exact results plus lint/format.

### I. Artifacts
Exact paths to evidence CSV/JSON, summary, and overlay.

### J. Limitations
Especially state whether scores are non-probabilistic.

### K. Status
State whether Milestone 6 is complete.

Do NOT begin Milestone 7.
