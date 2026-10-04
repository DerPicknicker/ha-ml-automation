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
from homeassistant.const import CONF_NAME, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback, split_entity_id
from homeassistant.helpers import selector

from .const import (
    CONF_ACTIVE_ABOVE,
    CONF_ACTIVE_STATES,
    CONF_CONDITION_ENTITY,
    CONF_CONTROL_OFF,
    CONF_CONTROL_ON,
    CONF_DEBOUNCE_SECONDS,
    CONF_GUARD_ABOVE,
    CONF_GUARD_ENTITY,
    CONF_GUARD_GRACE_MINUTES,
    CONF_IMPORT_HISTORY,
    CONF_LEAD_MINUTES,
    CONF_MIN_CONFIDENCE,
    CONF_MIN_DAYS,
    CONF_OFF_DELAY_MINUTES,
    CONF_SOURCE_ENTITY,
    CONF_SOURCE_MODE,
    CONF_TARGET_ENTITIES,
    CONF_WINDOW_DAYS,
    DEFAULT_ACTIVE_ABOVE,
    DEFAULT_DEBOUNCE_SECONDS,
    DEFAULT_GUARD_GRACE_MINUTES,
    DEFAULT_LEAD_MINUTES,
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_MIN_DAYS,
    DEFAULT_OFF_DELAY_MINUTES,
    DEFAULT_WINDOW_DAYS,
    DOMAIN,
    DOMAIN_ACTIVE_STATES,
    FALLBACK_ACTIVE_STATES,
    MODE_NUMERIC,
    MODE_STATE,
    NUMERIC_DOMAINS,
    TARGET_DOMAINS,
)

# Optional fields the user may clear again; an absent key means "not set".
_CLEARABLE = (CONF_GUARD_ENTITY, CONF_GUARD_ABOVE, CONF_CONDITION_ENTITY)


def _number(
    minimum: float, maximum: float, unit: str | None = None, step: float = 1
) -> selector.NumberSelector:
    config = selector.NumberSelectorConfig(
        min=minimum, max=maximum, step=step, mode=selector.NumberSelectorMode.BOX
    )
    if unit:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


def detect_source_mode(hass: HomeAssistant, entity_id: str) -> str:
    """Guess whether an entity is best read as a number or as a state."""
    domain = split_entity_id(entity_id)[0]
    if domain not in NUMERIC_DOMAINS:
        return MODE_STATE
    state = hass.states.get(entity_id)
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return MODE_NUMERIC
    try:
        float(state.state)
    except ValueError:
        return MODE_STATE
    return MODE_NUMERIC


def _source_schema(
    hass: HomeAssistant, entity_id: str, mode: str, current: dict[str, Any]
) -> vol.Schema:
    """Schema describing when the source counts as active."""
    fields: dict[Any, Any] = {}
    if mode == MODE_NUMERIC:
        state = hass.states.get(entity_id)
        unit = state.attributes.get("unit_of_measurement") if state else None
        fields[
            vol.Required(
                CONF_ACTIVE_ABOVE,
                default=current.get(CONF_ACTIVE_ABOVE, DEFAULT_ACTIVE_ABOVE),
            )
        ] = _number(-1_000_000, 1_000_000, unit, step=0.1)
    else:
        suggested = DOMAIN_ACTIVE_STATES.get(
            split_entity_id(entity_id)[0], FALLBACK_ACTIVE_STATES
        )
        default = current.get(CONF_ACTIVE_STATES, suggested)
        fields[vol.Required(CONF_ACTIVE_STATES, default=default)] = (
            selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=sorted({*suggested, *default}),
                    multiple=True,
                    custom_value=True,
                )
            )
        )
    fields[
        vol.Required(
            CONF_DEBOUNCE_SECONDS,
            default=current.get(
                CONF_DEBOUNCE_SECONDS,
                DEFAULT_DEBOUNCE_SECONDS if mode == MODE_NUMERIC else 0,
            ),
        )
    ] = _number(0, 3600, "s")
    return vol.Schema(fields)


