"""
=========================================================
Run Model Factory
=========================================================

    python Run.py

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QApplication

from Controller.Controller import Controller
from GUI.MainWindow import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Model Factory")
    controller = Controller()
    window = MainWindow(controller)
    window.show()
    controller.load_inventory()          # read the base model in the background
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
