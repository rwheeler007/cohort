"""Backward-compatibility shim -- canonical module is block_populator.py.

All classes and functions are re-exported from block_populator.
New code should import from block_populator directly.
"""

from .block_populator import (  # noqa: F401
    TasteProfile,
    AdaptedParameters,
    CompetitorProfile,
    BusinessInfo,
    PopulatorInput,
    PopulatorInput as RoundtableInput,
    BlockSpec,
    PageSpec,
    SkinSpec,
    BlockSiteSpec,
    BlockPopulator,
    BlockPopulator as RoundtableBridge,
)
