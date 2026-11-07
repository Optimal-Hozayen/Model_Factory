"""
=========================================================
Constructions Tab
=========================================================

Left : one row per envelope category -> which bundle is assigned
       ("keep base model" or a named bundle) and its U-value.
Right: bundle editor for the selected category - layers built from
       the material library, live R / U, validation, and
       Save (to Library/Bundles/<name>.json), Duplicate, Delete,
       Import, Export.

The base model construction of each category is listed as a
read-only "(base model)" bundle.

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from Core.Categories import CATEGORIES, CATEGORY_ORDER
from Core.Library import MATERIAL_KINDS, ROUGHNESS, Bundle, Layer, MaterialSpec
from Tabs.Common import hint, spin

KEEP = "(keep base model)"
KIND_FOR = {"opaque": ("opaque", "airgap", "nomass"), "glazing": ("glazing",), "any": MATERIAL_KINDS}


# =========================================================
# Material dialog
# =========================================================

class MaterialDialog(QDialog):
    """Create or edit one material of the library."""

    def __init__(self, spec: MaterialSpec | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Material")
        spec = spec or MaterialSpec(name="", kind="opaque", conductivity=0.035, density=30, specific_heat=1400)
        form = QFormLayout(self)
        self.name = QLineEdit(spec.name)
        self.kind = QComboBox()
        self.kind.addItems(MATERIAL_KINDS)
        self.kind.setCurrentText(spec.kind)
        self.k = spin(0.0, 50, spec.conductivity, 4, 0.001)
        self.density = spin(0.0, 20000, spec.density, 1)
        self.cp = spin(0.0, 10000, spec.specific_heat, 1)
        self.thickness = spin(0.1, 3000, spec.default_thickness_mm, 1, suffix=" mm")
        self.roughness = QComboBox()
        self.roughness.addItems(ROUGHNESS)
        self.roughness.setCurrentText(spec.roughness)
        self.r = spin(0.0, 50, spec.r_value, 4)
        self.u = spin(0.0, 7, spec.u_factor, 3)
        self.shgc = spin(0.0, 1, spec.shgc, 3)
        self.vt = spin(0.0, 1, spec.vt, 3)
        self.cost = spin(0.0, 100000, spec.cost, 2)
        self.carbon = spin(0.0, 100000, spec.carbon, 2)
        self._spec = spec
        for label, widget in [("Name", self.name), ("Kind", self.kind), ("Conductivity (W/m·K)", self.k),
                              ("Density (kg/m³)", self.density), ("Specific heat (J/kg·K)", self.cp),
                              ("Default thickness", self.thickness), ("Roughness", self.roughness),
                              ("R-value (m²K/W) - air gap / no-mass", self.r), ("U-factor (W/m²K) - glazing", self.u),
                              ("SHGC - glazing", self.shgc), ("Visible transmittance - glazing", self.vt),
                              ("Cost ($/m² per 100 mm; glazing/gap: $/m²)", self.cost),
                              ("Embodied carbon (kgCO₂e/m² per 100 mm)", self.carbon)]:
            form.addRow(label, widget)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.kind.currentTextChanged.connect(self._update_enabled)
        self._update_enabled(spec.kind)

    def _update_enabled(self, kind):
        for widget in (self.k, self.density, self.cp, self.thickness, self.roughness):
            widget.setEnabled(kind == "opaque")
        self.r.setEnabled(kind in ("airgap", "nomass"))
        for widget in (self.u, self.shgc, self.vt):
            widget.setEnabled(kind == "glazing")

    def spec(self) -> MaterialSpec:
        s = self._spec
        return MaterialSpec(
            name=self.name.text().strip(), kind=self.kind.currentText(), conductivity=self.k.value(),
            density=self.density.value(), specific_heat=self.cp.value(), default_thickness_mm=self.thickness.value(),
            roughness=self.roughness.currentText(), thermal_absorptance=s.thermal_absorptance,
            solar_absorptance=s.solar_absorptance, visible_absorptance=s.visible_absorptance,
            r_value=self.r.value(), u_factor=self.u.value(), shgc=self.shgc.value(), vt=self.vt.value(),
            cost=self.cost.value(), carbon=self.carbon.value(), source="user")

    def _accept(self):
        errors = self.spec().validate()
        if errors:
            QMessageBox.warning(self, "Invalid material", "\n".join(errors))
            return
        self.accept()


class MaterialLibraryDialog(QDialog):
    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.setWindowTitle("Material library")
        self.resize(900, 500)
        layout = QVBoxLayout(self)
        layout.addWidget(hint("User and default materials are saved in Library/materials.json. "
                              "Base model materials are read-only (duplicate to edit)."))
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(["Name", "Kind", "k", "ρ", "cp", "R", "U / SHGC / VT", "Cost", "Source"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        for text, slot in (("Add...", self.add), ("Edit / duplicate...", self.edit), ("Delete", self.delete)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)
        self.refresh()

    def refresh(self):
        materials = sorted(self.library.materials.values(), key=lambda m: (m.kind, m.name.lower()))
        self.table.setRowCount(len(materials))
        for r, m in enumerate(materials):
            glazing = f"{m.u_factor:g} / {m.shgc:g} / {m.vt:g}" if m.kind == "glazing" else ""
            values = [m.name, m.kind, f"{m.conductivity:g}" if m.kind == "opaque" else "",
                      f"{m.density:g}" if m.kind == "opaque" else "", f"{m.specific_heat:g}" if m.kind == "opaque" else "",
                      f"{m.r_value:g}" if m.kind in ("airgap", "nomass") else "", glazing, f"{m.cost:g}", m.source]
            for c, value in enumerate(values):
                self.table.setItem(r, c, QTableWidgetItem(value))

    def _selected(self):
        row = self.table.currentRow()
        return self.library.materials.get(self.table.item(row, 0).text()) if row >= 0 else None

    def add(self):
        dialog = MaterialDialog(parent=self)
        if dialog.exec() == QDialog.Accepted:
            self._store(dialog.spec())

    def edit(self):
        spec = self._selected()
        if spec is None:
            return
        dialog = MaterialDialog(spec, self)
        if spec.source == "model":
            dialog.name.setText(f"{spec.name} (copy)")
        if dialog.exec() == QDialog.Accepted:
            self._store(dialog.spec())

    def delete(self):
        spec = self._selected()
        if spec is None or spec.source == "model":
            return
        self.library.remove(spec.name)
        self.library.save()
        self.refresh()

    def _store(self, spec):
        try:
            self.library.add(spec)
            self.library.save()
        except ValueError as error:
            QMessageBox.warning(self, "Material", str(error))
        self.refresh()


# =========================================================
# Constructions tab
# =========================================================

class ConstructionsTab(QWidget):

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.library = controller.bundles
        self.materials = controller.materials
        self.inventory = None
        self.project_bundles = {}           # key -> Bundle embedded in the loaded project
        self.current_key = CATEGORY_ORDER[0]
        self.editing: Bundle | None = None
        self.editing_original_name = None
        self._loading = False
        controller.register(self)
        self.build_ui()
        self.pull(controller.project)

    # =====================================================
    # UI
    # =====================================================

    def build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(hint(
            "1) Select a category.  2) Create or edit a bundle and save it (Library/Bundles).  "
            "3) Select the bundle in the 'Assigned bundle' column."))
        splitter = QSplitter(Qt.Horizontal)

        # ---- left: assignments
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.assign_table = QTableWidget(len(CATEGORY_ORDER), 4)
        self.assign_table.setHorizontalHeaderLabels(["Category", "Objects", "Assigned bundle", "U (W/m²K)"])
        self.assign_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.assign_table.verticalHeader().setVisible(False)
        self.assign_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.assign_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.assign_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.combos = {}
        for row, key in enumerate(CATEGORY_ORDER):
            self.assign_table.setItem(row, 0, QTableWidgetItem(CATEGORIES[key].label))
            self.assign_table.setItem(row, 1, QTableWidgetItem("–"))
            combo = QComboBox()
            combo.currentIndexChanged.connect(lambda _i, k=key: self._assignment_changed(k))
            self.assign_table.setCellWidget(row, 2, combo)
            self.assign_table.setItem(row, 3, QTableWidgetItem(""))
            self.combos[key] = combo
        self.assign_table.resizeColumnToContents(0)
        left_layout.addWidget(self.assign_table)
        self.base_label = QLabel("")
        self.base_label.setWordWrap(True)
        left_layout.addWidget(self.base_label)
        splitter.addWidget(left)

        # ---- right: editor
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        lib_box = QGroupBox("Bundles for this category")
        lib_layout = QHBoxLayout(lib_box)
        self.bundle_list = QListWidget()
        self.bundle_list.setMaximumHeight(130)
        lib_layout.addWidget(self.bundle_list, 1)
        lib_buttons = QVBoxLayout()
        self.new_button = QPushButton("New")
        self.duplicate_button = QPushButton("Duplicate")
        self.delete_button = QPushButton("Delete")
        self.import_button = QPushButton("Import...")
        self.export_button = QPushButton("Export...")
        for button in (self.new_button, self.duplicate_button, self.delete_button, self.import_button, self.export_button):
            lib_buttons.addWidget(button)
        lib_layout.addLayout(lib_buttons)
        right_layout.addWidget(lib_box)

        edit_box = QGroupBox("Bundle editor")
        edit_layout = QVBoxLayout(edit_box)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.description_edit = QLineEdit()
        form.addRow("Bundle name", self.name_edit)
        form.addRow("Description", self.description_edit)
        edit_layout.addLayout(form)
        self.order_label = QLabel("")
        self.order_label.setStyleSheet("color:#555;")
        edit_layout.addWidget(self.order_label)
        self.layer_table = QTableWidget(0, 6)
        self.layer_table.setHorizontalHeaderLabels(["#", "Material", "Kind", "Thickness (mm)", "k (W/m·K)", "R (m²K/W)"])
        self.layer_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.layer_table.verticalHeader().setVisible(False)
        self.layer_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.layer_table.setSelectionMode(QAbstractItemView.SingleSelection)
        edit_layout.addWidget(self.layer_table, 1)
        layer_buttons = QHBoxLayout()
        self.add_layer_button = QPushButton("Add layer")
        self.remove_layer_button = QPushButton("Remove")
        self.up_button = QPushButton("Move up")
        self.down_button = QPushButton("Move down")
        self.materials_button = QPushButton("Material library...")
        for button in (self.add_layer_button, self.remove_layer_button, self.up_button, self.down_button):
            layer_buttons.addWidget(button)
        layer_buttons.addStretch()
        layer_buttons.addWidget(self.materials_button)
        edit_layout.addLayout(layer_buttons)
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("font-weight: 600;")
        edit_layout.addWidget(self.summary_label)
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #b00020;")
        edit_layout.addWidget(self.error_label)
        save_row = QHBoxLayout()
        self.save_button = QPushButton("Save bundle")
        self.save_assign_button = QPushButton("Save && assign to category")
        self.save_assign_button.setDefault(True)
        save_row.addStretch()
        save_row.addWidget(self.save_button)
        save_row.addWidget(self.save_assign_button)
        edit_layout.addLayout(save_row)
        right_layout.addWidget(edit_box, 1)
        splitter.addWidget(right)
        splitter.setSizes([520, 760])
        layout.addWidget(splitter, 1)

        self.assign_table.currentCellChanged.connect(lambda row, *_: self.select_category(CATEGORY_ORDER[row]) if row >= 0 else None)
        self.bundle_list.currentItemChanged.connect(self._bundle_selected)
        self.new_button.clicked.connect(self.new_bundle)
        self.duplicate_button.clicked.connect(self.duplicate_bundle)
        self.delete_button.clicked.connect(self.delete_bundle)
        self.import_button.clicked.connect(self.import_bundle)
        self.export_button.clicked.connect(self.export_bundle)
        self.add_layer_button.clicked.connect(self.add_layer)
        self.remove_layer_button.clicked.connect(self.remove_layer)
        self.up_button.clicked.connect(lambda: self.move_layer(-1))
        self.down_button.clicked.connect(lambda: self.move_layer(1))
        self.materials_button.clicked.connect(self.open_materials)
        self.save_button.clicked.connect(lambda: self.save_bundle(assign=False))
        self.save_assign_button.clicked.connect(lambda: self.save_bundle(assign=True))
        self.name_edit.textEdited.connect(self._editor_changed)
        self.description_edit.textEdited.connect(self._editor_changed)
        self.assign_table.selectRow(0)

    # =====================================================
    # Available bundles (library + base model seed + project-embedded)
    # =====================================================

    def available(self, key) -> dict:
        bundles = {}
        if self.inventory and key in self.inventory.seed_bundles:
            seed = self.inventory.seed_bundles[key]
            bundles[seed.name] = seed
        for bundle in self.library.for_category(key):
            bundles[bundle.name] = bundle
        embedded = self.project_bundles.get(key)
        if embedded is not None and embedded.name not in bundles:
            bundles[embedded.name] = embedded
        return bundles

    def refresh_combo(self, key, select: str | None = None):
        combo = self.combos[key]
        current = select if select is not None else combo.currentText()
        self._loading = True
        combo.clear()
        combo.addItem(KEEP)
        for name in self.available(key):
            combo.addItem(name)
        index = combo.findText(current)
        combo.setCurrentIndex(index if index >= 0 else 0)
        self._loading = False
        self._update_u(key)

    def _update_u(self, key):
        row = CATEGORY_ORDER.index(key)
        bundle = self.available(key).get(self.combos[key].currentText())
        u = bundle.u_with_films() if bundle else None
        self.assign_table.item(row, 3).setText(f"{u:.3f}" if u else "")

    def _assignment_changed(self, key):
        if not self._loading:
            self._update_u(key)

    # =====================================================
    # Category & bundle selection
    # =====================================================

    def select_category(self, key):
        self.current_key = key
        cat = CATEGORIES[key]
        self.order_label.setText(f"Layer order: {cat.layer_hint}   (first row = first layer)")
        if self.inventory:
            info = self.inventory.categories[key]
            used = ", ".join(f"{n} x{c}" for n, c in sorted(info.constructions.items(), key=lambda kv: -kv[1]))
            self.base_label.setText(f"<b>{cat.label}</b> in base model: {info.count} objects, "
                                    f"{info.area_m2:,.0f} m²<br>Constructions now: {used or 'none'}")
        self.refresh_bundle_list()

    def refresh_bundle_list(self, select=None):
        self.bundle_list.blockSignals(True)
        self.bundle_list.clear()
        for name, bundle in self.available(self.current_key).items():
            item = QListWidgetItem(bundle.summary() + ("   [base model, read-only]" if bundle.source == "model" else ""))
            item.setData(Qt.UserRole, name)
            self.bundle_list.addItem(item)
        self.bundle_list.blockSignals(False)
        target = select or self.combos[self.current_key].currentText()
        for row in range(self.bundle_list.count()):
            if self.bundle_list.item(row).data(Qt.UserRole) == target:
                self.bundle_list.setCurrentRow(row)
                return
        if self.bundle_list.count():
            self.bundle_list.setCurrentRow(0)
        else:
            self.load_editor(None)

    def _bundle_selected(self, item, _previous=None):
        if item is None:
            return
        bundle = self.available(self.current_key).get(item.data(Qt.UserRole))
        self.load_editor(bundle)

    # =====================================================
    # Editor
    # =====================================================

    def load_editor(self, bundle: Bundle | None):
        self.editing = bundle.copy() if bundle else None
        if bundle is not None:
            self.editing.source = bundle.source
        self.editing_original_name = bundle.name if bundle and bundle.source != "model" else None
        self.name_edit.setText(bundle.name if bundle else "")
        self.description_edit.setText(bundle.description if bundle else "")
        self.layer_table.setRowCount(0)
        if bundle:
            for layer in self.editing.layers:
                self._append_layer_row(layer)
        read_only = bundle is not None and bundle.source == "model"
        for widget in (self.save_button, self.delete_button):
            widget.setEnabled(bundle is not None and not read_only)
        self.save_assign_button.setText("Save as new && assign" if read_only else "Save && assign to category")
        self._editor_changed()

    def _materials_for_category(self):
        kinds = KIND_FOR[CATEGORIES[self.current_key].allowed]
        return self.materials.by_kind(kinds)

    def _append_layer_row(self, layer: Layer):
        row = self.layer_table.rowCount()
        self.layer_table.insertRow(row)
        self.layer_table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
        combo = QComboBox()
        names = [m.name for m in self._materials_for_category()]
        if layer.material.name not in names:
            names.insert(0, layer.material.name)       # material embedded in the bundle
        combo.addItems(names)
        combo.setCurrentText(layer.material.name)
        combo.currentTextChanged.connect(lambda _t, c=combo: self._material_changed(c))
        self.layer_table.setCellWidget(row, 1, combo)
        thickness = QDoubleSpinBox()
        thickness.setRange(0.0, 3000)
        thickness.setDecimals(1)
        thickness.setSingleStep(10)
        thickness.setSpecialValueText("-")  # air gap, no-mass and glazing layers
        thickness.setValue(layer.thickness_mm if layer.material.kind == "opaque" else 0.0)
        thickness.setEnabled(layer.material.kind == "opaque")
        thickness.valueChanged.connect(self._editor_changed)
        self.layer_table.setCellWidget(row, 3, thickness)
        for col in (2, 4, 5):
            self.layer_table.setItem(row, col, QTableWidgetItem(""))

    def _row_of(self, widget):
        for row in range(self.layer_table.rowCount()):
            if self.layer_table.cellWidget(row, 1) is widget:
                return row
        return -1

    def _material_changed(self, combo):
        row = self._row_of(combo)
        if row < 0 or self.editing is None:
            return
        spec = self.materials.materials.get(combo.currentText()) or self.editing.layers[row].material
        layer = self.editing.layers[row]
        layer.material = spec
        thickness = self.layer_table.cellWidget(row, 3)
        thickness.setEnabled(spec.kind == "opaque")
        thickness.blockSignals(True)
        thickness.setValue((spec.default_thickness_mm or 100.0) if spec.kind == "opaque" else 0.0)
        thickness.blockSignals(False)
        self._editor_changed()

    def _read_editor(self) -> Bundle | None:
        if self.editing is None:
            return None
        for row, layer in enumerate(self.editing.layers):
            layer.thickness_mm = self.layer_table.cellWidget(row, 3).value() if layer.material.kind == "opaque" else 0.0
        self.editing.name = self.name_edit.text().strip()
        self.editing.description = self.description_edit.text().strip()
        self.editing.category = self.current_key
        return self.editing

    def _editor_changed(self, *_):
        bundle = self._read_editor()
        if bundle is None:
            self.summary_label.setText("Select a bundle or click New.")
            self.error_label.setText("")
            return
        for row, layer in enumerate(bundle.layers):
            m = layer.material
            self.layer_table.item(row, 0).setText(str(row + 1))
            self.layer_table.item(row, 2).setText(m.kind)
            self.layer_table.item(row, 4).setText(f"{m.conductivity:g}" if m.kind == "opaque" else "")
            self.layer_table.item(row, 5).setText(f"{layer.resistance:.3f}" if m.kind != "glazing" else f"U {m.u_factor:g}")
        u_layers, u_films = bundle.u_layers(), bundle.u_with_films()
        if bundle.is_glazing:
            m = bundle.layers[0].material
            text = f"U = {m.u_factor:.3f} W/m²K   SHGC {m.shgc:g}   VT {m.vt:g}"
        else:
            text = (f"R layers = {bundle.r_layers():.3f} m²K/W   |   U (surface-to-surface) = "
                    f"{u_layers:.3f}   |   U with films = {u_films:.3f} W/m²K   |   "
                    f"thickness = {bundle.total_thickness_mm():.0f} mm" if u_layers else "No thermal resistance yet.")
            if self.current_key in ("interior_wall", "interior_slab"):
                text += "   |   " + ("symmetric" if bundle.is_symmetric() else "asymmetric, reversed copy used on the other side")
        cost, carbon = bundle.cost_per_m2(), bundle.carbon_per_m2()
        if cost or carbon:
            text += f"   |   {cost:.1f} $/m², {carbon:.1f} kgCO₂e/m²"
        self.summary_label.setText(text)
        errors = bundle.validate()
        self.error_label.setText("\n".join(errors))

    # ---- layer buttons
    def add_layer(self):
        if self.editing is None:
            self.new_bundle()
            return
        materials = self._materials_for_category()
        if not materials:
            QMessageBox.information(self, "No material", "Add a material in the material library first.")
            return
        self._read_editor()
        preferred = next((m for m in materials if m.name == "EPS"), materials[0])
        layer = Layer(preferred, preferred.default_thickness_mm if preferred.kind == "opaque" else 0.0)
        row = self.layer_table.currentRow()
        insert_at = row + 1 if row >= 0 else len(self.editing.layers)
        self.editing.layers.insert(insert_at, layer)
        self._rebuild_rows(select=insert_at)

    def remove_layer(self):
        row = self.layer_table.currentRow()
        if self.editing is None or row < 0:
            return
        self._read_editor()
        del self.editing.layers[row]
        self._rebuild_rows(select=min(row, len(self.editing.layers) - 1))

    def move_layer(self, step):
        row = self.layer_table.currentRow()
        if self.editing is None or row < 0:
            return
        target = row + step
        if not 0 <= target < len(self.editing.layers):
            return
        self._read_editor()
        layers = self.editing.layers
        layers[row], layers[target] = layers[target], layers[row]
        self._rebuild_rows(select=target)

    def _rebuild_rows(self, select=-1):
        self.layer_table.setRowCount(0)
        for layer in self.editing.layers:
            self._append_layer_row(layer)
        if select >= 0:
            self.layer_table.selectRow(select)
        self._editor_changed()

    def open_materials(self):
        if self.editing:
            self._read_editor()
        MaterialLibraryDialog(self.materials, self).exec()
        if self.editing:
            self._rebuild_rows(self.layer_table.currentRow())

    # ---- bundle buttons
    def new_bundle(self):
        cat = CATEGORIES[self.current_key]
        name = self.library.unique_name(f"New {cat.label.split(' (')[0].rstrip('s').lower()}")
        if cat.allowed == "glazing":
            glazing = self.materials.by_kind(("glazing",))
            layers = [Layer(glazing[0])] if glazing else []
        else:
            layers = []
        bundle = Bundle(name, self.current_key, layers)
        self.load_editor(bundle)
        self.editing_original_name = None

    def duplicate_bundle(self):
        if self.editing is None:
            return
        self._read_editor()
        base = self.editing.name.replace(" (base model)", "")
        copy = self.editing.copy(self.library.unique_name(f"{base} copy"))
        if copy.description.startswith("Base model construction"):
            copy.description = f"Based on {base} (base model)."
        self.load_editor(copy)
        self.editing_original_name = None

    def delete_bundle(self):
        name = self.editing_original_name
        if not name:
            return
        if QMessageBox.question(self, "Delete bundle", f"Delete '{name}' from the library?") != QMessageBox.Yes:
            return
        self.library.delete(name)
        self.refresh_combo(self.current_key)
        self.refresh_bundle_list()

    def save_bundle(self, assign=False):
        bundle = self._read_editor()
        if bundle is None:
            return
        if bundle.source == "model":
            name, ok = QInputDialog.getText(self, "Save as new bundle", "Name for the new bundle:",
                                            text=self.library.unique_name(bundle.name.replace(" (base model)", "")))
            if not ok or not name.strip():
                return
            bundle.name, bundle.source = name.strip(), "user"
            self.name_edit.setText(bundle.name)
        if bundle.name != self.editing_original_name and bundle.name in self.library.bundles:
            if QMessageBox.question(self, "Overwrite", f"A bundle named '{bundle.name}' exists. Overwrite it?") \
                    != QMessageBox.Yes:
                return
        try:
            path = self.library.save(bundle.copy(), old_name=self.editing_original_name)
        except ValueError as error:
            QMessageBox.warning(self, "Cannot save bundle", str(error))
            return
        # keep any assignment pointing at the renamed bundle
        old = self.editing_original_name
        combo = self.combos[self.current_key]
        keep_following = combo.currentText() in (old, bundle.name)
        self.refresh_combo(self.current_key, select=bundle.name if (assign or keep_following) else combo.currentText())
        self.editing_original_name = bundle.name
        self.refresh_bundle_list(select=bundle.name)
        self.controller.status.emit(f"Bundle saved: {path}")

    def import_bundle(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import bundle", str(self.library.folder), "Bundle (*.json)")
        if not path:
            return
        try:
            bundle = self.library.import_file(Path(path))
        except (ValueError, KeyError, json.JSONDecodeError) as error:
            QMessageBox.warning(self, "Import failed", str(error))
            return
        self.refresh_combo(bundle.category)
        if bundle.category != self.current_key:
            self.assign_table.selectRow(CATEGORY_ORDER.index(bundle.category))
        self.refresh_bundle_list(select=bundle.name)

    def export_bundle(self):
        bundle = self._read_editor()
        if bundle is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export bundle", f"{bundle.name}.json", "Bundle (*.json)")
        if path:
            Path(path).write_text(json.dumps(bundle.to_dict(), indent=2), encoding="utf-8")

    # =====================================================
    # Project sync
    # =====================================================

    def pull(self, project):
        self.project_bundles = {k: b for k, b in project.constructions.items() if b is not None}
        for key in CATEGORY_ORDER:
            bundle = project.constructions.get(key)
            self.refresh_combo(key, select=bundle.name if bundle else KEEP)
        self.select_category(self.current_key)

    def push(self, project):
        for key, combo in self.combos.items():
            name = combo.currentText()
            bundle = self.available(key).get(name) if name != KEEP else None
            project.constructions[key] = bundle.copy() if bundle else None
            if bundle is not None:
                project.constructions[key].name = bundle.name

    def on_inventory(self, inv):
        self.inventory = inv
        for row, key in enumerate(CATEGORY_ORDER):
            info = inv.categories[key]
            self.assign_table.item(row, 1).setText(str(info.count))
            for col in range(2):
                self.assign_table.item(row, col).setForeground(Qt.gray if info.count == 0 else Qt.black)
            self.refresh_combo(key)
        self.select_category(self.current_key)
