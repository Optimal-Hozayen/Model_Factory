"""
=========================================================
Outputs Tab
=========================================================

Reporting variables and meters: preset groups (heating, cooling,
equipment, lighting...) with a frequency per item, plus custom
variables. Existing base-model outputs can be kept or removed.

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from Core.Outputs import DEFAULT_GROUPS, FREQUENCIES, PRESETS
from Tabs.Common import hint


class OutputsTab(QWidget):

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        controller.register(self)
        self.build_ui()
        self.pull(controller.project)

    def build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(hint(
            "Ideal loads: heating and cooling are reported by the 'Zone Ideal Loads' variables and the "
            "DistrictHeatingWater / DistrictCooling meters. Results are written to eplusout.sql."))
        top = QHBoxLayout()
        self.keep_existing = QCheckBox("Keep the base model's existing outputs")
        self.frequency_all = QComboBox()
        self.frequency_all.addItems(FREQUENCIES)
        self.frequency_all.setCurrentText("Hourly")
        apply_all = QPushButton("Set all selected to")
        apply_all.clicked.connect(self.set_all_frequency)
        top.addWidget(self.keep_existing)
        top.addStretch()
        top.addWidget(apply_all)
        top.addWidget(self.frequency_all)
        layout.addLayout(top)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Output", "Type", "Frequency"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.items = []              # (item, kind, name, frequency combo)
        for group, entries in PRESETS.items():
            parent = QTreeWidgetItem(self.tree, [group, "", ""])
            parent.setFlags(parent.flags() | Qt.ItemIsAutoTristate | Qt.ItemIsUserCheckable)
            parent.setCheckState(0, Qt.Unchecked)
            for kind, name in entries:
                child = QTreeWidgetItem(parent, [name, "meter" if kind == "meter" else "variable", ""])
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Unchecked)
                combo = QComboBox()
                combo.addItems(FREQUENCIES)
                combo.setCurrentText("Hourly")
                self.tree.setItemWidget(child, 2, combo)
                self.items.append((child, kind, name, combo))
            parent.setExpanded(group in DEFAULT_GROUPS)
        layout.addWidget(self.tree, 2)

        custom = QGroupBox("Custom output variables / meters")
        custom_layout = QVBoxLayout(custom)
        self.custom_table = QTableWidget(0, 4)
        self.custom_table.setHorizontalHeaderLabels(["Variable or meter name", "Key (* = all)", "Frequency", "Type"])
        self.custom_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.custom_table.verticalHeader().setVisible(False)
        custom_layout.addWidget(self.custom_table)
        row = QHBoxLayout()
        add = QPushButton("Add custom")
        remove = QPushButton("Remove")
        add.clicked.connect(lambda: self.add_custom("", "*", "Hourly", "variable"))
        remove.clicked.connect(lambda: self.custom_table.removeRow(self.custom_table.currentRow()))
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()
        custom_layout.addLayout(row)
        layout.addWidget(custom, 1)
        self.existing_label = QLabel("")
        self.existing_label.setWordWrap(True)
        layout.addWidget(self.existing_label)

    def set_all_frequency(self):
        for item, _kind, _name, combo in self.items:
            if item.checkState(0) == Qt.Checked:
                combo.setCurrentText(self.frequency_all.currentText())

    def add_custom(self, name, key, frequency, kind):
        row = self.custom_table.rowCount()
        self.custom_table.insertRow(row)
        self.custom_table.setItem(row, 0, QTableWidgetItem(name))
        self.custom_table.setItem(row, 1, QTableWidgetItem(key))
        freq = QComboBox()
        freq.addItems(FREQUENCIES)
        freq.setCurrentText(frequency)
        self.custom_table.setCellWidget(row, 2, freq)
        typ = QComboBox()
        typ.addItems(["variable", "meter"])
        typ.setCurrentText(kind)
        self.custom_table.setCellWidget(row, 3, typ)

    # -------------------------------------------------
    def pull(self, project):
        out = project.outputs
        self.keep_existing.setChecked(out.keep_existing)
        variables = {(v["name"]): v for v in out.variables}
        meters = {(m["name"]): m for m in out.meters}
        preset_names = set()
        for item, kind, name, combo in self.items:
            source = variables if kind == "var" else meters
            preset_names.add((kind, name))
            if name in source:
                item.setCheckState(0, Qt.Checked)
                combo.setCurrentText(source[name].get("frequency", "Hourly"))
            else:
                item.setCheckState(0, Qt.Unchecked)
        self.custom_table.setRowCount(0)
        for v in out.variables:
            if ("var", v["name"]) not in preset_names:
                self.add_custom(v["name"], v.get("key", "*"), v.get("frequency", "Hourly"), "variable")
        for m in out.meters:
            if ("meter", m["name"]) not in preset_names:
                self.add_custom(m["name"], "", m.get("frequency", "Hourly"), "meter")

    def push(self, project):
        variables, meters = [], []
        for item, kind, name, combo in self.items:
            if item.checkState(0) != Qt.Checked:
                continue
            if kind == "var":
                variables.append({"name": name, "key": "*", "frequency": combo.currentText()})
            else:
                meters.append({"name": name, "frequency": combo.currentText()})
        for row in range(self.custom_table.rowCount()):
            name = (self.custom_table.item(row, 0).text() if self.custom_table.item(row, 0) else "").strip()
            if not name:
                continue
            key = (self.custom_table.item(row, 1).text() if self.custom_table.item(row, 1) else "*").strip() or "*"
            frequency = self.custom_table.cellWidget(row, 2).currentText()
            if self.custom_table.cellWidget(row, 3).currentText() == "meter":
                meters.append({"name": name, "frequency": frequency})
            else:
                variables.append({"name": name, "key": key, "frequency": frequency})
        project.outputs.keep_existing = self.keep_existing.isChecked()
        project.outputs.variables, project.outputs.meters = variables, meters

    def on_inventory(self, inv):
        existing = [f"{n} [{f}]" for n, _k, f in inv.output_variables] + [f"{n} (meter) [{f}]" for n, f in inv.output_meters]
        self.existing_label.setText("Base model already reports: " + ("; ".join(existing) if existing else "nothing"))
