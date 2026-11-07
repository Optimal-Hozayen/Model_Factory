"""
=========================================================
Constructions
=========================================================

Creates materials and constructions from bundles and assigns them to
every surface / sub-surface of the category. The default construction
sets are updated as well.

Surfaces in the base model have hard-assigned constructions, so the
assignment is done on the surfaces and not only on the construction set.

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

from __future__ import annotations

import openstudio

from Core.Categories import CATEGORIES, classify_subsurface, classify_surface, surface_layers_reversed
from Core.Inventory import material_to_spec
from Core.Library import Bundle, Layer
from Core.OsUtil import cast, opt

U_TOLERANCE = 1e-3     # relative tolerance for conductance read-back


class ConstructionApplier:

    def __init__(self, model, log=print):
        self.model = model
        self.log = log
        self._material_cache = {}

    # =====================================================
    # Materials
    # =====================================================

    def make_material(self, layer: Layer):
        if layer.key in self._material_cache:
            return self._material_cache[layer.key]
        m = layer.material
        if m.kind == "opaque":
            material = openstudio.model.StandardOpaqueMaterial(
                self.model, m.roughness, layer.thickness_mm / 1000.0, m.conductivity, m.density, m.specific_heat)
            material.setThermalAbsorptance(m.thermal_absorptance)
            material.setSolarAbsorptance(m.solar_absorptance)
            material.setVisibleAbsorptance(m.visible_absorptance)
            material.setName(f"{m.name} {layer.thickness_mm:g}mm")
        elif m.kind == "airgap":
            material = openstudio.model.AirGap(self.model, m.r_value)
            material.setName(f"{m.name}")
        elif m.kind == "nomass":
            material = openstudio.model.MasslessOpaqueMaterial(self.model, m.roughness, m.r_value)
            material.setName(f"{m.name}")
        elif m.kind == "glazing":
            material = openstudio.model.SimpleGlazing(self.model, m.u_factor, m.shgc)
            material.setVisibleTransmittance(m.vt)
            material.setName(f"{m.name}")
        else:
            raise ValueError(f"Unsupported material kind '{m.kind}'.")
        self._material_cache[layer.key] = material
        return material

    def make_construction(self, bundle: Bundle, reverse=False):
        layers = list(reversed(bundle.layers)) if reverse else list(bundle.layers)
        construction = openstudio.model.Construction(self.model)
        construction.setName(f"{bundle.name} [reversed]" if reverse else bundle.name)
        for index, layer in enumerate(layers):
            if not construction.insertLayer(index, self.make_material(layer)):
                raise RuntimeError(f"OpenStudio refused layer {index + 1} ({layer.describe()}) in '{bundle.name}'.")
        return construction

    # =====================================================
    # Apply
    # =====================================================

    def apply(self, constructions: dict) -> dict:
        """constructions: category -> Bundle | None. Returns the manifest used by verify()."""
        manifest = {}
        active = {key: b for key, b in constructions.items() if b is not None}
        if not active:
            self.log("Constructions: all categories keep the base model constructions.")
            return manifest

        for key, bundle in active.items():
            errors = bundle.validate()
            if errors:
                raise ValueError(f"Bundle '{bundle.name}' is invalid:\n" + "\n".join(errors))

        built = {}
        for key, bundle in active.items():
            forward = self.make_construction(bundle)
            needs_reverse = key in ("interior_slab", "interior_wall") and not bundle.is_symmetric()
            reverse = self.make_construction(bundle, reverse=True) if needs_reverse else forward
            built[key] = (forward, reverse)
            manifest[key] = {
                "bundle": bundle.to_dict(),
                "forward": forward.nameString(),
                "reverse": reverse.nameString(),
                "surfaces": {},
                "failed": [],
            }

        # ---------------- surfaces ----------------
        done = set()
        for surface in self.model.getSurfaces():
            key = classify_surface(surface)
            if key not in built or str(surface.handle()) in done:
                continue
            forward, reverse = built[key]
            if key == "interior_slab":
                chosen = reverse if surface_layers_reversed(surface, key) else forward
                self._assign(surface, chosen, manifest[key])
                done.add(str(surface.handle()))
            elif key == "interior_wall":
                self._assign(surface, forward, manifest[key])
                done.add(str(surface.handle()))
                adjacent = opt(surface.adjacentSurface())
                if adjacent is not None and classify_surface(adjacent) == key:
                    self._assign(adjacent, reverse, manifest[key])
                    done.add(str(adjacent.handle()))
            else:
                self._assign(surface, forward, manifest[key])
                done.add(str(surface.handle()))

        # ---------------- sub-surfaces ----------------
        for sub in self.model.getSubSurfaces():
            key = classify_subsurface(sub)
            if key in built:
                self._assign(sub, built[key][0], manifest[key])

        self._update_default_sets(built)

        for key, entry in manifest.items():
            self.log(f"Constructions: {CATEGORIES[key].label} -> '{entry['forward']}' on "
                     f"{len(entry['surfaces'])} object(s)" + (f", {len(entry['failed'])} FAILED" if entry["failed"] else ""))
        return manifest

    @staticmethod
    def _assign(planar, construction, entry):
        if planar.setConstruction(construction):
            entry["surfaces"][planar.nameString()] = construction.nameString()
        else:
            entry["failed"].append(planar.nameString())

    def _update_default_sets(self, built):
        def ensure(getter, setter):
            existing = opt(getter())
            if existing is not None:
                return existing
            created = (openstudio.model.DefaultSubSurfaceConstructions(self.model)
                       if "SubSurface" in setter.__name__ else openstudio.model.DefaultSurfaceConstructions(self.model))
            setter(created)
            return created

        for dcs in self.model.getDefaultConstructionSets():
            ext = ensure(dcs.defaultExteriorSurfaceConstructions, dcs.setDefaultExteriorSurfaceConstructions)
            inte = ensure(dcs.defaultInteriorSurfaceConstructions, dcs.setDefaultInteriorSurfaceConstructions)
            gnd = ensure(dcs.defaultGroundContactSurfaceConstructions, dcs.setDefaultGroundContactSurfaceConstructions)
            ext_sub = ensure(dcs.defaultExteriorSubSurfaceConstructions, dcs.setDefaultExteriorSubSurfaceConstructions)
            int_sub = ensure(dcs.defaultInteriorSubSurfaceConstructions, dcs.setDefaultInteriorSubSurfaceConstructions)
            for key, (forward, reverse) in built.items():
                if key == "exterior_wall":
                    ext.setWallConstruction(forward)
                elif key == "roof":
                    ext.setRoofCeilingConstruction(forward)
                elif key == "exposed_floor":
                    ext.setFloorConstruction(forward)
                elif key == "interior_wall":
                    inte.setWallConstruction(forward)
                elif key == "interior_slab":
                    inte.setFloorConstruction(reverse)
                    inte.setRoofCeilingConstruction(forward)
                elif key == "ground_wall":
                    gnd.setWallConstruction(forward)
                elif key == "ground_slab":
                    gnd.setFloorConstruction(forward)
                elif key == "window":
                    ext_sub.setFixedWindowConstruction(forward)
                    ext_sub.setOperableWindowConstruction(forward)
                    ext_sub.setGlassDoorConstruction(forward)
                elif key == "exterior_door":
                    ext_sub.setDoorConstruction(forward)
                    ext_sub.setOverheadDoorConstruction(forward)
                elif key == "interior_door":
                    int_sub.setDoorConstruction(forward)


# =========================================================
# Verification
# =========================================================

def _layer_keys(construction) -> list | None:
    layered = cast(construction, "Construction")
    if layered is None:
        return None
    keys = []
    for material in layered.layers():
        converted = material_to_spec(material)
        if converted is None:
            return None
        spec, thickness = converted
        keys.append(Layer(spec, thickness).key)
    return keys


def verify_constructions(model, manifest) -> list[tuple[str, str, str]]:
    checks = []
    for key, entry in manifest.items():
        label = CATEGORIES[key].label
        bundle = Bundle.from_dict(entry["bundle"])
        expected = {entry["forward"], entry["reverse"]}

        # 1. every surface of the category points to the new construction
        total = assigned = 0
        wrong = []
        for surface in list(model.getSurfaces()) + list(model.getSubSurfaces()):
            category = classify_surface(surface) if surface.iddObjectType().valueName() == "OS_Surface" \
                else classify_subsurface(surface)
            if category != key:
                continue
            total += 1
            name = opt(surface.construction())
            name = name.nameString() if name is not None else None
            if name in expected:
                assigned += 1
            elif len(wrong) < 5:
                wrong.append(f"{surface.nameString()}: {name}")
        status = "PASS" if total and assigned == total else "FAIL"
        detail = f"{assigned}/{total} objects use '{entry['forward']}'"
        if wrong:
            detail += ", e.g. " + ", ".join(wrong)
        checks.append((f"{label}: assignment", status, detail))

        # 2. layers in the model are exactly the bundle's layers
        forward = opt(model.getConstructionByName(entry["forward"]))
        keys = _layer_keys(forward) if forward is not None else None
        bundle_keys = [layer.key for layer in bundle.layers]
        ok = keys == bundle_keys
        checks.append((f"{label}: layers", "PASS" if ok else "FAIL",
                       " | ".join(layer.describe() for layer in bundle.layers) if ok
                       else f"model has {keys}, bundle has {bundle_keys}"))
        if entry["reverse"] != entry["forward"]:
            reverse = opt(model.getConstructionByName(entry["reverse"]))
            rkeys = _layer_keys(reverse) if reverse is not None else None
            ok = rkeys == bundle_keys[::-1]
            checks.append((f"{label}: reversed side", "PASS" if ok else "FAIL",
                           f"'{entry['reverse']}' = reversed layers" if ok else f"{rkeys}"))

        # 3. thermal read-back
        expected_u = bundle.u_layers()
        if bundle.is_glazing:
            glazing = cast(forward.to_Construction().get().layers()[0], "SimpleGlazing") if forward else None
            model_u = glazing.uFactor() if glazing is not None else None
        else:
            model_u = opt(forward.thermalConductance()) if forward is not None else None
        ok = model_u is not None and expected_u is not None and abs(model_u - expected_u) <= U_TOLERANCE * max(expected_u, 1e-6)
        checks.append((f"{label}: U read back from model", "PASS" if ok else "FAIL",
                       f"model {model_u:.4f} vs bundle {expected_u:.4f} W/m²K (surface-to-surface)"
                       if model_u is not None and expected_u is not None else f"model={model_u}, bundle={expected_u}"))
        if entry["failed"]:
            checks.append((f"{label}: OpenStudio refused", "FAIL", ", ".join(entry["failed"][:10])))
    return checks
