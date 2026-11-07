"""
=========================================================
Main Window
=========================================================

Model Factory: OpenStudio model setup.

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

from __future__ import annotations

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QFileDialog, QMainWindow, QMessageBox, QTabWidget

from Core.Settings import PROJECT_DIR
from Tabs.ConstructionsTab import ConstructionsTab
from Tabs.MeasuresTab import MeasuresTab
from Tabs.OutputsTab import OutputsTab
from Tabs.ProjectTab import ProjectTab
from Tabs.RunTab import RunTab
from Tabs.SchedulesTab import SchedulesTab
from Tabs.WeatherTab import WeatherTab


class MainWindow(QMainWindow):

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        controller.window = self
        self.setWindowTitle("Model Factory")
        self.resize(1400, 900)
        self.build_ui()
        self.build_menu()
        controller.status.connect(lambda text: self.statusBar().showMessage(text, 15000))
        controller.inventory_failed.connect(self._inventory_failed)
        controller.project_loaded.connect(self._update_title)
        self._update_title()

    def build_ui(self):
        self.tabs = QTabWidget()
        self.project_tab = ProjectTab(self.controller)
        self.weather_tab = WeatherTab(self.controller)
        self.constructions_tab = ConstructionsTab(self.controller)
        self.schedules_tab = SchedulesTab(self.controller)
        self.outputs_tab = OutputsTab(self.controller)
        self.measures_tab = MeasuresTab(self.controller)
        self.run_tab = RunTab(self.controller)
        for widget, title in [(self.project_tab, "Project"), (self.weather_tab, "Weather"),
                              (self.constructions_tab, "Constructions"), (self.schedules_tab, "Schedules && HVAC"),
                              (self.outputs_tab, "Outputs"), (self.measures_tab, "Measures"),
                              (self.run_tab, "Build && Run")]:
            self.tabs.addTab(widget, title)
        self.setCentralWidget(self.tabs)

    def build_menu(self):
        menu = self.menuBar().addMenu("&File")
        for text, shortcut, slot in [("New project", QKeySequence.New, self.controller.new_project),
                                     ("Open project...", QKeySequence.Open, self.open_project),
                                     ("Save project", QKeySequence.Save, self.save_project),
                                     ("Save project as...", QKeySequence.SaveAs, self.save_project_as)]:
            action = QAction(text, self)
            action.setShortcut(shortcut)
            action.triggered.connect(slot)
            menu.addAction(action)
        menu.addSeparator()
        quit_action = QAction("Exit", self)
        quit_action.triggered.connect(self.close)
        menu.addAction(quit_action)

    # -------------------------------------------------
    def open_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open project", str(PROJECT_DIR), "Project (*.json)")
        if path:
            try:
                self.controller.open_project(path)
            except (OSError, ValueError, KeyError) as error:
                QMessageBox.critical(self, "Open project", str(error))

    def save_project(self):
        if self.controller.project_path is None:
            self.save_project_as()
        else:
            self.controller.save_project()

    def save_project_as(self):
        self.controller.collect()
        default = PROJECT_DIR / f"{self.controller.project.name}.json"
        path, _ = QFileDialog.getSaveFileName(self, "Save project", str(default), "Project (*.json)")
        if path:
            self.controller.save_project(path)
            self._update_title()

    def _update_title(self):
        name = self.controller.project_path.name if self.controller.project_path else "unsaved project"
        self.setWindowTitle(f"Model Factory  [{name}]")

    def _inventory_failed(self, message):
        self.statusBar().showMessage(message.split("\n")[0], 20000)
        if "not found" not in message:
            QMessageBox.warning(self, "Base model", message.split("\n\n")[0])

    def closeEvent(self, event):
        self.controller.collect()
        self.controller.settings.save()
        super().closeEvent(event)
