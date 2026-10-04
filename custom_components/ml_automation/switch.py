"""Switch that lets ML Automation control the targets."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import MLAutomationEntity
from .manager import MLAutomationConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MLAutomationConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the switch."""
    async_add_entities([AutomationSwitch(entry)])


class AutomationSwitch(MLAutomationEntity, SwitchEntity):
    """Turns acting on the learned pattern on or off. Learning continues."""

    def __init__(self, entry: MLAutomationConfigEntry) -> None:
        """Initialise the switch."""
        super().__init__(entry, "automation")

    @property
    def is_on(self) -> bool:
        """Return whether the targets may be switched."""
        return self.manager.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Allow switching the targets."""
        await self.manager.async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop switching the targets."""
        await self.manager.async_set_enabled(False)
