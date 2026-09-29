We are starting a new project called **AstroIdentify**.

Read `CLAUDE.md` completely before changing anything. Treat it as the project's engineering contract.

For now, implement **Milestone 1 only: image ingestion and preprocessing**. Do not implement star detection, plate solving, catalogue queries, ML, FastAPI, frontend code, or any later milestone.

### Goal

I want to be able to give the program an astronomical image (`.jpg`, `.jpeg`, `.png`, or `.fits`) and run a command that:

1. loads and validates the image;
2. converts it into a consistent internal numerical representation;
3. preserves useful FITS metadata when present;
4. estimates the background level;
5. estimates image noise using a robust method;
6. normalizes the image for later scientific/computer-vision processing;
7. saves the processed numerical array;
8. creates a human-viewable preview;
9. writes machine-readable metadata/diagnostics;
10. reports useful errors for unsupported or invalid files.

Design the code so later modules can consume the preprocessing result without needing to know whether the original source was JPEG, PNG, or FITS.

### Architecture

Create a clean Python package under `src/astroidentify/`. Use modular preprocessing components rather than putting everything in one script.

A reasonable starting structure is:

```text
src/astroidentify/
    __init__.py
    config.py
    exceptions.py
    types.py
    preprocessing/
        __init__.py
        loader.py
        background.py
        normalize.py
        preview.py
        pipeline.py
```

You may improve this structure if there is a concrete reason, but keep it simple and extensible.

Use a `pyproject.toml` setup and Python 3.11+.

Prefer:

- NumPy
- Astropy
- Pillow
- pytest
- only other dependencies that are actually justified

Do not introduce OpenCV, ML frameworks, databases, web frameworks, Docker, cloud infrastructure, or other future dependencies yet unless they are genuinely necessary for Milestone 1.

### Internal API

Create typed dataclasses or equivalent models for the loaded image and preprocessing result.

The downstream interface should be conceptually simple, for example:

```python
result = preprocess_image(path, config)
```

The result should expose at least:

- source information;
- original dimensions;
- processed image array;
- normalized array;
- background estimate;
- noise estimate;
- preserved metadata;
- diagnostics needed by future stages.

Do not mutate the original input image/array unexpectedly.

### FITS

Treat FITS as a first-class scientific input.

Use `astropy.io.fits`.

Implement a deterministic way to choose a usable 2-D image HDU. Preserve useful FITS header metadata. Handle NaN/Inf values deliberately. If the FITS structure is unsupported, fail clearly instead of guessing.

### Background/noise

For the first implementation, robust statistics are fine. A median-based background estimate and MAD-based noise estimate are acceptable.

Keep this logic isolated so we can replace it later with more advanced astronomical background estimation.

### Normalization

Normalization must be deterministic and intended for downstream processing, not merely appearance.

Do not aggressively clip faint astronomical signals.

Keep display stretching for preview generation separate from the scientific normalized representation.

### CLI

Create a convenient command, ideally:

```bash
astroidentify preprocess path/to/image.jpg --output outputs/example
```

If you use a different command shape, document it clearly.

A successful run should create something like:

```text
outputs/example/
    processed.npy
    preview.png
    metadata.json
```

and print a concise summary containing dimensions, format, background estimate, noise estimate, and output locations.

### Testing

Write meaningful automated tests while implementing the functionality.

At minimum include tests for:

- grayscale image loading;
- RGB image loading;
- FITS loading;
- missing files;
- unsupported formats;
- output shape/dtype;
- background estimation on synthetic controlled data;
- noise estimation on synthetic controlled data;
- NaN/Inf handling;
- preview generation;
- metadata JSON;
- CLI success and failure behavior.

Use synthetic test data where it gives us deterministic numerical expectations.

### README

Create or update `README.md` with:

- project purpose;
- current milestone;
- installation instructions;
- example preprocessing command;
- output explanation;
- supported formats;
- testing command;
- note that source detection/astrometry/ML are intentionally future milestones.

### Work process

Before implementing, inspect the repository and briefly state what already exists and what you are going to add.

Then implement the milestone completely.

Run the tests and any configured lint/format checks. Fix failures before stopping.

Do not tell me that something passed unless you actually ran it.

At the end, give me:

1. files created/modified;
2. architecture summary;
3. exact setup/install commands;
4. exact command I should run on one of my astronomy images;
5. expected outputs;
6. test results;
7. any limitations or decisions I should know about.

Most importantly: **stop after Milestone 1.** I will run it on my own images and show you the results before we move to source/star detection.
