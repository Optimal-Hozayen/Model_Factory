"""
=========================================================
Schedules
=========================================================

Loads schedules from a folder and creates them in the model.

Supported files in the schedule folder
--------------------------------------
*.csv   One schedule per numeric column (header optional).
        Row count defines the time step:
            8760 / 8784 -> 60 min, 17520 -> 30 min, 35040 -> 15 min,
            52560 -> 10 min, 105120 -> 5 min, 525600 -> 1 min
        Non-numeric columns (timestamps, labels) are ignored.
*.json  Day-profile schedules (24 hourly values):
            {"name": "Heating 21C night 18C", "type": "Temperature",
             "weekday": [24 values], "saturday": [...], "sunday": [...],
             "holiday": [...]}            # saturday/sunday/holiday optional
        or {"name": ..., "type": ..., "constant": 21}
        A file may contain one object or a list of them.
*.osm   Every schedule inside another OpenStudio model (e.g. a schedule
        library) - cloned into the model at build time.

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

from __future__ import annotations

import csv
import json
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path

import openstudio

from Core.OsUtil import load_model, opt

SCHEDULE_TYPES = ("Temperature", "Fractional", "OnOff", "Any Number")
ROWS_TO_MINUTES = {8760: 60, 8784: 60, 17520: 30, 35040: 15, 52560: 10, 105120: 5, 525600: 1}


@dataclass
class ScheduleSpec:
    name: str
    kind: str                      # interval | profile | constant | osm
    type: str = "Any Number"       # Temperature | Fractional | OnOff | Any Number
    source: str = ""
    values: list = field(default_factory=list)       # interval values
    minutes: int = 60                                # interval length
    profile: dict = field(default_factory=dict)      # weekday/saturday/sunday/holiday -> 24 values
    constant: float = 0.0
    osm_object: str = ""                             # schedule name in the source OSM

    def stats(self) -> tuple[float, float, float] | None:
        data = self.values or [v for day in self.profile.values() for v in day] or (
            [self.constant] if self.kind == "constant" else [])
        if not data:
            return None
        return min(data), max(data), statistics.fmean(data)

    def describe(self) -> str:
        if self.kind == "interval":
            return f"{len(self.values)} values @ {self.minutes} min"
        if self.kind == "profile":
            return "day profiles: " + ", ".join(self.profile)
        if self.kind == "constant":
            return f"constant {self.constant:g}"
        return f"from {Path(self.source).name}"

    def preview(self, hours=168) -> list[float]:
        """Hourly values for plotting (first week)."""
        if self.kind == "interval":
            step = max(1, 60 // self.minutes)
            return [statistics.fmean(self.values[i:i + step]) for i in range(0, min(len(self.values), hours * step), step)]
        if self.kind == "profile":
            week = []
            for day in range(7):   # week starting on Monday
                key = "saturday" if day == 5 else "sunday" if day == 6 else "weekday"
                week.extend(self.profile.get(key) or self.profile.get("weekday", [0] * 24))
            return week[:hours]
        if self.kind == "constant":
            return [self.constant] * hours
        return []

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def guess_type(values) -> str:
    if not values:
        return "Any Number"
    low, high = min(values), max(values)
    unique = set(values)
    if unique <= {0.0, 1.0}:
        return "OnOff"
    if low >= 0 and high <= 1:
        return "Fractional"
    if -30 <= low and high <= 60 and high > 5:
        return "Temperature"
    return "Any Number"


# =========================================================
# Folder loading
# =========================================================

def _read_csv(path: Path) -> tuple[list[ScheduleSpec], list[str]]:
    specs, errors = [], []
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rows = [row for row in csv.reader(handle, dialect) if any(cell.strip() for cell in row)]
    if not rows:
        return specs, [f"{path.name}: empty file"]

    def is_number(text):
        try:
            float(text)
            return True
        except ValueError:
            return False

    header = None
    if not all(is_number(c) for c in rows[0] if c.strip()):
        header, rows = rows[0], rows[1:]
    n_cols = max(len(r) for r in rows)
    for col in range(n_cols):
        column = [r[col].strip() if col < len(r) else "" for r in rows]
        if not column or not all(is_number(c) for c in column):
            continue
        values = [float(c) for c in column]
        n = len(values)
        if n == 8784:
            values = values[:8760]
            errors.append(f"{path.name}: 8784 rows (leap year) - last 24 h dropped.")
            n = 8760
        if n not in ROWS_TO_MINUTES:
            errors.append(f"{path.name} col {col + 1}: {n} rows is not a full year at a standard time step "
                          f"({', '.join(map(str, ROWS_TO_MINUTES))}).")
            continue
        label = header[col].strip() if header and col < len(header) and header[col].strip() else f"col{col + 1}"
        numeric_cols = sum(1 for c in range(n_cols) if all(is_number(r[c]) for r in rows if c < len(r)))
        name = path.stem if numeric_cols == 1 else f"{path.stem}:{label}"
        specs.append(ScheduleSpec(name=name, kind="interval", type=guess_type(values), source=str(path),
                                  values=values, minutes=ROWS_TO_MINUTES[n]))
    if not specs and not errors:
        errors.append(f"{path.name}: no numeric column found")
    return specs, errors


def _read_json(path: Path) -> tuple[list[ScheduleSpec], list[str]]:
    specs, errors = [], []
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else [data]
    for index, item in enumerate(items):
        name = item.get("name") or f"{path.stem}_{index + 1}"
        stype = item.get("type", "Any Number")
        if stype not in SCHEDULE_TYPES:
            errors.append(f"{path.name}/{name}: unknown type '{stype}', using 'Any Number'.")
            stype = "Any Number"
        if "constant" in item:
            specs.append(ScheduleSpec(name=name, kind="constant", type=stype, source=str(path),
                                      constant=float(item["constant"])))
            continue
        profile = {}
        for day in ("weekday", "saturday", "sunday", "holiday"):
            if day in item:
                values = [float(v) for v in item[day]]
                if len(values) != 24:
                    errors.append(f"{path.name}/{name}: '{day}' needs 24 hourly values (got {len(values)}).")
                    break
                profile[day] = values
        else:
            if "weekday" not in profile:
                errors.append(f"{path.name}/{name}: a 'weekday' profile (24 values) is required.")
                continue
            specs.append(ScheduleSpec(name=name, kind="profile", type=stype, source=str(path), profile=profile))
    return specs, errors


def _read_osm(path: Path) -> tuple[list[ScheduleSpec], list[str]]:
    model = load_model(path)
    specs = []
    for schedule in model.getSchedules():
        limits = opt(schedule.scheduleTypeLimits())
        unit = limits.unitType() if limits is not None else ""
        stype = "Temperature" if unit == "Temperature" else "OnOff" if unit == "Availability" else \
            "Fractional" if unit == "Dimensionless" else "Any Number"
        specs.append(ScheduleSpec(name=schedule.nameString(), kind="osm", type=stype, source=str(path),
                                  osm_object=schedule.nameString()))
    return specs, []


def load_schedule_folder(folder) -> tuple[list[ScheduleSpec], list[str]]:
    folder = Path(folder)
    if not folder.is_dir():
        return [], [f"Folder not found: {folder}"]
    specs, errors = [], []
    readers = {".csv": _read_csv, ".json": _read_json, ".osm": _read_osm}
    for path in sorted(folder.iterdir()):
        reader = readers.get(path.suffix.lower())
        if reader is None:
            continue
        try:
            found, problems = reader(path)
            specs.extend(found)
            errors.extend(problems)
        except Exception as error:      # skip unreadable files
            errors.append(f"{path.name}: {error}")
    names = {}
    for spec in specs:                  # keep names unique
        if spec.name in names:
            names[spec.name] += 1
            spec.name = f"{spec.name} ({names[spec.name]})"
        else:
            names[spec.name] = 1
    return specs, errors


# =========================================================
# Create in model
# =========================================================

class ScheduleFactory:

    def __init__(self, model, log=print):
        self.model = model
        self.log = log
        self._limits = {}
        self._osm_cache = {}

    def type_limits(self, stype: str):
        if stype == "Any Number":
            return None
        if stype in self._limits:
            return self._limits[stype]
        limits = openstudio.model.ScheduleTypeLimits(self.model)
        limits.setName(f"MB {stype}")
        if stype == "Temperature":
            limits.setLowerLimitValue(-60)
            limits.setUpperLimitValue(200)
            limits.setNumericType("Continuous")
            limits.setUnitType("Temperature")
        elif stype == "Fractional":
            limits.setLowerLimitValue(0)
            limits.setUpperLimitValue(1)
            limits.setNumericType("Continuous")
            limits.setUnitType("Dimensionless")
        elif stype == "OnOff":
            limits.setLowerLimitValue(0)
            limits.setUpperLimitValue(1)
            limits.setNumericType("Discrete")
            limits.setUnitType("Availability")
        self._limits[stype] = limits
        return limits

    @staticmethod
    def _day(day_schedule, values):
        day_schedule.clearValues()
        for hour in range(24):
            last = hour == 23 or values[hour + 1] != values[hour]
            if last:
                day_schedule.addValue(openstudio.Time(0, hour + 1, 0, 0), float(values[hour]))

    def create(self, spec: ScheduleSpec):
        existing = opt(self.model.getScheduleByName(spec.name))
        if existing is not None:
            self.log(f"Schedules: '{spec.name}' already in model - replacing it with the loaded one.")
            existing.setName(f"{spec.name} (base model)")

        if spec.kind == "interval":
            interval = openstudio.Time(0, 0, int(spec.minutes), 0)
            start = openstudio.Date(openstudio.MonthOfYear(1), 1)
            vector = openstudio.Vector(len(spec.values))
            for index, value in enumerate(spec.values):
                vector[index] = float(value)
            series = openstudio.TimeSeries(openstudio.DateTime(start, interval), interval, vector, "")
            schedule = opt(openstudio.model.ScheduleInterval.fromTimeSeries(series, self.model))
            if schedule is None:
                raise RuntimeError(f"Could not create interval schedule '{spec.name}'.")
        elif spec.kind == "profile":
            schedule = openstudio.model.ScheduleRuleset(self.model)
            self._day(schedule.defaultDaySchedule(), spec.profile["weekday"])
            for day_key, flags in (("saturday", "setApplySaturday"), ("sunday", "setApplySunday")):
                if day_key in spec.profile:
                    rule = openstudio.model.ScheduleRule(schedule)
                    rule.setName(f"{spec.name} {day_key}")
                    getattr(rule, flags)(True)
                    self._day(rule.daySchedule(), spec.profile[day_key])
            if "holiday" in spec.profile:
                holiday = openstudio.model.ScheduleDay(self.model)
                self._day(holiday, spec.profile["holiday"])
                schedule.setHolidaySchedule(holiday)
            for setter in ("setSummerDesignDaySchedule", "setWinterDesignDaySchedule"):
                getattr(schedule, setter)(schedule.defaultDaySchedule())
        elif spec.kind == "constant":
            schedule = openstudio.model.ScheduleConstant(self.model)
            schedule.setValue(spec.constant)
        elif spec.kind == "osm":
            source = self._osm_cache.get(spec.source)
            if source is None:
                source = self._osm_cache[spec.source] = load_model(spec.source)
            original = opt(source.getScheduleByName(spec.osm_object))
            if original is None:
                raise ValueError(f"Schedule '{spec.osm_object}' not found in {spec.source}.")
            schedule = original.clone(self.model).to_Schedule().get()
        else:
            raise ValueError(f"Unknown schedule kind '{spec.kind}'.")

        schedule.setName(spec.name)
        limits = self.type_limits(spec.type)
        if limits is not None:
            schedule.setScheduleTypeLimits(limits)
        return schedule
