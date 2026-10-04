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
    forest = manager.model.forest
    return {
        "config": manager.conf,
        "status": manager.status,
        "enabled": manager.enabled,
        "learn_entities": {
            entity: {
                "state": state.state if (state := hass.states.get(entity)) else None,
                "active": manager.entity_active(entity),
                "active_above": manager.model.thresholds.get(entity),
                "learned_from": entity in manager.label_entities,
            }
            for entity in manager.learn_entities
        },
        "recorded": manager.recording_summary(),
        "active_slots": manager.model.active_slots,
        "suggestion": action.kind if (action := manager.suggestion) else None,
        "detected_active": manager.is_active,
        "in_use": manager.in_use,
        "predicted_active": manager.predicted_active,
        "probability": manager.probability,
        "days_of_data": manager.days_of_data,
        "model": {
            "trees": len(forest) if forest else 0,
            "trained_on_days": forest.days if forest else 0,
        },
        "habits": [asdict(habit) for habit in manager.habits],
        "last_action": manager.last_action,
    }
