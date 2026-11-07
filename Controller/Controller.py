"""
=========================================================
Controller
=========================================================

Owns the project, the libraries and the base-model inventory, and
keeps the tabs in sync:

    tab.pull(project)      project  -> widgets
    tab.push(project)      widgets  -> project
    tab.on_inventory(inv)  base model loaded / changed

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from Core.Inventory import build_inventory
from Core.Library import BundleLibrary, MaterialLibrary
from Core.Measures import scan_measures
from Core.Outputs import default_outputs
from Core.Project import Project
from Core.Settings import BUNDLE_DIR, MATERIAL_FILE, Settings, ensure_folders
from Core.Weather import list_epw
from Tabs.Common import run_in_thread


class Controller(QObject):
    inventory_loaded = Signal(object)
    inventory_failed = Signal(str)
    status = Signal(str)
    project_loaded = Signal()

    def __init__(self):
        super().__init__()
        ensure_folders()
        self.settings = Settings.load()
        self.materials = MaterialLibrary(MATERIAL_FILE)
        self.bundles = BundleLibrary(BUNDLE_DIR)
        self.inventory = None
        self.measures = {}
        self.measure_errors = []
        self.tabs = []
        self.project_path: Path | None = None
        self.project = self.new_project_object()
        self.scan_measures()
        self.window = None

    # =====================================================
    # Project
    # =====================================================

    def new_project_object(self) -> Project:
        project = Project(base_model=self.settings.base_model, measures_dir=self.settings.measures_dir)
        project.outputs.variables, project.outputs.meters = default_outputs()
        epws = list_epw(self.settings.weather_dir)
        if epws:
            project.weather.epw = str(epws[0])
        return project

    def register(self, tab):
        self.tabs.append(tab)

    def collect(self):
        """Widgets -> project (call before build / save)."""
        for tab in self.tabs:
            tab.push(self.project)

    def refresh(self):
        for tab in self.tabs:
            tab.pull(self.project)

    def new_project(self):
        self.project = self.new_project_object()
        self.project_path = None
        self.refresh()
        self.project_loaded.emit()
        self.load_inventory()

    def open_project(self, path):
        old_base = self.project.base_model
        self.project = Project.load(path)
        self.project_path = Path(path)
        self.settings.last_project = str(path)
        self.settings.save()
        self.scan_measures()
        self.refresh()
        self.project_loaded.emit()
        if self.project.base_model != old_base or self.inventory is None:
            self.load_inventory()
        self.status.emit(f"Project loaded: {path}")

    def save_project(self, path=None) -> Path:
        self.collect()
        path = Path(path or self.project_path)
        self.project.save(path)
        self.project_path = path
        self.settings.last_project = str(path)
        self.settings.save()
        self.status.emit(f"Project saved: {path}")
        return path

    # =====================================================
    # Base model & measures
    # =====================================================

    def load_inventory(self):
        path = self.project.base_model
        if not path or not Path(path).is_file():
            self.inventory = None
            self.inventory_failed.emit(f"Base model not found: {path or '(not set)'}")
            return
        self.status.emit(f"Reading base model {Path(path).name}...")
        run_in_thread(self, build_inventory, path, on_done=self._inventory_done,
                      on_error=lambda message: self.inventory_failed.emit(message))

    def _inventory_done(self, inventory):
        self.inventory = inventory
        self.materials.merge_model_materials(inventory.materials)
        self.status.emit(f"Base model loaded: {len(inventory.zones)} zones, "
                         f"{sum(c.count for c in inventory.categories.values())} envelope objects.")
        for tab in self.tabs:
            tab.on_inventory(inventory)
        self.inventory_loaded.emit(inventory)

    def scan_measures(self):
        self.measures, self.measure_errors = scan_measures(self.project.measures_dir)
        return self.measures
