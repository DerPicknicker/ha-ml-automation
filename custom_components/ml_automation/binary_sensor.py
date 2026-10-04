"""Binary sensors showing what ML Automation detects and expects."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import MLAutomationEntity
from .manager import MLAutomationConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MLAutomationConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary sensors."""
    async_add_entities([ActiveBinarySensor(entry), PredictedBinarySensor(entry)])


class ActiveBinarySensor(MLAutomationEntity, BinarySensorEntity):
    """Whether what the model learns from currently counts as active.

    The attributes show how each learning entity is read, including the
    threshold that was learned for numeric ones.
    """

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, entry: MLAutomationConfigEntry) -> None:
        """Initialise the binary sensor."""
        super().__init__(entry, "active")

    @property
    def is_on(self) -> bool | None:
        """Return the detected state."""
        return self.manager.is_active

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return how each learning entity is read."""
        manager = self.manager
        return {
            "entities": {
                entity: {
                    "active": manager.entity_active(entity),
                    "active_above": manager.model.thresholds.get(entity),
                    "learned_from": entity in manager.label_entities,
                }
                for entity in manager.learn_entities
            }
        }


class PredictedBinarySensor(MLAutomationEntity, BinarySensorEntity):
    """Whether the learned pattern expects the targets to be on right now."""

    def __init__(self, entry: MLAutomationConfigEntry) -> None:
        """Initialise the binary sensor."""
        super().__init__(entry, "predicted")

    @property
    def is_on(self) -> bool | None:
        """Return the expected state."""
        return self.manager.predicted_active

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the raw model output for the current time."""
        probability = self.manager.probability
        return {
            "probability": None if probability is None else round(probability * 100)
        }
