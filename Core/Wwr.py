"""
=========================================================
Wwr
=========================================================

Window-to-wall ratio per facade.

On each selected exterior wall the windows are replaced by one window
with the requested ratio (Surface.setWindowToWallRatio). Doors are kept.
The new window keeps the old window construction (or gets the window
bundle in the constructions step).

Modes
-----
walls_with_windows   only walls that already have windows (default)
all_exterior_walls   every exterior wall of the facade

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

from __future__ import annotations

from collections import Counter, defaultdict

from Core.Categories import WINDOW_TYPES, classify_surface
from Core.Inventory import FACADES, facade_of, window_area
from Core.OsUtil import opt


def apply_wwr(model, config, log) -> dict:
    facts = {"enabled": config.enabled}
    if not config.enabled:
        return facts
    for facade in FACADES:
        ratio = config.ratios.get(facade, 0.0)
        if config.apply_facade.get(facade, False) and not (0.0 < ratio < 0.95):
            raise ValueError(f"WWR for {facade} must be between 0 and 0.95 (got {ratio}).")

    fallback = Counter(opt(s.construction()).nameString() for s in model.getSubSurfaces()
                       if s.subSurfaceType() in WINDOW_TYPES and opt(s.construction()) is not None)
    fallback_construction = opt(model.getConstructionByName(fallback.most_common(1)[0][0])) if fallback else None

    per_facade = defaultdict(lambda: {"walls": 0, "changed": 0, "failed": []})
    for surface in model.getSurfaces():
        if classify_surface(surface) != "exterior_wall":
            continue
        facade = facade_of(surface, model)
        if not config.apply_facade.get(facade, False):
            continue
        windows = [s for s in surface.subSurfaces() if s.subSurfaceType() in WINDOW_TYPES]
        if config.mode == "walls_with_windows" and not windows:
            continue
        per_facade[facade]["walls"] += 1
        old_construction = opt(windows[0].construction()) if windows else fallback_construction
        new_window = opt(surface.setWindowToWallRatio(float(config.ratios[facade]), float(config.sill_height_m), True))
        if new_window is None:
            per_facade[facade]["failed"].append(surface.nameString())
            continue
        if old_construction is not None:
            new_window.setConstruction(old_construction)
        per_facade[facade]["changed"] += 1

    facts["facades"] = {}
    for facade in FACADES:
        if not config.apply_facade.get(facade, False):
            continue
        stats = per_facade[facade]
        facts["facades"][facade] = {"target": config.ratios[facade], **stats}
        log(f"WWR: {facade} -> {config.ratios[facade]:.0%} on {stats['changed']}/{stats['walls']} wall(s)"
            + (f", {len(stats['failed'])} non-rectangular wall(s) left unchanged" if stats["failed"] else ""))
    return facts


def measured_wwr(model, mode="walls_with_windows") -> dict:
    """facade -> (ratio over walls considered, ratio over all exterior walls)."""
    sums = defaultdict(lambda: [0.0, 0.0, 0.0])      # window, wall area (considered), wall area (all)
    for surface in model.getSurfaces():
        if classify_surface(surface) != "exterior_wall":
            continue
        facade = facade_of(surface, model)
        window = window_area(surface)
        sums[facade][0] += window
        sums[facade][2] += surface.grossArea()
        if mode == "all_exterior_walls" or window > 0:
            sums[facade][1] += surface.grossArea()
    return {f: (s[0] / s[1] if s[1] else 0.0, s[0] / s[2] if s[2] else 0.0) for f, s in sums.items()}


def verify_wwr(model, facts) -> list[tuple[str, str, str]]:
    if not facts.get("enabled"):
        return []
    checks = []
    achieved = measured_wwr(model, "walls_with_windows")
    for facade, stats in facts["facades"].items():
        target = stats["target"]
        considered, overall = achieved.get(facade, (0.0, 0.0))
        status = "PASS" if stats["walls"] and not stats["failed"] and abs(considered - target) < 0.01 else \
            "WARN" if stats["changed"] else "FAIL"
        checks.append((f"WWR {facade}", status,
                       f"target {target:.1%}, achieved {considered:.1%} on glazed walls "
                       f"({overall:.1%} of the whole facade); {stats['changed']}/{stats['walls']} walls changed"
                       + (f"; unchanged: {', '.join(stats['failed'][:5])}" if stats["failed"] else "")))
    return checks
