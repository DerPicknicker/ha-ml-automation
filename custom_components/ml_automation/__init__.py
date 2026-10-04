"""ML Automation: learn the habits of an entity and act on them."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .const import PLATFORMS
from .manager import MLAutomationConfigEntry, PatternManager


async def async_setup_entry(
    hass: HomeAssistant, entry: MLAutomationConfigEntry
) -> bool:
    """Set up a learned pattern from a config entry."""
    manager = PatternManager(hass, entry)
    await manager.async_start()
    entry.runtime_data = manager

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: MLAutomationConfigEntry
) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_stop()
    return unload_ok


async def async_remove_entry(
    hass: HomeAssistant, entry: MLAutomationConfigEntry
) -> None:
    """Delete the learned data together with the config entry."""
    await PatternManager(hass, entry).async_remove_store()


async def _async_update_listener(
    hass: HomeAssistant, entry: MLAutomationConfigEntry
) -> None:
    """Reload when the options changed."""
    await hass.config_entries.async_reload(entry.entry_id)
