"""Backward-compatibility shim -- canonical module is block_populator.py.

All classes and functions are re-exported from block_populator.
New code should import from block_populator directly.
"""

from .block_populator import (  # noqa: F401
    AdaptedParameters,
    BlockPopulator,
    BlockSiteSpec,
    BlockSpec,
    BusinessInfo,
    CompetitorProfile,
    PageSpec,
    PopulatorInput,
    SkinSpec,
    TasteProfile,
)
