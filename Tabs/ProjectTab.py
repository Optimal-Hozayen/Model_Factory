"""
=========================================================
Project Tab
=========================================================

Paths and a summary of the base model.

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLineEdit, QListWidget, QPlainTextEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QLabel,
)

from Core.Categories import CATEGORIES, CATEGORY_ORDER
from Tabs.Common import PathPicker, hint


class ProjectTab(QWidget):

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        controller.register(self)
        self.build_ui()
        self.pull(controller.project)

    def build_ui(self):
        layout = QVBoxLayout(self)

        paths = QGroupBox("Project and paths")
        form = QFormLayout(paths)
        self.name = QLineEdit()
        self.base_model = PathPicker("file", "OpenStudio model (*.osm)")
        self.measures_dir = PathPicker("dir")
        self.openstudio = PathPicker("file", "OpenStudio CLI (openstudio.exe openstudio)")
        self.runs_dir = PathPicker("dir")
        form.addRow("Project / simulation name", self.name)
        form.addRow("Base model (.osm)", self.base_model)
        form.addRow("Measures folder", self.measures_dir)
        form.addRow("OpenStudio executable", self.openstudio)
        form.addRow("Simulation output folder", self.runs_dir)
        buttons = QHBoxLayout()
        self.reload_button = QPushButton("Load / reload base model")
        self.save_paths_button = QPushButton("Remember these paths as defaults")
        buttons.addWidget(self.reload_button)
        buttons.addWidget(self.save_paths_button)
        buttons.addStretch()
        form.addRow(buttons)
        layout.addWidget(paths)

        summary = QGroupBox("What the base model contains")
        summary_layout = QVBoxLayout(summary)
        summary_layout.addWidget(hint(
            "Surfaces per envelope category. Bundles are assigned to each surface and to the default construction sets."))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Category", "Objects", "Area (m²)", "Hard-assigned", "Constructions in use"])
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        summary_layout.addWidget(self.table, 2)
        self.hvac_label = QLabel("Base model not loaded yet.")
        self.hvac_label.setWordWrap(True)
        summary_layout.addWidget(self.hvac_label)
        summary_layout.addWidget(QLabel("Model warnings"))
        self.warnings = QListWidget()
        summary_layout.addWidget(self.warnings, 1)
        layout.addWidget(summary, 1)

        notes_box = QGroupBox("Notes (saved with the project)")
        notes_layout = QVBoxLayout(notes_box)
        self.notes = QPlainTextEdit()
        self.notes.setMaximumHeight(70)
        notes_layout.addWidget(self.notes)
        layout.addWidget(notes_box)

        self.reload_button.clicked.connect(self.reload)
        self.save_paths_button.clicked.connect(self.save_defaults)

    # -------------------------------------------------
    def reload(self):
        self.push(self.controller.project)
        self.controller.scan_measures()
        self.controller.load_inventory()

    def save_defaults(self):
        s = self.controller.settings
        s.base_model, s.measures_dir = self.base_model.text(), self.measures_dir.text()
        s.openstudio_exe, s.runs_dir = self.openstudio.text(), self.runs_dir.text()
        s.save()
        self.controller.status.emit("Default paths saved to settings.json")

    def pull(self, project):
        s = self.controller.settings
        self.name.setText(project.name)
        self.base_model.setText(project.base_model)
        self.measures_dir.setText(project.measures_dir)
        self.openstudio.setText(s.openstudio_exe)
        self.runs_dir.setText(s.runs_dir)
        self.notes.setPlainText(project.notes)

    def push(self, project):
        project.name = self.name.text().strip() or "Simulation"
        project.base_model = self.base_model.text()
        project.measures_dir = self.measures_dir.text()
        project.notes = self.notes.toPlainText()
        self.controller.settings.openstudio_exe = self.openstudio.text()
        self.controller.settings.runs_dir = self.runs_dir.text()

    def on_inventory(self, inv):
        rows = [key for key in CATEGORY_ORDER if inv.categories[key].count]
        self.table.setRowCount(len(rows))
        for row, key in enumerate(rows):
            info = inv.categories[key]
            cons = ", ".join(f"{name} x{count}" for name, count in sorted(info.constructions.items(), key=lambda kv: -kv[1]))
            for col, value in enumerate([CATEGORIES[key].label, info.count, f"{info.area_m2:,.0f}",
                                         f"{info.hard_assigned}/{info.count}", cons]):
                self.table.setItem(row, col, QTableWidgetItem(str(value)))
        self.table.resizeColumnsToContents()
        h = inv.hvac_summary
        self.hvac_label.setText(
            f"Zones: {h['zones']}  |  implicit ideal air loads: {h['implicit_ideal_loads']}  |  "
            f"zones with thermostat: {h['zones_with_thermostat']} ({h['thermostats_without_schedules']} without schedules)  |  "
            f"air loops: {h['air_loops']}  |  plant loops: {h['plant_loops']}  |  "
            f"schedules: {len(inv.schedules)}  |  output variables: {len(inv.output_variables)}  |  "
            f"weather in model: {inv.weather_file or 'none'}")
        self.warnings.clear()
        self.warnings.addItems(inv.warnings or ["No warnings."])
