# AstroIdentify — Milestone 4 Implementation Prompt

Read `CLAUDE.md` completely before making any changes. It is the engineering contract for this task.

Milestones 1, 2, and 3 are complete and stable.

Your task is to implement **Milestone 4: Catalogue Matching** and stop exactly at its boundary.

Do **not** begin named-object identification, deep-sky target annotation, confidence scoring, ML, Solar-System support, FastAPI, frontend work, or deployment.

---

# 1. Goal

Given:

- a valid Milestone 3 WCS/plate solution for an astronomical image; and
- the full accepted Milestone 2 detection catalogue;

query **Gaia DR3** for stellar catalogue entries covering the solved image field, project those catalogue positions into image pixel coordinates, and perform a deterministic one-to-one crossmatch between Gaia stars and accepted image detections.

The output should make it possible to say:

> "These detected point sources correspond to these Gaia DR3 stars, with these measured residuals."

It must **not** yet say:

> "The main object in the image is M57."

That belongs to the next milestone.

---

# 2. Stable benchmark inputs

Use the existing real benchmark:

```text
data/raw/M57__Ring_Nebula-eQuinox-20260925-003755.png
```

Milestone 2 benchmark facts:

```text
2464 candidates
643 accepted detections
129 saturated candidates
116 accepted saturated sources
median accepted SNR ≈ 15.6
```

Milestone 3 is now verified complete.

Final production WCS products:

```text
outputs/m57-astrometry/plate_solution.json
outputs/m57-astrometry/solution.wcs
outputs/m57-astrometry/source_selection.png
outputs/m57-astrometry/wcs_overlay.png
outputs/m57-astrometry/correspondences.csv
outputs/m57-astrometry/solver.log
```

Final blind-solve values:

```text
mode                  blind
position hint         none
object-name hint      none
filename hint         none
scale constraint      none
centre RA             283.386455998 deg
centre Dec            +33.0270439152 deg
pixel scale           0.856952 arcsec/pixel
field width           36.5701 arcmin
field height          27.4170 arcmin
up position angle     33.6336 deg E of N
canonical parity      normal
selected solver stars 100
matched solver stars  25
median WCS residual   1.22 arcsec
RMS WCS residual      2.01 arcsec
max WCS residual      4.18 arcsec
```

The plate solve succeeded on attempt 1 in about 2.7 seconds.

The corrected plate-solving source selection used 95 saturated + 5 preferred sources, distributed over all 15 grid cells.

**Important:** those 100 sources were selected specifically for plate solving. Do not use only them for Milestone 4. Catalogue matching should use the complete **643 accepted Milestone 2 detections** by default.

---

# 3. First: baseline and repository inspection

Before modifying code:

1. read `CLAUDE.md` fully;
2. inspect the current repository structure;
3. inspect Milestone 2 detection result types/artifacts;
4. inspect Milestone 3 WCS/plate-solution types and loaders;
5. inspect existing CLI conventions;
6. inspect output/artifact helpers;
7. inspect dependencies already present in `pyproject.toml`;
8. run the full existing test suite;
9. run Ruff lint and format checks;
10. record `git status` and existing uncommitted changes.

Report the baseline test count before implementation.

Do not commit unless explicitly asked by the user.

---

# 4. Architecture

Add the smallest clean Milestone 4 catalogue package consistent with the existing repository.

A reasonable shape is:

```text
src/astroidentify/catalogs/
    __init__.py
    types.py
    footprint.py
    gaia.py
    epoch.py          # only if justified
    matching.py
    diagnostics.py
    outputs.py
    pipeline.py
```

This is guidance, not a requirement to create every file.

Avoid unnecessary abstraction.

The important separation is:

```text
network/provider
    ↓
catalogue rows
    ↓
WCS projection
    ↓
local matching
    ↓
outputs/diagnostics
```

Matching logic must not depend directly on the network client so it can be tested offline.

---

# 5. Input contract

Milestone 4 consumes stable products from prior milestones.

Required logical inputs:

- solved Milestone 3 WCS;
- plate-solution provenance;
- original image dimensions;
- full accepted Milestone 2 detection catalogue;
- original image or coordinate-exact preview for the diagnostic overlay.

