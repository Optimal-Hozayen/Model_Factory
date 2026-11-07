"""
=========================================================
Outputs
=========================================================

Output variables and meters. Presets for heating, cooling, equipment,
lighting, temperatures, etc.

With ideal air loads, heating and cooling are reported by the
"Zone Ideal Loads" variables and the DistrictHeatingWater /
DistrictCooling meters (Heating:Electricity stays at zero).

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

from __future__ import annotations

import openstudio

FREQUENCIES = ("Timestep", "Hourly", "Daily", "Monthly", "RunPeriod")

# group -> list of ("var" | "meter", name)
PRESETS: dict[str, list[tuple[str, str]]] = {
    "Heating": [
        ("var", "Zone Ideal Loads Supply Air Total Heating Energy"),
        ("var", "Zone Ideal Loads Zone Sensible Heating Energy"),
        ("var", "Zone Ideal Loads Supply Air Total Heating Rate"),
        ("meter", "DistrictHeatingWater:Facility"),
        ("meter", "Heating:EnergyTransfer"),
    ],
    "Cooling": [
        ("var", "Zone Ideal Loads Supply Air Total Cooling Energy"),
        ("var", "Zone Ideal Loads Zone Sensible Cooling Energy"),
        ("var", "Zone Ideal Loads Supply Air Total Cooling Rate"),
        ("meter", "DistrictCooling:Facility"),
        ("meter", "Cooling:EnergyTransfer"),
    ],
    "Equipment (plug loads)": [
        ("var", "Zone Electric Equipment Electricity Energy"),
        ("var", "Zone Electric Equipment Total Heating Energy"),
        ("meter", "InteriorEquipment:Electricity"),
    ],
    "Lighting": [
        ("var", "Zone Lights Electricity Energy"),
        ("meter", "InteriorLights:Electricity"),
    ],
    "Facility totals": [
        ("meter", "Electricity:Facility"),
        ("meter", "EnergyTransfer:Facility"),
    ],
    "Zone temperatures & setpoints": [
        ("var", "Zone Mean Air Temperature"),
        ("var", "Zone Operative Temperature"),
        ("var", "Zone Thermostat Heating Setpoint Temperature"),
        ("var", "Zone Thermostat Cooling Setpoint Temperature"),
    ],
    "Envelope heat flows": [
        ("var", "Surface Inside Face Conduction Heat Transfer Energy"),
        ("var", "Surface Window Heat Loss Energy"),
        ("var", "Surface Window Heat Gain Energy"),
        ("var", "Zone Windows Total Transmitted Solar Radiation Energy"),
    ],
    "Infiltration & ventilation": [
        ("var", "Zone Infiltration Sensible Heat Loss Energy"),
        ("var", "Zone Infiltration Sensible Heat Gain Energy"),
        ("var", "Zone Infiltration Air Change Rate"),
        ("var", "Zone Ideal Loads Outdoor Air Total Heating Energy"),
    ],
    "Occupancy": [
        ("var", "Zone People Occupant Count"),
        ("var", "Zone People Total Heating Energy"),
    ],
    "Site weather": [
        ("var", "Site Outdoor Air Drybulb Temperature"),
        ("var", "Site Direct Solar Radiation Rate per Area"),
        ("var", "Site Diffuse Solar Radiation Rate per Area"),
    ],
}

DEFAULT_GROUPS = ("Heating", "Cooling", "Equipment (plug loads)", "Lighting", "Facility totals",
                  "Zone temperatures & setpoints")


def default_outputs(frequency="Hourly"):
    variables, meters = [], []
    for group in DEFAULT_GROUPS:
        for kind, name in PRESETS[group]:
            if kind == "var":
                variables.append({"name": name, "key": "*", "frequency": frequency})
            else:
                meters.append({"name": name, "frequency": frequency})
    return variables, meters


def apply_outputs(model, config, log) -> dict:
    if not config.keep_existing:
        for variable in model.getOutputVariables():
            variable.remove()
        for meter in model.getOutputMeters():
            meter.remove()
        log("Outputs: removed the base model's output variables and meters.")

    existing_vars = {(v.variableName(), v.keyValue(), v.reportingFrequency()) for v in model.getOutputVariables()}
    existing_meters = {(m.name(), m.reportingFrequency()) for m in model.getOutputMeters()}
    added_vars, added_meters = [], []
    for item in config.variables:
        key = (item["name"], item.get("key") or "*", item.get("frequency", "Hourly"))
        if key in existing_vars:
            continue
        variable = openstudio.model.OutputVariable(key[0], model)
        variable.setKeyValue(key[1])
        variable.setReportingFrequency(key[2])
        existing_vars.add(key)
        added_vars.append(key)
    for item in config.meters:
        key = (item["name"], item.get("frequency", "Hourly"))
        if key in existing_meters:
            continue
        meter = openstudio.model.OutputMeter(model)
        meter.setName(key[0])
        meter.setReportingFrequency(key[1])
        existing_meters.add(key)
        added_meters.append(key)
    log(f"Outputs: {len(added_vars)} variable(s) and {len(added_meters)} meter(s) added "
        f"({len(model.getOutputVariables())} variables / {len(model.getOutputMeters())} meters in model).")
    return {"variables": [list(k) for k in config_keys_vars(config)], "meters": [list(k) for k in config_keys_meters(config)]}


def config_keys_vars(config):
    return [(i["name"], i.get("key") or "*", i.get("frequency", "Hourly")) for i in config.variables]


def config_keys_meters(config):
    return [(i["name"], i.get("frequency", "Hourly")) for i in config.meters]


def verify_outputs(model, facts) -> list[tuple[str, str, str]]:
    if not facts:
        return []
    have_vars = {(v.variableName(), v.keyValue(), v.reportingFrequency()) for v in model.getOutputVariables()}
    have_meters = {(m.name(), m.reportingFrequency()) for m in model.getOutputMeters()}
    missing = [f"{n} [{f}]" for n, k, f in map(tuple, facts["variables"]) if (n, k, f) not in have_vars]
    missing += [f"{n} [{f}]" for n, f in map(tuple, facts["meters"]) if (n, f) not in have_meters]
    total = len(facts["variables"]) + len(facts["meters"])
    return [("Requested outputs in model", "PASS" if not missing else "FAIL",
             f"{total - len(missing)}/{total} present" + (f"; missing: {', '.join(missing[:6])}" if missing else ""))]
