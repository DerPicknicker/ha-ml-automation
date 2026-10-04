"""Tests for the config and options flow."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import SOURCE_USER
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
            return key.default()
    raise AssertionError(f"{field} not in form")


async def _start(hass: HomeAssistant, control: str) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONTROL_ENTITY: control}
    )


async def test_two_steps_with_nothing_but_defaults(hass: HomeAssistant) -> None:
    hass.states.async_set(LAMP, "off", {"friendly_name": "Reading lamp"})

    result = await _start(hass, LAMP)
    assert result["step_id"] == "learn"
    # By default the controlled entity is what is learned from.
    assert _default(result, CONF_LEARN_ENTITIES) == [LAMP]

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Reading lamp"
    assert result["data"] == {CONF_CONTROL_ENTITY: LAMP, CONF_LEARN_ENTITIES: [LAMP]}


async def test_power_sensor_of_the_device_is_suggested(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    plug_entry = MockConfigEntry(domain="test")
    plug_entry.add_to_hass(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=plug_entry.entry_id, identifiers={("test", "plug")}
    )
    for domain, unique, name, device_class in (
        ("switch", "relay", "tv_plug", None),
        ("sensor", "power", "tv_plug_power", "power"),
        ("sensor", "current", "tv_plug_current", "current"),
        ("sensor", "energy", "tv_plug_energy", "energy"),
    ):
        entity_registry.async_get_or_create(
            domain,
            "test",
            unique,
            suggested_object_id=name,
            device_id=device.id,
            original_device_class=device_class,
        )

    result = await _start(hass, PLUG)

    # Power is enough; current would say the same, energy only ever grows.
    assert _default(result, CONF_LEARN_ENTITIES) == [PLUG, "sensor.tv_plug_power"]


async def test_other_entities_can_be_learned_from(hass: HomeAssistant) -> None:
    result = await _start(hass, PLUG)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LEARN_ENTITIES: [POWER, "media_player.tv"]}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_LEARN_ENTITIES] == [POWER, "media_player.tv"]


async def test_needs_something_to_learn_from(hass: HomeAssistant) -> None:
    result = await _start(hass, PLUG)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LEARN_ENTITIES: []}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_LEARN_ENTITIES: "no_entities"}


async def test_entity_can_only_be_controlled_once(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    result = await _start(hass, PLUG)
    await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    result = await _start(hass, PLUG)

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
