"""ML Automation: learn the habits of an entity and act on them."""

from __future__ import annotations

from typing import Any

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from .const import (
    CONF_CONTROL_ENTITY,
    CONF_LEARN_ENTITIES,
    CONF_RECOMMENDATIONS,
    DOMAIN,
    PLATFORMS,
)
from .manager import MLAutomationConfigEntry, PatternManager
from .recommendations import RecommendationManager

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register interfaces that apply exact recommendation IDs."""
    hass.data.setdefault(DOMAIN, {})

    def manager() -> RecommendationManager:
        if (value := hass.data[DOMAIN].get(CONF_RECOMMENDATIONS)) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="recommendations_not_loaded"
            )
        return value

    async def list_suggestions(call: ServiceCall) -> dict[str, Any]:
        """Return the current bounded recommendation queue."""
        current = manager()
        return {"suggestions": [item.as_dict() for item in current.suggestions]}

    async def apply_suggestion(call: ServiceCall) -> None:
        """Apply exactly the recommendation requested by the caller."""
        await manager().async_apply(
            call.data["suggestion_id"], call.context, authorize=call.data["authorize"]
        )

    async def dismiss_suggestion(call: ServiceCall) -> None:
        """Dismiss a specific recommendation."""
        await manager().async_dismiss(call.data["suggestion_id"])

    async def revoke_autonomy(call: ServiceCall) -> None:
        """Revoke permission for an action and its parameters."""
        await manager().async_revoke(call.data["action_key"])

    hass.services.async_register(
        DOMAIN,
        "list_suggestions",
        list_suggestions,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "apply_action_suggestion",
        apply_suggestion,
        schema=vol.Schema(
            {
                vol.Required("suggestion_id"): cv.string,
                vol.Optional("authorize", default=False): cv.boolean,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "dismiss_action_suggestion",
        dismiss_suggestion,
        schema=vol.Schema({vol.Required("suggestion_id"): cv.string}),
    )
    hass.services.async_register(
        DOMAIN,
        "revoke_action_autonomy",
        revoke_autonomy,
        schema=vol.Schema({vol.Required("action_key"): cv.string}),
    )
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: MLAutomationConfigEntry
) -> bool:
    """Set up a learned pattern from a config entry."""
    if entry.data.get(CONF_RECOMMENDATIONS):
        manager = RecommendationManager(hass, entry)
        platforms = [Platform.SENSOR, Platform.BUTTON, Platform.SWITCH]
    else:
        manager = PatternManager(hass, entry)
        platforms = PLATFORMS
    await manager.async_start()
    entry.runtime_data = manager
    if entry.data.get(CONF_RECOMMENDATIONS):
        hass.data[DOMAIN][CONF_RECOMMENDATIONS] = manager

    await hass.config_entries.async_forward_entry_setups(entry, platforms)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: MLAutomationConfigEntry
) -> bool:
    """Unload a config entry."""
    platforms = (
        [Platform.SENSOR, Platform.BUTTON, Platform.SWITCH]
        if entry.data.get(CONF_RECOMMENDATIONS)
        else PLATFORMS
    )
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, platforms):
        await entry.runtime_data.async_stop()
        if entry.data.get(CONF_RECOMMENDATIONS):
            hass.data[DOMAIN].pop(CONF_RECOMMENDATIONS, None)
    return unload_ok


async def async_remove_entry(
    hass: HomeAssistant, entry: MLAutomationConfigEntry
) -> None:
    """Delete the learned data together with the config entry."""
    manager = (
        RecommendationManager(hass, entry)
        if entry.data.get(CONF_RECOMMENDATIONS)
        else PatternManager(hass, entry)
    )
    await manager.async_remove_store()


async def async_migrate_entry(
    hass: HomeAssistant, entry: MLAutomationConfigEntry
) -> bool:
    """Migrate entries created before thresholds were learned automatically."""
    if entry.version > 3:
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
    if entry.version == 2:
        # Version 3 also accepts a zero-field action recommendation entry;
        # existing switching data and options keep their meaning.
        hass.config_entries.async_update_entry(entry, version=3)
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: MLAutomationConfigEntry
) -> None:
    """Reload when the options changed."""
    await hass.config_entries.async_reload(entry.entry_id)
