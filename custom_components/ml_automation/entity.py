"""Base entity for ML Automation."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .manager import MLAutomationConfigEntry, PatternManager, signal_update


class MLAutomationEntity(Entity):
    """Entity that reflects the state of a pattern manager."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: MLAutomationConfigEntry, key: str) -> None:
        """Initialise the entity."""
        self.manager: PatternManager = entry.runtime_data
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="ML Automation",
            model="Learned pattern",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        """Follow updates of the manager."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_update(self.manager.entry.entry_id),
                self.async_write_ha_state,
            )
        )
