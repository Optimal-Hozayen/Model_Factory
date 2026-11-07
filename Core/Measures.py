"""
=========================================================
Measures
=========================================================

Reads the measures (measure.xml), checks the arguments and writes
the workflow (OSW).

Measures with a known role:
    SetNISTInfiltrationCorrelations  -> infiltration (airtightness test value)
    TBDMeasure                       -> thermal bridging & derating (Quebec / BETBG sets)
    envelope_and_internal_load_breakdown, openstudio_results -> reporting
    SetThermostatSchedules           -> superseded by the Schedules & HVAC tab
The WWR change is done in Core/Wwr.py.

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import openstudio

ROLES = {
    "SetNISTInfiltrationCorrelations": "Infiltration",
    "TBDMeasure": "Thermal bridging",
    "openstudio_results": "Report (HTML results)",
    "envelope_and_internal_load_breakdown": "Report (envelope / load breakdown)",
    "SetThermostatSchedules": "Not needed (use the Schedules & HVAC tab)",
}
TYPE_ORDER = {"ModelMeasure": 0, "EnergyPlusMeasure": 1, "ReportingMeasure": 2}


@dataclass
class MeasureArgument:
    name: str
    display_name: str
    type: str                     # Boolean | Double | Integer | String | Choice
    default: str | None
    required: bool
    choices: list = field(default_factory=list)
    choice_labels: list = field(default_factory=list)
    units: str = ""
    description: str = ""

    @property
    def model_dependent(self) -> bool:
        """Choices that are object handles generated from a model (not usable as fixed lists)."""
        return any(c.startswith("{") and c.endswith("}") for c in self.choices)


@dataclass
class MeasureInfo:
    name: str                     # directory name
    path: str
    display_name: str
    measure_type: str
    description: str
    arguments: list

    @property
    def role(self) -> str:
        return ROLES.get(self.name, self.measure_type.replace("Measure", " measure"))

    def argument(self, name) -> MeasureArgument | None:
        return next((a for a in self.arguments if a.name == name), None)


def read_measure(folder) -> MeasureInfo:
    folder = Path(folder)
    root = ET.parse(folder / "measure.xml").getroot()
    measure_type = root.findtext(".//attribute[name='Measure Type']/value") or "ModelMeasure"
    arguments = []
    for node in root.findall("arguments/argument"):
        arguments.append(MeasureArgument(
            name=node.findtext("name"), display_name=node.findtext("display_name") or node.findtext("name"),
            type=node.findtext("type") or "String", default=node.findtext("default_value"),
            required=(node.findtext("required") or "false").lower() == "true",
            choices=[c.findtext("value") for c in node.findall("choices/choice")],
            choice_labels=[c.findtext("display_name") or c.findtext("value") for c in node.findall("choices/choice")],
            units=node.findtext("units") or "", description=node.findtext("description") or "",
        ))
    return MeasureInfo(name=folder.name, path=str(folder), display_name=root.findtext("display_name") or folder.name,
                       measure_type=measure_type, description=(root.findtext("description") or "").strip(),
                       arguments=arguments)


def scan_measures(measures_dir) -> tuple[dict, list[str]]:
    measures, errors = {}, []
    folder = Path(measures_dir)
    if not folder.is_dir():
        return measures, [f"Measures folder not found: {folder}"]
    for sub in sorted(folder.iterdir()):
        if (sub / "measure.xml").is_file() and ((sub / "measure.rb").is_file() or (sub / "measure.py").is_file()):
            try:
                measures[sub.name] = read_measure(sub)
            except ET.ParseError as error:
                errors.append(f"{sub.name}: {error}")
    return measures, errors


def coerce(argument: MeasureArgument, value):
    """Convert a GUI/JSON value to the Python type OpenStudio expects."""
    if argument.type == "Boolean":
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes")
        return bool(value)
    if argument.type == "Double":
        return float(value)
    if argument.type == "Integer":
        return int(float(value))
    return str(value)


def validate_arguments(info: MeasureInfo, arguments: dict) -> list[str]:
    errors = []
    for name, value in arguments.items():
        arg = info.argument(name)
        if arg is None:
            errors.append(f"{info.name}: unknown argument '{name}'.")
            continue
        try:
            coerced = coerce(arg, value)
        except (TypeError, ValueError):
            errors.append(f"{info.name}.{name}: '{value}' is not a valid {arg.type}.")
            continue
        if arg.type == "Choice" and arg.choices and not arg.model_dependent and coerced not in arg.choices:
            errors.append(f"{info.name}.{name}: '{coerced}' is not one of {arg.choices}.")
    for arg in info.arguments:
        if arg.required and arg.default is None and arg.name not in arguments:
            errors.append(f"{info.name}: required argument '{arg.name}' has no value.")
    return errors


def write_workflow(osw_path, seed_osm, weather_file, measures_dir, measure_configs, registry, log) -> list[dict]:
    """Write the OSW. Returns the list of steps written (for the manifest)."""
    osw = openstudio.WorkflowJSON()
    osw.setSeedFile(openstudio.toPath(str(seed_osm)))
    if weather_file:
        osw.setWeatherFile(openstudio.toPath(str(weather_file)))
    osw.addMeasurePath(openstudio.toPath(str(measures_dir)))

    enabled = [m for m in measure_configs if m.enabled]
    for config in enabled:
        if config.name not in registry:
            raise FileNotFoundError(f"Measure '{config.name}' was not found in {measures_dir}.")
        errors = validate_arguments(registry[config.name], config.arguments)
        if errors:
            raise ValueError("\n".join(errors))

    steps_by_type, written = {}, []
    ordered = sorted(enabled, key=lambda m: TYPE_ORDER.get(registry[m.name].measure_type, 0))
    for config in ordered:
        info = registry[config.name]
        step = openstudio.MeasureStep(config.name)
        for name, value in config.arguments.items():
            step.setArgument(name, coerce(info.argument(name), value))
        steps_by_type.setdefault(info.measure_type, []).append(step)
        written.append({"measure": config.name, "type": info.measure_type, "arguments": config.arguments})
        log(f"Workflow: + {config.name} " + ", ".join(f"{k}={v}" for k, v in config.arguments.items()))
    for measure_type, steps in steps_by_type.items():
        osw.setMeasureSteps(openstudio.MeasureType(measure_type), steps)
    osw_path = Path(osw_path)
    osw_path.parent.mkdir(parents=True, exist_ok=True)
    if not osw.saveAs(openstudio.toPath(str(osw_path))):
        raise RuntimeError(f"Could not write {osw_path}")
    return written
