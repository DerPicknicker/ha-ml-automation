"""Diagnostics for ML Automation."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from .manager import MLAutomationConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MLAutomationConfigEntry
) -> dict[str, Any]:
    """Return what is needed to understand why a pattern behaves as it does."""
    manager = entry.runtime_data
    source = hass.states.get(manager.source_entity)
    forest = manager.forest
    return {
        "config": manager.conf,
        "status": manager.status,
        "enabled": manager.enabled,
        "source_state": source.state if source else None,
        "detected_active": manager.is_active,
        "in_use": manager.is_busy,
        "predicted_active": manager.predicted_active,
        "probability": manager.probability,
        "days_of_data": manager.days_of_data,
        "transitions": manager.event_count,
        "model": {
            "trees": len(forest) if forest else 0,
            "trained_on_days": forest.days if forest else 0,
        },
        "habits": [asdict(habit) for habit in manager.habits],
        "last_action": manager.last_action,
    }
