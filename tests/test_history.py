"""Tests for seeding the recording from the recorder."""

from __future__ import annotations

from typing import Any

from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from homeassistant.core import HomeAssistant

from .conftest import NOW, PLUG, POWER, TV_CONFIG, local, setup_entry, slot


async def test_history_is_imported(
    recorder_mock,
    hass: HomeAssistant,
    setup_env: None,
    hass_storage: dict[str, Any],
    freezer,
) -> None:
    freezer.move_to(local(12, 0, day=2))
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(POWER, "0.0")
    await hass.async_block_till_done()
    freezer.move_to(local(12, 0, day=7))
    hass.states.async_set(PLUG, "on")
    hass.states.async_set(POWER, "1.0")
    await hass.async_block_till_done()
    for day in (8, 9, 10):
        freezer.move_to(local(18, 0, day=day))
        hass.states.async_set(POWER, "90.0")
        await hass.async_block_till_done()
        # A short dip that must not split the evening in two.
        freezer.move_to(local(18, 30, day=day))
        hass.states.async_set(POWER, "5.0")
        await hass.async_block_till_done()
        freezer.move_to(local(18, 31, day=day))
        hass.states.async_set(POWER, "95.0")
        await hass.async_block_till_done()
        freezer.move_to(local(19, 0, day=day))
        hass.states.async_set(POWER, "1.0")
        await hass.async_block_till_done()
    freezer.move_to(NOW)
    await async_wait_recording_done(hass)

    entry = await setup_entry(hass, hass_storage, TV_CONFIG)
    manager = entry.runtime_data

    recorded = manager._days["2026-03-09"]
    assert recorded[POWER][slot(17, 55)] == 1.0
    assert recorded[POWER][slot(18, 0)] == 90.0
    assert recorded[POWER][slot(18, 30)] == 77.0
    assert recorded[PLUG][slot(3, 0)] == 1.0
    # Recording started at noon on the 2nd; before that nothing is known.
    assert manager._days["2026-03-02"][POWER][slot(11, 55)] is None
    assert manager._days["2026-03-02"][POWER][slot(12, 0)] == 0.0
    assert manager._days["2026-03-07"][POWER][slot(12, 0)] == 1.0
    # And nothing is made up for the rest of today.
    assert manager._days["2026-03-11"][POWER][slot(12, 5)] is None

    assert manager.model.thresholds[POWER] is not None
    assert {(habit.kind, habit.minute) for habit in manager.habits} == {
        ("on", 18 * 60),
        ("off", 19 * 60),
    }

    # Re-learn starts over from the last week only.
    await manager.async_relearn()
    assert min(manager._days) == "2026-03-04"
    assert {(habit.kind, habit.minute) for habit in manager.habits} == {
        ("on", 18 * 60),
        ("off", 19 * 60),
    }

    # A reload must not bring the older days back.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert min(entry.runtime_data._days) == "2026-03-04"
