"""Tests for the config and options flow."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.ml_automation.const import (
    CONF_CONTROL_ENTITY,
    CONF_LEAD_MINUTES,
    CONF_LEARN_ENTITIES,
    CONF_OFF_DELAY_MINUTES,
    DOMAIN,
)

from .conftest import LAMP, PLUG, POWER, TV_CONFIG, setup_entry

pytestmark = pytest.mark.usefixtures("setup_env")


def _default(result: dict[str, Any], field: str) -> Any:
    for key in result["data_schema"].schema:
        if key == field:
            if key.default is vol.UNDEFINED:
                raise vol.Invalid(f"{field} has no default")
            return key.default()
    raise AssertionError(f"{field} not in form")


async def _start(hass: HomeAssistant, learn: list[str]) -> dict[str, Any]:
    """Start the flow and answer the first question: what to learn from."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LEARN_ENTITIES: learn}
    )


async def test_learning_from_the_device_itself(hass: HomeAssistant) -> None:
    hass.states.async_set(LAMP, "off", {"friendly_name": "Reading lamp"})

    result = await _start(hass, [LAMP])
    assert result["step_id"] == "control"
    # What is learned from can be switched, so that is what gets controlled.
    assert _default(result, CONF_CONTROL_ENTITY) == LAMP

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Reading lamp"
    assert result["data"] == {CONF_CONTROL_ENTITY: LAMP, CONF_LEARN_ENTITIES: [LAMP]}


async def test_switch_of_the_power_sensors_device_is_suggested(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    plug_entry = MockConfigEntry(domain="test")
    plug_entry.add_to_hass(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=plug_entry.entry_id, identifiers={("test", "plug")}
    )
    for domain, unique, name, category in (
        ("sensor", "power", "tv_plug_power", None),
        ("switch", "relay", "tv_plug", None),
        # A settings toggle of the plug, not the plug itself.
        ("switch", "led", "tv_plug_led", EntityCategory.CONFIG),
        ("light", "ring", "tv_plug_ring", None),
    ):
        entity_registry.async_get_or_create(
            domain,
            "test",
            unique,
            suggested_object_id=name,
            device_id=device.id,
            entity_category=category,
        )

    result = await _start(hass, ["sensor.tv_plug_power"])
    assert _default(result, CONF_CONTROL_ENTITY) == PLUG

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    assert result["data"] == {
        CONF_CONTROL_ENTITY: PLUG,
        CONF_LEARN_ENTITIES: ["sensor.tv_plug_power"],
    }


async def test_nothing_to_suggest_for_unrelated_sensors(hass: HomeAssistant) -> None:
    result = await _start(hass, ["binary_sensor.hallway_motion", POWER])

    with pytest.raises(vol.Invalid):
        # No default: the field has to be filled in.
        _default(result, CONF_CONTROL_ENTITY)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONTROL_ENTITY: LAMP}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_CONTROL_ENTITY: LAMP,
        CONF_LEARN_ENTITIES: ["binary_sensor.hallway_motion", POWER],
    }


async def test_needs_something_to_learn_from(hass: HomeAssistant) -> None:
    result = await _start(hass, [])

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {CONF_LEARN_ENTITIES: "no_entities"}


async def test_entity_can_only_be_controlled_once(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    result = await _start(hass, [PLUG])
    await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    result = await _start(hass, [POWER])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONTROL_ENTITY: PLUG}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    hass.states.async_set(POWER, "1.0")
    entry = await setup_entry(hass, hass_storage, TV_CONFIG)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    assert _default(result, CONF_LEARN_ENTITIES) == [PLUG, POWER]
    assert _default(result, CONF_LEAD_MINUTES) == 15

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_LEARN_ENTITIES: [POWER], CONF_LEAD_MINUTES: 5},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    manager = entry.runtime_data
    assert manager.learn_entities == [POWER]
    assert manager.conf[CONF_LEAD_MINUTES] == 5
    assert manager.conf[CONF_OFF_DELAY_MINUTES] == 60
    assert manager.control_entity == PLUG
