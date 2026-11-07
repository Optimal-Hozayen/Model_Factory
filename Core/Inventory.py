"""
=========================================================
Inventory
=========================================================

Summary of the base model used by the GUI: surface categories and
their constructions, materials, schedules, zones, space types,
thermostats, outputs, weather and WWR.

Only plain Python data is stored (no OpenStudio objects).

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field



from Core.Categories import CATEGORY_ORDER, classify_subsurface, classify_surface, surface_layers_reversed
from Core.Library import Bundle, Layer, MaterialSpec
from Core.OsUtil import cast, load_model, name_of, opt


# =========================================================
# Model material -> MaterialSpec
# =========================================================

def material_to_spec(material) -> tuple[MaterialSpec, float] | None:
    """Return (spec, thickness_mm) or None if the material type is not supported."""
    name = material.nameString()
    std = cast(material, "StandardOpaqueMaterial")
    if std is not None:
        spec = MaterialSpec(
            name=name, kind="opaque", conductivity=std.conductivity(), density=std.density(),
            specific_heat=std.specificHeat(), default_thickness_mm=round(std.thickness() * 1000, 3),
            roughness=std.roughness(), thermal_absorptance=std.thermalAbsorptance(),
            solar_absorptance=std.solarAbsorptance(), visible_absorptance=std.visibleAbsorptance(),
            source="model",
        )
        return spec, spec.default_thickness_mm
    nomass = cast(material, "MasslessOpaqueMaterial")
    if nomass is not None:
        return MaterialSpec(name=name, kind="nomass", r_value=nomass.thermalResistance(),
                            roughness=nomass.roughness(), source="model"), 0.0
    gap = cast(material, "AirGap")
    if gap is not None:
        return MaterialSpec(name=name, kind="airgap", r_value=gap.thermalResistance(), source="model"), 0.0
    glazing = cast(material, "SimpleGlazing")
    if glazing is not None:
        vt = opt(glazing.visibleTransmittance())
        return MaterialSpec(name=name, kind="glazing", u_factor=glazing.uFactor(),
                            shgc=glazing.solarHeatGainCoefficient(), vt=vt if vt else 0.6,
                            source="model"), 0.0
    return None


def construction_to_bundle(construction, category: str, reverse=False) -> Bundle | None:
    layered = cast(construction, "Construction")
    if layered is None:
        return None
    layers = []
    for material in layered.layers():
        converted = material_to_spec(material)
        if converted is None:
            return None
        spec, thickness = converted
        layers.append(Layer(spec, thickness))
    if reverse:
        layers.reverse()
    return Bundle(name=f"{construction.nameString()} (base model)", category=category, layers=layers,
                  description="Base model construction.",
                  source="model")


# =========================================================
# Inventory
# =========================================================

@dataclass
class CategoryInfo:
    key: str
    count: int = 0
    area_m2: float = 0.0
    constructions: dict = field(default_factory=dict)      # construction name -> count
    hard_assigned: int = 0


@dataclass
class Inventory:
    path: str = ""
    categories: dict = field(default_factory=dict)          # key -> CategoryInfo
    seed_bundles: dict = field(default_factory=dict)        # key -> Bundle (most used construction)
    materials: list = field(default_factory=list)           # MaterialSpec from the model
    construction_names: list = field(default_factory=list)
    schedules: list = field(default_factory=list)           # (name, type, unit type)
    zones: list = field(default_factory=list)               # dict(name, space_types, thermostat, ideal_loads, equipment)
    space_types: list = field(default_factory=list)
    output_variables: list = field(default_factory=list)    # (name, key, frequency)
    output_meters: list = field(default_factory=list)       # (name, frequency)
    weather_file: str = ""
    site: dict = field(default_factory=dict)
    climate_zone: str = ""
    wwr: dict = field(default_factory=dict)                 # facade -> (window area, wall area)
    hvac_summary: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    def wwr_ratio(self, facade: str) -> float | None:
        window, wall = self.wwr.get(facade, (0.0, 0.0))
        return window / wall if wall > 0 else None


FACADES = ("North", "East", "South", "West")


def facade_of(surface, model) -> str:
    """Absolute facade of an exterior wall from its outward normal."""
    north = model.getBuilding().northAxis()
    space = opt(surface.space())
    space_north = space.directionofRelativeNorth() if space is not None else 0.0
    azimuth = (math.degrees(surface.azimuth()) + north + space_north) % 360.0
    if azimuth >= 315 or azimuth < 45:
        return "North"
    if azimuth < 135:
        return "East"
    if azimuth < 225:
        return "South"
    return "West"


def window_area(surface) -> float:
    return sum(sub.grossArea() * sub.multiplier() for sub in surface.subSurfaces()
               if sub.subSurfaceType() in ("FixedWindow", "OperableWindow", "GlassDoor"))


def build_inventory(osm_path) -> Inventory:
    model = load_model(osm_path)
    return inventory_from_model(model, str(osm_path))


def inventory_from_model(model, path="") -> Inventory:
    inv = Inventory(path=path)
    infos = {key: CategoryInfo(key) for key in CATEGORY_ORDER}
    reverse_votes = defaultdict(lambda: [0, 0])          # (category, construction) -> [reversed, as-is]

    for surface in model.getSurfaces():
        category = classify_surface(surface)
        if category is None:
            continue
        info = infos[category]
        info.count += 1
        info.area_m2 += surface.netArea()
        cname = name_of(surface.construction()) or "(none)"
        info.constructions[cname] = info.constructions.get(cname, 0) + 1
        if not surface.isConstructionDefaulted():
            info.hard_assigned += 1
        reverse_votes[(category, cname)][0 if surface_layers_reversed(surface, category) else 1] += 1

    for sub in model.getSubSurfaces():
        category = classify_subsurface(sub)
        if category is None:
            continue
        info = infos[category]
        info.count += 1
        info.area_m2 += sub.grossArea() * sub.multiplier()
        cname = name_of(sub.construction()) or "(none)"
        info.constructions[cname] = info.constructions.get(cname, 0) + 1
        if not sub.isConstructionDefaulted():
            info.hard_assigned += 1

    inv.categories = infos

    # Seed bundles = most used construction per category
    for key, info in infos.items():
        if not info.constructions:
            continue
        cname = max(info.constructions, key=info.constructions.get)
        construction = opt(model.getConstructionByName(cname))
        if construction is None:
            continue
        votes = reverse_votes[(key, cname)]
        reverse = votes[0] > votes[1]
        bundle = construction_to_bundle(construction, key, reverse=reverse)
        if bundle is not None:
            inv.seed_bundles[key] = bundle
        if len(info.constructions) > 1:
            inv.warnings.append(
                f"{key}: {len(info.constructions)} different constructions in use "
                f"({', '.join(f'{n} x{c}' for n, c in info.constructions.items())})."
            )

    # Catalog
    seen = set()
    for material in model.getMaterials():
        converted = material_to_spec(material)
        if converted and converted[0].name not in seen:
            inv.materials.append(converted[0])
            seen.add(converted[0].name)
    inv.construction_names = sorted(c.nameString() for c in model.getConstructionBases())

    for schedule in model.getSchedules():
        limits = opt(schedule.scheduleTypeLimits())
        unit = limits.unitType() if limits is not None else ""
        inv.schedules.append((schedule.nameString(), schedule.iddObjectType().valueName().replace("OS_", ""), unit))
    inv.schedules.sort(key=lambda item: item[0].lower())

    # Zones / HVAC
    equipment_counter = Counter()
    for zone in sorted(model.getThermalZones(), key=lambda z: z.nameString()):
        thermostat = opt(zone.thermostatSetpointDualSetpoint())
        equipment = [e.iddObjectType().valueName().replace("OS_", "") for e in zone.equipment()]
        equipment_counter.update(equipment)
        inv.zones.append({
            "name": zone.nameString(),
            "space_types": sorted({name_of(s.spaceType()) or "" for s in zone.spaces()}),
            "thermostat": thermostat is not None,
            "heating_sch": name_of(thermostat.heatingSetpointTemperatureSchedule()) if thermostat else None,
            "cooling_sch": name_of(thermostat.coolingSetpointTemperatureSchedule()) if thermostat else None,
            "ideal_loads": zone.useIdealAirLoads(),
            "equipment": equipment,
        })
    inv.space_types = sorted(st.nameString() for st in model.getSpaceTypes())
    inv.hvac_summary = {
        "zones": len(inv.zones),
        "implicit_ideal_loads": sum(1 for z in inv.zones if z["ideal_loads"]),
        "zones_with_thermostat": sum(1 for z in inv.zones if z["thermostat"]),
        "thermostats_without_schedules": sum(1 for z in inv.zones if z["thermostat"] and not (z["heating_sch"] or z["cooling_sch"])),
        "air_loops": len(model.getAirLoopHVACs()),
        "plant_loops": len(model.getPlantLoops()),
        "zone_equipment": dict(equipment_counter),
    }

    inv.output_variables = [(v.variableName(), v.keyValue(), v.reportingFrequency()) for v in model.getOutputVariables()]
    inv.output_meters = [(m.name(), m.reportingFrequency()) for m in model.getOutputMeters()]

    weather = opt(model.weatherFile())
    if weather is not None:
        weather_path = opt(weather.path())
        inv.weather_file = str(weather_path) if weather_path is not None else weather.city()
    site = model.getSite()
    inv.site = {"latitude": site.latitude(), "longitude": site.longitude(),
                "time_zone": site.timeZone(), "elevation": site.elevation()}
    for cz in model.getClimateZones().climateZones():
        if cz.institution() == "ASHRAE" and cz.value():
            inv.climate_zone = cz.value()

    wwr = defaultdict(lambda: [0.0, 0.0])
    for surface in model.getSurfaces():
        if classify_surface(surface) == "exterior_wall":
            facade = facade_of(surface, model)
            wwr[facade][0] += window_area(surface)
            wwr[facade][1] += surface.grossArea()
    inv.wwr = {facade: tuple(values) for facade, values in wwr.items()}

    for key, info in infos.items():
        if info.count and info.hard_assigned:
            inv.warnings.append(
                f"{key}: {info.hard_assigned}/{info.count} surfaces have a hard-assigned construction "
                f"(a Default Construction Set change alone would NOT reach them)."
            )
    return inv


def zone_matches(zone: dict, selector: str) -> bool:
    """Zone selector grammar used by schedule assignments.

    ALL                       every thermal zone
    spacetype:<name>          zones containing a space of that space type
    contains:<text>           zone name contains text (case-insensitive)
    zone:<name>               exactly one zone
    """
    if selector in ("", "ALL"):
        return True
    kind, _, value = selector.partition(":")
    if kind == "spacetype":
        return value in zone["space_types"]
    if kind == "contains":
        return value.lower() in zone["name"].lower()
    if kind == "zone":
        return zone["name"] == value
    return False


def describe_selector(selector: str) -> str:
    if selector in ("", "ALL"):
        return "All thermal zones"
    kind, _, value = selector.partition(":")
    return {"spacetype": f"Space type: {value}", "contains": f"Zone name contains: {value}",
            "zone": f"Zone: {value}"}.get(kind, selector)
