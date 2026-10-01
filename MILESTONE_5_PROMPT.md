# Milestone 5 Prompt — Object Annotation and Identification

Read `CLAUDE.md` completely before changing code.

Milestones 1–4.1 are complete. This task is **Milestone 5 only**.

Goal:

> Use AstroIdentify's solved/refined WCS to query a named astronomical-object catalogue, determine which catalogued objects lie in the image field, project them into pixels, associate them with image evidence where appropriate, and produce structured identifications plus an annotated image.

Do not implement Milestone 6 confidence calibration, ML verification, Solar-System support, FastAPI, frontend work, or deployment.

Do not commit unless explicitly asked.

## 1. Baseline first

Before production changes:

1. run the full current test suite;
2. run `ruff check`;
3. run `ruff format --check`;
4. record `git status`;
5. inspect current `catalogs/`, WCS loading, cache/query helpers, output conventions, CLI conventions and source models.

Report exact baseline counts.

Do not redesign previous milestones.

## 2. Preserve the benchmark-blind rule

Benchmark:

```text
data/raw/M57__Ring_Nebula-eQuinox-20260925-003755.png
```

The filename reveals the object identity.

Production code MUST NOT extract or use:
- `M57`;
- `Ring Nebula`;
- `NGC 6720`;
- known benchmark RA/Dec;
- any target-specific constant.

The only valid production path is:

```text
image
→ WCS
→ WCS-derived sky region
→ object catalogue query
→ pixel projection
→ filtering/association
→ annotation
```

Known target identity may only be used after output exists for human validation.

## 3. Create a focused object-identification package

Prefer something like:

```text
src/astroidentify/objects/
    __init__.py
    types.py
    footprint.py
    simbad.py
    naming.py
    filtering.py
    association.py
    overlay.py
    outputs.py
    pipeline.py
```

Adapt to repository structure where appropriate.

Keep network access isolated. Local projection/filtering/annotation must be unit-testable offline.

Do not build a generic plugin framework.

## 4. WCS selection

Default:

```text
refined_solution.wcs
    ↓ fallback
solution.wcs
```

unless an explicit WCS is provided.

Validate it and record:
- path;
- whether refined;
- source artifact.

Never overwrite either WCS.

## 5. Compute sky footprint

Using WCS and image width/height:

- compute sky coordinates of image corners;
- derive a conservative query region;
- handle rotation, RA wraparound and high declination;
- after remote retrieval, project every row into image coordinates for exact filtering.

A covering cone is acceptable for the remote query.

Do not manually reproduce Astropy WCS transformations.

## 6. SIMBAD query

Use SIMBAD as the Milestone 5 named-object source unless a concrete repository constraint prevents it.

Use an official structured interface such as TAP/ADQL. Do not scrape HTML.

Retrieve useful fields where available:

```text
main identifier
aliases
object type
RA
Dec
major axis
minor axis
position angle
magnitudes
redshift/basic metadata
```

Missing optional fields must remain optional.

Do not require every row to have angular size or magnitude.

## 7. Reuse M4-style caching

Implement deterministic caching consistent with the Gaia client where practical.

Record:
- service/table/query;
- query geometry;
- retrieval time;
- live vs cache;
- cache key;
- returned row count.

Support offline replay.

Normal tests must not use network.

A network failure without valid cache must be a clear error, not an empty successful field.

## 8. Typed object model

Create a typed representation including, where available:

```text
catalogue
catalogue_id
main_id
display_name
aliases
object_type
object_type_description
ra_deg
dec_deg
projected_x
projected_y
inside_image
footprint_intersects_image
major_axis
minor_axis
position_angle
magnitudes
redshift
association
provenance
```

Preserve raw catalogue identity even when a friendlier display name is chosen.

## 9. Display names

Implement a deterministic general preference, for example:

```text
Messier
→ NGC
→ IC
→ other common catalogue ID
→ SIMBAD main_id
```

Do not special-case the benchmark.

Test the naming logic with unrelated synthetic aliases.

## 10. Object-type policy

Gaia ordinary stars belong to Milestone 4.

Milestone 5 should retain/annotate named or scientifically meaningful non-stellar objects, such as:
- planetary nebulae;
- galaxies;
- clusters;
- nebulae;
- H II regions;
- supernova remnants;
- AGN/quasars where appropriate;
- other useful SIMBAD non-stellar classes.

Do not annotate every ordinary star.

Preserve raw query results even when filtered from annotation.

Document type filtering explicitly.

## 11. Project into image

Use the selected WCS with AstroIdentify canonical 0-based pixel coordinates.

Important:

> Do NOT apply the Astrometry.net XYLS `+1` conversion here.

That conversion belongs only at the solver-source boundary.

For every object compute:
- projected x/y;
- centre in image;
- whether angular footprint intersects image.

## 12. Handle angular extent

If major/minor axes and PA are available:

- verify units and convention;
- centralize conversion;
- use size for field intersection;
- draw an approximate ellipse/footprint only when reliable.

If size is absent:
- use a position marker;
- do not fabricate dimensions.

Include tests for an object's centre outside the frame while its extended footprint overlaps it.

## 13. Image association

Do not force all named objects to correspond to M2 point-source detections.

For compact objects:
- optionally associate nearest accepted detection;
- record pixel/angular separation;
- use a documented threshold.

For extended objects:
- report catalogue position/extent even without a point-source match;
- optionally compute simple, interpretable local-image evidence;
- do not build segmentation or ML.

A valid status can be:

```text
catalogued_in_field
```

