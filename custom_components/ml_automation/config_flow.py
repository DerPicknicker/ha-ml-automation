"""Config flow for ML Automation."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback, split_entity_id
from homeassistant.helpers import entity_registry as er, selector

from .const import (
    CONF_CONTROL_ENTITY,
    CONF_GUARD_GRACE_MINUTES,
    CONF_LEAD_MINUTES,
    CONF_LEARN_ENTITIES,
    CONF_MIN_CONFIDENCE,
    CONF_MIN_DAYS,
    CONF_OFF_DELAY_MINUTES,
    CONF_RECOMMENDATIONS,
    CONF_WINDOW_DAYS,
    CONTROL_DOMAINS,
    DEFAULT_GUARD_GRACE_MINUTES,
    DEFAULT_LEAD_MINUTES,
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_MIN_DAYS,
    DEFAULT_OFF_DELAY_MINUTES,
    DEFAULT_WINDOW_DAYS,
    DOMAIN,
)


def _number(minimum: float, maximum: float, unit: str) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=1,
            unit_of_measurement=unit,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def suggest_control_entity(
    hass: HomeAssistant, learn_entities: list[str]
) -> str | None:
    """Suggest what to control, given what is learned from.

    If something switchable is learned from, that is the obvious candidate.
    Otherwise look at the devices: the power sensor of a smart plug sits on
    the same device as the plug's switch.
    """
    for entity_id in learn_entities:
        if split_entity_id(entity_id)[0] in CONTROL_DOMAINS:
            return entity_id

    registry = er.async_get(hass)
    for entity_id in learn_entities:
        entry = registry.async_get(entity_id)
        if entry is None or entry.device_id is None:
            continue
        siblings = [
            sibling
            for sibling in er.async_entries_for_device(registry, entry.device_id)
            # Skip the configuration toggles many devices come with.
            if sibling.domain in CONTROL_DOMAINS and sibling.entity_category is None
        ]
        if siblings:
            return min(
                siblings,
                key=lambda sibling: (
                    CONTROL_DOMAINS.index(sibling.domain),
                    sibling.entity_id,
                ),
            ).entity_id
    return None


def _learn_field(default: list[str]) -> dict[Any, Any]:
    return {
        vol.Required(CONF_LEARN_ENTITIES, default=default): selector.EntitySelector(
            selector.EntitySelectorConfig(multiple=True)
        )
    }


def _settings_schema(current: dict[str, Any]) -> vol.Schema:
    """Schema of everything that has a sensible default."""
    return vol.Schema(
        {
            **_learn_field(
                current.get(CONF_LEARN_ENTITIES) or [current[CONF_CONTROL_ENTITY]]
            ),
            vol.Required(
                CONF_LEAD_MINUTES,
                default=current.get(CONF_LEAD_MINUTES, DEFAULT_LEAD_MINUTES),
            ): _number(0, 240, "min"),
            vol.Required(
                CONF_OFF_DELAY_MINUTES,
                default=current.get(CONF_OFF_DELAY_MINUTES, DEFAULT_OFF_DELAY_MINUTES),
            ): _number(0, 720, "min"),
            vol.Required(
                CONF_GUARD_GRACE_MINUTES,
                default=current.get(
                    CONF_GUARD_GRACE_MINUTES, DEFAULT_GUARD_GRACE_MINUTES
                ),
            ): _number(0, 240, "min"),
            vol.Required(
                CONF_WINDOW_DAYS,
                default=current.get(CONF_WINDOW_DAYS, DEFAULT_WINDOW_DAYS),
            ): _number(3, 365, "d"),
            vol.Required(
                CONF_MIN_DAYS, default=current.get(CONF_MIN_DAYS, DEFAULT_MIN_DAYS)
            ): _number(2, 60, "d"),
            vol.Required(
                CONF_MIN_CONFIDENCE,
                default=current.get(CONF_MIN_CONFIDENCE, DEFAULT_MIN_CONFIDENCE),
            ): _number(10, 100, "%"),
        }
    )


class MLAutomationConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a new learned pattern: what to learn from, and what to control."""

    VERSION = 3

    def __init__(self) -> None:
        """Initialise the flow."""
        self._learn_entities: list[str] = []
        self._recommendations_flow = False

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return MLAutomationOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add the zero-field recommender, then offer additional switch patterns."""
        if self._recommendations_flow or not any(
            entry.data.get(CONF_RECOMMENDATIONS)
            for entry in self._async_current_entries()
        ):
            self._recommendations_flow = True
            await self.async_set_unique_id("action_recommendations")
            self._abort_if_unique_id_configured()
            if user_input is not None:
                return self.async_create_entry(
                    title="Action recommendations", data={CONF_RECOMMENDATIONS: True}
                )
            return self.async_show_form(step_id="user", data_schema=vol.Schema({}))
        return await self.async_step_pattern(user_input)

    async def async_step_pattern(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the data for an additional, existing-style switching pattern."""
        errors = {}
        if user_input is not None:
            if user_input[CONF_LEARN_ENTITIES]:
                self._learn_entities = user_input[CONF_LEARN_ENTITIES]
                return await self.async_step_control()
            errors[CONF_LEARN_ENTITIES] = "no_entities"

        return self.async_show_form(
            step_id="pattern", data_schema=vol.Schema(_learn_field([])), errors=errors
        )

    async def async_step_control(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the entity to control."""
        if user_input is not None:
            control_entity = user_input[CONF_CONTROL_ENTITY]
            await self.async_set_unique_id(control_entity)
            self._abort_if_unique_id_configured()
            state = self.hass.states.get(control_entity)
            return self.async_create_entry(
                title=state.name if state else control_entity,
                data={
                    CONF_CONTROL_ENTITY: control_entity,
                    CONF_LEARN_ENTITIES: self._learn_entities,
                },
            )

        suggestion = suggest_control_entity(self.hass, self._learn_entities)
        field = (
            vol.Required(CONF_CONTROL_ENTITY, default=suggestion)
            if suggestion
            else vol.Required(CONF_CONTROL_ENTITY)
        )
        return self.async_show_form(
            step_id="control",
            data_schema=vol.Schema(
                {
                    field: selector.EntitySelector(
                        selector.EntitySelectorConfig(domain=CONTROL_DOMAINS)
                    )
                }
            ),
        )


class MLAutomationOptionsFlow(OptionsFlow):
    """Tune a learned pattern. Everything here has a default."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the settings."""
        if self.config_entry.data.get(CONF_RECOMMENDATIONS):
            if user_input is not None:
                return self.async_create_entry(title="", data={})
            return self.async_show_form(
                step_id="recommendations", data_schema=vol.Schema({})
            )
        errors = {}
        if user_input is not None:
            if user_input[CONF_LEARN_ENTITIES]:
                return self.async_create_entry(data=user_input)
            errors[CONF_LEARN_ENTITIES] = "no_entities"

        return self.async_show_form(
            step_id="init",
            data_schema=_settings_schema(
                {**self.config_entry.data, **self.config_entry.options}
            ),
            errors=errors,
        )

    async def async_step_recommendations(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show how action recommendations are controlled without setup options."""
        return await self.async_step_init(user_input)
