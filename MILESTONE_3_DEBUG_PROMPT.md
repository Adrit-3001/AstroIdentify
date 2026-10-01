We need to debug Milestone 3 of AstroIdentify systematically.

Read the existing `CLAUDE.md` completely, including the new **Milestone 3 Integration Debugging** addendum. Treat the addendum as authoritative where it overrides earlier Milestone 3 rules.

Do NOT begin Milestone 4. Do NOT redesign Milestones 1 or 2. Do NOT try random solver flags before reproducing and classifying the failure.

## Goal
Determine why the real Unistellar M57 benchmark cannot currently be plate-solved by local Astrometry.net, then make only the general-purpose changes necessary for a production solve without a sky-position hint.

## Current repository state
Milestones 1 and 2 are complete. Milestone 3 already has:
- `src/astroidentify/astrometry/`;
- source selection;
- XYLS writer;
- local `solve-field` subprocess backend;
- WCS parser;
- overlays/diagnostics;
- structured outputs;
- `astroidentify solve`;
- mocked/fake solver tests;
- a real-binary synthetic integration test.

Run the current tests and ruff checks first; do not assume prior counts are still exact.

## Real benchmark

```text
data/raw/M57__Ring_Nebula-eQuinox-20260925-003755.png
2560 x 1920
```

Milestone 2 previously reported:

```text
2464 candidates
643 accepted
1821 rejected
129 saturated
116 saturated accepted
117 edge flagged
9 edge accepted
median accepted SNR ≈ 15.6
```

Milestone 3 previously selected:

```text
100 preferred sources
all unsaturated
all non-edge
selected from 511 eligible
15 spatial grid cells
~6–7 per cell
median selected SNR ≈ 29
```

## Installed Astrometry.net environment
The machine now has:

```text
astrometry.net 0.93
astrometry-data-tycho2
astrometry-data-2mass-06
astrometry-data-2mass-07
astrometry-data-2mass-08-19
```

Logs confirm both Tycho-2 and 2MASS indexes are actually searched. Do not treat missing 2MASS data as the explanation. Do not install more indexes unless diagnostics prove an uncovered scale is required.

## Failures already observed
You must account for all of these before changing code.

1. AstroIdentify XYLS, 100 selected sources, fully blind, Tycho-2 only -> timeout after 600 s, no WCS.
2. Same after installing 2MASS -> timeout after 600 s, no WCS; 2MASS indexes confirmed in logs.
3. Direct AstroIdentify XYLS with scale 0.5–1.2 arcsec/pixel, no RA/Dec -> did not solve in the test budget.
4. Direct raw PNG with Astrometry.net native extraction, scale 0.5–1.2, no RA/Dec -> `simplexy` found 16,479 sources and did not solve in the test budget.
5. Direct AstroIdentify XYLS with scale 0.75–0.95, 100 sources, no RA/Dec -> processed all groups through 91–100 and returned `Field 1 did not solve`; no WCS. A weak false hypothesis near RA 345.719°, Dec +58.3095°, scale 0.936915 arcsec/pixel with only 2 matches was correctly rejected.

The narrow run is especially important: do not assume the remaining issue is merely a broad scale search.

## Diagnostic-only position exception
For this debugging task ONLY, you are explicitly allowed to use the known approximate M57 position as a diagnostic oracle:

```text
RA ≈ 283.396 degrees
Dec ≈ +33.029 degrees
```

Use it only in direct diagnostic `solve-field` commands or an isolated diagnostic harness. Never add it to production defaults/configuration, never parse it from the filename, and never count a position-constrained solve as Milestone 3 success.

The diagnostic question is:

```text
Can Astrometry.net solve this image/source geometry at all when the search region is known?
```

## Phase 1 — Baseline and code inspection
Before modifying implementation:
1. run full tests;
2. run ruff lint/format checks;
3. record `git status` and relevant diff;
4. inspect source selection;
5. inspect XYLS writer;
6. inspect solver command builder;
7. inspect timeout semantics;
8. inspect coordinate conversion tests;
9. inspect M57 source catalogue and selection artifacts.

Do not commit anything.

## Phase 2 — Reproducible diagnostic harness
Create a clearly non-production diagnostic harness under `scripts/` or equivalent if useful. Do not add a public user-facing diagnostic command unless there is a strong reason.

Use:

```text
outputs/m57-astrometry-diagnostics/
```

Create `run_manifest.json` recording for every run:
- run name;
- raster vs XYLS;
- source policy/count/order;
- saturated-source policy;
- scale bounds;
- whether RA/Dec hint was used;
- search radius;
- exact `solve-field` args;
- timeout/CPU limit;
- runtime;
- exit status;
- solved/unsolved/timeout/error;
- WCS path;
- centre/scale/FOV/orientation/parity when solved;
- correspondence count/residuals when available;
- solver log path.

No online plate-solving service.

## Phase 3 — Known-position raw-raster diagnostic
Run local Astrometry.net on the ORIGINAL PNG with:
- diagnostic RA/Dec above;
- a generous radius;
- a broad plausible scale range, initially around 0.3–2.5 arcsec/pixel unless you justify another range;
- local indexes only;
- no object-name lookup.

