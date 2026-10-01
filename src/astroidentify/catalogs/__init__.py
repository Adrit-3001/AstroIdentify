"""Milestone 4: Gaia DR3 catalogue matching for a plate-solved image.

Associates catalogue stars with detected point sources. It does not identify named objects.
"""

from astroidentify.catalogs.footprint import project_to_image, query_region, validate_wcs
from astroidentify.catalogs.gaia import GaiaDR3Provider, build_adql, parse_votable
from astroidentify.catalogs.matching import assign_one_to_one, candidate_edges
from astroidentify.catalogs.outputs import CatalogOutputPaths, save_catalog_outputs
from astroidentify.catalogs.pipeline import MatchInputs, load_match_inputs, match_catalog
from astroidentify.catalogs.types import (
    CatalogMatch,
    CatalogMatchResult,
    CatalogQueryResult,
    CatalogTable,
    QueryRegion,
)

__all__ = [
    "CatalogMatch",
    "CatalogMatchResult",
    "CatalogOutputPaths",
    "CatalogQueryResult",
    "CatalogTable",
    "GaiaDR3Provider",
    "MatchInputs",
    "QueryRegion",
    "assign_one_to_one",
    "build_adql",
    "candidate_edges",
    "load_match_inputs",
    "match_catalog",
    "parse_votable",
    "project_to_image",
    "query_region",
    "save_catalog_outputs",
    "validate_wcs",
]
