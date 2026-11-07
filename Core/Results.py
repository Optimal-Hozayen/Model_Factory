"""
=========================================================
Results
=========================================================

Reads eplusout.sql after a run:

* constructions used by EnergyPlus per category, with U-values
  (including TBD derated constructions)
* annual end uses, total site energy and EUI
* summary.csv with the main annual results

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from Core.Categories import CATEGORIES

GJ_TO_KWH = 277.7777778


def find_sql(build_folder) -> Path | None:
    folder = Path(build_folder)
    for candidate in (folder / "run" / "eplusout.sql", folder / "eplusout.sql"):
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def _to_kwh(value: float, units: str) -> float:
    units = (units or "").strip()
    if units == "GJ":
        return value * GJ_TO_KWH
    if units == "MJ":
        return value / 3.6
    if units == "kBtu":
        return value * 0.29307107
    return value     # kWh


def read_end_uses(con) -> dict:
    rows = con.execute(
        "SELECT RowName, ColumnName, Units, Value FROM TabularDataWithStrings "
        "WHERE ReportName='AnnualBuildingUtilityPerformanceSummary' AND TableName='End Uses' "
        "AND ReportForString='Entire Facility'").fetchall()
    end_uses = defaultdict(float)
    for row, column, units, value in rows:
        if "Water" in column and "District" not in column:
            continue                                   # water volumes, not energy
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        end_uses[row.strip()] += _to_kwh(number, units)
    return dict(end_uses)


def read_site(con) -> dict:
    facts = {}
    rows = con.execute(
        "SELECT RowName, ColumnName, Units, Value FROM TabularDataWithStrings "
        "WHERE ReportName='AnnualBuildingUtilityPerformanceSummary' AND TableName='Site and Source Energy'").fetchall()
    for row, column, units, value in rows:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if row.strip() == "Total Site Energy" and column.startswith("Total Energy"):
            facts["TotalSiteEnergy_kWh"] = _to_kwh(number, units)
        if row.strip() == "Total Site Energy" and column.startswith("Energy Per Total Building Area"):
            per = units.strip()
            facts["EUI_kWh_m2"] = number * GJ_TO_KWH / 1000 if per == "MJ/m2" else number
    area = con.execute(
        "SELECT Value FROM TabularDataWithStrings WHERE ReportName='AnnualBuildingUtilityPerformanceSummary' "
        "AND TableName='Building Area' AND RowName='Total Building Area'").fetchone()
    if area:
        facts["FloorArea_m2"] = float(area[0])
    return facts


def _layer_thicknesses(con) -> dict:
    """construction index -> [layer thickness (m), ...] from the SQL file."""
    layers = defaultdict(list)
    for cindex, _layer, thickness in con.execute(
            "SELECT cl.ConstructionIndex, cl.LayerIndex, m.Thickness FROM ConstructionLayers cl "
            "JOIN Materials m ON cl.MaterialIndex = m.MaterialIndex ORDER BY cl.ConstructionIndex, cl.LayerIndex"):
        layers[cindex].append(round(thickness or 0.0, 4))
    return layers


def read_surface_constructions(con, manifest) -> list[dict]:
    """Constructions used by EnergyPlus per category:

    * "bundle"            the bundle construction (or its reversed copy)
    * "bundle + TBD"      per-surface copies made by TBD, same layer
                          thicknesses with the insulation layer derated
    * anything else       not from the bundle
    U is the area-weighted EnergyPlus U-value without air films.
    """
    surface_category, expected_stack, expected_names = {}, {}, {}
    for key, entry in (manifest.get("constructions") or {}).items():
        for name in entry["surfaces"]:
            surface_category[name.upper()] = key
        stack = [round(layer["thickness_mm"] / 1000.0, 4) if layer["material"]["kind"] == "opaque" else 0.0
                 for layer in entry["bundle"]["layers"]]
        expected_stack[key] = (stack, stack[::-1])
        expected_names[key] = {entry["forward"].upper(), entry["reverse"].upper()}
    stacks = _layer_thicknesses(con)
    rows = con.execute(
        "SELECT s.SurfaceName, c.ConstructionIndex, c.Name, c.Uvalue, s.Area FROM Surfaces s "
        "JOIN Constructions c ON s.ConstructionIndex = c.ConstructionIndex").fetchall()
    grouped = defaultdict(lambda: defaultdict(lambda: {"n": 0, "area": 0.0, "ua": 0.0, "umin": None, "umax": None,
                                                        "names": set()}))
    for surface, cindex, construction, u_value, area in rows:
        key = surface_category.get(surface.upper())
        if key is None:
            continue
        name = construction.upper()
        if name in expected_names[key]:
            group = "bundle"
        elif name.endswith(" C TBD") and stacks.get(cindex) in expected_stack[key]:
            group = "bundle + TBD derating"
        else:
            group = construction
        cell = grouped[key][group]
        cell["n"] += 1
        cell["area"] += area or 0.0
        cell["ua"] += (u_value or 0.0) * (area or 0.0)
        cell["names"].add(construction)
        if u_value:
            cell["umin"] = u_value if cell["umin"] is None else min(cell["umin"], u_value)
            cell["umax"] = u_value if cell["umax"] is None else max(cell["umax"], u_value)
    result = []
    for key, groups in grouped.items():
        bundle_name = manifest["constructions"][key]["forward"]
        for group, cell in sorted(groups.items(), key=lambda kv: -kv[1]["n"]):
            from_bundle = group in ("bundle", "bundle + TBD derating")
            label = bundle_name if group == "bundle" else \
                f"{bundle_name}  + TBD derating ({len(cell['names'])} per-surface copies)" if from_bundle else group
            u_range = f"{cell['umin']:.3f}–{cell['umax']:.3f}" \
                if cell["umin"] is not None and round(cell["umax"], 3) != round(cell["umin"], 3) else ""
            result.append({"category": CATEGORIES[key].label, "construction": label, "surfaces": cell["n"],
                           "area_m2": cell["area"], "u_value": cell["ua"] / cell["area"] if cell["area"] else None,
                           "u_range": u_range, "from_bundle": from_bundle})
    return result


def analyse(build_folder) -> dict:
    folder = Path(build_folder)
    sql = find_sql(folder)
    if sql is None:
        raise FileNotFoundError(f"No eplusout.sql under {folder} - did the simulation finish?")
    manifest_file = folder / "manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8")) if manifest_file.is_file() else {}
    con = sqlite3.connect(str(sql))
    try:
        end_uses = read_end_uses(con)
        site = read_site(con)
        constructions = read_surface_constructions(con, manifest) if manifest else []
        severe = con.execute("SELECT COUNT(*) FROM Errors WHERE ErrorType IN (1, 2)").fetchone()[0] \
            if _has_table(con, "Errors") else None
    finally:
        con.close()

    heating = end_uses.get("Heating", 0.0)
    cooling = end_uses.get("Cooling", 0.0)
    summary = {
        "SimulationID": folder.name,
        "HeatingDemand": heating, "CoolingDemand": cooling,
        "InteriorEquipment": end_uses.get("Interior Equipment", 0.0),
        "InteriorLighting": end_uses.get("Interior Lighting", 0.0),
        "Fans": end_uses.get("Fans", 0.0),
        "TotalEnergy": site.get("TotalSiteEnergy_kWh"),
        "EUI": site.get("EUI_kWh_m2"),
        "FloorArea_m2": site.get("FloorArea_m2"),
    }
    result = {"sql": str(sql), "end_uses_kWh": end_uses, "site": site, "summary": summary,
              "constructions": constructions, "severe_or_fatal": severe}
    (folder / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    with open(folder / "summary.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary))
        writer.writeheader()
        writer.writerow(summary)
    return result


def _has_table(con, name) -> bool:
    return con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def run_command(openstudio_exe, osw_path) -> list[str]:
    return [str(openstudio_exe), "run", "-w", str(osw_path)]
