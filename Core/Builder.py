"""
=========================================================
Builder
=========================================================

Builds model.osm and workflow.osw from the base model.

Steps:
    1. weather, site, design days, climate zone, run period
    2. window-to-wall ratio (before constructions so new windows get the window bundle)
    3. constructions
    4. schedules (thermostats, HVAC availability)
    5. output variables and meters
    6. save model.osm and write workflow.osw
    7. verification on the saved model and on the translated IDF

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import openstudio

from Core.Categories import CATEGORIES
from Core.Constructions import ConstructionApplier, verify_constructions
from Core.Hvac import HvacScheduler, verify_hvac
from Core.Measures import scan_measures, write_workflow
from Core.OsUtil import load_model, save_model
from Core.Outputs import apply_outputs, verify_outputs
from Core.Project import Project
from Core.Schedules import ScheduleFactory, ScheduleSpec
from Core.Weather import apply_weather, verify_weather
from Core.Wwr import apply_wwr, verify_wwr

STALE_OUTPUTS = ("run", "reports", "generated_files", "out.osw", "openstudio_stdout.txt", "openstudio_stderr.txt")


@dataclass
class BuildResult:
    folder: Path
    osm: Path
    osw: Path
    checks: list = field(default_factory=list)     # (check, status, detail)
    seconds: float = 0.0

    @property
    def failed(self) -> list:
        return [c for c in self.checks if c[1] == "FAIL"]

    @property
    def ok(self) -> bool:
        return not self.failed


def build(project: Project, output_folder, log=print) -> BuildResult:
    start = time.time()
    folder = Path(output_folder)
    folder.mkdir(parents=True, exist_ok=True)
    for stale in STALE_OUTPUTS:                      # clear outputs of a previous run
        target = folder / stale
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.is_file():
            target.unlink()

    log(f"Loading base model {project.base_model} ...")
    model = load_model(project.base_model)
    manifest = {"project": project.to_dict(), "built": time.strftime("%Y-%m-%d %H:%M:%S")}

    # 1. weather -------------------------------------------------------
    manifest["weather"] = apply_weather(model, project.weather, log)

    # 2. WWR -----------------------------------------------------------
    manifest["wwr"] = apply_wwr(model, project.wwr, log)

    # 3. constructions ---------------------------------------------------
    manifest["constructions"] = ConstructionApplier(model, log).apply(project.constructions)

    # 4. schedules / HVAC ------------------------------------------------
    factory = ScheduleFactory(model, log)
    specs = {item["name"]: ScheduleSpec.from_dict(item) for item in project.schedules.loaded}
    referenced = {a.schedule for a in project.schedules.assignments}
    created = {}
    for name in sorted(referenced):
        if name in specs:
            created[name] = factory.create(specs[name])
            log(f"Schedules: created '{name}' ({specs[name].describe()}, type {specs[name].type}).")
    scheduler = HvacScheduler(model, log, project.schedules.create_missing_thermostats)
    manifest["hvac"] = scheduler.apply(project.schedules.assignments, created)

    # 5. outputs ---------------------------------------------------------
    manifest["outputs"] = apply_outputs(model, project.outputs, log)

    # save + workflow ------------------------------------------------------
    osm = save_model(model, folder / "model.osm")
    log(f"Saved {osm}")
    registry, errors = scan_measures(project.measures_dir)
    for error in errors:
        log(f"Measures: {error}")
    weather = project.weather.epw or None
    osw = folder / "workflow.osw"
    manifest["workflow"] = write_workflow(osw, osm, weather, project.measures_dir, project.measures, registry, log)

    # verification -----------------------------------------------------
    log("Verifying: re-loading the saved model ...")
    saved = load_model(osm)
    checks = [("Model saved and re-loaded", "PASS", str(osm))]
    checks += verify_weather(saved, manifest["weather"])
    checks += verify_wwr(saved, manifest["wwr"])
    checks += verify_constructions(saved, manifest["constructions"])
    checks += verify_hvac(saved, manifest["hvac"])
    checks += verify_outputs(saved, manifest["outputs"])
    if project.deep_check:
        log("Verifying: translating to EnergyPlus IDF (deep check) ...")
        checks += deep_check(saved, manifest)
    checks.append(("Workflow written", "PASS",
                   f"{len(manifest['workflow'])} measure(s): " + ", ".join(s["measure"] for s in manifest["workflow"])))

    manifest["checks"] = checks
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    write_report(folder / "verification.txt", project, checks)
    result = BuildResult(folder, osm, osw, checks, time.time() - start)
    log(f"Build finished in {result.seconds:.0f} s - {len(result.failed)} failed check(s).")
    return result


# =========================================================
# Deep check on the translated IDF
# =========================================================

def deep_check(model, manifest) -> list[tuple[str, str, str]]:
    translator = openstudio.energyplus.ForwardTranslator()
    workspace = translator.translateModel(model)
    checks = []
    errors = [e.logMessage() for e in translator.errors()]
    checks.append(("IDF translation", "PASS" if not errors else "FAIL",
                   "no translator errors" if not errors else "; ".join(errors[:5])))

    idf_constructions = {}
    for type_name in ("BuildingSurface:Detailed", "FenestrationSurface:Detailed"):
        for obj in workspace.getObjectsByType(openstudio.IddObjectType(type_name)):
            idf_constructions[obj.nameString().upper()] = obj.getString(2).get() if obj.getString(2).is_initialized() else ""
    for key, entry in manifest["constructions"].items():
        expected = entry["surfaces"]
        matched = sum(1 for name, construction in expected.items()
                      if idf_constructions.get(name.upper(), "").upper() == construction.upper())
        defined = workspace.getObjectByTypeAndName(openstudio.IddObjectType("Construction"), entry["forward"])
        layers = defined.get().numFields() - 1 if defined.is_initialized() else 0
        n_layers = len(entry["bundle"]["layers"])
        status = "PASS" if matched == len(expected) and layers == n_layers else "FAIL"
        checks.append((f"{CATEGORIES[key].label}: in EnergyPlus IDF", status,
                       f"{matched}/{len(expected)} IDF surfaces use '{entry['forward']}'"
                       + (" or its reversed twin" if entry["reverse"] != entry["forward"] else "") + f" ({layers} layers in IDF)"))

    thermostat_names = set()
    for obj in workspace.getObjectsByType(openstudio.IddObjectType("ThermostatSetpoint:DualSetpoint")):
        for index in (1, 2):
            if obj.getString(index).is_initialized():
                thermostat_names.add(obj.getString(index).get().upper())
    for record in manifest["hvac"]:
        if record["target"] in ("heating_setpoint", "cooling_setpoint") and record["applied"]:
            ok = record["schedule"].upper() in thermostat_names
            checks.append((f"IDF thermostats use '{record['schedule']}'", "PASS" if ok else "FAIL",
                           "found in ThermostatSetpoint:DualSetpoint" if ok else "not referenced in IDF"))

    variables = {o.getString(1).get().upper() for o in workspace.getObjectsByType(openstudio.IddObjectType("Output:Variable"))
                 if o.getString(1).is_initialized()}
    wanted = {v[0].upper() for v in manifest["outputs"]["variables"]} if manifest.get("outputs") else set()
    missing = sorted(wanted - variables)
    checks.append(("IDF output variables", "PASS" if not missing else "FAIL",
                   f"{len(wanted) - len(missing)}/{len(wanted)} present" + (f"; missing {missing[:4]}" if missing else "")))
    return checks


def write_report(path, project, checks):
    width = max((len(c[0]) for c in checks), default=20)
    lines = ["Model Factory - verification report", f"Project: {project.name}",
             f"Base model: {project.base_model}", ""]
    for name, status, detail in checks:
        lines.append(f"[{status:4}] {name.ljust(width)}  {detail}")
    failed = [c for c in checks if c[1] == "FAIL"]
    lines += ["", f"{len(checks)} checks, {len(failed)} failed."]
    Path(path).write_text("\n".join(lines), encoding="utf-8")
