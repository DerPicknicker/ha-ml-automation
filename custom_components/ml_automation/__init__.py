"""ML Automation: learn the habits of an entity and act on them."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .const import CONF_CONTROL_ENTITY, CONF_LEARN_ENTITIES, PLATFORMS
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


async def async_migrate_entry(
    hass: HomeAssistant, entry: MLAutomationConfigEntry
) -> bool:
    """Migrate entries created before thresholds were learned automatically."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        # Version 1 had one source with a hand-entered threshold and a list of
        # targets. The source becomes the learning data, the first target the
        # controlled entity; what was recorded is rebuilt from the recorder.
        old = {**entry.data, **entry.options}
        source = old["source_entity"]
        control = (old.get("target_entities") or [source])[0]
        kept = {
            key: old[key]
            for key in (
                "control_on",
                "control_off",
                "lead_minutes",
                "off_delay_minutes",
                "guard_grace_minutes",
                "window_days",
                "min_days",
                "min_confidence",
            )
            if old.get(key) is not None
        }
        hass.config_entries.async_update_entry(
            entry,
            data={CONF_CONTROL_ENTITY: control, CONF_LEARN_ENTITIES: [source]},
            options=kept,
            version=2,
        )
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: MLAutomationConfigEntry
) -> None:
    """Reload when the options changed."""
    await hass.config_entries.async_reload(entry.entry_id)
