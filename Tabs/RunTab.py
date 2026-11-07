"""
=========================================================
Build & Run Tab
=========================================================

Build: model.osm, workflow.osw and verification table
Run: OpenStudio CLI (measures + EnergyPlus)
Results: constructions used by EnergyPlus and annual end uses

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QProcess, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPlainTextEdit, QPushButton,
    QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)
from PySide6.QtCore import Qt

from Core.Builder import build
from Core.Results import analyse, run_command
from Tabs.Common import STATUS_COLORS, hint, run_in_thread


class RunTab(QWidget):

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.result = None
        self.process: QProcess | None = None
        self._run_after_build = False
        controller.register(self)
        self.build_ui()

    def build_ui(self):
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.folder_label = QLabel("")
        self.folder_label.setWordWrap(True)
        top.addWidget(self.folder_label, 1)
        self.deep_check = QCheckBox("Deep check (verify in the EnergyPlus IDF)")
        self.deep_check.setChecked(True)
        top.addWidget(self.deep_check)
        layout.addLayout(top)
        buttons = QHBoxLayout()
        self.build_button = QPushButton("1. Build model")
        self.run_button = QPushButton("2. Run simulation")
        self.both_button = QPushButton("Build && run")
        self.open_button = QPushButton("Open output folder")
        self.report_button = QPushButton("Open HTML report")
        for b in (self.build_button, self.run_button, self.both_button):
            b.setMinimumHeight(34)
        self.run_button.setEnabled(False)
        buttons.addWidget(self.build_button)
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.both_button)
        buttons.addStretch()
        buttons.addWidget(self.report_button)
        buttons.addWidget(self.open_button)
        layout.addLayout(buttons)
        self.status = QLabel("Set up the tabs, then build the model.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        splitter = QSplitter(Qt.Vertical)
        self.tabs = QTabWidget()
        self.checks = self._table(["Check", "Status", "Detail"], stretch=2)
        self.results = self._table(["Item", "Value", "Unit"], stretch=0)
        self.used = self._table(["Category", "Construction used by EnergyPlus", "Surfaces", "Area (m²)",
                                 "U avg (W/m²K, no films)", "U range", "From your bundle"], stretch=1)
        self.tabs.addTab(self.checks, "Verification")
        self.tabs.addTab(self.results, "Annual results")
        self.tabs.addTab(self.used, "Constructions in EnergyPlus")
        splitter.addWidget(self.tabs)
        log_box = QGroupBox("Log")
        log_layout = QVBoxLayout(log_box)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(20000)
        log_layout.addWidget(self.log)
        splitter.addWidget(log_box)
        splitter.setSizes([480, 220])
        layout.addWidget(splitter, 1)
        layout.addWidget(hint("Output files: model.osm, workflow.osw, manifest.json, verification.txt, "
                              "run/eplusout.sql, reports/, results.json, summary.csv."))

        self.build_button.clicked.connect(lambda: self.start_build(run_after=False))
        self.both_button.clicked.connect(lambda: self.start_build(run_after=True))
        self.run_button.clicked.connect(self.start_run)
        self.open_button.clicked.connect(lambda: self._open(self.output_folder()))
        self.report_button.clicked.connect(self.open_report)

    @staticmethod
    def _table(headers, stretch):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(stretch, QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        return table

    # =====================================================
    def output_folder(self) -> Path:
        name = "".join(c if c.isalnum() or c in "-_ ." else "_" for c in self.controller.project.name).strip() or "Simulation"
        return Path(self.controller.settings.runs_dir) / name

    def _busy(self, busy):
        for b in (self.build_button, self.both_button):
            b.setEnabled(not busy)
        self.run_button.setEnabled(not busy and self.result is not None)

    def append(self, text):
        self.log.appendPlainText(text)

    def start_build(self, run_after=False):
        self.controller.collect()
        project = self.controller.project
        project.deep_check = self.deep_check.isChecked()
        if not Path(project.base_model).is_file():
            QMessageBox.warning(self, "Base model", f"Base model not found:\n{project.base_model}")
            return
        folder = self.output_folder()
        self.folder_label.setText(f"Output folder: {folder}")
        self._run_after_build = run_after
        self.result = None
        self.checks.setRowCount(0)
        self.log.clear()
        self.status.setText("Building...")
        self._busy(True)
        run_in_thread(self, build, project, folder, with_log=True,
                      on_done=self.build_done, on_error=self.build_failed, on_log=self.append)

    def build_done(self, result):
        self.result = result
        self.checks.setRowCount(len(result.checks))
        for row, (name, status, detail) in enumerate(result.checks):
            for col, value in enumerate((name, status, detail)):
                item = QTableWidgetItem(value)
                item.setBackground(STATUS_COLORS.get(status))
                item.setToolTip(value)
                self.checks.setItem(row, col, item)
        self.checks.resizeColumnToContents(0)
        failed, warned = len(result.failed), sum(1 for c in result.checks if c[1] == "WARN")
        self.status.setText(f"Build finished in {result.seconds:.0f} s - {len(result.checks)} checks: "
                            f"{failed} failed, {warned} warning(s). Model: {result.osm}")
        self.tabs.setCurrentIndex(0)
        self._busy(False)
        if self._run_after_build:
            if failed:
                QMessageBox.warning(self, "Not running", f"{failed} verification check(s) failed - fix them before simulating.")
            else:
                self.start_run()

    def build_failed(self, message):
        self._busy(False)
        self.status.setText("Build failed - see the log.")
        self.append(message)
        QMessageBox.critical(self, "Build failed", message.split("\n\n")[0])

    # =====================================================
    def start_run(self):
        if self.result is None:
            return
        if self.result.failed and QMessageBox.question(
                self, "Failed checks", f"{len(self.result.failed)} check(s) failed. Simulate anyway?") != QMessageBox.Yes:
            return
        exe = self.controller.settings.resolved_openstudio()
        if exe is None:
            QMessageBox.warning(self, "OpenStudio", "OpenStudio executable not found - set it in the Project tab.")
            return
        command = run_command(exe, self.result.osw)
        self.append("\n$ " + " ".join(f'"{c}"' if " " in c else c for c in command))
        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(self.result.folder))
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_process)
        self.process.finished.connect(self.run_finished)
        self.status.setText("Simulating (measures + EnergyPlus)...")
        self._busy(True)
        self.run_button.setEnabled(False)
        self.process.start(command[0], command[1:])

    def _read_process(self):
        text = bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        for line in text.splitlines():
            if "warning: already initialized constant" in line or "warning: previous definition" in line:
                continue          # TBD ruby constant warnings
            self.append(line)

    def run_finished(self, exit_code, _status):
        self._busy(False)
        if exit_code != 0:
            self.status.setText(f"OpenStudio finished with exit code {exit_code} - see the log and run/eplusout.err.")
            return
        self.status.setText("Simulation finished - reading results...")
        run_in_thread(self, analyse, self.result.folder, on_done=self.show_results,
                      on_error=lambda m: (self.append(m), self.status.setText("Could not read results.")))

    def show_results(self, res):
        summary = res["summary"]
        rows = [("Heating", summary["HeatingDemand"], "kWh"), ("Cooling", summary["CoolingDemand"], "kWh"),
                ("Interior equipment", summary["InteriorEquipment"], "kWh"),
                ("Interior lighting", summary["InteriorLighting"], "kWh"), ("Fans", summary["Fans"], "kWh"),
                ("Total site energy", summary["TotalEnergy"], "kWh"), ("EUI", summary["EUI"], "kWh/m²"),
                ("Floor area", summary["FloorArea_m2"], "m²"), ("Severe/fatal EnergyPlus errors", res["severe_or_fatal"], "")]
        rows += [(f"End use: {k}", v, "kWh") for k, v in sorted(res["end_uses_kWh"].items()) if v]
        self.results.setRowCount(len(rows))
        for r, (name, value, unit) in enumerate(rows):
            text = f"{value:,.1f}" if isinstance(value, float) else str(value)
            for c, v in enumerate((name, text, unit)):
                self.results.setItem(r, c, QTableWidgetItem(v))
        self.used.setRowCount(len(res["constructions"]))
        for r, row in enumerate(res["constructions"]):
            values = (row["category"], row["construction"], str(row["surfaces"]), f"{row['area_m2']:,.0f}",
                      f"{row['u_value']:.3f}" if row["u_value"] else "", row.get("u_range", ""),
                      "yes" if row["from_bundle"] else "NO")
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                if c == 6:
                    item.setBackground(STATUS_COLORS["PASS" if row["from_bundle"] else "FAIL"])
                self.used.setItem(r, c, item)
        for table in (self.results, self.used):
            table.resizeColumnsToContents()
        self.tabs.setCurrentIndex(1)
        self.status.setText(f"Done. Heating {summary['HeatingDemand']:,.0f} kWh, cooling {summary['CoolingDemand']:,.0f} kWh, "
                            f"EUI {summary['EUI'] or 0:.1f} kWh/m². Results saved to results.json / summary.csv.")

    # =====================================================
    def open_report(self):
        reports = sorted(self.output_folder().glob("reports/*.html"))
        if reports:
            self._open(reports[0])
        else:
            QMessageBox.information(self, "Report", "No HTML report yet - run the simulation with openstudio_results enabled.")

    @staticmethod
    def _open(path):
        path = Path(path)
        if not path.exists():
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def pull(self, project):
        self.deep_check.setChecked(project.deep_check)
        self.folder_label.setText(f"Output folder: {self.output_folder()}")

    def push(self, project):
        project.deep_check = self.deep_check.isChecked()

    def on_inventory(self, inv):
        self.folder_label.setText(f"Output folder: {self.output_folder()}")
