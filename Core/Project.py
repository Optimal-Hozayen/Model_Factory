"""
=========================================================
Project
=========================================================

Project settings (all tabs) saved as one JSON file. One project
describes one model variant.

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from Core.Categories import CATEGORY_ORDER
from Core.Library import Bundle

PROJECT_VERSION = 1


@dataclass
class WeatherConfig:
    epw: str = ""
    import_design_days: bool = True
    design_day_filter: str = "99.6% heating + 0.4% cooling"     # or "all"
    climate_zone: str = "6A"                                    # ASHRAE, "" = leave model value
    run_period: dict = field(default_factory=lambda: {"begin_month": 1, "begin_day": 1,
                                                      "end_month": 12, "end_day": 31})


@dataclass
class ScheduleAssignment:
    zones: str = "ALL"                  # selector, see Inventory.zone_matches
    target: str = "heating_setpoint"    # see Hvac.TARGETS
    schedule: str = ""                  # schedule name (loaded or already in model)

    @classmethod
    def from_dict(cls, data):
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class SchedulesConfig:
    folder: str = ""
    loaded: list = field(default_factory=list)          # ScheduleSpec dicts (with their values)
    assignments: list = field(default_factory=list)     # ScheduleAssignment
    create_missing_thermostats: bool = True


@dataclass
class OutputsConfig:
    keep_existing: bool = True
    variables: list = field(default_factory=list)       # {"name", "key", "frequency"}
    meters: list = field(default_factory=list)          # {"name", "frequency"}


@dataclass
class WwrConfig:
    enabled: bool = False
    mode: str = "walls_with_windows"                    # or "all_exterior_walls"
    ratios: dict = field(default_factory=lambda: {"North": 0.3, "East": 0.3, "South": 0.3, "West": 0.3})
    apply_facade: dict = field(default_factory=lambda: {"North": True, "East": True, "South": True, "West": True})
    sill_height_m: float = 0.9


@dataclass
class MeasureConfig:
    name: str                       # measure directory name
    enabled: bool = True
    arguments: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data):
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def default_measures() -> list[MeasureConfig]:
    """Default measures and arguments."""
    return [
        MeasureConfig("SetNISTInfiltrationCorrelations", True, {
            "airtightness_value": 3.0, "airtightness_pressure": 75.0, "airtightness_area": "5-sided",
            "air_barrier": False, "hvac_schedule": "Lookup From Model", "climate_zone": "6A",
            "building_type": "MidriseApartment",
        }),
        MeasureConfig("TBDMeasure", True, {"option": "efficient (BETBG)"}),
        MeasureConfig("envelope_and_internal_load_breakdown", False, {}),
        MeasureConfig("openstudio_results", True, {"units": "SI"}),
    ]


def default_assignments() -> list[ScheduleAssignment]:
    """Default thermostat schedules (Heating M / Cooling M on all zones)."""
    return [
        ScheduleAssignment("ALL", "heating_setpoint", "Heating M"),
        ScheduleAssignment("ALL", "cooling_setpoint", "Cooling M"),
    ]


@dataclass
class Project:
    name: str = "Baseline"
    base_model: str = ""
    measures_dir: str = ""
    weather: WeatherConfig = field(default_factory=WeatherConfig)
    constructions: dict = field(default_factory=lambda: {key: None for key in CATEGORY_ORDER})  # key -> Bundle | None
    schedules: SchedulesConfig = field(default_factory=lambda: SchedulesConfig(assignments=default_assignments()))
    outputs: OutputsConfig = field(default_factory=OutputsConfig)
    wwr: WwrConfig = field(default_factory=WwrConfig)
    measures: list = field(default_factory=default_measures)    # MeasureConfig, in run order
    deep_check: bool = True                                     # translate to IDF and check constructions there
    notes: str = ""

    # ---------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "version": PROJECT_VERSION,
            "name": self.name,
            "base_model": self.base_model,
            "measures_dir": self.measures_dir,
            "weather": asdict(self.weather),
            "constructions": {k: (b.to_dict() if b is not None else None) for k, b in self.constructions.items()},
            "schedules": {
                "folder": self.schedules.folder,
                "loaded": self.schedules.loaded,
                "assignments": [asdict(a) for a in self.schedules.assignments],
                "create_missing_thermostats": self.schedules.create_missing_thermostats,
            },
            "outputs": asdict(self.outputs),
            "wwr": asdict(self.wwr),
            "measures": [asdict(m) for m in self.measures],
            "deep_check": self.deep_check,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        project = cls()
        project.name = data.get("name", project.name)
        project.base_model = data.get("base_model", "")
        project.measures_dir = data.get("measures_dir", "")
        project.weather = WeatherConfig(**{k: v for k, v in data.get("weather", {}).items()
                                           if k in WeatherConfig.__dataclass_fields__})
        constructions = {key: None for key in CATEGORY_ORDER}
        for key, value in data.get("constructions", {}).items():
            if key in constructions and value:
                constructions[key] = Bundle.from_dict(value)
        project.constructions = constructions
        sched = data.get("schedules", {})
        project.schedules = SchedulesConfig(
            folder=sched.get("folder", ""), loaded=sched.get("loaded", []),
            assignments=[ScheduleAssignment.from_dict(a) for a in sched.get("assignments", [])],
            create_missing_thermostats=sched.get("create_missing_thermostats", True),
        )
        project.outputs = OutputsConfig(**{k: v for k, v in data.get("outputs", {}).items()
                                           if k in OutputsConfig.__dataclass_fields__})
        project.wwr = WwrConfig(**{k: v for k, v in data.get("wwr", {}).items()
                                   if k in WwrConfig.__dataclass_fields__})
        if "measures" in data:
            project.measures = [MeasureConfig.from_dict(m) for m in data["measures"]]
        project.deep_check = data.get("deep_check", True)
        project.notes = data.get("notes", "")
        return project

    def save(self, path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path) -> "Project":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
