"""
=========================================================
Hvac
=========================================================

Assigns schedules to thermostats and HVAC systems per zone group
(replaces the SetThermostatSchedules measure).

Targets
-------
heating_setpoint      ThermostatSetpointDualSetpoint heating schedule
cooling_setpoint      ThermostatSetpointDualSetpoint cooling schedule
hvac_availability     availability of the zone's HVAC (ideal loads or zone
                      equipment) and of the air loop serving the zone
heating_availability  ideal loads heating availability
cooling_availability  ideal loads cooling availability

Zones with implicit ideal loads ("Use Ideal Air Loads") cannot take
availability schedules. For the availability targets they are converted
to an explicit ZoneHVAC:IdealLoadsAirSystem with the zone's outdoor air
specification.

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

from __future__ import annotations

import openstudio

from Core.Inventory import zone_matches
from Core.OsUtil import cast, name_of, opt

TARGETS = {
    "heating_setpoint": ("Thermostat heating setpoint", "Temperature"),
    "cooling_setpoint": ("Thermostat cooling setpoint", "Temperature"),
    "hvac_availability": ("HVAC availability (system on/off)", "OnOff"),
    "heating_availability": ("Ideal loads heating availability", "OnOff"),
    "cooling_availability": ("Ideal loads cooling availability", "OnOff"),
}

# Zone equipment types that expose setAvailabilitySchedule in the SDK
_EQUIPMENT_TYPES = [
    "ZoneHVACIdealLoadsAirSystem", "ZoneHVACPackagedTerminalAirConditioner", "ZoneHVACPackagedTerminalHeatPump",
    "ZoneHVACFourPipeFanCoil", "ZoneHVACUnitHeater", "ZoneHVACBaseboardConvectiveElectric",
    "ZoneHVACBaseboardConvectiveWater", "ZoneHVACWaterToAirHeatPump", "ZoneHVACTerminalUnitVariableRefrigerantFlow",
    "ZoneHVACEnergyRecoveryVentilator", "ZoneHVACUnitVentilator", "ZoneHVACLowTempRadiantElectric",
    "ZoneHVACBaseboardRadiantConvectiveElectric", "ZoneHVACBaseboardRadiantConvectiveWater",
]


def _zone_dict(zone):
    return {"name": zone.nameString(),
            "space_types": sorted({name_of(s.spaceType()) or "" for s in zone.spaces()})}


class HvacScheduler:

    def __init__(self, model, log=print, create_missing_thermostats=True):
        self.model = model
        self.log = log
        self.create_missing_thermostats = create_missing_thermostats
        self.zones = sorted(model.getThermalZones(), key=lambda z: z.nameString())

    def select(self, selector):
        return [z for z in self.zones if zone_matches(_zone_dict(z), selector)]

    # -------------------------------------------------
    def _own_thermostat(self, zone):
        thermostat = opt(zone.thermostatSetpointDualSetpoint())
        if thermostat is None:
            if not self.create_missing_thermostats:
                return None
            thermostat = openstudio.model.ThermostatSetpointDualSetpoint(self.model)
            thermostat.setName(f"{zone.nameString()} Thermostat")
            zone.setThermostatSetpointDualSetpoint(thermostat)
            return opt(zone.thermostatSetpointDualSetpoint())
        users = thermostat.getSources(openstudio.IddObjectType("OS:ThermalZone"))
        if len(users) > 1:   # shared thermostat, copy it for this zone
            copy = thermostat.clone(self.model).to_ThermostatSetpointDualSetpoint().get()
            zone.setThermostatSetpointDualSetpoint(copy)
            return opt(zone.thermostatSetpointDualSetpoint())
        return thermostat

    def _explicit_ideal_loads(self, zone):
        for equipment in zone.equipment():
            ideal = cast(equipment, "ZoneHVACIdealLoadsAirSystem")
            if ideal is not None:
                return ideal
        if not zone.useIdealAirLoads():
            return None
        zone.setUseIdealAirLoads(False)
        ideal = openstudio.model.ZoneHVACIdealLoadsAirSystem(self.model)
        ideal.setName(f"{zone.nameString()} Ideal Loads")
        for space in zone.spaces():
            dsoa = opt(space.designSpecificationOutdoorAir())
            if dsoa is not None:
                ideal.setDesignSpecificationOutdoorAirObject(dsoa)
                break
        ideal.addToThermalZone(zone)
        return ideal

    # -------------------------------------------------
    def apply(self, assignments, schedules_by_name) -> list[dict]:
        """Apply in order (later rows override earlier ones). Returns a record per assignment."""
        records = []
        for assignment in assignments:
            schedule = schedules_by_name.get(assignment.schedule) or opt(self.model.getScheduleByName(assignment.schedule))
            record = {"zones": assignment.zones, "target": assignment.target, "schedule": assignment.schedule,
                      "matched": 0, "applied": 0, "converted": 0, "skipped": [], "zone_names": []}
            records.append(record)
            if schedule is None:
                record["skipped"].append(f"schedule '{assignment.schedule}' not found")
                continue
            zones = self.select(assignment.zones)
            record["matched"] = len(zones)
            for zone in zones:
                ok = self._apply_one(zone, assignment.target, schedule, record)
                if ok:
                    record["applied"] += 1
                    record["zone_names"].append(zone.nameString())
            self.log(f"Schedules: {TARGETS[assignment.target][0]} <- '{assignment.schedule}' on "
                     f"{record['applied']}/{record['matched']} zone(s)"
                     + (f" ({record['converted']} implicit ideal loads made explicit)" if record["converted"] else ""))
        return records

    def _apply_one(self, zone, target, schedule, record) -> bool:
        if target in ("heating_setpoint", "cooling_setpoint"):
            thermostat = self._own_thermostat(zone)
            if thermostat is None:
                record["skipped"].append(f"{zone.nameString()}: no thermostat")
                return False
            setter = thermostat.setHeatingSetpointTemperatureSchedule if target == "heating_setpoint" \
                else thermostat.setCoolingSetpointTemperatureSchedule
            if not setter(schedule):
                record["skipped"].append(f"{zone.nameString()}: schedule type rejected (needs Temperature)")
                return False
            return True

        was_implicit = zone.useIdealAirLoads()
        ideal = self._explicit_ideal_loads(zone)
        if was_implicit and ideal is not None:
            record["converted"] += 1
        applied = False
        if target == "hvac_availability":
            for equipment in zone.equipment():
                for type_name in _EQUIPMENT_TYPES:
                    concrete = cast(equipment, type_name)
                    if concrete is not None and hasattr(concrete, "setAvailabilitySchedule"):
                        applied = concrete.setAvailabilitySchedule(schedule) or applied
                        break
            air_loop = opt(zone.airLoopHVAC())
            if air_loop is not None:
                applied = air_loop.setAvailabilitySchedule(schedule) or applied
        elif ideal is not None:
            setter = ideal.setHeatingAvailabilitySchedule if target == "heating_availability" \
                else ideal.setCoolingAvailabilitySchedule
            applied = setter(schedule)
        if not applied:
            record["skipped"].append(f"{zone.nameString()}: no HVAC object accepts this schedule")
        return applied


# =========================================================
# Verification
# =========================================================

def verify_hvac(model, records) -> list[tuple[str, str, str]]:
    checks = []
    zones = {z.nameString(): z for z in model.getThermalZones()}
    # last assignment per (zone, target)
    final = {}
    for record in records:
        for zone_name in record["zone_names"]:
            final[(zone_name, record["target"])] = record["schedule"]
    for record in records:
        label = f"{TARGETS[record['target']][0]} <- {record['schedule']}"
        if record["skipped"] and not record["applied"]:
            checks.append((label, "FAIL", "; ".join(record["skipped"][:5])))
            continue
        confirmed = 0
        expected = [z for z in record["zone_names"] if final.get((z, record["target"])) == record["schedule"]]
        for zone_name in expected:
            actual = _read_target(zones[zone_name], record["target"])
            if record["schedule"] in actual:
                confirmed += 1
        status = "PASS" if confirmed == len(expected) and record["matched"] == record["applied"] else \
            "WARN" if confirmed == len(expected) else "FAIL"
        detail = f"{confirmed}/{len(expected)} zones read back OK ({record['matched']} matched selector)"
        if record["skipped"]:
            detail += f"; skipped: {'; '.join(record['skipped'][:3])}"
        if len(expected) < len(record["zone_names"]):
            detail += f"; {len(record['zone_names']) - len(expected)} overridden by a later row"
        checks.append((label, status, detail))
    unconditioned = [name for name, z in zones.items()
                     if opt(z.thermostatSetpointDualSetpoint()) is None and (z.useIdealAirLoads() or len(z.equipment()))]
    if unconditioned:
        checks.append(("Zones with HVAC but no thermostat", "WARN", f"{len(unconditioned)}: {', '.join(unconditioned[:8])}"))
    return checks


def _read_target(zone, target) -> set[str]:
    names = set()
    if target in ("heating_setpoint", "cooling_setpoint"):
        thermostat = opt(zone.thermostatSetpointDualSetpoint())
        if thermostat is not None:
            getter = thermostat.heatingSetpointTemperatureSchedule if target == "heating_setpoint" \
                else thermostat.coolingSetpointTemperatureSchedule
            name = name_of(getter())
            if name:
                names.add(name)
        return names
    for equipment in zone.equipment():
        ideal = cast(equipment, "ZoneHVACIdealLoadsAirSystem")
        if ideal is not None:
            if target == "heating_availability":
                names.add(name_of(ideal.heatingAvailabilitySchedule()))
            elif target == "cooling_availability":
                names.add(name_of(ideal.coolingAvailabilitySchedule()))
            else:
                names.add(name_of(ideal.availabilitySchedule()))
        else:
            for type_name in _EQUIPMENT_TYPES:
                concrete = cast(equipment, type_name)
                if concrete is not None and hasattr(concrete, "availabilitySchedule"):
                    names.add(name_of(concrete.availabilitySchedule()))
                    break
    air_loop = opt(zone.airLoopHVAC())
    if air_loop is not None and target == "hvac_availability":
        names.add(name_of(air_loop.availabilitySchedule()))
    return names
