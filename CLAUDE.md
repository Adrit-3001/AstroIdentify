# CLAUDE.md — AstroIdentify

## 1. Project Overview

AstroIdentify is an extensible astronomy/computer-vision project that will eventually accept an astronomical image with little or no user-provided context, determine where in the sky the image was taken, identify catalogued objects in the field, annotate the image, and provide interpretable evidence/confidence for its identifications.

The project must be developed incrementally. Each milestone should produce a working, testable system and should not prematurely implement later milestones.

### Long-term vision

Eventually the pipeline may include:

1. Image ingestion and preprocessing
2. Astronomical source/star detection
3. Astrometric plate solving / WCS
4. Catalogue matching (e.g. Gaia, SIMBAD)
5. Object annotation and identification
6. Evidence/confidence estimation
7. Computer-vision / ML verification
8. Solar-system object support
9. FastAPI backend
10. Web frontend and deployment

The current focus is **Milestone 1 only**.

---

## 2. Current Milestone: Milestone 1 — Image Ingestion and Preprocessing

Build a reliable preprocessing foundation for astronomical images.

The milestone must support:

- JPEG/JPG
- PNG
- FITS

The pipeline should:

1. Load the image.
2. Preserve original image information whenever practical.
3. Convert the image into a consistent internal numerical representation.
4. Handle grayscale and RGB images.
5. Estimate image background.
6. Estimate image noise.
7. Normalize the image for downstream analysis.
8. Produce useful diagnostic information.
9. Save a processed representation and preview.
10. Expose the functionality through both Python APIs and a CLI.

Do **not** implement source detection, plate solving, catalogue queries, ML classification, FastAPI, frontend code, or deployment in this milestone.

Design for them, but do not build them yet.

---

## 3. Engineering Principles

### Modularity

Keep astronomy logic separated by responsibility.

Prefer small modules with explicit interfaces over large scripts.

The project should be easy to extend with modules such as:

```text
src/astroidentify/
    preprocessing/
    detection/
    astrometry/
    catalogs/
    annotation/
    confidence/
    models/
```

Only `preprocessing` needs substantial implementation during Milestone 1.

### Library first, CLI second

Core functionality belongs in importable Python modules.

CLI commands should call library functions instead of containing business logic.

Bad:

```python
# CLI contains all image loading and processing logic
```

Good:

```python
result = preprocess_image(path, config)
```

and the CLI simply exposes that functionality.

### No premature ML

Do not use neural networks where standard astronomical/image-processing techniques are sufficient.

Milestone 1 requires no ML.

### Preserve scientific information

Do not apply aggressive beautification, denoising, sharpening, or transformations that could destroy faint astronomical signals.

A preview image may be contrast-stretched for visualization, but the scientific/internal processed array must remain clearly distinguished from display-only output.

### Reproducibility

Processing should be deterministic unless randomness is explicitly required.

Configuration values must be explicit and testable.

### Type safety

Use Python type hints throughout public interfaces.

Prefer `pathlib.Path` over raw path strings internally.

### Error handling

Fail with clear domain-specific errors.

Examples:

- unsupported image format
- corrupt image
- malformed FITS file
- FITS file without usable image data
- invalid dimensions
- output path failure

Do not silently swallow errors.

### Logging

Use Python's `logging` module.

Library code must not print directly to stdout.

CLI code may display user-facing summaries.

### Avoid unnecessary abstraction

Build interfaces that support future milestones, but do not construct unused plugin systems, dependency-injection frameworks, database layers, cloud services, or web infrastructure.

---

## 4. Recommended Repository Structure

Use this structure unless there is a strong technical reason to adjust it:

