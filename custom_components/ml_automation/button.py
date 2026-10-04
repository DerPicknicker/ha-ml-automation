"""Buttons to make ML Automation predict now or forget what it learned."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import MLAutomationEntity
from .manager import MLAutomationConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MLAutomationConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the buttons."""
    async_add_entities([PredictNowButton(entry), RelearnButton(entry)])


class PredictNowButton(MLAutomationEntity, ButtonEntity):
    """Re-evaluates the pattern and applies the expected state right away."""

    def __init__(self, entry: MLAutomationConfigEntry) -> None:
        """Initialise the button."""
        super().__init__(entry, "predict_now")

    async def async_press(self) -> None:
        """Predict and act now."""
        await self.manager.async_predict_now()


class RelearnButton(MLAutomationEntity, ButtonEntity):
    """Discards all learned data so the pattern is learned afresh."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: MLAutomationConfigEntry) -> None:
        """Initialise the button."""
        super().__init__(entry, "relearn")

    async def async_press(self) -> None:
        """Forget everything."""
        await self.manager.async_relearn()
