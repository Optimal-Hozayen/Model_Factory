"""
=========================================================
OsUtil
=========================================================

Small helpers around the OpenStudio Python SDK.

Author: Mahmoud Awad <mahmoud.awad@mail.concordia.ca>
"""

from __future__ import annotations

from pathlib import Path

import openstudio

_LOGGER_SILENCED = False


def silence_openstudio_logger(level=None):
    """Only print OpenStudio errors (the model gives many geometry warnings)."""
    global _LOGGER_SILENCED
    if _LOGGER_SILENCED:
        return
    logger = openstudio.Logger.instance().standardOutLogger()
    logger.setLogLevel(level if level is not None else openstudio.Error)
    _LOGGER_SILENCED = True


def load_model(osm_path) -> "openstudio.model.Model":
    silence_openstudio_logger()
    osm_path = Path(osm_path)
    if not osm_path.is_file():
        raise FileNotFoundError(f"OSM file not found: {osm_path}")
    loaded = openstudio.model.Model.load(openstudio.toPath(str(osm_path)))
    if not loaded.is_initialized():
        raise RuntimeError(f"OpenStudio could not load the model: {osm_path}")
    return loaded.get()


def save_model(model, osm_path) -> Path:
    osm_path = Path(osm_path)
    osm_path.parent.mkdir(parents=True, exist_ok=True)
    if not model.save(openstudio.toPath(str(osm_path)), True):
        raise RuntimeError(f"OpenStudio failed to save: {osm_path}")
    return osm_path


def opt(value):
    """Return the content of an OpenStudio optional, or None."""
    if value is None:
        return None
    if hasattr(value, "is_initialized"):
        return value.get() if value.is_initialized() else None
    return value


def name_of(obj) -> str | None:
    obj = opt(obj)
    return obj.nameString() if obj is not None else None


def cast(model_object, type_name: str):
    """Cast a ModelObject to a concrete class, e.g. cast(obj, 'ZoneHVACIdealLoadsAirSystem')."""
    caster = getattr(model_object, f"to_{type_name}", None)
    if caster is None:
        return None
    return opt(caster())
