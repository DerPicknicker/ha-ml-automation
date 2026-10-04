"""Numbers for tuning ML Automation from a dashboard."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_LEAD_MINUTES,
    CONF_OFF_DELAY_MINUTES,
    DEFAULT_LEAD_MINUTES,
    DEFAULT_OFF_DELAY_MINUTES,
)
from .entity import MLAutomationEntity
from .manager import MLAutomationConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MLAutomationConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the numbers."""
    async_add_entities(
        [
            OptionNumber(entry, CONF_LEAD_MINUTES, DEFAULT_LEAD_MINUTES, 240),
            OptionNumber(entry, CONF_OFF_DELAY_MINUTES, DEFAULT_OFF_DELAY_MINUTES, 720),
        ]
    )


class OptionNumber(MLAutomationEntity, NumberEntity):
    """A numeric option of the config entry."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(
        self, entry: MLAutomationConfigEntry, key: str, default: int, maximum: int
    ) -> None:
        """Initialise the number."""
        super().__init__(entry, key)
        self._entry = entry
        self._key = key
        self._default = default
        self._attr_native_max_value = maximum

    @property
    def native_value(self) -> float:
        """Return the configured value."""
        return self.manager.conf.get(self._key, self._default)

    async def async_set_native_value(self, value: float) -> None:
        """Store the value as an option, which reloads the entry."""
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, self._key: int(value)}
        )