Do not:

- redetect sources;
- rerun preprocessing independently;
- use only the 100 plate-solving stars;
- parse target semantics from the filename;
- use known M57 coordinates as catalogue-query input.

The sky query region must come from the solved WCS.

---

# 6. Validate the WCS before catalogue work

Catalogue matching must fail clearly if the WCS is invalid.

Validate:

- plate solution says solved;
- WCS artifact exists;
- Astropy can load it;
- image dimensions are consistent;
- centre/corner transforms are finite;
- a pixel -> world -> pixel round trip is numerically sound.

Reuse Milestone 3 validation utilities rather than duplicating WCS logic.

Do not use raw Astrometry.net parity/rotation strings for projection. Use the canonical Astropy WCS/canonical geometry.

---

# 7. Derive the catalogue query region from WCS

The image is rotated relative to RA/Dec.

Do not approximate it as a naive axis-aligned RA/Dec rectangle.

Implement a robust query-region strategy.

Recommended initial approach:

1. compute celestial centre from WCS;
2. compute all four celestial corners;
3. compute the angular distance from centre to each corner;
4. choose a cone radius equal to the farthest corner plus a small documented/configurable safety margin;
5. query Gaia over that cone;
6. project returned rows into image pixel coordinates;
7. retain only rows inside the actual image bounds, optionally with a very small explicit matching margin.

This separates:

```text
network query region = safe superset
actual field          = exact WCS/pixel filter
```

Persist the query centre/radius/footprint provenance.

Test rotated WCS and RA wrap behavior.

---

# 8. Implement a Gaia DR3 provider

Use **Gaia DR3** as the first production stellar catalogue.

Use a mature mechanism such as `astroquery.gaia` / Gaia TAP if compatible with the project.

Do not scrape HTML.

The provider should expose a typed result independent of the raw network client.

Retrieve only useful columns. At minimum:

```text
source_id
ra
dec
ra_error
dec_error
phot_g_mean_mag
phot_bp_mean_mag
phot_rp_mean_mag
pmra
pmdec
parallax
```

You may include a small number of additional quality fields if clearly justified.

Do not expand this into stellar classification.

Preserve missing values as null/NaN according to the project's serialization rules.

---

# 9. Query completeness and row limits

Do not use an arbitrary small `TOP N` that can silently truncate a dense field.

The benchmark field is only about 36.6 × 27.4 arcmin, so a compact selected-column Gaia query should be manageable.

If you introduce a safety row limit:

- make it explicit in config;
- record it in query metadata;
- detect/report truncation;
- do not treat a truncated response as complete without a warning/status.

Do not fetch unnecessary Gaia columns.

---

# 10. Network and cache design

Normal unit tests must not need the network.

Isolate network access behind the Gaia provider.

Implement clear behavior for:

- query success;
- empty response;
- timeout;
- service error;
- malformed response;
- cache hit/miss if caching is implemented.

A small deterministic disk cache is acceptable and useful for repeated development runs, but keep it simple.

Cache identity must include enough of:

- catalogue release/table;
- query centre;
- query radius/footprint;
- selected columns;
- relevant filters/configuration.

Do not accidentally reuse another field's cached response.

Record whether a benchmark result came from live query or cache.

---

# 11. Observation epoch and proper motion

Gaia positions have a catalogue reference epoch and the image may have a later observation epoch.

Do not infer date/time from the semantic filename.

Inspect the existing Milestone 1 metadata contract to determine whether a reliable observation timestamp is available from supported FITS/raster metadata.

If a reliable timestamp exists and Gaia has the required motion information, implement proper-motion propagation cleanly with Astropy.

If there is no reliable timestamp:

- do not invent one;
- do not parse the M57 filename timestamp;
- match at the Gaia catalogue coordinates;
- record `epoch_propagated = false`;
- document high-proper-motion stars as a limitation.

Do not let optional epoch propagation block the entire milestone if the benchmark metadata does not support it reliably.

---

# 12. Project Gaia rows into image pixels

