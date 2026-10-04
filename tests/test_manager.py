"""Tests for recording, learning and acting inside Home Assistant."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant

from custom_components.ml_automation.const import (
    CONF_CONTROL_ENTITY,
    CONF_CONTROL_ON,
    CONF_LEARN_ENTITIES,
    DOMAIN,
)
from custom_components.ml_automation.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import (
    LAMP,
    LAMP_CONFIG,
    NOW,
    PLUG,
    POWER,
    TV_CONFIG,
    daily_use,
    local,
    move_to,
    run_minutes,
    setup_entry,
    slot,
)

pytestmark = pytest.mark.usefixtures("setup_env")

TODAY = NOW.date().isoformat()


def _state(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state


async def _press(hass: HomeAssistant, button: str) -> None:
    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: button}, blocking=True
    )
    await hass.async_block_till_done()


async def test_starts_in_learning_mode(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass.states.async_set(POWER, "1.0")
    hass.states.async_set(PLUG, "on")
    await setup_entry(hass, hass_storage, TV_CONFIG)

    assert _state(hass, "sensor.tv_status") == "learning"
    assert _state(hass, "sensor.tv_learned_patterns") == "0"
    assert _state(hass, "sensor.tv_next_switch_on") == "unknown"
    # Without data there is no telling what counts as active for a sensor.
    assert _state(hass, "binary_sensor.tv_detected_activity") == "unknown"
    assert _state(hass, "switch.tv_automation") == "on"


async def test_always_on_socket_is_learned_from_its_power(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """The plug was on all the time; only its power shows the habit."""
    hass.states.async_set(POWER, "1.0")
    hass.states.async_set(PLUG, "on")
    await setup_entry(hass, hass_storage, TV_CONFIG, daily_use())

    assert _state(hass, "sensor.tv_status") == "controlling"
    # Switch-on and switch-off, at the same time on every day of the week.
    patterns = hass.states.get("sensor.tv_learned_patterns")
    assert patterns.state == "2"
    assert patterns.attributes["patterns"][0] == {
        "kind": "on",
        "time": "18:00",
        "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        "confidence": 100,
    }
    assert patterns.attributes["trained_on_days"] == 10
    assert _state(hass, "sensor.tv_days_of_data") == "10"
    # 18:00 minus 15 minutes, 19:00 plus 60 minutes; Berlin is UTC+1 in March.
    assert _state(hass, "sensor.tv_next_switch_on") == "2026-03-11T16:45:00+00:00"
    assert _state(hass, "sensor.tv_next_switch_off") == "2026-03-11T19:00:00+00:00"


async def test_threshold_is_learned_from_the_data(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass.states.async_set(POWER, "1.0")
    hass.states.async_set(PLUG, "on")
    await setup_entry(hass, hass_storage, TV_CONFIG, daily_use())

    activity = hass.states.get("binary_sensor.tv_detected_activity")
    assert activity.state == "off"
    assert activity.attributes["entities"] == {
        # On, but that says nothing about use, so it is not learned from.
        PLUG: {"active": True, "active_above": 0.5, "learned_from": False},
        # Halfway between standby (1 W) and watching (90 W).
        POWER: {"active": False, "active_above": 45.5, "learned_from": True},
    }

    hass.states.async_set(POWER, "60.0")
    await _press(hass, "button.tv_predict_now")
    assert _state(hass, "binary_sensor.tv_detected_activity") == "on"


async def test_switches_on_early(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(POWER, "0.0")
    hass.states.async_set(PLUG, "off")
    turn_on = async_mock_service(hass, "homeassistant", "turn_on")
    await setup_entry(hass, hass_storage, TV_CONFIG, daily_use())

    await move_to(hass, freezer, local(17, 44))
    assert not turn_on

    await move_to(hass, freezer, local(17, 45))
    assert len(turn_on) == 1
    assert turn_on[0].data[ATTR_ENTITY_ID] == PLUG


async def test_does_not_switch_on_what_is_on(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(POWER, "1.0")
    hass.states.async_set(PLUG, "on")
    turn_on = async_mock_service(hass, "homeassistant", "turn_on")
    await setup_entry(hass, hass_storage, TV_CONFIG, daily_use())

    await move_to(hass, freezer, local(17, 45))

    assert not turn_on


async def test_switches_off_late(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(POWER, "1.0")
    hass.states.async_set(PLUG, "on")
    turn_off = async_mock_service(hass, "homeassistant", "turn_off")
    await setup_entry(hass, hass_storage, TV_CONFIG, daily_use())

    await move_to(hass, freezer, local(19, 59))
    assert not turn_off

    await move_to(hass, freezer, local(20, 0))
    assert len(turn_off) == 1
    assert turn_off[0].data[ATTR_ENTITY_ID] == PLUG


async def test_switch_off_waits_while_in_use(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(POWER, "85.0")
    hass.states.async_set(PLUG, "on")
    turn_off = async_mock_service(hass, "homeassistant", "turn_off")
    entry = await setup_entry(hass, hass_storage, TV_CONFIG, daily_use())

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
    # The TV really was in use until then; nothing to discount.
    assert TODAY not in entry.runtime_data._ignored


async def test_any_learning_entity_keeps_it_on(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    player = "media_player.tv"
    hass.states.async_set(POWER, "1.0")
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(player, "playing")
    turn_off = async_mock_service(hass, "homeassistant", "turn_off")
    config = {**TV_CONFIG, CONF_LEARN_ENTITIES: [PLUG, POWER, player]}
    await setup_entry(hass, hass_storage, config, daily_use())

    await move_to(hass, freezer, local(20, 0))
    assert not turn_off

    hass.states.async_set(player, "off")
    await move_to(hass, freezer, local(20, 1))
    await move_to(hass, freezer, local(20, 16))
    assert len(turn_off) == 1


async def test_automation_switch_stops_control(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(POWER, "0.0")
    hass.states.async_set(PLUG, "off")
    turn_on = async_mock_service(hass, "homeassistant", "turn_on")
    await setup_entry(hass, hass_storage, TV_CONFIG, daily_use())

    await hass.services.async_call(
        "switch", "turn_off", {ATTR_ENTITY_ID: "switch.tv_automation"}, blocking=True
    )
    assert _state(hass, "sensor.tv_status") == "ready"

    await move_to(hass, freezer, local(17, 45))
    assert not turn_on


async def test_direction_can_be_disabled(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(POWER, "0.0")
    hass.states.async_set(PLUG, "off")
    turn_on = async_mock_service(hass, "homeassistant", "turn_on")
    await setup_entry(
        hass, hass_storage, TV_CONFIG, daily_use(), options={CONF_CONTROL_ON: False}
    )

    await move_to(hass, freezer, local(17, 45))

    assert not turn_on


async def test_relearn_forgets_everything(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(POWER, "0.0")
    hass.states.async_set(PLUG, "off")
    turn_on = async_mock_service(hass, "homeassistant", "turn_on")
    entry = await setup_entry(hass, hass_storage, TV_CONFIG, daily_use())

    await _press(hass, "button.tv_re_learn")

    assert _state(hass, "sensor.tv_status") == "learning"
    assert _state(hass, "sensor.tv_learned_patterns") == "0"
    assert entry.runtime_data._days == {}

    await move_to(hass, freezer, local(17, 45))
    assert not turn_on


async def test_learns_from_what_it_records(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    """No history, no threshold given: three evenings in front of the TV."""
    hass.states.async_set(POWER, "1.0")
    hass.states.async_set(PLUG, "on")
    entry = await setup_entry(hass, hass_storage, TV_CONFIG)
    manager = entry.runtime_data

    for day in (11, 12, 13):
        await run_minutes(hass, freezer, local(17, 0, day=day), local(17, 59, day=day))
        hass.states.async_set(POWER, "90.0")
        await run_minutes(hass, freezer, local(18, 0, day=day), local(18, 59, day=day))
        hass.states.async_set(POWER, "1.0")
        await run_minutes(hass, freezer, local(19, 0, day=day), local(20, 0, day=day))

    recorded = manager._days["2026-03-11"][POWER]
    assert recorded[slot(17, 55)] == 1.0
    assert recorded[slot(18, 0)] == 90.0
    assert recorded[slot(12, 0)] is None
    # Today is never part of the model.
    assert _state(hass, "sensor.tv_status") == "learning"

    await move_to(hass, freezer, local(0, 0, day=14))

    assert manager.model.thresholds[POWER] == 45.5
    assert {(habit.kind, habit.minute) for habit in manager.habits} == {
        ("on", 18 * 60),
        ("off", 19 * 60),
    }


async def test_short_spikes_are_averaged_out(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(POWER, "1.0")
    hass.states.async_set(PLUG, "on")
    entry = await setup_entry(hass, hass_storage, TV_CONFIG, daily_use())

    await move_to(hass, freezer, local(12, 5))
    hass.states.async_set(POWER, "90.0")
    await move_to(hass, freezer, local(12, 6))
    hass.states.async_set(POWER, "1.0")
    await run_minutes(hass, freezer, local(12, 7), local(12, 10))

    # One minute at 90 W in a five minute slot stays below the threshold.
    assert entry.runtime_data._days[TODAY][POWER][slot(12, 5)] == pytest.approx(18.8)


async def test_own_actions_are_not_learned(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    """A lamp learned from itself must not learn from being switched by us."""
    hass.states.async_set(LAMP, "off")
    turn_on = async_mock_service(hass, "homeassistant", "turn_on")
    turn_off = async_mock_service(hass, "homeassistant", "turn_off")
    entry = await setup_entry(hass, hass_storage, LAMP_CONFIG, daily_use(LAMP))
    manager = entry.runtime_data

    await move_to(hass, freezer, local(17, 45))
    assert len(turn_on) == 1
    hass.states.async_set(LAMP, "on")
    # On from 17:45, but the habit is 18:00: the lead time does not count.
    assert manager._ignored[TODAY] == [slot(17, 45), slot(17, 50), slot(17, 55)]

    # Nothing blocks switching off a lamp that is only learned from itself.
    await move_to(hass, freezer, local(20, 0))
    assert len(turn_off) == 1
    # And it was only still on after 19:00 because we had not acted yet.
    assert manager._ignored[TODAY][3:] == list(range(slot(19, 0), slot(20, 0)))


async def test_data_survives_reload(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(POWER, "1.0")
    hass.states.async_set(PLUG, "on")
    entry = await setup_entry(hass, hass_storage, TV_CONFIG, daily_use())

    await run_minutes(hass, freezer, local(12, 1), local(12, 5))
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    manager = entry.runtime_data
    assert manager._days[TODAY][POWER][slot(12, 0)] == 1.0
    assert len(manager._days) == 11
    assert _state(hass, "sensor.tv_learned_patterns") == "2"


async def test_number_changes_lead_time(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass.states.async_set(POWER, "1.0")
    await setup_entry(hass, hass_storage, TV_CONFIG, daily_use())

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
    await setup_entry(hass, hass_storage, TV_CONFIG, daily_use())

    # Noon: the last thing that was due is yesterday's switch-off.
    assert _state(hass, "binary_sensor.tv_predicted_state") == "off"

    await move_to(hass, freezer, local(17, 45))
    assert _state(hass, "binary_sensor.tv_predicted_state") == "on"
    # The model itself only expects activity from 18:00.
    predicted = hass.states.get("binary_sensor.tv_predicted_state")
    assert predicted.attributes["probability"] == 0

    await move_to(hass, freezer, local(18, 30))
    predicted = hass.states.get("binary_sensor.tv_predicted_state")
    assert predicted.attributes["probability"] == 100

    await move_to(hass, freezer, local(20, 0))
    assert _state(hass, "binary_sensor.tv_predicted_state") == "off"


async def test_predict_now_applies_expected_state(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(POWER, "1.0")
    hass.states.async_set(PLUG, "on")
    turn_on = async_mock_service(hass, "homeassistant", "turn_on")
    turn_off = async_mock_service(hass, "homeassistant", "turn_off")
    await setup_entry(hass, hass_storage, TV_CONFIG, daily_use())

    # Noon, plug was left on: the pattern says it should be off.
    await _press(hass, "button.tv_predict_now")
    assert len(turn_off) == 1
    assert not turn_on

    # In the evening, in the middle of the usual TV time, with the plug off.
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(POWER, "0.0")
    await move_to(hass, freezer, local(18, 30))
    turn_on.clear()
    await _press(hass, "button.tv_predict_now")
    assert len(turn_on) == 1
    assert turn_on[0].data[ATTR_ENTITY_ID] == PLUG
    assert len(turn_off) == 1


async def test_predict_now_respects_use_and_switch(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass.states.async_set(POWER, "85.0")
    hass.states.async_set(PLUG, "on")
    turn_off = async_mock_service(hass, "homeassistant", "turn_off")
    await setup_entry(hass, hass_storage, TV_CONFIG, daily_use())

    # Watching TV at noon: predicted off, but in use.
    await _press(hass, "button.tv_predict_now")
    assert not turn_off
    assert _state(hass, "sensor.tv_status") == "postponed"

    hass.states.async_set(POWER, "1.0")
    await hass.services.async_call(
        "switch", "turn_off", {ATTR_ENTITY_ID: "switch.tv_automation"}, blocking=True
    )
    await _press(hass, "button.tv_predict_now")
    assert not turn_off


async def test_predict_now_without_pattern_does_nothing(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass.states.async_set(POWER, "1.0")
    hass.states.async_set(PLUG, "on")
    turn_off = async_mock_service(hass, "homeassistant", "turn_off")
    await setup_entry(hass, hass_storage, TV_CONFIG)

    await _press(hass, "button.tv_predict_now")

    assert not turn_off
    assert _state(hass, "binary_sensor.tv_predicted_state") == "unknown"


async def test_diagnostics(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    hass.states.async_set(POWER, "1.0")
    hass.states.async_set(PLUG, "on")
    entry = await setup_entry(hass, hass_storage, TV_CONFIG, daily_use())

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["status"] == "controlling"
    assert diagnostics["learn_entities"][POWER] == {
        "state": "1.0",
        "active": False,
        "active_above": 45.5,
        "learned_from": True,
    }
    assert diagnostics["model"] == {"trees": 20, "trained_on_days": 10}
    assert len(diagnostics["habits"]) == 14


async def test_migrates_hand_configured_entries(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Entries from before thresholds were learned keep working."""
    hass.states.async_set(POWER, "1.0")
    hass.states.async_set(PLUG, "on")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="TV",
        version=1,
        data={
            "source_entity": POWER,
            "source_mode": "numeric",
            "active_above": 20.0,
            "target_entities": [PLUG],
            "lead_minutes": 10,
        },
        options={"off_delay_minutes": 30, "guard_entity": None},
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == 2
    assert entry.data == {CONF_CONTROL_ENTITY: PLUG, CONF_LEARN_ENTITIES: [POWER]}
    assert entry.options == {"lead_minutes": 10, "off_delay_minutes": 30}
    assert entry.runtime_data.label_entities == [POWER]