```text
astroidentify/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── .gitignore
├── src/
│   └── astroidentify/
│       ├── __init__.py
│       ├── config.py
│       ├── exceptions.py
│       ├── logging.py
│       ├── types.py
│       └── preprocessing/
│           ├── __init__.py
│           ├── loader.py
│           ├── background.py
│           ├── normalize.py
│           ├── pipeline.py
│           └── preview.py
├── scripts/
├── tests/
│   ├── preprocessing/
│   └── fixtures/
├── data/
│   ├── raw/
│   ├── processed/
│   └── benchmark/
└── outputs/
```

Do not commit large astronomy datasets or generated outputs.

Use `.gitkeep` where necessary.

---

## 5. Python and Dependencies

Target modern Python, preferably Python 3.11+.

Prefer mature scientific packages:

- numpy
- astropy
- Pillow
- matplotlib where useful
- pytest
- optional: scipy if justified

Do not add OpenCV unless it is actually required in the current milestone.

Use a `pyproject.toml` based setup.

Keep runtime and development dependencies clearly separated.

---

## 6. Internal Image Representation

Create a clear internal representation for loaded/preprocessed images.

A useful design is a dataclass such as:

```python
@dataclass
class AstronomyImage:
    data: np.ndarray
    source_path: Path
    original_shape: tuple[int, ...]
    is_color: bool
    metadata: dict[str, Any]
```

A preprocessing result may contain:

```python
@dataclass
class PreprocessingResult:
    image: AstronomyImage
    normalized: np.ndarray
    background_level: float
    noise_sigma: float
    diagnostics: dict[str, Any]
```

These are examples, not mandatory exact implementations.

Important requirements:

- downstream modules should not need to know whether the source was PNG, JPEG, or FITS;
- FITS metadata should be preserved when available;
- image arrays should use well-defined numeric dtypes;
- NaN/Inf values must be handled deliberately.

---

## 7. FITS Handling

FITS is scientifically important and should be treated as a first-class format.

Use `astropy.io.fits`.

Requirements:

- locate a usable image HDU;
- preserve relevant headers/metadata;
- support common 2-D FITS images;
- fail clearly on unsupported dimensionality rather than guessing;
- handle NaN/Inf values intentionally;
- do not normalize away meaningful dynamic range during loading.

If a FITS file contains multiple potentially usable image HDUs, choose a deterministic documented strategy.

---

## 8. Background and Noise Estimation

Implement robust initial estimates.

Do not assume the image background is exactly black.

A reasonable first implementation may use robust statistics such as:

- median for background level;
- MAD-based estimate for noise.

For example:

```text
sigma ≈ 1.4826 × median(|x - median(x)|)
```

If a more astronomy-specific method is implemented, document why.

Keep the estimator modular because later milestones may replace it with tiled/background-map estimation.

---

## 9. Normalization

Normalization is for downstream numerical stability, not cosmetic enhancement.

Requirements:

- deterministic;
- resistant to isolated bright stars;
- documented;
- should not mutate the raw input;
- avoid clipping faint sources unnecessarily.

The result should normally be a floating-point array.

If percentile-based scaling is used, expose relevant percentiles through configuration.

---

## 10. Preview Generation

Preview output is for humans and may use display-specific stretching.

Keep this separate from the scientific processed representation.

A preview should:

- be easy to inspect;
- preserve aspect ratio;
- work for grayscale and color inputs;
- not overwrite source files;
- be saved to a predictable output path.

It is acceptable for preview generation to use percentile contrast stretching or another documented visualization transform.

---

## 11. CLI

Provide a simple CLI suitable for development.

Target usage such as:

```bash
astroidentify preprocess data/raw/m57.jpg --output outputs/m57
```

or, if console entry points are not yet configured:

```bash
python -m astroidentify.preprocessing.pipeline data/raw/m57.jpg
```

The command should report concise information such as:

```text
Input: data/raw/m57.jpg
Dimensions: 1920 x 1080
Format: JPEG
Background estimate: 12.7
Noise sigma: 3.4
Processed array: outputs/m57/processed.npy
Preview: outputs/m57/preview.png
Metadata: outputs/m57/metadata.json
```