def _control_schema(current: dict[str, Any]) -> vol.Schema:
    """Schema describing what is switched and when."""

    def optional(key: str) -> vol.Optional:
        if current.get(key) is None:
            return vol.Optional(key)
        return vol.Optional(key, description={"suggested_value": current[key]})

    return vol.Schema(
        {
            vol.Optional(
                CONF_TARGET_ENTITIES, default=current.get(CONF_TARGET_ENTITIES, [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=TARGET_DOMAINS, multiple=True)
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
            optional(CONF_GUARD_ENTITY): selector.EntitySelector(),
            optional(CONF_GUARD_ABOVE): _number(-1_000_000, 1_000_000, step=0.1),
            vol.Required(
                CONF_GUARD_GRACE_MINUTES,
                default=current.get(
                    CONF_GUARD_GRACE_MINUTES, DEFAULT_GUARD_GRACE_MINUTES
                ),
            ): _number(0, 240, "min"),
            optional(CONF_CONDITION_ENTITY): selector.EntitySelector(),
        }
    )


def _learning_schema(current: dict[str, Any], *, initial: bool) -> vol.Schema:
    """Schema describing how patterns are learned."""
    fields: dict[Any, Any] = {
        vol.Required(
            CONF_WINDOW_DAYS, default=current.get(CONF_WINDOW_DAYS, DEFAULT_WINDOW_DAYS)
        ): _number(3, 365, "d"),
        vol.Required(
            CONF_MIN_DAYS, default=current.get(CONF_MIN_DAYS, DEFAULT_MIN_DAYS)
        ): _number(2, 60, "d"),
        vol.Required(
            CONF_MIN_CONFIDENCE,
            default=current.get(CONF_MIN_CONFIDENCE, DEFAULT_MIN_CONFIDENCE),
        ): _number(10, 100, "%"),
    }
    if initial:
        fields[vol.Required(CONF_IMPORT_HISTORY, default=True)] = (
            selector.BooleanSelector()
        )
    return vol.Schema(fields)


def _with_cleared(user_input: dict[str, Any]) -> dict[str, Any]:
    """Make cleared optional fields explicit so they override older values."""
    return {**{key: None for key in _CLEARABLE}, **user_input}


class MLAutomationConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a new learned pattern."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._data: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return MLAutomationOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a name and the entity to learn from."""
        if user_input is not None:
            self._data = dict(user_input)
            self._data[CONF_SOURCE_MODE] = detect_source_mode(
                self.hass, user_input[CONF_SOURCE_ENTITY]
            )
            return await self.async_step_source()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME): selector.TextSelector(),
                    vol.Required(CONF_SOURCE_ENTITY): selector.EntitySelector(),
                }
            ),
        )

    async def async_step_source(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Define when the source counts as active."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_control()

        return self.async_show_form(
            step_id="source",
            data_schema=_source_schema(
                self.hass,
                self._data[CONF_SOURCE_ENTITY],
                self._data[CONF_SOURCE_MODE],
                {},
            ),
            description_placeholders={"entity": self._data[CONF_SOURCE_ENTITY]},
        )

    async def async_step_control(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Define what is switched."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_learning()

        return self.async_show_form(step_id="control", data_schema=_control_schema({}))

    async def async_step_learning(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Define how patterns are learned."""
        if user_input is not None:
            self._data.update(user_input)
            name = self._data.pop(CONF_NAME)
            return self.async_create_entry(title=name, data=self._data)

        return self.async_show_form(
            step_id="learning", data_schema=_learning_schema({}, initial=True)
        )


class MLAutomationOptionsFlow(OptionsFlow):
    """Change the settings of a learned pattern."""

    def __init__(self) -> None:
        """Initialise the flow."""
        self._options: dict[str, Any] = {}

    @property
    def _current(self) -> dict[str, Any]:
        return {**self.config_entry.data, **self.config_entry.options}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Define when the source counts as active."""
        current = self._current
        if user_input is not None:
            self._options = dict(user_input)
            return await self.async_step_control()

        return self.async_show_form(
            step_id="init",
            data_schema=_source_schema(
                self.hass,
                current[CONF_SOURCE_ENTITY],
                current[CONF_SOURCE_MODE],
                current,
            ),
            description_placeholders={"entity": current[CONF_SOURCE_ENTITY]},
        )

    async def async_step_control(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Define what is switched."""
        if user_input is not None:
            self._options.update(_with_cleared(user_input))
            return await self.async_step_learning()

        return self.async_show_form(
            step_id="control", data_schema=_control_schema(self._current)
        )

    async def async_step_learning(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Define how patterns are learned."""
        if user_input is not None:
            self._options.update(user_input)
            return self.async_create_entry(data=self._options)

        return self.async_show_form(
            step_id="learning", data_schema=_learning_schema(self._current, initial=False)
        )