Use the Milestone 3 Astropy WCS.

For every queried Gaia row with finite coordinates:

1. obtain the sky coordinate to use (propagated or catalogue epoch);
2. transform RA/Dec -> canonical image x/y using explicit Astropy origin handling;
3. persist predicted x/y;
4. locally filter to the actual image footprint.

The canonical image convention remains:

```text
x left -> right
y top -> bottom
0-based pixel centres
```

Do not introduce another +1 conversion here. The +1 conversion is specifically for Astrometry.net XYLS input. Astropy WCS matching should use the already established canonical origin contract.

Add tests that would catch a hidden X/Y flip or one-pixel shift.

---

# 13. Match against ALL accepted Milestone 2 detections

Use the full accepted source set from Milestone 2.

For the benchmark this is 643 detections.

Do not restrict the match pool to:

- Milestone 3 selected 100;
- saturated sources only;
- unsaturated sources only.

Catalogue matching and plate-solving source selection have different goals.

Preserve each Milestone 2 detection/source ID.

---

# 14. Candidate match radius

Define a scientifically reasonable maximum catalogue-to-detection separation.

Prefer configuration in arcseconds.

Use the existing benchmark information to justify the default:

```text
WCS median residual ≈ 1.22 arcsec
WCS RMS residual    ≈ 2.01 arcsec
WCS max residual    ≈ 4.18 arcsec
pixel scale         ≈ 0.857 arcsec/pixel
```

The tolerance must allow realistic centroid/WCS residuals but not be so large that unrelated stars are routinely paired.

Make the threshold configurable and record it in output metadata.

Do not tune it solely to maximize benchmark match count.

---

# 15. Deterministic one-to-one crossmatching

Generate candidate edges between projected Gaia positions and accepted detections only when separation is within the configured tolerance.

Enforce:

```text
one Gaia source -> at most one detection
one detection   -> at most one Gaia source
```

Use a deterministic strategy.

A globally optimal assignment over candidate distances is acceptable if simple and dependency-compatible.

A deterministic greedy nearest-separation assignment is also acceptable if tests cover conflicts/ties and behavior is justified.

Do not simply perform independent nearest-neighbor queries and keep duplicate/conflicting matches.

Stable tie-breaking is required.

---

# 16. Match record schema

Every accepted match should preserve enough information for later Milestones 5–7.

Include at least:

```text
detection_source_id
gaia_source_id
observed_x_px
observed_y_px
predicted_x_px
predicted_y_px
residual_px
residual_arcsec
catalog_ra_deg
catalog_dec_deg
projected_ra_deg/projected_dec_deg if epoch propagation changes them
epoch_propagated
phot_g_mean_mag
phot_bp_mean_mag
phot_rp_mean_mag
parallax
pmra
pmdec
```

Also preserve useful detection measurements by reference or selected copied fields, but do not duplicate the entire detection record unnecessarily.

---

# 17. Summary metrics

Create objective catalogue-match diagnostics.

At minimum report:

```text
catalogue provider/release
remote rows returned
rows projected into image
accepted detections considered
one-to-one matches
detection match fraction
in-frame Gaia match fraction
median residual arcsec
RMS residual arcsec
max residual arcsec
median residual pixels
RMS residual pixels
unmatched detections
unmatched in-frame Gaia sources
epoch propagation used yes/no
```

Do not convert these into a probability/confidence that the target is M57.

---

# 18. Outputs

Create structured artifacts under a dedicated Milestone 4 output directory, conceptually:

```text
outputs/m57-catalog/
    catalog_query.json
    gaia_sources.csv
    catalog_matches.csv
    catalog_match_summary.json
    catalog_overlay.png
```

JSON companions for row tables are optional if they add value and remain manageable.

## `catalog_query.json`

Record:

- provider/release/table;
- query centre;
- query radius/footprint;
- selected columns;
- query filters;
- row count;
- row-limit/truncation state;
- network/cache source;
- query timestamp;
- observation epoch used if any.

## `catalog_match_summary.json`

Record:

- input plate-solution/WCS provenance;
- input detection provenance;
- matching configuration;
- summary metrics;
- warnings;
- artifact paths.

