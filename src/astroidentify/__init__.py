"""AstroIdentify: locate and identify objects in astronomical images.

Milestone 1 provides image ingestion and preprocessing::

    from astroidentify import preprocess_image, save_outputs

    result = preprocess_image("data/raw/m57.fits")
    save_outputs(result, "outputs/m57")
"""

# Defined before the imports below because submodules import it (module-level dunders are
# allowed before imports, so no E402 suppression is needed).
__version__ = "0.1.0"

from astroidentify.config import PreprocessingConfig
from astroidentify.preprocessing import (
    OutputPaths,
    load_image,
    preprocess,
    preprocess_image,
    save_outputs,
)
from astroidentify.types import (
    AstronomyImage,
    BackgroundEstimate,
    ImageFormat,
    NormalizationParams,
    PreprocessingResult,
)

__all__ = [
    "AstronomyImage",
    "BackgroundEstimate",
    "ImageFormat",
    "NormalizationParams",
    "OutputPaths",
    "PreprocessingConfig",
    "PreprocessingResult",
    "__version__",
    "load_image",
    "preprocess",
    "preprocess_image",
    "save_outputs",
]
