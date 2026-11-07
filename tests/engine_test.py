"""
Headless end-to-end test of the engine (no GUI).

    python tests/engine_test.py <base.osm> <measures_dir> <epw> <out_dir> [--run <openstudio_exe>]

Builds two variants:
  A_baseline   base constructions, Heating M / Cooling M,
               NIST infiltration, TBD efficient, openstudio_results)
  B_variant    EPS 200 mm walls, 300 mm roof, triple glazing, WWR 30 %, schedules
               loaded from a folder (JSON day profile + CSV interval file)
and prints every verification check. With --run, simulates both and compares.

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from Core.Builder import build                          # noqa: E402
from Core.Inventory import build_inventory              # noqa: E402
from Core.Library import Bundle, Layer, default_materials  # noqa: E402
from Core.Outputs import default_outputs                # noqa: E402
from Core.Project import Project, ScheduleAssignment     # noqa: E402
from Core.Results import analyse, run_command           # noqa: E402
from Core.Schedules import load_schedule_folder         # noqa: E402


def make_schedule_folder() -> Path:
    folder = Path(tempfile.mkdtemp(prefix="mb_sched_"))
    (folder / "setpoints.json").write_text(json.dumps([
        {"name": "Heating 21C day 18C night", "type": "Temperature",
         "weekday": [18] * 6 + [21] * 16 + [18] * 2, "saturday": [18] * 8 + [21] * 14 + [18] * 2,
         "sunday": [18] * 8 + [21] * 14 + [18] * 2},
        {"name": "Cooling 26C", "type": "Temperature", "constant": 26},
    ]), encoding="utf-8")
    hours = 8760
    with open(folder / "corridor_hvac_availability.csv", "w") as handle:
        handle.write("availability\n")
        for h in range(hours):
            handle.write("1\n" if 6 <= h % 24 < 23 else "0\n")
    return folder


def print_checks(result):
    for name, status, detail in result.checks:
        print(f"  [{status:4}] {name}: {detail}")
    print(f"  -> {len(result.failed)} FAIL, built in {result.seconds:.0f}s\n")


def main():
    base, measures, epw, out = sys.argv[1:5]
    run_exe = sys.argv[sys.argv.index("--run") + 1] if "--run" in sys.argv else None
    out = Path(out)

    print("=== Inventory ===")
    inv = build_inventory(base)
    for key, info in inv.categories.items():
        if info.count:
            print(f"  {key:15} {info.count:4} objects  {info.area_m2:8.1f} m²  hard={info.hard_assigned}  {info.constructions}")
    for warning in inv.warnings:
        print("  WARN", warning)
    print("  HVAC:", inv.hvac_summary)
    print("  WWR:", {f: round(inv.wwr_ratio(f) or 0, 3) for f in inv.wwr})

    variables, meters = default_outputs()

    # ---------------- A: baseline equivalent ----------------
    a = Project(name="A_baseline", base_model=base, measures_dir=measures)
    a.weather.epw = epw
    a.outputs.variables, a.outputs.meters = variables, meters
    print("=== Build A ===")
    result_a = build(a, out / "A_baseline", log=lambda m: print("  ", m))
    print_checks(result_a)

    # ---------------- B: variant ----------------
    mats = {m.name: m for m in default_materials()}
    wall_seed = inv.seed_bundles["exterior_wall"]
    # replace the three "XPS 100mm" layers with one EPS 200 mm layer
    layers = [l for l in wall_seed.layers if not l.material.name.startswith("XPS")]
    layers.insert(2, Layer(mats["EPS"], 200))
    wall = Bundle("Wall - EPS 200mm", "exterior_wall", layers)
    roof_seed = inv.seed_bundles["roof"]
    roof_layers = [l for l in roof_seed.layers if not l.material.name.startswith("XPS")]
    roof_layers.insert(2, Layer(mats["EPS"], 300))
    roof = Bundle("Roof - EPS 300mm", "roof", roof_layers)
    window = Bundle("Window - triple U0.8", "window", [Layer(mats["Triple glazing U0.8"])])
    slab = inv.seed_bundles["interior_slab"].copy("Slab - base layers")
    print(f"  wall U={wall.u_with_films():.3f}  roof U={roof.u_with_films():.3f}  slab sym={slab.is_symmetric()}")

    b = Project(name="B_variant", base_model=base, measures_dir=measures)
    b.weather.epw = epw
    b.outputs.variables, b.outputs.meters = variables, meters
    b.constructions.update(exterior_wall=wall, roof=roof, window=window, interior_slab=slab)
    b.wwr.enabled = True
    b.wwr.ratios = {f: 0.30 for f in ("North", "East", "South", "West")}
    folder = make_schedule_folder()
    specs, errors = load_schedule_folder(folder)
    print("  loaded schedules:", [(s.name, s.kind, s.type, s.describe()) for s in specs], errors)
    b.schedules.folder = str(folder)
    b.schedules.loaded = [s.to_dict() for s in specs]
    b.schedules.assignments = [
        ScheduleAssignment("ALL", "heating_setpoint", "Heating 21C day 18C night"),
        ScheduleAssignment("ALL", "cooling_setpoint", "Cooling 26C"),
        ScheduleAssignment("spacetype:Corridor_Zonelist", "hvac_availability", "corridor_hvac_availability"),
    ]
    print("=== Build B ===")
    result_b = build(b, out / "B_variant", log=lambda m: print("  ", m))
    print_checks(result_b)
    b.save(out / "B_variant.project.json")
    reloaded = Project.load(out / "B_variant.project.json")
    assert reloaded.constructions["exterior_wall"].u_layers() == wall.u_layers(), "project round-trip failed"
    print("  project JSON round-trip OK")

    if run_exe:
        for result in (result_a, result_b):
            print(f"=== Simulating {result.folder.name} ===")
            proc = subprocess.run(run_command(run_exe, result.osw), cwd=result.folder, capture_output=True, text=True)
            (result.folder / "openstudio_stdout.txt").write_text(proc.stdout)
            (result.folder / "openstudio_stderr.txt").write_text(proc.stderr)
            print("  exit code", proc.returncode)
            if proc.returncode != 0:
                print(proc.stdout[-3000:])
                continue
            res = analyse(result.folder)
            print("  summary:", {k: round(v, 1) if isinstance(v, float) else v for k, v in res["summary"].items()})
            print("  severe/fatal:", res["severe_or_fatal"])
            for row in res["constructions"]:
                print(f"    {row['category']:28} {row['construction'][:55]:55} n={row['surfaces']:4} "
                      f"U={row['u_value'] or 0:.3f} bundle={row['from_bundle']}")


if __name__ == "__main__":
    main()
