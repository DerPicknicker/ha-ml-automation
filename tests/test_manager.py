"""Tests for observing, learning and acting inside Home Assistant."""

from __future__ import annotations

from typing import Any

import pytest

from pytest_homeassistant_custom_component.common import async_mock_service

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant

from custom_components.ml_automation.const import (
    CONF_CONDITION_ENTITY,
    CONF_CONTROL_ON,
    CONF_DEBOUNCE_SECONDS,
    CONF_SOURCE_ENTITY,
    CONF_SOURCE_MODE,
    CONF_TARGET_ENTITIES,
    MODE_STATE,
)

from .conftest import PLUG, POWER, TV_CONFIG, daily_events, local, move_to, setup_entry

pytestmark = pytest.mark.usefixtures("setup_env")

LAMP = "light.lamp"
LAMP_CONFIG = {
    CONF_SOURCE_ENTITY: LAMP,
    CONF_SOURCE_MODE: MODE_STATE,
    CONF_DEBOUNCE_SECONDS: 0,
    CONF_TARGET_ENTITIES: [LAMP],
}


def _state(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state


async def test_starts_in_learning_mode(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass.states.async_set(POWER, "1.0")
    await setup_entry(hass, hass_storage, TV_CONFIG)

    assert _state(hass, "sensor.tv_status") == "learning"
    assert _state(hass, "sensor.tv_learned_patterns") == "0"
    assert _state(hass, "sensor.tv_next_switch_on") == "unknown"
    assert _state(hass, "binary_sensor.tv_detected_activity") == "off"
    assert _state(hass, "switch.tv_automation") == "on"


async def test_learned_pattern_is_exposed(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass.states.async_set(POWER, "1.0")
    await setup_entry(hass, hass_storage, TV_CONFIG, daily_events())

    assert _state(hass, "sensor.tv_status") == "controlling"
    # On and off, for workdays and for the weekend.
    assert _state(hass, "sensor.tv_learned_patterns") == "4"
    assert _state(hass, "sensor.tv_days_of_data") == "10"
    # 18:00 minus 15 minutes, 19:00 plus 60 minutes; Berlin is UTC+1 in March.
    assert _state(hass, "sensor.tv_next_switch_on") == "2026-03-11T16:45:00+00:00"
    assert _state(hass, "sensor.tv_next_switch_off") == "2026-03-11T19:00:00+00:00"


async def test_switches_on_early(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(POWER, "1.0")
    hass.states.async_set(PLUG, "off")
    turn_on = async_mock_service(hass, "homeassistant", "turn_on")
    await setup_entry(hass, hass_storage, TV_CONFIG, daily_events())

    await move_to(hass, freezer, local(17, 44))
    assert not turn_on

    await move_to(hass, freezer, local(17, 45))
    assert len(turn_on) == 1
    assert turn_on[0].data[ATTR_ENTITY_ID] == [PLUG]


async def test_does_not_switch_on_what_is_on(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(POWER, "1.0")
    hass.states.async_set(PLUG, "on")
    turn_on = async_mock_service(hass, "homeassistant", "turn_on")
    await setup_entry(hass, hass_storage, TV_CONFIG, daily_events())

    await move_to(hass, freezer, local(17, 45))

    assert not turn_on


async def test_switches_off_late(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(POWER, "1.0")
    hass.states.async_set(PLUG, "on")
    turn_off = async_mock_service(hass, "homeassistant", "turn_off")
    await setup_entry(hass, hass_storage, TV_CONFIG, daily_events())

    await move_to(hass, freezer, local(19, 59))
    assert not turn_off

    await move_to(hass, freezer, local(20, 0))
    assert len(turn_off) == 1
    assert turn_off[0].data[ATTR_ENTITY_ID] == [PLUG]


async def test_switch_off_waits_while_in_use(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(POWER, "85.0")
    hass.states.async_set(PLUG, "on")
    turn_off = async_mock_service(hass, "homeassistant", "turn_off")
    await setup_entry(hass, hass_storage, TV_CONFIG, daily_events())

    # Still watching at the time the TV would normally be switched off.
    await move_to(hass, freezer, local(20, 0))
    assert not turn_off
    assert _state(hass, "sensor.tv_status") == "postponed"

    await move_to(hass, freezer, local(21, 30))
    assert not turn_off

    # Watching ends; the grace period of 15 minutes starts with the next tick.
    hass.states.async_set(POWER, "1.0")
    await move_to(hass, freezer, local(21, 31))
    await move_to(hass, freezer, local(21, 45))
    assert not turn_off

    await move_to(hass, freezer, local(21, 46))
    assert len(turn_off) == 1
    assert _state(hass, "sensor.tv_status") == "controlling"


async def test_automation_switch_stops_control(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(POWER, "1.0")
    hass.states.async_set(PLUG, "off")
    turn_on = async_mock_service(hass, "homeassistant", "turn_on")
    await setup_entry(hass, hass_storage, TV_CONFIG, daily_events())

    await hass.services.async_call(
        "switch", "turn_off", {ATTR_ENTITY_ID: "switch.tv_automation"}, blocking=True
    )
    assert _state(hass, "sensor.tv_status") == "ready"

    await move_to(hass, freezer, local(17, 45))
    assert not turn_on


async def test_direction_can_be_disabled(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(POWER, "1.0")
    hass.states.async_set(PLUG, "off")
    turn_on = async_mock_service(hass, "homeassistant", "turn_on")
    config = {**TV_CONFIG, CONF_CONTROL_ON: False}
    await setup_entry(hass, hass_storage, config, daily_events())

    await move_to(hass, freezer, local(17, 45))

    assert not turn_on


async def test_condition_blocks_switch_on(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(POWER, "1.0")
    hass.states.async_set(PLUG, "off")
    hass.states.async_set("person.louis", "not_home")
    turn_on = async_mock_service(hass, "homeassistant", "turn_on")
    config = {**TV_CONFIG, CONF_CONDITION_ENTITY: "person.louis"}
    await setup_entry(hass, hass_storage, config, daily_events())

    await move_to(hass, freezer, local(17, 45))

    assert not turn_on


async def test_relearn_forgets_everything(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(POWER, "1.0")
    hass.states.async_set(PLUG, "off")
    turn_on = async_mock_service(hass, "homeassistant", "turn_on")
    entry = await setup_entry(hass, hass_storage, TV_CONFIG, daily_events())

    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: "button.tv_re_learn"}, blocking=True
    )

    assert _state(hass, "sensor.tv_status") == "learning"
    assert _state(hass, "sensor.tv_learned_patterns") == "0"
    assert entry.runtime_data.event_count == 0

    await move_to(hass, freezer, local(17, 45))
    assert not turn_on


async def test_learns_from_live_changes(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(POWER, "1.0")
    entry = await setup_entry(hass, hass_storage, TV_CONFIG)
    manager = entry.runtime_data

    # Same habit on three consecutive days.
    for day in (11, 12, 13):
        await move_to(hass, freezer, local(18, 0, day=day))
        hass.states.async_set(POWER, "90.0")
        await hass.async_block_till_done()
        assert _state(hass, "binary_sensor.tv_detected_activity") == "on"

        await move_to(hass, freezer, local(19, 0, day=day))
        hass.states.async_set(POWER, "1.0")
        await hass.async_block_till_done()

    assert manager.event_count == 6
    # Today is never part of the model, so two complete days so far.
    assert _state(hass, "sensor.tv_status") == "learning"

    await move_to(hass, freezer, local(0, 0, day=14))
    await move_to(hass, freezer, local(0, 1, day=14))

    assert [(habit.kind, habit.minute) for habit in manager.habits] == [
        ("on", 18 * 60),
        ("off", 19 * 60),
    ]


async def test_short_spikes_are_ignored(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(POWER, "1.0")
    config = {**TV_CONFIG, CONF_DEBOUNCE_SECONDS: 60}
    entry = await setup_entry(hass, hass_storage, config)
    manager = entry.runtime_data

    hass.states.async_set(POWER, "90.0")
    await hass.async_block_till_done()
    hass.states.async_set(POWER, "1.0")
    await hass.async_block_till_done()
    await move_to(hass, freezer, local(12, 5))
    assert manager.event_count == 0

    hass.states.async_set(POWER, "90.0")
    await hass.async_block_till_done()
    await move_to(hass, freezer, local(12, 10))
    assert manager.event_count == 1
    assert manager.is_active is True


async def test_own_actions_do_not_shift_the_habit(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    """When source and target are the same, our own switching is not a habit."""
    hass.states.async_set(LAMP, "off")
    async_mock_service(hass, "homeassistant", "turn_on")
    entry = await setup_entry(hass, hass_storage, LAMP_CONFIG, daily_events())
    manager = entry.runtime_data
    before = manager.event_count

    await move_to(hass, freezer, local(17, 45))
    hass.states.async_set(LAMP, "on")
    await hass.async_block_till_done()

    # Recorded at the learned time (18:00), not at the time we acted (17:45).
    assert manager.event_count == before + 1
    assert manager._events[-1] == {
        "ts": local(18, 0).timestamp(),
        "kind": "on",
        "auto": True,
    }


async def test_undoing_our_action_is_not_reinforced(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(LAMP, "off")
    async_mock_service(hass, "homeassistant", "turn_on")
    entry = await setup_entry(hass, hass_storage, LAMP_CONFIG, daily_events())
    manager = entry.runtime_data
    before = manager.event_count

    await move_to(hass, freezer, local(17, 45))
    hass.states.async_set(LAMP, "on")
    await hass.async_block_till_done()
    await move_to(hass, freezer, local(17, 50))
    hass.states.async_set(LAMP, "off")
    await hass.async_block_till_done()

    assert manager.event_count == before


async def test_own_target_does_not_block_switch_off(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(LAMP, "on")
    turn_off = async_mock_service(hass, "homeassistant", "turn_off")
    await setup_entry(hass, hass_storage, LAMP_CONFIG, daily_events())

    await move_to(hass, freezer, local(20, 0))

    assert len(turn_off) == 1


async def test_data_survives_reload(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass.states.async_set(POWER, "1.0")
    entry = await setup_entry(hass, hass_storage, TV_CONFIG, daily_events())

    hass.states.async_set(POWER, "90.0")
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data.event_count == 21
    assert _state(hass, "sensor.tv_learned_patterns") == "4"


async def test_number_changes_lead_time(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass.states.async_set(POWER, "1.0")
    await setup_entry(hass, hass_storage, TV_CONFIG, daily_events())

    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: "number.tv_switch_on_early", "value": 30},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert _state(hass, "sensor.tv_next_switch_on") == "2026-03-11T16:30:00+00:00"


async def test_predicted_state_follows_the_schedule(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(POWER, "1.0")
    await setup_entry(hass, hass_storage, TV_CONFIG, daily_events())

    # Noon: the last thing that was due is yesterday's switch-off.
    assert _state(hass, "binary_sensor.tv_predicted_state") == "off"

    await move_to(hass, freezer, local(17, 45))
    assert _state(hass, "binary_sensor.tv_predicted_state") == "on"

    await move_to(hass, freezer, local(20, 0))
    assert _state(hass, "binary_sensor.tv_predicted_state") == "off"


async def test_predict_now_applies_expected_state(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(POWER, "1.0")
    hass.states.async_set(PLUG, "on")
    turn_on = async_mock_service(hass, "homeassistant", "turn_on")
    turn_off = async_mock_service(hass, "homeassistant", "turn_off")
    await setup_entry(hass, hass_storage, TV_CONFIG, daily_events())

    async def press() -> None:
        await hass.services.async_call(
            "button", "press", {ATTR_ENTITY_ID: "button.tv_predict_now"}, blocking=True
        )
        await hass.async_block_till_done()

    # Noon, plug was left on: the pattern says it should be off.
    await press()
    assert len(turn_off) == 1
    assert not turn_on

    # In the evening, in the middle of the usual TV time, with the plug off.
    hass.states.async_set(PLUG, "off")
    await move_to(hass, freezer, local(18, 30))
    turn_on.clear()
    await press()
    assert len(turn_on) == 1
    assert turn_on[0].data[ATTR_ENTITY_ID] == [PLUG]
    assert len(turn_off) == 1


async def test_predict_now_respects_guard_and_switch(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass.states.async_set(POWER, "85.0")
    hass.states.async_set(PLUG, "on")
    turn_off = async_mock_service(hass, "homeassistant", "turn_off")
    await setup_entry(hass, hass_storage, TV_CONFIG, daily_events())

    # Watching TV at noon: predicted off, but in use.
    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: "button.tv_predict_now"}, blocking=True
    )
    assert not turn_off
    assert _state(hass, "sensor.tv_status") == "postponed"

    hass.states.async_set(POWER, "1.0")
    await hass.services.async_call(
        "switch", "turn_off", {ATTR_ENTITY_ID: "switch.tv_automation"}, blocking=True
    )
    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: "button.tv_predict_now"}, blocking=True
    )
    assert not turn_off


async def test_predict_now_without_pattern_does_nothing(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass.states.async_set(POWER, "1.0")
    hass.states.async_set(PLUG, "on")
    turn_off = async_mock_service(hass, "homeassistant", "turn_off")
    await setup_entry(hass, hass_storage, TV_CONFIG)

    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: "button.tv_predict_now"}, blocking=True
    )

    assert not turn_off
    assert _state(hass, "binary_sensor.tv_predicted_state") == "unknown"
