"""Tests for the config and options flow."""

from __future__ import annotations

from typing import Any

import pytest

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ml_automation.const import (
    CONF_ACTIVE_ABOVE,
    CONF_ACTIVE_STATES,
    CONF_GUARD_ABOVE,
    CONF_GUARD_ENTITY,
    CONF_LEAD_MINUTES,
    CONF_OFF_DELAY_MINUTES,
    CONF_SOURCE_ENTITY,
    CONF_SOURCE_MODE,
    CONF_TARGET_ENTITIES,
    DOMAIN,
    MODE_NUMERIC,
    MODE_STATE,
)

from .conftest import PLUG, POWER, TV_CONFIG, setup_entry

pytestmark = pytest.mark.usefixtures("setup_env")


async def test_numeric_source_flow(hass: HomeAssistant) -> None:
    hass.states.async_set(POWER, "1.0", {"unit_of_measurement": "W"})

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "TV", CONF_SOURCE_ENTITY: POWER}
    )
    assert result["step_id"] == "source"
    assert CONF_ACTIVE_ABOVE in result["data_schema"].schema

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACTIVE_ABOVE: 30}
    )
    assert result["step_id"] == "control"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TARGET_ENTITIES: [PLUG], CONF_LEAD_MINUTES: 10}
    )
    assert result["step_id"] == "learning"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "TV"
    data = result["data"]
    assert data[CONF_SOURCE_MODE] == MODE_NUMERIC
    assert data[CONF_ACTIVE_ABOVE] == 30
    assert data[CONF_TARGET_ENTITIES] == [PLUG]
    assert data[CONF_LEAD_MINUTES] == 10
    assert data[CONF_OFF_DELAY_MINUTES] == 60
    assert CONF_NAME not in data


async def test_state_source_flow(hass: HomeAssistant) -> None:
    hass.states.async_set("media_player.tv", "off")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "TV", CONF_SOURCE_ENTITY: "media_player.tv"},
    )
    assert CONF_ACTIVE_STATES in result["data_schema"].schema

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SOURCE_MODE] == MODE_STATE
    assert "playing" in result["data"][CONF_ACTIVE_STATES]
    assert result["data"][CONF_TARGET_ENTITIES] == []


async def test_options_flow(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    hass.states.async_set(POWER, "1.0")
    entry = await setup_entry(hass, hass_storage, TV_CONFIG)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ACTIVE_ABOVE: 50}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_GUARD_ENTITY: "sensor.amp_power", CONF_GUARD_ABOVE: 5},
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    manager = entry.runtime_data
    assert manager.conf[CONF_ACTIVE_ABOVE] == 50
    assert manager.conf[CONF_GUARD_ENTITY] == "sensor.amp_power"
    # Untouched settings keep their value.
    assert manager.conf[CONF_TARGET_ENTITIES] == [PLUG]

    # Clearing an optional field really removes it.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    assert entry.runtime_data.conf[CONF_GUARD_ENTITY] is None
