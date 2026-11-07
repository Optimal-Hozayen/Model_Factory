"""
=========================================================
Weather
=========================================================

EPW selection: header parsing, assignment to the model (weather
file + site), optional design days from the matching .ddy file,
ASHRAE climate zone and run period.

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

from __future__ import annotations

from pathlib import Path

import openstudio

from Core.OsUtil import opt

ASHRAE_ZONES = ["", "1A", "1B", "2A", "2B", "3A", "3B", "3C", "4A", "4B", "4C",
                "5A", "5B", "5C", "6A", "6B", "7", "8"]


def read_epw_header(epw_path) -> dict:
    """Parse the LOCATION line of an EPW (fast, no OpenStudio needed)."""
    epw_path = Path(epw_path)
    with open(epw_path, "r", encoding="latin-1") as handle:
        first = handle.readline().strip().split(",")
    if not first or first[0].upper() != "LOCATION" or len(first) < 10:
        raise ValueError(f"{epw_path.name} is not a valid EPW file (no LOCATION header).")
    return {
        "city": first[1], "state": first[2], "country": first[3], "source": first[4],
        "wmo": first[5], "latitude": float(first[6]), "longitude": float(first[7]),
        "time_zone": float(first[8]), "elevation": float(first[9]),
    }


def find_ddy(epw_path) -> Path | None:
    candidate = Path(epw_path).with_suffix(".ddy")
    return candidate if candidate.is_file() else None


def list_epw(folder) -> list[Path]:
    folder = Path(folder)
    return sorted(folder.glob("*.epw")) if folder.is_dir() else []


def _keep_design_day(name: str, mode: str) -> bool:
    if mode == "all":
        return True
    lowered = name.lower()
    return ("99.6%" in lowered and "htg" in lowered and "db" in lowered and "wind" not in lowered) or \
           (".4%" in lowered and "clg" in lowered and "db=>mwb" in lowered)


def apply_weather(model, config, log) -> dict:
    """Apply WeatherConfig to the model. Returns facts used by the verification step."""
    facts = {}
    if not config.epw:
        log("Weather: no EPW selected, keeping the base model's weather settings.")
        return facts
    epw_path = Path(config.epw)
    if not epw_path.is_file():
        raise FileNotFoundError(f"Weather file not found: {epw_path}")

    epw = openstudio.EpwFile(openstudio.toPath(str(epw_path)))
    if not openstudio.model.WeatherFile.setWeatherFile(model, epw):
        raise RuntimeError("OpenStudio refused the EPW file.")
    site = model.getSite()
    site.setName(f"{epw.city()}_{epw.stateProvinceRegion()}_{epw.country()}")
    site.setLatitude(epw.latitude())
    site.setLongitude(epw.longitude())
    site.setTimeZone(epw.timeZone())
    site.setElevation(epw.elevation())
    facts.update(epw=str(epw_path), city=epw.city(), latitude=epw.latitude(), longitude=epw.longitude())
    log(f"Weather: {epw_path.name} ({epw.city()}, lat {epw.latitude():.2f}, lon {epw.longitude():.2f}).")

    if config.import_design_days:
        ddy = find_ddy(epw_path)
        if ddy is None:
            log("Weather: no .ddy next to the EPW - keeping the model's existing design days.")
        else:
            ddy_model = opt(openstudio.energyplus.loadAndTranslateIdf(openstudio.toPath(str(ddy))))
            if ddy_model is None:
                log(f"Weather: could not read {ddy.name}; design days unchanged.")
            else:
                kept = [d for d in ddy_model.getDesignDays() if _keep_design_day(d.nameString(), "all"
                        if config.design_day_filter == "all" else "filtered")]
                if kept:
                    for old in model.getDesignDays():
                        old.remove()
                    for day in kept:
                        day.clone(model)
                    facts["design_days"] = [d.nameString() for d in kept]
                    log(f"Weather: replaced design days with {len(kept)} from {ddy.name}.")
                else:
                    log(f"Weather: {ddy.name} had no design day matching the filter; design days unchanged.")

    if config.climate_zone:
        zones = model.getClimateZones()
        zones.setClimateZone(openstudio.model.ClimateZones.ashraeInstitutionName(), config.climate_zone)
        facts["climate_zone"] = config.climate_zone
        log(f"Weather: ASHRAE climate zone set to {config.climate_zone}.")

    rp = config.run_period or {}
    run_period = model.getRunPeriod()
    run_period.setBeginMonth(int(rp.get("begin_month", 1)))
    run_period.setBeginDayOfMonth(int(rp.get("begin_day", 1)))
    run_period.setEndMonth(int(rp.get("end_month", 12)))
    run_period.setEndDayOfMonth(int(rp.get("end_day", 31)))
    facts["run_period"] = rp
    return facts


def verify_weather(model, facts) -> list[tuple[str, str, str]]:
    checks = []
    if not facts.get("epw"):
        return checks
    weather = opt(model.weatherFile())
    path = str(opt(weather.path())) if weather is not None and opt(weather.path()) is not None else ""
    ok = Path(path).name == Path(facts["epw"]).name
    checks.append(("Weather file set on model", "PASS" if ok else "FAIL", path or "(none)"))
    site = model.getSite()
    ok = abs(site.latitude() - facts["latitude"]) < 1e-3 and abs(site.longitude() - facts["longitude"]) < 1e-3
    checks.append(("Site location matches EPW", "PASS" if ok else "FAIL",
                   f"lat {site.latitude():.3f}, lon {site.longitude():.3f}"))
    if "climate_zone" in facts:
        value = ""
        for cz in model.getClimateZones().climateZones():
            if cz.institution() == "ASHRAE":
                value = cz.value()
        checks.append(("ASHRAE climate zone", "PASS" if value == facts["climate_zone"] else "FAIL", value))
    if "design_days" in facts:
        names = sorted(d.nameString() for d in model.getDesignDays())
        checks.append(("Design days", "PASS" if len(names) == len(facts["design_days"]) else "WARN",
                       "; ".join(names)))
    return checks
