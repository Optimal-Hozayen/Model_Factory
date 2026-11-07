"""
=========================================================
Schedules & HVAC Tab
=========================================================

1. Load schedules from a folder (CSV, JSON, OSM)
2. Check the schedule types and preview the first week
3. Assign schedules to HVAC targets per zone group. Rows are applied
   top to bottom, a later row overrides an earlier one.

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from Core.Hvac import TARGETS
from Core.Inventory import describe_selector, zone_matches
from Core.Project import ScheduleAssignment, default_assignments
from Core.Schedules import SCHEDULE_TYPES, ScheduleSpec, load_schedule_folder
from Tabs.Common import PathPicker, hint

try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
except ImportError:            # plotting is optional
    FigureCanvas = None


class SchedulesTab(QWidget):

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.specs: list[ScheduleSpec] = []
        self.inventory = None
        controller.register(self)
        self.build_ui()
        self.pull(controller.project)

    # =====================================================
    # UI
    # =====================================================

    def build_ui(self):
        layout = QVBoxLayout(self)

        folder_box = QGroupBox("1. Schedule folder")
        folder_layout = QVBoxLayout(folder_box)
        row = QHBoxLayout()
        self.folder = PathPicker("dir")
        self.load_button = QPushButton("Load schedules")
        self.example_button = QPushButton("Write example files...")
        row.addWidget(self.folder, 1)
        row.addWidget(self.load_button)
        row.addWidget(self.example_button)
        folder_layout.addLayout(row)
        folder_layout.addWidget(hint(
            "CSV: one schedule per numeric column, a full year (8760 hourly, 35040 at 15 min...). "
            "JSON: 24-hour 'weekday' / 'saturday' / 'sunday' / 'holiday' profiles, or a 'constant'. "
            "OSM: all schedules of another OpenStudio model. Base model schedules are also listed below."))
        layout.addWidget(folder_box)

        splitter = QSplitter(Qt.Horizontal)
        loaded_box = QGroupBox("2. Loaded schedules (check the type)")
        loaded_layout = QVBoxLayout(loaded_box)
        self.schedule_table = QTableWidget(0, 6)
        self.schedule_table.setHorizontalHeaderLabels(["Name", "Type", "Content", "Min", "Max", "Mean"])
        self.schedule_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.schedule_table.verticalHeader().setVisible(False)
        self.schedule_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.schedule_table.setEditTriggers(QTableWidget.NoEditTriggers)
        loaded_layout.addWidget(self.schedule_table)
        self.load_errors = QLabel("")
        self.load_errors.setWordWrap(True)
        self.load_errors.setStyleSheet("color:#b00020;")
        loaded_layout.addWidget(self.load_errors)
        splitter.addWidget(loaded_box)

        preview_box = QGroupBox("Preview - first week (hourly)")
        preview_layout = QVBoxLayout(preview_box)
        if FigureCanvas is not None:
            self.figure = Figure(figsize=(5, 2.6), tight_layout=True)
            self.canvas = FigureCanvas(self.figure)
            preview_layout.addWidget(self.canvas)
        else:
            preview_layout.addWidget(QLabel("Install matplotlib to preview schedules."))
        splitter.addWidget(preview_box)
        splitter.setSizes([700, 500])
        layout.addWidget(splitter, 1)

        assign_box = QGroupBox("3. Assign schedules to HVAC (rows applied top to bottom)")
        assign_layout = QVBoxLayout(assign_box)
        self.hvac_label = QLabel("")
        self.hvac_label.setWordWrap(True)
        assign_layout.addWidget(self.hvac_label)
        self.assign_table = QTableWidget(0, 4)
        self.assign_table.setHorizontalHeaderLabels(["Zones", "HVAC target", "Schedule", "Zones matched"])
        for col in range(3):
            self.assign_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.Stretch)
        self.assign_table.verticalHeader().setVisible(False)
        self.assign_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        assign_layout.addWidget(self.assign_table, 1)
        buttons = QHBoxLayout()
        self.add_button = QPushButton("Add assignment")
        self.remove_button = QPushButton("Remove")
        self.defaults_button = QPushButton("Reset to defaults (Heating M / Cooling M on all zones)")
        self.create_thermostats = QCheckBox("Create a thermostat for zones that have none")
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.remove_button)
        buttons.addWidget(self.defaults_button)
        buttons.addStretch()
        buttons.addWidget(self.create_thermostats)
        assign_layout.addLayout(buttons)
        layout.addWidget(assign_box, 1)

        self.load_button.clicked.connect(self.load_folder)
        self.example_button.clicked.connect(self.write_examples)
        self.schedule_table.currentCellChanged.connect(lambda row, *_: self.preview(row))
        self.add_button.clicked.connect(lambda: self.add_assignment(ScheduleAssignment()))
        self.remove_button.clicked.connect(self.remove_assignment)
        self.defaults_button.clicked.connect(self.reset_defaults)

    # =====================================================
    # Loaded schedules
    # =====================================================

    def load_folder(self):
        specs, errors = load_schedule_folder(self.folder.text())
        if not specs and errors:
            QMessageBox.warning(self, "No schedules", "\n".join(errors[:15]))
        self.set_specs(specs)
        self.load_errors.setText("\n".join(errors[:10]))
        self.controller.status.emit(f"{len(specs)} schedule(s) loaded from {self.folder.text()}")

    def set_specs(self, specs):
        self.specs = specs
        self.schedule_table.setRowCount(len(specs))
        for row, spec in enumerate(specs):
            self.schedule_table.setItem(row, 0, QTableWidgetItem(spec.name))
            self.schedule_table.item(row, 0).setToolTip(spec.source)
            combo = QComboBox()
            combo.addItems(SCHEDULE_TYPES)
            combo.setCurrentText(spec.type)
            combo.currentTextChanged.connect(lambda text, s=spec: setattr(s, "type", text))
            self.schedule_table.setCellWidget(row, 1, combo)
            stats = spec.stats()
            values = [spec.describe()] + ([f"{v:.2f}" for v in stats] if stats else ["", "", ""])
            for col, value in enumerate(values, start=2):
                self.schedule_table.setItem(row, col, QTableWidgetItem(value))
        self.refresh_schedule_choices()
        if specs:
            self.schedule_table.selectRow(0)

    def preview(self, row):
        if FigureCanvas is None or not (0 <= row < len(self.specs)):
            return
        spec = self.specs[row]
        self.figure.clear()
        axis = self.figure.add_subplot(111)
        data = spec.preview()
        if data:
            axis.step(range(len(data)), data, where="post", linewidth=1.4)
            axis.set_xticks(range(0, 169, 24))
            axis.set_xticklabels(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun", ""])
            axis.grid(True, alpha=0.3)
            axis.set_title(f"{spec.name} ({spec.type})", fontsize=9)
        else:
            axis.text(0.5, 0.5, "Preview available after build (cloned from OSM)", ha="center", va="center")
            axis.set_axis_off()
        self.canvas.draw_idle()

    def write_examples(self):
        folder = Path(self.folder.text() or ".")
        if not folder.is_dir():
            QMessageBox.warning(self, "Folder", "Choose an existing folder first.")
            return
        (folder / "example_setpoints.json").write_text(json.dumps([
            {"name": "Heating 21C day / 18C night", "type": "Temperature",
             "weekday": [18] * 6 + [21] * 16 + [18] * 2, "saturday": [18] * 8 + [21] * 14 + [18] * 2,
             "sunday": [18] * 8 + [21] * 14 + [18] * 2},
            {"name": "Cooling 26C", "type": "Temperature", "constant": 26},
            {"name": "HVAC on 6h-23h", "type": "OnOff", "weekday": [0] * 6 + [1] * 17 + [0]},
        ], indent=2), encoding="utf-8")
        with open(folder / "example_hourly.csv", "w", encoding="utf-8") as handle:
            handle.write("heating_setpoint,availability\n")
            for hour in range(8760):
                h = hour % 24
                handle.write(f"{21 if 6 <= h < 22 else 18},{1 if 6 <= h < 23 else 0}\n")
        self.controller.status.emit(f"Example schedule files written to {folder}")
        self.load_folder()

    # =====================================================
    # Assignments
    # =====================================================

    def schedule_names(self):
        names = [s.name for s in self.specs]
        if self.inventory:
            names += [f"{n}" for n, _t, _u in self.inventory.schedules if n not in names]
        return names

    def zone_choices(self):
        choices = [("All thermal zones", "ALL")]
        if self.inventory:
            choices += [(f"Space type: {st}", f"spacetype:{st}") for st in self.inventory.space_types]
            choices += [(f"Zone: {z['name']}", f"zone:{z['name']}") for z in self.inventory.zones]
        return choices

    def add_assignment(self, assignment: ScheduleAssignment):
        row = self.assign_table.rowCount()
        self.assign_table.insertRow(row)
        zones = QComboBox()
        zones.setEditable(True)
        zones.setToolTip("Pick a group, or type 'contains:<text>' to match zone names.")
        for label, selector in self.zone_choices():
            zones.addItem(label, selector)
        index = zones.findData(assignment.zones)
        if index >= 0:
            zones.setCurrentIndex(index)
        else:
            zones.setEditText(assignment.zones)
        zones.currentTextChanged.connect(lambda *_: self.update_matches())
        target = QComboBox()
        for key, (label, _type) in TARGETS.items():
            target.addItem(label, key)
        target.setCurrentIndex(max(0, target.findData(assignment.target)))
        schedule = QComboBox()
        schedule.setEditable(True)
        schedule.addItems(self.schedule_names())
        schedule.setCurrentText(assignment.schedule)
        schedule.currentTextChanged.connect(lambda *_: self.update_matches())
        target.currentIndexChanged.connect(lambda *_: self.update_matches())
        self.assign_table.setCellWidget(row, 0, zones)
        self.assign_table.setCellWidget(row, 1, target)
        self.assign_table.setCellWidget(row, 2, schedule)
        self.assign_table.setItem(row, 3, QTableWidgetItem(""))
        self.update_matches()

    def remove_assignment(self):
        row = self.assign_table.currentRow()
        if row >= 0:
            self.assign_table.removeRow(row)

    def reset_defaults(self):
        self.assign_table.setRowCount(0)
        for assignment in default_assignments():
            self.add_assignment(assignment)

    def refresh_schedule_choices(self):
        names = self.schedule_names()
        for row in range(self.assign_table.rowCount()):
            combo = self.assign_table.cellWidget(row, 2)
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(names)
            combo.setCurrentText(current)
            combo.blockSignals(False)
        self.update_matches()

    @staticmethod
    def _selector(combo) -> str:
        text = combo.currentText().strip()
        index = combo.findText(text)
        if index >= 0:
            return combo.itemData(index)
        if text.split(":", 1)[0] in ("contains", "zone", "spacetype") or text == "ALL":
            return text
        return f"contains:{text}" if text else "ALL"

    def read_assignments(self):
        rows = []
        for row in range(self.assign_table.rowCount()):
            rows.append(ScheduleAssignment(
                zones=self._selector(self.assign_table.cellWidget(row, 0)),
                target=self.assign_table.cellWidget(row, 1).currentData(),
                schedule=self.assign_table.cellWidget(row, 2).currentText().strip()))
        return rows

    def update_matches(self):
        known = set(self.schedule_names())
        types = {s.name: s.type for s in self.specs}
        if self.inventory:
            for name, _t, unit in self.inventory.schedules:
                types.setdefault(name, "Temperature" if unit == "Temperature" else unit or "Any Number")
        for row, assignment in enumerate(self.read_assignments()):
            item = self.assign_table.item(row, 3)
            if item is None:
                continue
            notes = []
            if self.inventory:
                n = sum(1 for z in self.inventory.zones if zone_matches(z, assignment.zones))
                notes.append(f"{n} zone(s)")
            if assignment.schedule not in known:
                notes.append("schedule not found")
            needed = TARGETS[assignment.target][1]
            actual = types.get(assignment.schedule)
            if needed == "Temperature" and actual and actual not in ("Temperature", "Any Number"):
                notes.append(f"needs a Temperature schedule (this one is {actual})")
            item.setText("  ".join(notes))
            item.setToolTip(describe_selector(assignment.zones))

    # =====================================================
    # Project sync
    # =====================================================

    def pull(self, project):
        sched = project.schedules
        self.folder.setText(sched.folder or self.controller.settings.schedules_dir)
        self.set_specs([ScheduleSpec.from_dict(item) for item in sched.loaded])
        self.assign_table.setRowCount(0)
        for assignment in sched.assignments:
            self.add_assignment(assignment)
        self.create_thermostats.setChecked(sched.create_missing_thermostats)

    def push(self, project):
        project.schedules.folder = self.folder.text()
        project.schedules.loaded = [spec.to_dict() for spec in self.specs]
        project.schedules.assignments = self.read_assignments()
        project.schedules.create_missing_thermostats = self.create_thermostats.isChecked()
        if self.folder.text():
            self.controller.settings.schedules_dir = self.folder.text()

    def on_inventory(self, inv):
        self.inventory = inv
        h = inv.hvac_summary
        self.hvac_label.setText(
            f"Base model HVAC: {h['zones']} zones, {h['implicit_ideal_loads']} on implicit ideal air loads, "
            f"{h['zones_with_thermostat']} with a thermostat ({h['thermostats_without_schedules']} have no schedules yet), "
            f"{h['air_loops']} air loops. Availability targets use an explicit ZoneHVAC:IdealLoadsAirSystem.")
        current = self.read_assignments()
        self.assign_table.setRowCount(0)
        for assignment in current:
            self.add_assignment(assignment)
        self.refresh_schedule_choices()
