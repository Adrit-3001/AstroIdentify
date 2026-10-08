# Milestone 6.1 Prompt — Object Inspection / Field Visualization

Read `CLAUDE.md` completely before changing code.

Milestones 1–6 are complete. This task is **Milestone 6.1 only**.

Goal:

> Let a user select an object AstroIdentify has already identified and generate a scientifically faithful inspection view showing exactly where it lies, with display-only stretching and optional bright reference stars.

The motivating case is the North America Nebula field: NGC 7000 is correctly returned by SIMBAD and projected into the image, but the diffuse nebulosity is hard to see in the raw display.

Do not begin Milestone 7.

Do not change detection, astrometry, Gaia matching, SIMBAD identification, or evidence grading unless a verified compatibility defect is found.

Do not commit unless explicitly asked.

## 1. Baseline

Before changes:
1. run all tests;
2. run ruff check and format check;
3. record git status;
4. inspect current object outputs and Gaia catalogue outputs;
5. inspect current overlay utilities and image-loading code.

Report exact baseline counts.

## 2. Preserve scientific/display separation

This feature is visualization only.

The stretched image must never feed into:
- preprocessing;
- source detection;
- astrometric centroiding;
- plate solving;
- Gaia matching;
- WCS refinement;
- SIMBAD querying;
- object association;
- Milestone 6 evidence scoring.

Add tests protecting this boundary where practical.

## 3. Add an object-inspection command

Implement a CLI command consistent with the project, conceptually:

```bash
astroidentify inspect-object   data/raw/test_field_03.png   --objects outputs/test-field-03-objects   --catalog outputs/test-field-03-catalog   --object "NGC 7000"   --output outputs/test-field-03-ngc7000
```

It must operate from saved artifacts.

`--object` is a post-identification selector.

Do not re-run SIMBAD simply because an object is selected.

## 4. Object selection

Allow deterministic exact matching against:
1. display name;
2. main ID;
3. aliases.

Normalize harmless whitespace/case only.

If multiple objects match an alias, fail clearly and list candidates.

If no object matches, fail clearly.

Record how selection occurred.

## 5. Display stretch

Implement display-only modes:

```text
none
asinh
percentile
```

Default to `asinh` unless existing conventions strongly suggest otherwise.

Requirements:
- deterministic;
- same width/height;
- no geometric warp;
- no scientific artifact overwritten;
- simple configurable parameters;
- metadata records parameters;
- preserve RGB colour reasonably.

## 6. Full inspection overlay

Generate:

```text
inspection_full.png
```

Show:
- selected object's projected centre;
- display name;
- main ID if different;
- object type;
- extent/ellipse if reliable;
- north/east orientation marker if practical;
- scale bar;
- nearby bright reference stars.

Make the selected object visually dominant.

Do not draw every Milestone 5 object by default.

## 7. Zoom inspection

Generate:

```text
inspection_zoom.png
```

Centre the crop on the selected projected position.

If reliable extent exists:
- choose a crop that includes it plus context.

If extent is missing or larger than the frame:
- use a deterministic contextual crop;
- explicitly state that it is not the measured object boundary.

Record crop bounds.

## 8. Reference stars

Use existing Milestone 4 Gaia products.

Select the brightest nearby stars, default around 15.

Rank by Gaia brightness.

Show compact markers.

For labels:
- prefer already available human-readable names;
- otherwise use compact Gaia/HD/HIP identifiers only when helpful;
- unnamed references may remain unlabeled.

Do not modify Milestone 5's ordinary-star filtering policy.

Useful options may include:

```text
--reference-stars 15
--label-reference-stars
```

Avoid clutter.

## 9. Extent handling

If the selected object has reliable major/minor axes and PA:
- project and draw the footprint with WCS.

If extent is missing:
- use a marker only;
- write `extent_available=false`;
- record a warning that no visual boundary is claimed.

If the object exceeds the frame:
- do not draw misleading closed boundaries;
- use supported visible footprint geometry only;
- report that it extends beyond frame.

Do not hard-code NGC 7000 dimensions.

## 10. Metadata

Write:

```text
inspection_summary.json
```

Include:
- schema version;
- input image;
- selected catalogue object;
- selection method;
- aliases;
- object type;
- RA/Dec;
- projected x/y;
- WCS source/path;
- extent metadata;
- visible fraction if known;
- stretch mode/parameters;
- reference-star policy/count;
- crop bounds;
- warnings;
- artifact paths.

## 11. North America Nebula benchmark

Use:

```text
data/raw/test_field_03.png
```

and existing object/catalog products.

Select:

```text
NGC 7000
```

only from saved Milestone 5 outputs.

This is post-identification visualization, so the selector is allowed.

Verify:
- the selected row is the previously identified NGC 7000 object;
- projected centre remains consistent with existing output;
- display stretch makes faint large-scale nebulosity easier to inspect;
- bright reference stars provide landmarks;
- no object name is fed back into any scientific stage;
- if no reliable extent exists, the output clearly says only the catalogue centre is known from this row.

Also test one compact object to ensure generic behavior.

## 12. Tests

Add offline tests for:
- display-name selection;
- main-ID selection;
- alias selection;
- case/whitespace normalization;
- ambiguous alias;
- missing object;
- asinh determinism;
- percentile determinism;
- unchanged image geometry;
- marker coordinate correctness;
- no +1 offset;
- extent projection;
- no-size fallback;
- reference-star brightness ranking;
- reference-star count limit;
- crop bounds;
- summary serialization;
- no upstream artifact mutation.

Normal tests must not require network access.

## 13. README

Document:
- purpose of `inspect-object`;
- display-only stretching;
- example command;
- reference-star option;
- catalogue centre vs catalogue extent;
- limitations for large/diffuse objects;
- North America Nebula as an example only.

## Required final report

### A. Baseline
Tests, lint, git status.

### B. Architecture
Files/modules added or modified.

### C. CLI
Exact command/options implemented.

### D. Stretch
Algorithms and parameters.

### E. Reference stars
Selection/ranking/label policy.

### F. Extent behavior
Reliable/missing/oversized handling.

### G. NGC 7000 inspection
Report:
- selected identity;
- catalogue type;
- aliases;
- projected x/y;
- extent availability;
- stretch mode;
- reference stars shown;
- crop bounds;
- manual visual result.

### H. Tests
Exact counts and lint status.

### I. Artifacts
Exact paths to:
- `inspection_full.png`
- `inspection_zoom.png`
- `inspection_summary.json`

### J. Limitations
Especially missing nebular extent data.

### K. Status
State whether Milestone 6.1 is complete.

Do NOT begin Milestone 7.