even when:

```text
image_association = none
```

## 14. Identification semantics

Keep catalogue presence separate from visual association.

Use objective statuses rather than confidence percentages.

Do not call every returned catalogue row a confirmed visual detection.

A Milestone 5 identification should mean:

> The WCS places this catalogued object at this location in the image, with optional image-association evidence recorded separately.

Confidence comes later.

## 15. Optional primary-object heuristic

Only implement if simple and genuinely useful.

If implemented:
- keep it deterministic;
- label it explicitly as a heuristic;
- use general factors such as non-stellar type, centrality, angular extent, fraction of footprint in image and image association;
- do not assume nearest-to-centre is always the target;
- no learned weights;
- no probability.

It is acceptable to omit primary-object selection entirely in Milestone 5 and simply identify all meaningful objects.

## 16. Generate object overlay

Create:

```text
object_overlay.png
```

Requirements:
- unchanged image dimensions;
- WCS-accurate placement;
- readable labels;
- target-agnostic display names;
- point markers for unknown-size objects;
- footprints for reliable extended objects;
- limited clutter;
- no Gaia-star field overlay unless deliberately optional.

Visually inspect the benchmark output.

## 17. Outputs

Produce artifacts conceptually like:

```text
outputs/<name>-objects/
    object_query.json
    catalog_objects.csv
    catalog_objects.json
    object_associations.csv
    identification_summary.json
    object_overlay.png
```

Record in the summary:
- input hash/identity;
- WCS used;
- catalogue service;
- query geometry;
- cache/live state;
- rows returned;
- in-field objects;
- object types;
- annotated count;
- primary heuristic result if any;
- warnings;
- artifact paths.

No percentage confidence.

## 18. CLI

Add an `identify` command consistent with the existing CLI.

Conceptually:

```bash
astroidentify identify   data/raw/M57__Ring_Nebula-eQuinox-20260925-003755.png   --astrometry outputs/m57-astrometry-centroid-fixed   --catalog outputs/m57-catalog-centroid-fixed   --output outputs/m57-objects
```

Reuse existing artifacts. Do not unnecessarily rerun Gaia.

Useful options may include:
- `--wcs`
- `--cache-dir`
- `--offline`
- `--refresh`
- `--max-labels`

No target-name arguments.

## 19. Offline tests

Add comprehensive offline tests for:

### Footprint / WCS
- rotated field;
- RA wraparound;
- high declination;
- inside/outside projection;
- full corner coverage.

### SIMBAD parsing
- valid row;
- aliases;
- missing fields;
- object type;
- angular dimensions;
- malformed response;
- service failure.

### Naming
- Messier preferred;
- NGC fallback;
- IC fallback;
- canonical fallback;
- no M57-specific path.

### Filtering
- planetary nebula retained;
- galaxy retained;
- cluster retained;
- ordinary star excluded by default;
- unknown type deterministic.

### Extent
- point object;
- ellipse;
- centre outside but extent intersects;
- missing size.

### Association
- nearby compact source;
- source outside threshold;
- extended object without point detection;
- deterministic ties.

### Cache
- hit;
- miss;
- offline hit;
- offline miss;
- refresh;
- deterministic key.

### Overlay
- dimensions unchanged;
- known coordinate lands correctly;
- no axis flip;
- no off-by-one.

### CLI
- cached/mock success;
- missing WCS;
- network failure;
- offline behavior;
- valid empty field.

Add one opt-in live SIMBAD integration test.

## 20. Real benchmark

Run a fresh live query or fresh valid cache.

Do not provide any target hint.

Record:
- WCS used;
- query geometry;
- returned rows;
- retained in-field objects;
- aliases;
- object types;
- projected x/y;
- angular sizes;
- association status;
- runtime;
- cache status.

After the production result exists, manually inspect whether a catalogue object appears at the prominent central extended feature.

If it does, validate:
- identity/aliases;
- type;
- projected position;
- angular size if available;
- overlay alignment.

If it does not, debug query coverage, filters, fields, projection and cache—but never hard-code the expected object.

## 21. Documentation

Update README with:
- Milestone 5 purpose;
- SIMBAD requirement;
- cache/offline behavior;
- identify command;
- WCS choice;
- type-filter policy;
- naming rule;
- outputs;
- annotation example;
- limitations;
- explicit statement that confidence calibration is deferred.

## 22. Required final report

Return:

### A. Baseline
- exact tests;
- lint/format;
- git status.

### B. Architecture
- every new/modified file and responsibility.

### C. Catalogue query
- service/interface;
- query geometry;
- fields retrieved;
- cache behavior.

### D. Object policy
- included/excluded types;
- naming priority;
- angular extent logic;
- association rules.

### E. Benchmark result table

Report:

```text
WCS used
query rows
objects in field
objects annotated
runtime
live/cache
```

Then list important retained objects:

```text
display name
main ID
object type
RA/Dec
projected x/y
angular size
association status
```

### F. Central-object validation

Without using known identity as input, state what catalogue object the pipeline placed at/near the prominent central extended feature.

Explain exactly which WCS and catalogue data support that result.

No confidence percentage.

### G. Overlay
Describe visual inspection and any placement/clutter issues.

### H. Tests
Exact test count plus live-test status.

### I. Artifacts
Exact paths to:
- query metadata;
- catalog object table;
- associations;
- summary;
- overlay;
- cache/raw response.

### J. Limitations
Be explicit.

### K. Status
State whether Milestone 5 is complete.

Do NOT begin Milestone 6.