Keep schema versioning consistent with the existing project conventions.

---

# 19. Diagnostic overlay

Generate `catalog_overlay.png` aligned exactly with the original image coordinates.

It should make the crossmatch visually inspectable without being unreadably cluttered.

Recommended semantics:

- accepted image detections: subtle marker;
- projected Gaia positions: distinct marker;
- successful matches: clear marker and/or short residual line between predicted and observed position;
- optional labels only for a limited number of bright/debug matches.

Do not label the Ring Nebula, M57, Messier/NGC names, or other deep-sky objects in this milestone.

Do not display hundreds of full Gaia IDs over the image.

If useful, put a compact legend/summary below the image rather than over important image content.

---

# 20. CLI

Add a command consistent with the existing CLI style.

Conceptually:

```bash
astroidentify catalog-match \
  data/raw/M57__Ring_Nebula-eQuinox-20260925-003755.png \
  --plate-solution outputs/m57-astrometry/plate_solution.json \
  --wcs outputs/m57-astrometry/solution.wcs \
  --output outputs/m57-catalog
```

Use the actual naming/API style that best matches the current CLI.

Prefer consuming existing saved WCS/detection products rather than re-running the 3 previous milestones every time, unless the existing architecture strongly favors a reusable pipeline composition.

CLI output should be concise and scientific, e.g.:

```text
Catalogue: Gaia DR3
Rows returned: ...
Rows in image: ...
Accepted detections: ...
Matches: ...
Median residual: ... arcsec (... px)
RMS residual: ... arcsec
Epoch propagation: yes/no
Matches: outputs/.../catalog_matches.csv
Overlay: outputs/.../catalog_overlay.png
Summary: outputs/.../catalog_match_summary.json
```

No target-name verdict.

---

# 21. Unit tests — no live network

All existing tests must continue to pass.

Add deterministic Milestone 4 tests.

## Footprint/query geometry

Test:

- synthetic WCS centre/corners;
- rotated WCS;
- farthest-corner cone radius;
- exact in-image pixel filtering;
- RA wrap case;
- finite/non-finite handling.

## Gaia provider

With mocks/fixtures test:

- correct query region;
- required columns;
- successful response parsing;
- null values;
- empty result;
- timeout;
- service error;
- cache behavior if implemented;
- truncation behavior if applicable.

## Projection

Test:

- known sky -> expected pixel;
- origin 0 behavior;
- no X/Y flip;
- no +1 leak from Astrometry.net source-list logic;
- rows just inside/outside image bounds.

## Matching

Test:

- perfect matches;
- small residuals;
- outside threshold rejection;
- two detections competing for one Gaia source;
- two Gaia sources competing for one detection;
- deterministic conflict resolution;
- stable tie behavior;
- unmatched detections;
- unmatched Gaia sources;
- output sorting/reproducibility.

## Epoch

If implemented:

- no reliable timestamp -> no propagation;
- reliable timestamp -> propagation;
- missing PM -> safe behavior;
- synthetic high proper motion -> expected shift.

## Outputs

Test:

- query JSON;
- match CSV;
- summary JSON;
- provenance;
- overlay alignment.

## CLI

Test success/failure using a fake catalogue provider.

Normal tests must not require Gaia/network access.

---

# 22. Live Gaia integration benchmark

After implementation/tests/lint are clean, run a separate live Gaia DR3 benchmark using the solved M57 field.

This is the first real external catalogue integration.

Use:

```text
data/raw/M57__Ring_Nebula-eQuinox-20260925-003755.png
outputs/m57-astrometry/plate_solution.json
outputs/m57-astrometry/solution.wcs
```

and the complete accepted Milestone 2 source catalogue.

Do not supply:

- M57 coordinates manually;
- M57 name;
- coordinates parsed from filename;
- a hand-selected Gaia source list around M57.

The query footprint must be derived from the WCS.

If live Gaia is unavailable, report integration-blocked accurately rather than fabricating data.

A cached response obtained from a successful real Gaia query may be used for repeatability after the initial live query, provided provenance says so.

