"""
=========================================================
Library
=========================================================

Materials, layers and construction bundles.

A bundle is a named, ordered list of layers for one envelope category
(e.g. "Wall - EPS 200 mm"). Each bundle is saved as one JSON file in
Library/Bundles. Layers keep a copy of the material properties.

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from Core.Categories import CATEGORIES

MATERIAL_KINDS = ("opaque", "airgap", "nomass", "glazing")
ROUGHNESS = ("VeryRough", "Rough", "MediumRough", "MediumSmooth", "Smooth", "VerySmooth")


# =========================================================
# Material
# =========================================================

@dataclass
class MaterialSpec:
    name: str
    kind: str = "opaque"                 # opaque | airgap | nomass | glazing
    # opaque
    conductivity: float = 0.0            # W/m.K
    density: float = 0.0                 # kg/m3
    specific_heat: float = 0.0           # J/kg.K
    default_thickness_mm: float = 100.0
    roughness: str = "MediumRough"
    thermal_absorptance: float = 0.9
    solar_absorptance: float = 0.7
    visible_absorptance: float = 0.7
    # airgap / nomass
    r_value: float = 0.0                 # m2K/W
    # glazing (simple glazing system)
    u_factor: float = 0.0                # W/m2K (NFRC, with films)
    shgc: float = 0.0
    vt: float = 0.0
    # economics / carbon (optional)
    cost: float = 0.0                    # opaque: $/m2 per 100 mm ; others: $/m2
    carbon: float = 0.0                  # opaque: kgCO2e/m2 per 100 mm ; others: kgCO2e/m2
    source: str = "user"                 # user | default | model

    def validate(self) -> list[str]:
        errors = []
        if not self.name.strip():
            errors.append("Material name is empty.")
        if self.kind not in MATERIAL_KINDS:
            errors.append(f"Unknown material kind '{self.kind}'.")
        if self.kind == "opaque":
            if self.conductivity <= 0 or self.density <= 0 or self.specific_heat <= 0:
                errors.append(f"'{self.name}': conductivity, density and specific heat must be > 0.")
            if self.roughness not in ROUGHNESS:
                errors.append(f"'{self.name}': roughness must be one of {', '.join(ROUGHNESS)}.")
        elif self.kind in ("airgap", "nomass"):
            if self.r_value <= 0:
                errors.append(f"'{self.name}': R-value must be > 0.")
        elif self.kind == "glazing":
            if not (0 < self.u_factor <= 7.0):
                errors.append(f"'{self.name}': U-factor must be between 0 and 7 W/m²K.")
            if not (0 < self.shgc < 1):
                errors.append(f"'{self.name}': SHGC must be between 0 and 1.")
            if not (0 < self.vt < 1):
                errors.append(f"'{self.name}': visible transmittance must be between 0 and 1.")
        return errors

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "MaterialSpec":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


# =========================================================
# Layer & Bundle
# =========================================================

@dataclass
class Layer:
    material: MaterialSpec
    thickness_mm: float = 0.0            # used for opaque layers only

    @property
    def resistance(self) -> float:
        m = self.material
        if m.kind == "opaque":
            return (self.thickness_mm / 1000.0) / m.conductivity if m.conductivity > 0 else 0.0
        if m.kind in ("airgap", "nomass"):
            return m.r_value
        return 0.0

    @property
    def key(self) -> tuple:
        """Key used to compare a layer with the model layer."""
        m = self.material
        if m.kind == "opaque":
            return ("opaque", round(self.thickness_mm / 1000.0, 5), round(m.conductivity, 5),
                    round(m.density, 3), round(m.specific_heat, 3))
        if m.kind in ("airgap", "nomass"):
            return (m.kind, round(m.r_value, 5))
        return ("glazing", round(m.u_factor, 4), round(m.shgc, 4), round(m.vt, 4))

    def describe(self) -> str:
        m = self.material
        if m.kind == "opaque":
            return f"{m.name} {self.thickness_mm:g} mm"
        if m.kind in ("airgap", "nomass"):
            return f"{m.name} (R={m.r_value:g})"
        return f"{m.name} (U={m.u_factor:g}, SHGC={m.shgc:g})"

    def to_dict(self):
        return {"material": self.material.to_dict(), "thickness_mm": self.thickness_mm}

    @classmethod
    def from_dict(cls, data):
        return cls(MaterialSpec.from_dict(data["material"]), float(data.get("thickness_mm", 0.0)))


@dataclass
class Bundle:
    name: str
    category: str
    layers: list[Layer] = field(default_factory=list)
    description: str = ""
    source: str = "user"                 # user | model (seeded from the base model)
    updated: str = ""

    # ---------------- thermal ----------------
    @property
    def is_glazing(self) -> bool:
        return len(self.layers) == 1 and self.layers[0].material.kind == "glazing"

    def r_layers(self) -> float:
        return sum(layer.resistance for layer in self.layers)

    def u_layers(self) -> float | None:
        """Surface-to-surface conductance, the value OpenStudio reports as thermalConductance."""
        if self.is_glazing:
            return self.layers[0].material.u_factor
        r = self.r_layers()
        return 1.0 / r if r > 0 else None

    def u_with_films(self) -> float | None:
        if self.is_glazing:
            return self.layers[0].material.u_factor
        cat = CATEGORIES[self.category]
        r = self.r_layers() + cat.rsi + cat.rse
        return 1.0 / r if r > 0 else None

    def total_thickness_mm(self) -> float:
        return sum(layer.thickness_mm for layer in self.layers if layer.material.kind == "opaque")

    def is_symmetric(self) -> bool:
        keys = [layer.key for layer in self.layers]
        return keys == keys[::-1]

    def cost_per_m2(self) -> float:
        total = 0.0
        for layer in self.layers:
            m = layer.material
            total += m.cost * (layer.thickness_mm / 100.0) if m.kind == "opaque" else m.cost
        return total

    def carbon_per_m2(self) -> float:
        total = 0.0
        for layer in self.layers:
            m = layer.material
            total += m.carbon * (layer.thickness_mm / 100.0) if m.kind == "opaque" else m.carbon
        return total

    # ---------------- validation ----------------
    def validate(self) -> list[str]:
        errors = []
        if not self.name.strip():
            errors.append("Bundle name is empty.")
        if self.category not in CATEGORIES:
            errors.append(f"Unknown category '{self.category}'.")
            return errors
        if not self.layers:
            errors.append(f"'{self.name}' has no layers.")
            return errors
        allowed = CATEGORIES[self.category].allowed
        kinds = [layer.material.kind for layer in self.layers]
        if "glazing" in kinds and len(self.layers) != 1:
            errors.append(f"'{self.name}': a glazing system must be the only layer.")
        if allowed == "glazing" and kinds != ["glazing"]:
            errors.append(f"'{self.name}': windows need exactly one glazing-system layer.")
        if allowed == "opaque" and "glazing" in kinds:
            errors.append(f"'{self.name}': glazing layers are not allowed for {CATEGORIES[self.category].label}.")
        if len(self.layers) > 10:
            errors.append(f"'{self.name}': EnergyPlus allows at most 10 layers.")
        if kinds and kinds[0] == "airgap" or kinds and kinds[-1] == "airgap" and len(kinds) > 1:
            errors.append(f"'{self.name}': an air gap cannot be the first or last layer (EnergyPlus rule).")
        for layer in self.layers:
            errors.extend(layer.material.validate())
            if layer.material.kind == "opaque" and not (0.1 <= layer.thickness_mm <= 3000):
                errors.append(f"'{layer.material.name}': thickness must be between 0.1 and 3000 mm.")
        return errors

    # ---------------- io ----------------
    def to_dict(self):
        return {
            "name": self.name, "category": self.category, "description": self.description,
            "source": self.source, "updated": self.updated,
            "layers": [layer.to_dict() for layer in self.layers],
            "summary": {
                "u_layers_W_m2K": self.u_layers(), "u_with_films_W_m2K": self.u_with_films(),
                "r_layers_m2K_W": self.r_layers(), "thickness_mm": self.total_thickness_mm(),
            },
        }

    @classmethod
    def from_dict(cls, data) -> "Bundle":
        return cls(
            name=data["name"], category=data["category"], description=data.get("description", ""),
            source=data.get("source", "user"), updated=data.get("updated", ""),
            layers=[Layer.from_dict(item) for item in data.get("layers", [])],
        )

    def copy(self, new_name: str | None = None) -> "Bundle":
        clone = copy.deepcopy(self)
        if new_name:
            clone.name = new_name
        clone.source = "user"
        return clone

    def summary(self) -> str:
        u = self.u_with_films()
        u_text = f"U={u:.3f} W/m²K" if u else "U=n/a"
        return f"{self.name}  |  {len(self.layers)} layers  |  {u_text}"


# =========================================================
# Default material library
# =========================================================

def default_materials() -> list[MaterialSpec]:
    return [
        MaterialSpec("EPS", "opaque", 0.035, 20.0, 1400.0, 100, "MediumSmooth", cost=12.0, carbon=2.8, source="default"),
        MaterialSpec("XPS", "opaque", 0.030, 35.0, 1400.0, 100, "MediumSmooth", cost=22.0, carbon=2.1, source="default"),
        MaterialSpec("Mineral Wool", "opaque", 0.037, 80.0, 840.0, 100, "MediumSmooth", cost=18.0, carbon=4.3, source="default"),
        MaterialSpec("Polyiso", "opaque", 0.024, 32.0, 1400.0, 100, "MediumSmooth", cost=28.0, carbon=6.8, source="default"),
        MaterialSpec("Air gap (R 0.18)", "airgap", r_value=0.18, source="default"),
        MaterialSpec("Triple glazing U0.8", "glazing", u_factor=0.8, shgc=0.35, vt=0.55, source="default"),
        MaterialSpec("Double glazing U1.6", "glazing", u_factor=1.6, shgc=0.40, vt=0.65, source="default"),
    ]


class MaterialLibrary:
    """User + default materials (saved) and base-model materials (read-only, not saved)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.materials: dict[str, MaterialSpec] = {}
        self.load()

    def load(self):
        self.materials = {m.name: m for m in default_materials()}
        if self.path.is_file():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for item in data.get("materials", []):
                spec = MaterialSpec.from_dict(item)
                self.materials[spec.name] = spec

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        saved = [m.to_dict() for m in self.materials.values() if m.source != "model"]
        self.path.write_text(json.dumps({"materials": saved}, indent=2), encoding="utf-8")

    def add(self, spec: MaterialSpec, overwrite=True):
        errors = spec.validate()
        if errors:
            raise ValueError("\n".join(errors))
        if spec.name in self.materials and not overwrite:
            raise ValueError(f"Material '{spec.name}' already exists.")
        self.materials[spec.name] = spec

    def remove(self, name: str):
        self.materials.pop(name, None)

    def merge_model_materials(self, specs: list[MaterialSpec]):
        for spec in specs:
            if spec.name not in self.materials:
                self.materials[spec.name] = spec

    def by_kind(self, kinds) -> list[MaterialSpec]:
        return sorted((m for m in self.materials.values() if m.kind in kinds), key=lambda m: m.name.lower())


def safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9 ._-]+", "_", name).strip().replace(" ", "_") or "bundle"


class BundleLibrary:
    """Bundles stored one-per-file in a folder."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.bundles: dict[str, Bundle] = {}
        self.errors: list[str] = []
        self.load()

    def load(self):
        self.bundles, self.errors = {}, []
        self.folder.mkdir(parents=True, exist_ok=True)
        for file in sorted(self.folder.glob("*.json")):
            try:
                bundle = Bundle.from_dict(json.loads(file.read_text(encoding="utf-8")))
                self.bundles[bundle.name] = bundle
            except (OSError, ValueError, KeyError) as error:
                self.errors.append(f"{file.name}: {error}")

    def path_for(self, name: str) -> Path:
        return self.folder / f"{safe_filename(name)}.json"

    def save(self, bundle: Bundle, old_name: str | None = None) -> Path:
        errors = bundle.validate()
        if errors:
            raise ValueError("\n".join(errors))
        if old_name and old_name != bundle.name:
            self.delete(old_name)
        bundle.updated = datetime.now().isoformat(timespec="seconds")
        if bundle.source == "model":
            bundle.source = "user"
        path = self.path_for(bundle.name)
        path.write_text(json.dumps(bundle.to_dict(), indent=2), encoding="utf-8")
        self.bundles[bundle.name] = bundle
        return path

    def delete(self, name: str):
        self.bundles.pop(name, None)
        path = self.path_for(name)
        if path.is_file():
            path.unlink()

    def for_category(self, category: str) -> list[Bundle]:
        return sorted((b for b in self.bundles.values() if b.category == category), key=lambda b: b.name.lower())

    def import_file(self, file: Path) -> Bundle:
        bundle = Bundle.from_dict(json.loads(Path(file).read_text(encoding="utf-8")))
        self.save(bundle)
        return bundle

    def unique_name(self, base: str) -> str:
        if base not in self.bundles:
            return base
        index = 2
        while f"{base} ({index})" in self.bundles:
            index += 1
        return f"{base} ({index})"