Determine:
1. Does the raw PNG solve when the sky region is known?
2. If yes, what is actual pixel scale?
3. What are FOV, orientation and parity?
4. What match/residual quality is reported?
5. Did previous scale bounds contain the solved value?

If this fails, do not jump to source-selection changes. Investigate native extraction, actual scale, index coverage and processed-image distortion.

## Phase 4 — Known-position AstroIdentify XYLS matrix
If the raster can solve, run the same diagnostic position against AstroIdentify source lists. Compare at minimum:

A. current spatially balanced preferred 100;
B. brightest accepted top 100 with spatial balancing disabled;
C. brightest accepted top 200;
D. brightest accepted top 400 where feasible;
E. one or more variants including suitable saturated stars;
F. high-SNR ordering if materially different from flux order.

Preserve exact source IDs and XYLS artifacts for each run. Determine whether a particular ranking/subset solves reliably.

## Phase 5 — Coordinate convention verification
If raw raster solves but XYLS fails, treat coordinate conversion as a prime suspect.

Canonical AstroIdentify coordinates:

```text
x: left -> right
y: top -> bottom
0-based pixel centres
```

The current implementation reportedly maps solver coordinates as canonical + 1. Do not trust synthetic evidence alone. Verify against the real binary and real image.

Explicitly test/verify:
- x origin;
- y origin;
- y-axis direction;
- image-height conversion;
- parity/reflection behavior.

Temporary diagnostic XYLS variants with vertical/horizontal/both flips are allowed. Do not permanently change the mapping unless a diagnostic proves the current mapping wrong. If a bug is found, centralize the fix and add a regression test that fails under the old behavior.

## Phase 6 — Determine empirical scale
If ANY known-position diagnostic solves, derive pixel scale and FOV from the solved WCS. Treat this as stronger evidence than the prior 0.5–1.2 or 0.75–0.95 assumptions.

Explain whether prior scale bounds actually contained the solved scale. Do not hard-code an M57-specific scale globally unless it follows from a reusable telescope/export profile.

## Phase 7 — Distortion / processed-export investigation
If raster and XYLS behavior differs or residuals vary strongly across the field, inspect for:
- resampling;
- stacking;
- digital upscaling;
- nonlinear warping;
- field distortion;
- edge-dependent centroid shifts.

Use correspondences/residuals where available. Inspect Astrometry.net 0.93 help/documentation before using SIP/tweak/distortion controls. Do not blindly add high-order distortion fitting.

## Phase 8 — Source selection changes only if diagnostics justify them
Current policy roughly prioritizes unsaturated, non-edge, spatially balanced sources.

Do not change it merely because blind solving failed. Change it only if controlled tests demonstrate a better target-agnostic policy.

Potential general changes may include:
- bright saturated sources earlier;
- closer correspondence to catalogue brightness ranking;
- less aggressive spatial balancing;
- more than 100 sources;
- separate solver-rank vs detection-quality rank.

Any production change must be deterministic, target-agnostic and regression-tested.

## Phase 9 — Fix solver scheduling
There is already a clear scheduling issue: attempt 1 can consume the entire wall-clock budget and prevent fallbacks.

After diagnosing the solving behavior, implement:
- separate per-attempt timeout and overall timeout;
- one timeout does not automatically abort all fallbacks;
- scale-constrained attempt runs early when `--scale-low/--scale-high` are explicitly supplied;
- metadata records every attempt and runtime;
- statuses distinguish timeout, unsolved, solver error and solved.

Add tests.

## Phase 10 — Production retry
After understanding the root cause and making only necessary fixes, retry the normal benchmark.

The FINAL benchmark must not use:
- RA hint;
- Dec hint;
- M57 target name;
- filename parsing;
- constellation knowledge.

A generic camera-derived scale constraint is allowed.

Acceptable final modes:

```text
fully blind
```

or

```text
position-blind, camera-scale-constrained
```

Do not proceed to catalogue/object identification.

## Phase 11 — Final validation
If a no-position-hint WCS is obtained:
1. inspect `wcs_overlay.png`;
2. inspect source-selection overlay;
3. inspect correspondence residuals;
4. verify pixel -> sky -> pixel round trips;
5. verify centre/corners/FOV/scale/orientation;
6. confirm diagnostic coordinates did not leak into production command/config;
7. run full tests;
8. run ruff checks.

## Required final report
Report:

1. Baseline tests/lint/git state before changes.
2. A diagnostic matrix table with run, input, source policy, source count, position hint, scale bounds, result, runtime, solved scale and notes.
3. Evidence-backed root cause; separate multiple causes if necessary.
4. Every file modified and why.
5. Source-selection findings.
6. Coordinate-convention findings.
7. Empirical scale/FOV findings.
8. Distortion findings.
9. Solver scheduling changes.
10. Final production benchmark exact command and result, including whether it is fully blind or camera-scale-constrained, centre RA/Dec, scale, FOV, orientation/parity, match count, residuals, runtime and successful attempt.
11. Exact tests/lint results.
12. Paths to diagnostic manifest, final plate solution, WCS, WCS overlay, source-selection overlay, correspondences and solver log.
13. Remaining limitations.

Do not call Milestone 3 complete unless the real benchmark solves without a sky-position hint.
Do not begin Milestone 4.