---

# 23. Benchmark analysis

For the real benchmark report:

- query centre/radius;
- number of Gaia rows returned;
- number projected inside actual image bounds;
- accepted detections considered (expect 643 unless the accepted set changed for a justified reason);
- number matched;
- match fractions;
- median/RMS/max residual in arcsec and pixels;
- whether proper motion was applied;
- magnitude distribution of matched vs unmatched Gaia rows if easy/useful;
- runtime/query time;
- whether result was live or cached.

Inspect `catalog_overlay.png` visually.

Specifically check for:

- systematic offset in one direction;
- obvious rotation error;
- X/Y inversion;
- edge-dependent errors;
- matches landing on the correct bright stars;
- implausibly many crowded-field mismatches.

If a systematic offset appears, diagnose before increasing the match radius.

---

# 24. Do not overfit the benchmark

The benchmark filename says M57, but Milestone 4 must remain target-agnostic.

Never:

- special-case M57;
- inject its RA/Dec;
- choose Gaia stars because you know the field;
- tune source IDs manually;
- use a target-specific match radius;
- hide failed/unmatched data.

Any algorithmic improvement must be general and tested on synthetic cases.

---

# 25. README/documentation

Update README/status documentation to show:

```text
Milestone 1 complete
Milestone 2 complete
Milestone 3 complete
Milestone 4 current / complete depending on result
```

Document:

- what catalogue matching does;
- Gaia DR3 dependency;
- network behavior;
- query footprint method;
- match strategy;
- tolerance;
- output artifacts;
- benchmark command;
- cache behavior;
- known limitations;
- explicit statement that named-object identification is next milestone and not implemented yet.

---

# 26. Completion criteria

Do not call Milestone 4 complete until:

1. baseline M1–M3 tests still pass;
2. WCS footprint/query geometry is implemented and tested;
3. Gaia DR3 provider is implemented behind a clean boundary;
4. unit tests do not require network access;
5. catalogue positions project correctly into canonical image pixels;
6. actual-image filtering works for rotated fields;
7. the full accepted detection catalogue is used by default;
8. one-to-one matching is deterministic and tested;
9. every match preserves detection ID + Gaia source ID;
10. pixel + angular residuals are reported;
11. unmatched counts are reported;
12. query/match outputs are machine-readable;
13. catalogue overlay is generated;
14. network/cache failure modes are clear;
15. full tests pass;
16. Ruff lint/format pass;
17. a live Gaia query/crossmatch is attempted on the real solved benchmark;
18. the real overlay/residuals are inspected;
19. documentation is updated;
20. named-object identification has NOT been implemented.

Stop after this milestone.

---

# 27. Required final report

When finished, provide a structured report with:

## A. Baseline

- tests before changes;
- Ruff before changes;
- initial git status.

## B. Files changed

List every modified/created file and its purpose.

## C. Architecture

Briefly explain:

- catalogue provider boundary;
- footprint derivation;
- Gaia query;
- world-to-pixel projection;
- one-to-one matching;
- output flow.

## D. Matching policy

Report:

- match radius/tolerance;
- assignment algorithm;
- conflict handling;
- deterministic tie behavior.

## E. Epoch handling

State whether proper motion is implemented/applied and why.

## F. Real benchmark

Report exact values for:

```text
catalogue/release
query geometry
rows returned
rows in image
accepted detections
matches
detection match fraction
catalogue match fraction
median residual arcsec
RMS residual arcsec
max residual arcsec
median residual px
RMS residual px
epoch propagation
query runtime
total runtime
live/cache
```

## G. Visual validation

Describe what you inspected in `catalog_overlay.png`, especially any systematic offset/rotation/edge issue.

## H. Tests and lint

Exact final counts/results.

## I. Artifacts

Give exact paths to:

- `catalog_query.json`;
- Gaia source table;
- match table;
- summary JSON;
- overlay;
- any cache entry if relevant.

## J. Remaining limitations

Be explicit.

## K. Milestone status

Say either:

```text
Milestone 4 complete
```

or accurately describe the remaining integration blocker.

Do not begin Milestone 5.
