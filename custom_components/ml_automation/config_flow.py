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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er, selector

from .const import (
    CONF_CONTROL_ENTITY,
    CONF_CONTROL_OFF,
    CONF_CONTROL_ON,
    CONF_GUARD_GRACE_MINUTES,
    CONF_LEAD_MINUTES,
    CONF_LEARN_ENTITIES,
    CONF_MIN_CONFIDENCE,
    CONF_MIN_DAYS,
    CONF_OFF_DELAY_MINUTES,
    CONF_WINDOW_DAYS,
    CONTROL_DOMAINS,
    DEFAULT_GUARD_GRACE_MINUTES,
    DEFAULT_LEAD_MINUTES,
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_MIN_DAYS,
    DEFAULT_OFF_DELAY_MINUTES,
    DEFAULT_WINDOW_DAYS,
    DOMAIN,
    USAGE_DEVICE_CLASSES,
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


def suggest_learn_entities(hass: HomeAssistant, control_entity: str) -> list[str]:
    """Suggest what to learn from: the controlled entity and its usage sensors.

    A smart plug is usually always on, so its state says little. The power it
    measures does, and it lives on the same device.
    """
    suggestion = [control_entity]
    registry = er.async_get(hass)
    entry = registry.async_get(control_entity)
    if entry is None or entry.device_id is None:
        return suggestion

    by_class: dict[str, list[str]] = {}
    for sibling in er.async_entries_for_device(registry, entry.device_id):
        if sibling.domain != "sensor":
            continue
        device_class = sibling.device_class or sibling.original_device_class
        by_class.setdefault(device_class or "", []).append(sibling.entity_id)
    for device_class in USAGE_DEVICE_CLASSES:
        if device_class in by_class:
            # One kind of measurement is enough; power beats current.
            return suggestion + sorted(by_class[device_class])
    return suggestion


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
                CONF_CONTROL_ON, default=current.get(CONF_CONTROL_ON, True)
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_LEAD_MINUTES,
                default=current.get(CONF_LEAD_MINUTES, DEFAULT_LEAD_MINUTES),
            ): _number(0, 240, "min"),
            vol.Required(
                CONF_CONTROL_OFF, default=current.get(CONF_CONTROL_OFF, True)
            ): selector.BooleanSelector(),
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
    """Set up a new learned pattern: what to control, and what to learn from."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialise the flow."""
        self._control_entity = ""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return MLAutomationOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the entity to control."""
        if user_input is not None:
            self._control_entity = user_input[CONF_CONTROL_ENTITY]
            await self.async_set_unique_id(self._control_entity)
            self._abort_if_unique_id_configured()
            return await self.async_step_learn()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CONTROL_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain=CONTROL_DOMAINS)
                    )
                }
            ),
        )

    async def async_step_learn(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the data to learn from."""
        errors = {}
        if user_input is not None:
            if user_input[CONF_LEARN_ENTITIES]:
                state = self.hass.states.get(self._control_entity)
                return self.async_create_entry(
                    title=state.name if state else self._control_entity,
                    data={CONF_CONTROL_ENTITY: self._control_entity, **user_input},
                )
            errors[CONF_LEARN_ENTITIES] = "no_entities"

        return self.async_show_form(
            step_id="learn",
            data_schema=vol.Schema(
                _learn_field(suggest_learn_entities(self.hass, self._control_entity))
            ),
            errors=errors,
            description_placeholders={"entity": self._control_entity},
        )


class MLAutomationOptionsFlow(OptionsFlow):
    """Tune a learned pattern. Everything here has a default."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the settings."""
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
