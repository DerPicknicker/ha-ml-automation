"""Tests for seeding the model from the recorder."""

from __future__ import annotations

from typing import Any

from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from homeassistant.core import HomeAssistant

from custom_components.ml_automation.const import (
    CONF_DEBOUNCE_SECONDS,
    CONF_IMPORT_HISTORY,
)

from .conftest import NOW, POWER, TV_CONFIG, local, setup_entry


async def test_history_is_imported(
    recorder_mock,
    hass: HomeAssistant,
    setup_env: None,
    hass_storage: dict[str, Any],
    freezer,
) -> None:
    freezer.move_to(local(12, 0, day=7))
    hass.states.async_set(POWER, "1.0")
    await hass.async_block_till_done()
    for day in (8, 9, 10):
        freezer.move_to(local(18, 0, day=day))
        hass.states.async_set(POWER, "90.0")
        await hass.async_block_till_done()
        # A short dip that the debounce time has to swallow.
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

    config = {**TV_CONFIG, CONF_IMPORT_HISTORY: True, CONF_DEBOUNCE_SECONDS: 120}
    entry = await setup_entry(hass, hass_storage, config)
    manager = entry.runtime_data

    assert manager.event_count == 6
    # The 7th is not counted, we only saw it from noon.
    assert manager.days_of_data == 3
    assert {(habit.kind, habit.minute) for habit in manager.habits} == {
        ("on", 18 * 60),
        ("off", 19 * 60),
    }

    # A reload must not import the same history a second time.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.event_count == 6
