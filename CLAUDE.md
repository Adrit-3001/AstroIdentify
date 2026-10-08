# CLAUDE.md Update — Milestone 6.1: Object Inspection / Field Visualization

## Roadmap update

1. ✅ Image ingestion and preprocessing
2. ✅ Astronomical source/star detection
3. ✅ Blind astrometric plate solving / WCS
4. ✅ Gaia DR3 catalogue matching
4.1. ✅ Saturated-star astrometric centroid calibration
5. ✅ Object annotation and identification
6. ✅ Evidence and confidence estimation
6.1. 🚧 Object inspection / field visualization
7. ⬜ Computer-vision / ML verification
8. ⬜ Solar-system support
9. ⬜ FastAPI backend
10. ⬜ Web frontend and deployment

**Current focus: Milestone 6.1 only. Do not begin Milestone 7.**

## Purpose

Milestone 6.1 is a small usability/visualization milestone.

The scientific pipeline already identifies catalogue objects correctly, but diffuse objects can be difficult to see in the raw telescope image. This milestone must let a user select an already identified object and visually inspect where it lies without changing any scientific measurements or identification logic.

Primary use case:

```text
AstroIdentify identifies NGC 7000 in the field
        ↓
user selects NGC 7000 for inspection
        ↓
tool renders a stretched view with its catalogue position/extent
and nearby bright reference stars
```

This must remain generic and work for any object already present in Milestone 5 outputs.

The selected object name is allowed only as a post-identification selector. It must never influence detection, plate solving, catalogue querying, evidence grading, or any upstream result.

## Scientific/display separation

Keep analysis pixels and display pixels separate:

```text
original image
    ├── scientific pipeline → unchanged
    └── display-only stretch → inspection image
```

No display stretch may feed back into preprocessing, detection, astrometry, Gaia matching, SIMBAD querying, association, or evidence scoring.

## Object inspection command

Add a command or equivalent workflow conceptually like:

```bash
astroidentify inspect-object   data/raw/example.png   --objects outputs/example-objects   --catalog outputs/example-catalog   --object "NGC 7000"   --output outputs/example-inspection
```

`--object` is post-identification only.

Match deterministically against:
- display name;
- main ID;
- exact aliases.

Normalize only harmless whitespace/case differences. Do not silently fuzzy-match.

If multiple objects match, fail with an ambiguity message. If none match, fail clearly.

## Inspection view

The output should make the selected object's location visually obvious.

Include:
- projected catalogue centre;
- display name and type;
- angular extent when reliable;
- WCS-derived orientation;
- scale bar;
- optional N/E indicator;
- nearby bright reference stars;
- full-frame context;
- zoomed crop where useful.

If catalogue angular size is unavailable, show a centre marker and explicitly state that no reliable extent is available.

Do not invent a nebular outline.

## Display-only stretch

Support at least:

```text
none
asinh
percentile
```

A reasonable default for faint deep-sky inspection is `asinh`.

The stretch must:
- preserve geometry exactly;
- never warp the image;
- remain deterministic;
- record parameters in metadata;
- preserve colour reasonably for RGB input.

Conceptual option:

```text
--stretch none|asinh|percentile
```

Do not implement destructive or generative enhancement.

## Bright reference stars

Add an optional overlay of the brightest nearby stars using already available Gaia/Milestone 4 products.

Requirements:
- default to a small number, roughly 10–20;
- rank by Gaia brightness;
- use human-readable names only when already available cleanly;
- otherwise use compact Gaia/HD/HIP identifiers only when helpful;
- unnamed stars may be markers without labels;
- do not modify Milestone 5's default star-filter policy.

Conceptual options:

```text
--reference-stars 15
--label-reference-stars
```

These stars are visual landmarks, not new identification evidence.

## Extent behavior

If Milestone 5 provides reliable major/minor axes and PA:
- project the footprint accurately;
- draw it on the inspection view.

If the selected object has no reliable extent:
- show only the catalogue centre;
- do not manufacture a radius.

If the object is larger than the frame:
- avoid misleading closed boundaries;
- show only supported visible arcs/footprint geometry;
- record that the object extends beyond the image.

Do not hard-code NGC 7000 dimensions.

## Outputs

Prefer:

```text
inspection_full.png
inspection_zoom.png
inspection_summary.json
```

The zoom must be contextual unless catalogue extent is actually known.

`inspection_summary.json` should record:
- input image;
- selected object ID/name/type;
- selection method;
- aliases;
- catalogue RA/Dec;
- projected x/y;
- WCS source/path;
- extent metadata;
- visible fraction if known;
- stretch mode/parameters;
- reference-star count;
- crop bounds;
- warnings;
- artifact paths.

## North America Nebula case

The current NGC 7000 SIMBAD row may not provide a reliable visual nebular extent.

The tool must still show:
- exact WCS-projected catalogue position;
- aliases;
- catalogue type;
- stretched surrounding field;
- nearby bright reference stars.

If no reliable extent exists, say so explicitly.

Do not hard-code a North America Nebula outline or size.

## Tests

All previous tests must remain green.

Add offline tests for:
- selection by display name;
- selection by main ID;
- selection by alias;
- ambiguous alias failure;
- unknown object failure;
- no hidden +1 offset;
- asinh/percentile determinism;
- unchanged image dimensions;
- marker coordinate correctness;
- extent projection;
- no-size fallback;
- reference-star ranking/count limit;
- full-view and crop metadata;
- no upstream artifact mutation.

Normal tests must not use network access.

## Definition of done

Milestone 6.1 is complete when:
1. previous tests remain green;
2. an identified object can be selected by name/ID/alias;
3. a display-only stretched view is generated;
4. the projected object location is obvious;
5. reliable extent is shown where available;
6. missing extent is explicitly reported;
7. bright reference stars can be shown without changing Milestone 5 filtering;
8. full context and zoom are available where appropriate;
9. metadata records every display transform;
10. no scientific stage consumes the stretched image;
11. the NGC 7000 test field is manually inspected;
12. ruff lint/format checks pass;
13. Milestone 7 is not started.

Stop there.
