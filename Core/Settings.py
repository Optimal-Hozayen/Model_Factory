"""
=========================================================
Settings
=========================================================

Application paths, saved in settings.json next to Run.py.

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_FILE = APP_ROOT / "settings.json"

LIBRARY_DIR = APP_ROOT / "Library"
BUNDLE_DIR = LIBRARY_DIR / "Bundles"
MATERIAL_FILE = LIBRARY_DIR / "materials.json"
PROJECT_DIR = APP_ROOT / "Projects"
RUNS_DIR = APP_ROOT / "Runs"


@dataclass
class Settings:
    """Paths set in the Project tab (saved in settings.json)."""
    base_model: str = ""
    measures_dir: str = ""
    weather_dir: str = ""
    schedules_dir: str = ""
    openstudio_exe: str = ""
    runs_dir: str = str(RUNS_DIR)
    last_project: str = ""
    extra: dict = field(default_factory=dict)

    # -------------------------------------------------
    @classmethod
    def load(cls) -> "Settings":
        if SETTINGS_FILE.is_file():
            try:
                data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
                return cls(**known)
            except (OSError, ValueError, TypeError):
                pass
        settings = cls()
        settings.save()
        return settings

    def save(self):
        SETTINGS_FILE.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    # -------------------------------------------------
    def resolved_openstudio(self) -> str | None:
        """Return a working openstudio executable or None."""
        candidates = [self.openstudio_exe, shutil.which("openstudio"), shutil.which("openstudio.exe")]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return str(Path(candidate))
        return None


def ensure_folders():
    for folder in (LIBRARY_DIR, BUNDLE_DIR, PROJECT_DIR, RUNS_DIR):
        folder.mkdir(parents=True, exist_ok=True)
