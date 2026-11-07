"""
=========================================================
Weather Tab
=========================================================

EPW file, design days, ASHRAE climate zone and run period.

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from Core.Weather import ASHRAE_ZONES, find_ddy, list_epw, read_epw_header
from Tabs.Common import PathPicker, hint


class WeatherTab(QWidget):

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        controller.register(self)
        self.build_ui()
        self.pull(controller.project)

    def build_ui(self):
        layout = QVBoxLayout(self)

        folder_box = QGroupBox("Weather files")
        folder_layout = QVBoxLayout(folder_box)
        row = QHBoxLayout()
        self.folder = PathPicker("dir")
        self.refresh_button = QPushButton("List EPW files")
        row.addWidget(QLabel("Folder"))
        row.addWidget(self.folder, 1)
        row.addWidget(self.refresh_button)
        folder_layout.addLayout(row)
        self.list = QListWidget()
        self.list.setMaximumHeight(170)
        folder_layout.addWidget(self.list)
        pick_row = QHBoxLayout()
        self.other_button = QPushButton("Use another EPW file...")
        pick_row.addWidget(self.other_button)
        pick_row.addStretch()
        folder_layout.addLayout(pick_row)
        layout.addWidget(folder_box)

        selected = QGroupBox("Selected weather")
        form = QFormLayout(selected)
        self.epw_label = QLabel("(none - the base model's weather settings are kept)")
        self.epw_label.setWordWrap(True)
        self.location = QLabel("")
        self.ddy_label = QLabel("")
        form.addRow("EPW", self.epw_label)
        form.addRow("Location", self.location)
        form.addRow("Design days", self.ddy_label)
        self.import_ddy = QCheckBox("Replace design days with the .ddy file next to the EPW")
        self.ddy_filter = QComboBox()
        self.ddy_filter.addItems(["99.6% heating + 0.4% cooling", "all"])
        form.addRow(self.import_ddy, self.ddy_filter)
        self.climate_zone = QComboBox()
        self.climate_zone.addItems(ASHRAE_ZONES)
        form.addRow("ASHRAE climate zone (written to model)", self.climate_zone)
        run_row = QHBoxLayout()
        self.begin_month, self.begin_day, self.end_month, self.end_day = (QSpinBox() for _ in range(4))
        for box, top in ((self.begin_month, 12), (self.begin_day, 31), (self.end_month, 12), (self.end_day, 31)):
            box.setRange(1, top)
        for label, box in (("from month", self.begin_month), ("day", self.begin_day),
                           ("to month", self.end_month), ("day", self.end_day)):
            run_row.addWidget(QLabel(label))
            run_row.addWidget(box)
        run_row.addStretch()
        form.addRow("Run period", run_row)
        layout.addWidget(selected)
        layout.addWidget(hint(
            "The EPW is set on the model (weather file and site) and in the workflow. "
            "The NIST infiltration measure can use 'Lookup From Model' for this climate zone."))
        layout.addStretch()

        self.refresh_button.clicked.connect(self.list_files)
        self.list.currentItemChanged.connect(lambda item, _: item and self.select(item.data(256)))
        self.other_button.clicked.connect(self.pick_other)

    # -------------------------------------------------
    def list_files(self):
        self.list.clear()
        for path in list_epw(self.folder.text()):
            self.list.addItem(path.name)
            self.list.item(self.list.count() - 1).setData(256, str(path))
            if str(path) == self._epw:
                self.list.setCurrentRow(self.list.count() - 1)

    def pick_other(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select EPW", self.folder.text(), "EnergyPlus weather (*.epw)")
        if path:
            self.select(path)

    def select(self, path):
        self._epw = path or ""
        if not path:
            self.epw_label.setText("(none - the base model's weather settings are kept)")
            self.location.setText("")
            self.ddy_label.setText("")
            return
        self.epw_label.setText(path)
        try:
            h = read_epw_header(path)
            self.location.setText(f"{h['city']}, {h['state']}, {h['country']} - lat {h['latitude']:.2f}, "
                                  f"lon {h['longitude']:.2f}, UTC{h['time_zone']:+g}, {h['elevation']:.0f} m  ({h['source']})")
        except (OSError, ValueError) as error:
            self.location.setText(f"Error: {error}")
        ddy = find_ddy(path)
        self.ddy_label.setText(ddy.name if ddy else "no .ddy next to the EPW - the model's design days are kept")
        self.import_ddy.setEnabled(ddy is not None)

    def pull(self, project):
        self.folder.setText(self.controller.settings.weather_dir)
        w = project.weather
        self._epw = w.epw
        self.list_files()
        self.select(w.epw)
        self.import_ddy.setChecked(w.import_design_days)
        self.ddy_filter.setCurrentText(w.design_day_filter)
        self.climate_zone.setCurrentText(w.climate_zone)
        rp = w.run_period
        self.begin_month.setValue(rp.get("begin_month", 1))
        self.begin_day.setValue(rp.get("begin_day", 1))
        self.end_month.setValue(rp.get("end_month", 12))
        self.end_day.setValue(rp.get("end_day", 31))

    def push(self, project):
        w = project.weather
        w.epw = self._epw
        w.import_design_days = self.import_ddy.isChecked()
        w.design_day_filter = self.ddy_filter.currentText()
        w.climate_zone = self.climate_zone.currentText()
        w.run_period = {"begin_month": self.begin_month.value(), "begin_day": self.begin_day.value(),
                        "end_month": self.end_month.value(), "end_day": self.end_day.value()}
        if self.folder.text() and Path(self.folder.text()).is_dir():
            self.controller.settings.weather_dir = self.folder.text()

    def on_inventory(self, inv):
        if not self.climate_zone.currentText() and inv.climate_zone:
            self.climate_zone.setCurrentText(inv.climate_zone)
