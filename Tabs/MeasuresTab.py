"""
=========================================================
Measures Tab
=========================================================

* Window-to-wall ratio per facade
* OpenStudio measures from the measures folder (NIST infiltration,
  TBD thermal bridging, reports). Argument forms are built from measure.xml.

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QScrollArea, QSplitter, QVBoxLayout, QWidget,
)

from Core.Inventory import FACADES
from Core.Project import MeasureConfig
from Tabs.Common import MeasureArgsForm, hint, spin


class MeasuresTab(QWidget):

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.configs: dict[str, MeasureConfig] = {}
        self.form: MeasureArgsForm | None = None
        self.form_measure = None
        controller.register(self)
        self.build_ui()
        self.pull(controller.project)

    def build_ui(self):
        layout = QVBoxLayout(self)

        # ---------------- WWR ----------------
        wwr_box = QGroupBox("Window-to-wall ratio changer (applied before constructions)")
        wwr_layout = QGridLayout(wwr_box)
        self.wwr_enabled = QCheckBox("Change the window-to-wall ratio")
        self.wwr_mode = QComboBox()
        self.wwr_mode.addItem("Only walls that already have windows", "walls_with_windows")
        self.wwr_mode.addItem("All exterior walls of the facade", "all_exterior_walls")
        self.sill = spin(0.0, 3.0, 0.9, 2, 0.05, " m")
        wwr_layout.addWidget(self.wwr_enabled, 0, 0, 1, 2)
        wwr_layout.addWidget(QLabel("Walls"), 0, 2)
        wwr_layout.addWidget(self.wwr_mode, 0, 3, 1, 2)
        wwr_layout.addWidget(QLabel("Sill height"), 0, 5)
        wwr_layout.addWidget(self.sill, 0, 6)
        self.same_all = QCheckBox("Same ratio on every facade")
        self.same_value = spin(0.01, 0.94, 0.30, 2, 0.05)
        wwr_layout.addWidget(self.same_all, 1, 0, 1, 2)
        wwr_layout.addWidget(self.same_value, 1, 2)
        self.facade_widgets = {}
        for col, facade in enumerate(FACADES):
            check = QCheckBox(facade)
            value = spin(0.01, 0.94, 0.30, 2, 0.05)
            current = QLabel("now: –")
            current.setStyleSheet("color:#555;")
            wwr_layout.addWidget(check, 2, col * 2)
            wwr_layout.addWidget(value, 2, col * 2 + 1)
            wwr_layout.addWidget(current, 3, col * 2, 1, 2)
            self.facade_widgets[facade] = (check, value, current)
        wwr_layout.addWidget(hint("One window per wall with the requested ratio, doors are kept. Non-rectangular walls are skipped."), 4, 0, 1, 8)
        layout.addWidget(wwr_box)
        self.same_all.toggled.connect(self._sync_same)
        self.same_value.valueChanged.connect(self._sync_same)

        # ---------------- OpenStudio measures ----------------
        splitter = QSplitter(Qt.Horizontal)
        left = QGroupBox("OpenStudio measures (tick to run; order = run order)")
        left_layout = QVBoxLayout(left)
        self.list = QListWidget()
        left_layout.addWidget(self.list)
        row = QHBoxLayout()
        self.up = QPushButton("Up")
        self.down = QPushButton("Down")
        self.rescan = QPushButton("Rescan measures folder")
        row.addWidget(self.up)
        row.addWidget(self.down)
        row.addStretch()
        row.addWidget(self.rescan)
        left_layout.addLayout(row)
        splitter.addWidget(left)

        right = QGroupBox("Arguments")
        right_layout = QVBoxLayout(right)
        self.title = QLabel("")
        self.title.setStyleSheet("font-weight:600;")
        self.title.setWordWrap(True)
        self.description = QLabel("")
        self.description.setWordWrap(True)
        self.description.setStyleSheet("color:#555;")
        right_layout.addWidget(self.title)
        right_layout.addWidget(self.description)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        right_layout.addWidget(self.scroll, 1)
        self.reset_args = QPushButton("Reset arguments to measure defaults")
        right_layout.addWidget(self.reset_args, 0, Qt.AlignRight)
        splitter.addWidget(right)
        splitter.setSizes([420, 760])
        layout.addWidget(splitter, 1)

        self.list.currentItemChanged.connect(self._selected)
        self.list.itemChanged.connect(self._checked)
        self.up.clicked.connect(lambda: self._move(-1))
        self.down.clicked.connect(lambda: self._move(1))
        self.rescan.clicked.connect(self._rescan)
        self.reset_args.clicked.connect(self._reset)

    # =====================================================
    def _sync_same(self, *_):
        same = self.same_all.isChecked()
        for check, value, _ in self.facade_widgets.values():
            if same:
                check.setChecked(True)
                value.setValue(self.same_value.value())
            value.setEnabled(not same)
            check.setEnabled(not same)

    def _store_form(self):
        if self.form is not None and self.form_measure in self.configs:
            self.configs[self.form_measure].arguments = self.form.values()

    def _fill_list(self, names):
        registry = self.controller.measures
        self.list.blockSignals(True)
        self.list.clear()
        for name in names:
            config = self.configs[name]
            info = registry.get(name)
            label = f"{name}  ({info.role})" if info else f"{name}  (not found in measures folder)"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if config.enabled and info else Qt.Unchecked)
            self.list.addItem(item)
        self.list.blockSignals(False)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _order(self):
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]

    def _selected(self, item, _previous=None):
        self._store_form()
        if item is None:
            return
        name = item.data(Qt.UserRole)
        info = self.controller.measures.get(name)
        self.form_measure = name
        if info is None:
            self.title.setText(name)
            self.description.setText("This measure is referenced by the project but is not in the measures folder.")
            self.form = None
            self.scroll.setWidget(QLabel(""))
            return
        self.title.setText(f"{info.display_name}   ({info.measure_type}, {info.role})")
        self.description.setText(info.description[:500])
        self.form = MeasureArgsForm(info, self.configs[name].arguments)
        self.scroll.setWidget(self.form)

    def _checked(self, item):
        name = item.data(Qt.UserRole)
        if name in self.configs:
            self.configs[name].enabled = item.checkState() == Qt.Checked

    def _move(self, step):
        row = self.list.currentRow()
        target = row + step
        if row < 0 or not 0 <= target < self.list.count():
            return
        self._store_form()
        order = self._order()
        order[row], order[target] = order[target], order[row]
        self._fill_list(order)
        self.list.setCurrentRow(target)

    def _rescan(self):
        self._store_form()
        self.controller.collect()
        self.controller.scan_measures()
        self.pull(self.controller.project)

    def _reset(self):
        if self.form_measure in self.configs:
            self.configs[self.form_measure].arguments = {}
            self.form = None
            self._selected(self.list.currentItem())

    # =====================================================
    def pull(self, project):
        w = project.wwr
        self.wwr_enabled.setChecked(w.enabled)
        self.wwr_mode.setCurrentIndex(max(0, self.wwr_mode.findData(w.mode)))
        self.sill.setValue(w.sill_height_m)
        values = list(w.ratios.values())
        same = len(set(values)) == 1 and all(w.apply_facade.values())
        self.same_all.setChecked(same)
        self.same_value.setValue(values[0] if values else 0.3)
        for facade, (check, value, _) in self.facade_widgets.items():
            check.setChecked(w.apply_facade.get(facade, False))
            value.setValue(w.ratios.get(facade, 0.3))
        self._sync_same()

        self.configs = {m.name: copy.deepcopy(m) for m in project.measures}
        order = [m.name for m in project.measures]
        for name in self.controller.measures:
            if name not in self.configs:
                self.configs[name] = MeasureConfig(name, enabled=False, arguments={})
                order.append(name)
        self.form, self.form_measure = None, None
        self._fill_list(order)

    def push(self, project):
        w = project.wwr
        w.enabled = self.wwr_enabled.isChecked()
        w.mode = self.wwr_mode.currentData()
        w.sill_height_m = self.sill.value()
        w.ratios = {f: widgets[1].value() for f, widgets in self.facade_widgets.items()}
        w.apply_facade = {f: widgets[0].isChecked() for f, widgets in self.facade_widgets.items()}
        self._store_form()
        project.measures = [copy.deepcopy(self.configs[name]) for name in self._order()]

    def on_inventory(self, inv):
        for facade, (_check, _value, label) in self.facade_widgets.items():
            ratio = inv.wwr_ratio(facade)
            label.setText(f"now: {ratio:.1%} of facade" if ratio is not None else "now: no walls")
