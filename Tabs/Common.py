"""
=========================================================
Common GUI helpers
=========================================================

Background worker, path pickers, status colours and the generic
measure-argument form (built from measure.xml).

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QWidget,
)

STATUS_COLORS = {"PASS": QColor("#d8f3dc"), "WARN": QColor("#fff3bf"), "FAIL": QColor("#ffd6d6")}


# =========================================================
# Worker thread
# =========================================================

class Worker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    log = Signal(str)

    def __init__(self, function, *args, **kwargs):
        super().__init__()
        self.function, self.args, self.kwargs = function, args, kwargs

    def run(self):
        try:
            result = self.function(*self.args, log=self.log.emit, **self.kwargs) \
                if self.kwargs.pop("_with_log", False) else self.function(*self.args, **self.kwargs)
            self.finished.emit(result)
        except Exception as error:      # show the error in the GUI
            self.failed.emit(f"{error}\n\n{traceback.format_exc()}")


def run_in_thread(owner, function, *args, on_done=None, on_error=None, on_log=None, with_log=False, **kwargs):
    """Run function(*args) in a QThread."""
    thread = QThread(owner)
    worker = Worker(function, *args, _with_log=with_log, **kwargs)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    if on_done:
        worker.finished.connect(on_done)
    if on_error:
        worker.failed.connect(on_error)
    if on_log:
        worker.log.connect(on_log)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.finished.connect(lambda: owner._threads.remove((thread, worker)) if (thread, worker) in owner._threads else None)
    if not hasattr(owner, "_threads"):
        owner._threads = []
    owner._threads.append((thread, worker))
    thread.start()
    return thread


# =========================================================
# Widgets
# =========================================================

class PathPicker(QWidget):
    """Line edit + Browse button. mode: 'file', 'dir' or 'save'."""

    def __init__(self, mode="file", file_filter="All files (*)", parent=None):
        super().__init__(parent)
        self.mode, self.file_filter = mode, file_filter
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        self.button = QPushButton("Browse...")
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.button)
        self.button.clicked.connect(self.browse)

    def browse(self):
        start = self.edit.text()
        if self.mode == "dir":
            path = QFileDialog.getExistingDirectory(self, "Select folder", start)
        elif self.mode == "save":
            path, _ = QFileDialog.getSaveFileName(self, "Save as", start, self.file_filter)
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Select file", start, self.file_filter)
        if path:
            self.edit.setText(path)

    def text(self):
        return self.edit.text().strip()

    def setText(self, value):
        self.edit.setText(value or "")


def spin(minimum, maximum, value=0.0, decimals=3, step=None, suffix=""):
    box = QDoubleSpinBox()
    box.setRange(minimum, maximum)
    box.setDecimals(decimals)
    box.setValue(value)
    if step:
        box.setSingleStep(step)
    if suffix:
        box.setSuffix(suffix)
    return box


def hint(text):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: #555;")
    return label


# =========================================================
# Generic measure argument form
# =========================================================

class MeasureArgsForm(QWidget):
    """Builds input widgets from a MeasureInfo; values() returns {name: value} for set arguments."""

    def __init__(self, info, values: dict, parent=None):
        super().__init__(parent)
        self.info = info
        self.widgets = {}
        form = QFormLayout(self)
        for arg in info.arguments:
            current = values.get(arg.name, arg.default)
            widget = self._widget(arg, current)
            label = arg.display_name + (f" [{arg.units}]" if arg.units else "")
            if arg.model_dependent:
                widget.setEnabled(False)
                widget.setToolTip("This argument lists objects of a specific model and cannot be set here.")
            form.addRow(QLabel(label), widget)
            if arg.description:
                widget.setToolTip(arg.description[:600])
            self.widgets[arg.name] = (arg, widget)

    @staticmethod
    def _widget(arg, current):
        if arg.type == "Boolean":
            box = QCheckBox()
            box.setChecked(str(current).strip().lower() in ("true", "1", "yes"))
            return box
        if arg.type == "Choice":
            combo = QComboBox()
            for value, label in zip(arg.choices, arg.choice_labels):
                combo.addItem(label if label == value else f"{label}", value)
            index = combo.findData(str(current)) if current is not None else -1
            combo.setCurrentIndex(index if index >= 0 else 0)
            return combo
        if arg.type == "Integer":
            box = QSpinBox()
            box.setRange(-10 ** 9, 10 ** 9)
            box.setValue(int(float(current)) if current not in (None, "") else 0)
            return box
        if arg.type == "Double":
            box = QDoubleSpinBox()
            box.setRange(-1e9, 1e9)
            box.setDecimals(4)
            box.setValue(float(current) if current not in (None, "") else 0.0)
            return box
        edit = QLineEdit("" if current is None else str(current))
        return edit

    def values(self) -> dict:
        result = {}
        for name, (arg, widget) in self.widgets.items():
            if arg.model_dependent:
                continue
            if isinstance(widget, QCheckBox):
                value = widget.isChecked()
            elif isinstance(widget, QComboBox):
                value = widget.currentData()
            elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                value = widget.value()
            else:
                value = widget.text()
            result[name] = value            # keep every value so the OSW lists all arguments
        return result
