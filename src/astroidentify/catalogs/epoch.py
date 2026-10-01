"""Observation epoch and Gaia proper-motion propagation.

Gaia DR3 positions refer to ``ref_epoch`` (J2016.0). Stars move, so positions are
propagated to the observation time when, and only when, a reliable timestamp is available:

1. an explicit ``CatalogConfig.observation_epoch`` (e.g. from an observing log);
2. FITS ``DATE-OBS`` or ``MJD-OBS`` from the image header;
3. EXIF ``DateTimeOriginal`` from JPEG/PNG metadata. It has no time zone; it is read as UTC,
   and the resulting error of up to a day is negligible for proper motion.

Filenames are never parsed. Without a timestamp, catalogue-epoch coordinates are used and
``EpochInfo.propagated`` is ``False``.

Propagation uses Astropy ``SkyCoord.apply_space_motion`` with proper motion only (no
parallax/radial velocity): over years, perspective and parallax terms are far below the
matching tolerance. Rows without proper motion keep their catalogue coordinates.
"""

from __future__ import annotations

import logging
import warnings
from typing import Any

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.time import Time

from astroidentify.catalogs.types import CatalogTable, EpochInfo
from astroidentify.exceptions import ConfigurationError

logger = logging.getLogger(__name__)

GAIA_DR3_EPOCH = 2016.0


def resolve_observation_epoch(
    image_metadata: dict[str, Any], explicit: str | None = None
) -> tuple[Time | None, str | None]:
    """Return ``(time, source)`` from supported metadata, or ``(None, None)``.

    Raises:
        ConfigurationError: ``explicit`` is not a parseable date/time.
    """
    if explicit:
        try:
            return Time(explicit, scale="utc"), "user"
        except ValueError as exc:
            raise ConfigurationError(f"unparseable observation epoch {explicit!r}: {exc}") from exc

    summary = (image_metadata.get("fits") or {}).get("summary") or {}
    if summary.get("DATE-OBS"):
        try:
            return Time(str(summary["DATE-OBS"]), scale="utc"), "fits_date_obs"
        except ValueError:
            logger.warning("ignoring unparseable FITS DATE-OBS %r", summary["DATE-OBS"])
    if summary.get("MJD-OBS") is not None:
        try:
            return Time(float(summary["MJD-OBS"]), format="mjd", scale="utc"), "fits_mjd_obs"
        except (TypeError, ValueError):
            logger.warning("ignoring unparseable FITS MJD-OBS %r", summary["MJD-OBS"])

    exif_time = (image_metadata.get("exif") or {}).get("DateTimeOriginal")
    if exif_time:
        text = str(exif_time).strip()
        try:
            date, _, clock = text.partition(" ")
            return Time(f"{date.replace(':', '-')}T{clock or '00:00:00'}", scale="utc"), "exif"
        except ValueError:
            logger.warning("ignoring unparseable EXIF DateTimeOriginal %r", text)
    return None, None


def propagate(
    rows: CatalogTable, observation: Time | None, source: str | None
) -> tuple[np.ndarray, np.ndarray, EpochInfo]:
    """Return ``(ra, dec, info)``: positions at the observation epoch, if known."""
    ra = np.asarray(rows["ra"], float)
    dec = np.asarray(rows["dec"], float)
    ref = rows.get("ref_epoch")
    catalog_epoch = (
        float(np.nanmedian(ref)) if ref is not None and np.isfinite(ref).any() else GAIA_DR3_EPOCH
    )
    if observation is None:
        return (
            ra,
            dec,
            EpochInfo(
                propagated=False,
                catalog_epoch=catalog_epoch,
                observation_epoch=None,
                observation_epoch_source=None,
                reason="no reliable observation timestamp in the image metadata or configuration; "
                "catalogue-epoch coordinates used",
            ),
        )
    pmra, pmdec = rows.get("pmra"), rows.get("pmdec")
    if pmra is None or pmdec is None:
        return (
            ra,
            dec,
            EpochInfo(
                False, catalog_epoch, observation.isot, source, 0, "catalogue has no proper motions"
            ),
        )

    movable = np.isfinite(ra) & np.isfinite(dec) & np.isfinite(pmra) & np.isfinite(pmdec)
    new_ra, new_dec = ra.copy(), dec.copy()
    if movable.any():
        epochs = (
            np.where(np.isfinite(ref[movable]), ref[movable], catalog_epoch)
            if ref is not None
            else catalog_epoch
        )
        coords = SkyCoord(
            ra=ra[movable] * u.deg,
            dec=dec[movable] * u.deg,
            pm_ra_cosdec=pmra[movable] * u.mas / u.yr,  # Gaia pmra already includes cos(dec)
            pm_dec=pmdec[movable] * u.mas / u.yr,
            obstime=Time(epochs, format="jyear", scale="tcb"),
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # ERFA notes the assumed (large) distance
            moved = coords.apply_space_motion(new_obstime=observation)
        new_ra[movable] = moved.ra.deg
        new_dec[movable] = moved.dec.deg
    return (
        new_ra,
        new_dec,
        EpochInfo(
            propagated=True,
            catalog_epoch=catalog_epoch,
            observation_epoch=observation.isot,
            observation_epoch_source=source,
            n_propagated=int(movable.sum()),
            reason="proper motion applied where available; rows without it keep "
            "catalogue positions",
        ),
    )
