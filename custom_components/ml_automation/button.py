"""Buttons to apply a suggestion, predict now, or learn afresh."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_RECOMMENDATIONS
from .entity import MLAutomationEntity, RecommendationEntity
from .manager import MLAutomationConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MLAutomationConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the buttons."""
    if entry.data.get(CONF_RECOMMENDATIONS):
        async_add_entities(
            [
                ActionRecommendationButton(entry, "apply_action_suggestion"),
                ActionRecommendationButton(entry, "dismiss_action_suggestion"),
                ActionRecommendationButton(entry, "always_apply_action"),
                ActionRecommendationButton(entry, "refresh_action_patterns"),
                ActionRecommendationButton(entry, "relearn_actions"),
            ]
        )
        return
    async_add_entities(
        [ApplySuggestionButton(entry), PredictNowButton(entry), RelearnButton(entry)]
    )


class ActionRecommendationButton(RecommendationEntity, ButtonEntity):
    """Apply, dismiss, authorize, or refresh the shared recommendation feed."""

    def __init__(self, entry: MLAutomationConfigEntry, operation: str) -> None:
        """Initialize an ordinary dashboard button."""
        super().__init__(entry, operation)
        self._operation = operation
        if operation == "relearn_actions":
            self._attr_entity_category = EntityCategory.CONFIG

    @property
    def available(self) -> bool:
        """Enable action buttons only while a suggestion exists."""
        return (
            self._operation in ("refresh_action_patterns", "relearn_actions")
            or self.manager.suggestion is not None
        )

    async def async_press(self) -> None:
        """Apply the current proposal using its exact ID."""
        if self._operation == "relearn_actions":
            await self.manager.async_relearn()
        elif self._operation == "refresh_action_patterns":
            await self.manager.async_learn()
            await self.manager.async_refresh()
        elif (proposal := self.manager.suggestion) is not None:
            if self._operation == "dismiss_action_suggestion":
                await self.manager.async_dismiss(proposal.id)
            else:
                await self.manager.async_apply(
                    proposal.id,
                    self._context,
                    authorize=self._operation == "always_apply_action",
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