Do not dump large arrays to stdout.

---

## 12. Output Artifacts

For a successful preprocessing run, produce structured outputs similar to:

```text
outputs/<image-name>/
    processed.npy
    preview.png
    metadata.json
```

`metadata.json` should contain machine-readable diagnostics such as:

```json
{
  "source": "m57.jpg",
  "width": 1920,
  "height": 1080,
  "channels": 3,
  "background_level": 12.7,
  "noise_sigma": 3.4,
  "normalization": {
    "method": "percentile",
    "lower_percentile": 1.0,
    "upper_percentile": 99.5
  }
}
```

The exact schema may evolve, but centralize serialization so future versions remain manageable.

---

## 13. Tests

Milestone 1 is not complete without automated tests.

At minimum test:

### Loading

- grayscale PNG
- RGB PNG/JPEG
- FITS image
- unsupported extension
- missing file
- corrupt file where practical

### Processing

- output shape is correct
- normalized output is finite
- source input is not unexpectedly mutated
- background estimate behaves correctly on controlled synthetic data
- noise estimate behaves correctly on controlled synthetic data
- NaN/Inf handling is deterministic

### Outputs

- preview is generated
- processed `.npy` can be reloaded
- metadata JSON is valid
- CLI returns success/failure codes correctly

Use synthetic arrays for numerical unit tests rather than relying only on real astronomy images.

---

## 14. Quality Gates

Before declaring Milestone 1 complete:

1. `pytest` passes.
2. Formatting/linting passes if configured.
3. At least one JPEG/PNG astronomy image works end-to-end.
4. At least one FITS image works end-to-end.
5. CLI generates processed array, preview, and metadata.
6. README documents installation and Milestone 1 usage.
7. No later-milestone functionality has been unnecessarily implemented.
8. Public APIs have type hints and docstrings where useful.
9. No large generated data is committed.
10. The codebase remains simple enough to understand.

---

## 15. Future Compatibility Requirements

Although they must not be implemented now, Milestone 1 should make these future operations easy:

```python
processed = preprocess_image(path)
sources = detect_sources(processed)
solution = solve_astrometry(sources, processed)
objects = query_catalogs(solution)
annotation = annotate_image(processed, objects)
```

The preprocessing result therefore needs to preserve enough information for:

- pixel coordinates;
- original dimensions;
- astronomical metadata;
- later WCS attachment;
- image display/annotation;
- ML crops.

Avoid any design that forces future modules to reload or reinterpret the original image independently.

---

## 16. Coding Style

- Write readable Python over clever Python.
- Keep functions focused.
- Prefer descriptive names.
- Avoid giant classes.
- Avoid deep inheritance.
- Document non-obvious astronomy/math decisions.
- Keep configuration centralized.
- Keep constants out of implementation code where they may need tuning.
- Avoid duplicate conversion/normalization logic.
- Use comments to explain *why*, not restate *what* the code says.

---

## 17. How to Work on Tasks

When implementing a task:

1. Inspect the existing repository first.
2. Preserve functioning code unless there is a reason to change it.
3. State assumptions when an implementation choice materially affects behavior.
4. Implement the smallest coherent solution.
5. Add/update tests with the implementation.
6. Run relevant tests.
7. Fix regressions before stopping.
8. Update README when user-facing usage changes.
9. Summarize exactly what changed and any remaining limitations.

Do not claim tests passed unless they were actually run successfully.

---

## 18. Current Definition of Done

Milestone 1 is complete when a user can provide a supported astronomy image and run one command that:

1. loads it;
2. validates it;
3. creates a consistent scientific numerical representation;
4. estimates background and noise;
5. normalizes it;
6. saves the processed array;
7. creates a visual preview;
8. saves structured metadata;
9. handles errors cleanly;
10. passes the automated test suite.

Stop there.

The next milestone will introduce astronomical source/star detection, but it should only begin after the Milestone 1 output has been reviewed.
