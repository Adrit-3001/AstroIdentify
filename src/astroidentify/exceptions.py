"""Domain-specific exceptions for AstroIdentify.

Every error raised deliberately by the library derives from :class:`AstroIdentifyError`,
so callers (such as the CLI) can distinguish expected input/output problems from bugs.
"""

from __future__ import annotations


class AstroIdentifyError(Exception):
    """Base class for all AstroIdentify errors."""


class ConfigurationError(AstroIdentifyError, ValueError):
    """A configuration value is invalid."""


class ImageLoadError(AstroIdentifyError):
    """An image could not be loaded."""


class ImageNotFoundError(ImageLoadError):
    """The input path does not exist or is not a regular file."""


class UnsupportedFormatError(ImageLoadError):
    """The input file type is not supported."""


class CorruptImageError(ImageLoadError):
    """The file claims a supported format but cannot be decoded."""


class FitsError(ImageLoadError):
    """Base class for FITS-specific loading problems."""


class MalformedFitsError(FitsError, CorruptImageError):
    """The file is not a readable FITS file."""


class NoImageDataError(FitsError):
    """The FITS file contains no usable image data (e.g. only tables or empty HDUs)."""


class UnsupportedDimensionsError(FitsError):
    """The FITS image data has a dimensionality we refuse to guess about (e.g. cubes)."""


class InvalidImageError(AstroIdentifyError):
    """Image data was decoded but is unusable (bad dimensions, no finite pixels, ...)."""


class OutputError(AstroIdentifyError):
    """Output artifacts could not be written."""


class DetectionError(AstroIdentifyError):
    """Base class for source-detection failures."""


class InvalidDetectionInputError(DetectionError):
    """The preprocessing result cannot be turned into a usable detection plane."""


class BackgroundEstimationError(DetectionError):
    """The local background/RMS model could not be computed."""


class AstrometryError(AstroIdentifyError):
    """Base class for plate-solving failures.

    ``log`` optionally carries solver output so failures keep their evidence.
    """

    def __init__(self, message: str, *, log: str | None = None) -> None:
        super().__init__(message)
        self.log = log


class NoUsableSourcesError(AstrometryError):
    """No detected source is usable for plate solving."""


class TooFewSourcesError(AstrometryError):
    """Fewer usable sources than the configured minimum."""


class SolverNotFoundError(AstrometryError):
    """The plate-solver executable (Astrometry.net ``solve-field``) is not available."""


class IndexDataUnavailableError(AstrometryError):
    """No Astrometry.net index files are configured/installed."""


class SolverTimeoutError(AstrometryError):
    """The plate solver exceeded its time limit."""


class SolverProcessError(AstrometryError):
    """The plate-solver process failed (non-zero exit status)."""


class InvalidWCSError(AstrometryError):
    """A WCS output is missing, malformed or not a usable celestial transform."""
