"""
=========================================================
Categories
=========================================================

Envelope categories used by the Constructions tab and the
rules that map every OpenStudio surface / sub-surface to one
category.

Layer order convention for bundles (first row = first layer):

    exterior wall, roof, exposed floor,
    ground wall, ground slab ......... OUTSIDE  -> INSIDE
    interior wall .................... side A   -> side B
    interior slab .................... TOP (floor finish) -> BOTTOM (ceiling finish)
    doors ............................ outside  -> inside

Layers are reversed automatically where needed (floor side of an
interior slab, adjacent side of an interior wall).

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

from __future__ import annotations

from dataclasses import dataclass

GROUND_CONDITIONS = {
    "Ground", "Foundation", "GroundFCfactorMethod",
    "GroundSlabPreprocessorAverage", "GroundSlabPreprocessorCore",
    "GroundSlabPreprocessorPerimeter", "GroundBasementPreprocessorAverageWall",
    "GroundBasementPreprocessorAverageFloor", "GroundBasementPreprocessorUpperWall",
    "GroundBasementPreprocessorLowerWall",
}
INTERIOR_CONDITIONS = {"Surface", "Adiabatic", "Zone", "OtherSideCoefficients", "OtherSideConditionsModel"}
WINDOW_TYPES = {"FixedWindow", "OperableWindow", "GlassDoor"}
DOOR_TYPES = {"Door", "OverheadDoor"}


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    allowed: str            # "opaque", "glazing" or "any"
    rsi: float              # inside film resistance  (m2K/W) - for display U-values
    rse: float              # outside film resistance (m2K/W)
    layer_hint: str
    is_subsurface: bool = False


CATEGORIES: dict[str, Category] = {c.key: c for c in [
    Category("exterior_wall", "Exterior walls", "opaque", 0.13, 0.04, "outside -> inside"),
    Category("interior_wall", "Interior walls", "opaque", 0.13, 0.13, "side A -> side B"),
    Category("roof", "Roofs", "opaque", 0.10, 0.04, "outside (top) -> inside"),
    Category("interior_slab", "Interior slabs (floors / ceilings)", "opaque", 0.17, 0.10,
             "top (floor finish) -> bottom (ceiling finish)"),
    Category("ground_wall", "Ground-facing walls", "opaque", 0.13, 0.0, "soil side -> inside"),
    Category("ground_slab", "Ground-facing slabs", "opaque", 0.17, 0.0, "soil side (bottom) -> inside (top)"),
    Category("exposed_floor", "Exposed floors (over outdoors)", "opaque", 0.17, 0.04, "outside (bottom) -> inside (top)"),
    Category("window", "Windows", "glazing", 0.0, 0.0, "one glazing system", True),
    Category("exterior_door", "Exterior doors", "any", 0.13, 0.04, "outside -> inside", True),
    Category("interior_door", "Interior doors", "opaque", 0.13, 0.13, "side A -> side B", True),
]}

CATEGORY_ORDER = list(CATEGORIES)


def classify_surface(surface) -> str | None:
    surface_type = surface.surfaceType()
    boundary = surface.outsideBoundaryCondition()
    if surface_type == "Wall":
        if boundary == "Outdoors":
            return "exterior_wall"
        if boundary in GROUND_CONDITIONS:
            return "ground_wall"
        if boundary in INTERIOR_CONDITIONS:
            return "interior_wall"
    elif surface_type == "RoofCeiling":
        if boundary == "Outdoors":
            return "roof"
        if boundary in INTERIOR_CONDITIONS:
            return "interior_slab"
        if boundary in GROUND_CONDITIONS:
            return "ground_slab"
    elif surface_type == "Floor":
        if boundary == "Outdoors":
            return "exposed_floor"
        if boundary in GROUND_CONDITIONS:
            return "ground_slab"
        if boundary in INTERIOR_CONDITIONS:
            return "interior_slab"
    return None


def classify_subsurface(subsurface) -> str | None:
    sub_type = subsurface.subSurfaceType()
    parent = subsurface.surface()
    parent_boundary = parent.get().outsideBoundaryCondition() if parent.is_initialized() else "Outdoors"
    exterior = parent_boundary == "Outdoors"
    if sub_type in WINDOW_TYPES:
        return "window" if exterior else None          # interior windows are left untouched
    if sub_type in DOOR_TYPES:
        return "exterior_door" if exterior else "interior_door"
    return None                                        # skylights, TDDs: untouched


def surface_layers_reversed(surface, category: str) -> bool:
    """True when the OpenStudio surface needs the bundle layers reversed.

    Interior slabs are entered top -> bottom. A RoofCeiling surface's
    outside face points up, so it uses the bundle as written; a Floor
    surface's outside face points down, so it needs the reverse.
    Ground slabs / exposed floors are entered outside(bottom) -> inside.
    """
    if category == "interior_slab":
        return surface.surfaceType() == "Floor"
    return False
