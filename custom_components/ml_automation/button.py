"""Buttons to apply a suggestion, predict now, or learn afresh."""

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
    async_add_entities(
        [ApplySuggestionButton(entry), PredictNowButton(entry), RelearnButton(entry)]
    )


class ApplySuggestionButton(MLAutomationEntity, ButtonEntity):
    """Does what is currently suggested; unavailable while nothing is."""

    def __init__(self, entry: MLAutomationConfigEntry) -> None:
        """Initialise the button."""
        super().__init__(entry, "apply_suggestion")

    @property
    def available(self) -> bool:
        """Return whether there is a suggestion to apply."""
        return self.manager.suggestion is not None

    async def async_press(self) -> None:
        """Apply the suggestion."""
        await self.manager.async_apply_suggestion()


class PredictNowButton(MLAutomationEntity, ButtonEntity):
    """Re-evaluates the pattern and applies the expected state right away."""

    def __init__(self, entry: MLAutomationConfigEntry) -> None:
        """Initialise the button."""
        super().__init__(entry, "predict_now")

    async def async_press(self) -> None:
        """Predict and act now."""
        await self.manager.async_predict_now()


class RelearnButton(MLAutomationEntity, ButtonEntity):
    """Forgets what was learned and learns again from the last few days."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: MLAutomationConfigEntry) -> None:
        """Initialise the button."""
        super().__init__(entry, "relearn")

    async def async_press(self) -> None:
        """Forget everything."""
        await self.manager.async_relearn()
