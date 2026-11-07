"""
Offscreen GUI test.

    QT_QPA_PLATFORM=offscreen python tests/gui_test.py <screenshot_dir>

Requires settings.json pointing to an existing base model / measures / weather.

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt                     # noqa: E402
from PySide6.QtWidgets import QApplication        # noqa: E402

from Controller.Controller import Controller      # noqa: E402
from Core.Library import BundleLibrary            # noqa: E402
from Core.Categories import CATEGORY_ORDER        # noqa: E402
from GUI.MainWindow import MainWindow              # noqa: E402

shots = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/shots")
shots.mkdir(parents=True, exist_ok=True)
app = QApplication(sys.argv)
controller = Controller()
window = MainWindow(controller)
window.resize(1500, 950)
window.show()


def wait(condition, timeout=300):
    start = time.time()
    while not condition():
        app.processEvents()
        time.sleep(0.05)
        if time.time() - start > timeout:
            raise TimeoutError("timed out")


def shot(name, index):
    window.tabs.setCurrentIndex(index)
    for _ in range(10):
        app.processEvents()
    window.grab().save(str(shots / f"{name}.png"))


controller.load_inventory()
wait(lambda: controller.inventory is not None)
print("inventory loaded")
shot("1_project", 0)
shot("2_weather", 1)

# ---------------- Constructions: build a bundle from the base-model seed ----------------
tab = window.constructions_tab
tab.assign_table.selectRow(CATEGORY_ORDER.index("exterior_wall"))
app.processEvents()
assert tab.editing is not None and tab.editing.source == "model", "seed bundle should load"
n_before = len(tab.editing.layers)
tab.duplicate_bundle()
tab.name_edit.setText("GUI Wall EPS 250")
# remove the three XPS layers (rows 2,3,4), add EPS and set 250 mm
for _ in range(3):
    tab.layer_table.selectRow(2)
    tab.remove_layer()
tab.layer_table.selectRow(1)
tab.add_layer()                                     # inserted after the air gap
row = 2
tab.layer_table.cellWidget(row, 1).setCurrentText("EPS")
tab.layer_table.cellWidget(row, 3).setValue(250)
app.processEvents()
bundle = tab._read_editor()
print("editor layers:", [l.describe() for l in bundle.layers], "U=", round(bundle.u_with_films(), 4))
assert [l.material.name for l in bundle.layers][2] == "EPS" and bundle.layers[2].thickness_mm == 250
assert len(bundle.layers) == n_before - 2
tab.save_bundle(assign=True)
assert tab.combos["exterior_wall"].currentText() == "GUI Wall EPS 250"
assert BundleLibrary(controller.bundles.folder).bundles["GUI Wall EPS 250"].layers[2].thickness_mm == 250, \
    "bundle must be on disk and reload identically"
print("bundle saved + reloaded from disk OK")

# window bundle from default glazing
tab.assign_table.selectRow(CATEGORY_ORDER.index("window"))
app.processEvents()
tab.new_bundle()
tab.name_edit.setText("GUI Triple glazing")
tab.layer_table.cellWidget(0, 1).setCurrentText("Triple glazing U0.8")
tab.save_bundle(assign=True)
tab.assign_table.selectRow(CATEGORY_ORDER.index("exterior_wall"))
shot("3_constructions", 2)

# ---------------- Schedules ----------------
sched = window.schedules_tab
folder = shots / "schedules"
folder.mkdir(exist_ok=True)
sched.folder.setText(str(folder))
sched.write_examples()
print("schedules loaded:", [s.name for s in sched.specs])
sched.assign_table.setRowCount(0)
from Core.Project import ScheduleAssignment                      # noqa: E402
sched.add_assignment(ScheduleAssignment("ALL", "heating_setpoint", "Heating 21C day / 18C night"))
sched.add_assignment(ScheduleAssignment("ALL", "cooling_setpoint", "Cooling 26C"))
sched.add_assignment(ScheduleAssignment("spacetype:Corridor_Zonelist", "hvac_availability", "HVAC on 6h-23h"))
sched.schedule_table.selectRow(0)
print("matches:", [sched.assign_table.item(r, 3).text() for r in range(3)])
shot("4_schedules", 3)
shot("5_outputs", 4)

meas = window.measures_tab
meas.wwr_enabled.setChecked(True)
meas.same_all.setChecked(True)
meas.same_value.setValue(0.35)
for i in range(meas.list.count()):
    if meas.list.item(i).data(Qt.UserRole) == "TBDMeasure":
        meas.list.setCurrentRow(i)
shot("6_measures", 5)

# ---------------- Save / reopen project ----------------
controller.project.name = "GUI_test"
window.project_tab.name.setText("GUI_test")
path = controller.save_project(shots / "GUI_test.json")
controller.open_project(path)
assert window.constructions_tab.combos["exterior_wall"].currentText() == "GUI Wall EPS 250"
assert controller.project.wwr.enabled and abs(controller.project.wwr.ratios["North"] - 0.35) < 1e-9
assert len(controller.project.schedules.assignments) == 3
print("project save/open round-trip OK")

# ---------------- Build through the GUI ----------------
run = window.run_tab
run.start_build(run_after=False)
wait(lambda: run.result is not None or run.status.text().startswith("Build failed"), timeout=600)
print(run.status.text())
fails = [c for c in run.result.checks if c[1] != "PASS"]
for c in run.result.checks:
    print(f"  [{c[1]}] {c[0]}: {c[2][:110]}")
shot("7_build", 6)
print("NON-PASS:", fails)
app.quit()
