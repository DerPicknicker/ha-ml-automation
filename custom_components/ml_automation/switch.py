"""Switches deciding what ML Automation does by itself."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import MLAutomationEntity
from .learner import KIND_OFF, KIND_ON
from .manager import MLAutomationConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MLAutomationConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the switches."""
    async_add_entities(
        [
            AutomationSwitch(entry),
            DirectionSwitch(entry, KIND_ON),
            DirectionSwitch(entry, KIND_OFF),
        ]
    )


class AutomationSwitch(MLAutomationEntity, SwitchEntity):
    """Master switch: off means nothing is switched, only suggested."""

    def __init__(self, entry: MLAutomationConfigEntry) -> None:
        """Initialise the switch."""
        super().__init__(entry, "automation")

    @property
    def is_on(self) -> bool:
        """Return whether the controlled entity may be switched at all."""
        return self.manager.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Allow switching."""
        await self.manager.async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop switching."""
        await self.manager.async_set_enabled(False)


class DirectionSwitch(MLAutomationEntity, SwitchEntity):
    """Whether the controlled entity is switched on, or off, automatically.

    With one of the two off the integration only ever switches the other way,
    e.g. it cuts standby power but never powers anything up.
    """

    def __init__(self, entry: MLAutomationConfigEntry, kind: str) -> None:
        """Initialise the switch."""
        super().__init__(entry, f"control_{kind}")
        self._kind = kind

    @property
    def is_on(self) -> bool:
        """Return whether this direction is automatic."""
        return self.manager.directions[self._kind]

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch this way automatically."""
        await self.manager.async_set_direction(self._kind, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Only suggest switching this way."""
        await self.manager.async_set_direction(self._kind, False)
