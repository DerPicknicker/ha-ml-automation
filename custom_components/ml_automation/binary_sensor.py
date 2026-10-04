"""Binary sensors showing what ML Automation detects and expects."""

from __future__ import annotations

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
    """Whether the source currently counts as active.

    Handy for checking that the threshold or the active states are right.
    """

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, entry: MLAutomationConfigEntry) -> None:
        """Initialise the binary sensor."""
        super().__init__(entry, "active")

    @property
    def is_on(self) -> bool | None:
        """Return the detected state."""
        return self.manager.is_active


class PredictedBinarySensor(MLAutomationEntity, BinarySensorEntity):
    """Whether the learned pattern expects the targets to be on right now."""

    def __init__(self, entry: MLAutomationConfigEntry) -> None:
        """Initialise the binary sensor."""
        super().__init__(entry, "predicted")

    @property
    def is_on(self) -> bool | None:
        """Return the expected state."""
        return self.manager.predicted_active
