"""Fixtures for the ML Automation tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.core import HomeAssistant

from custom_components.ml_automation.const import (
    CONF_ACTIVE_ABOVE,
    CONF_DEBOUNCE_SECONDS,
    CONF_IMPORT_HISTORY,
    CONF_SOURCE_ENTITY,
    CONF_SOURCE_MODE,
    CONF_TARGET_ENTITIES,
    DOMAIN,
    MODE_NUMERIC,
)

TZ = ZoneInfo("Europe/Berlin")
# A Wednesday, with no daylight saving change in the days before it.
NOW = datetime(2026, 3, 11, 12, 0, tzinfo=TZ)

POWER = "sensor.tv_power"
PLUG = "switch.tv_plug"

TV_CONFIG = {
    CONF_SOURCE_ENTITY: POWER,
    CONF_SOURCE_MODE: MODE_NUMERIC,
    CONF_ACTIVE_ABOVE: 20.0,
    CONF_DEBOUNCE_SECONDS: 0,
    CONF_TARGET_ENTITIES: [PLUG],
    CONF_IMPORT_HISTORY: False,
}


@pytest.fixture
async def setup_env(
    hass: HomeAssistant, enable_custom_integrations: None, freezer
) -> None:
    """Make the integration loadable and run the test at a fixed local time.

    Not autouse, because tests using the recorder have to request
    `recorder_mock` before anything that pulls in `hass`.
    """
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to(NOW)


def local(hour: int, minute: int = 0, *, day: int = NOW.day) -> datetime:
    """Return a local time on the given day of the test month."""
    return datetime(NOW.year, NOW.month, day, hour, minute, tzinfo=TZ)


def daily_events(days: int = 10, on: int = 18, off: int = 19) -> dict[str, Any]:
    """Stored data of someone who used the device at the same time every day."""
    events = []
    observed = []
    for offset in range(1, days + 1):
        day = (NOW - timedelta(days=offset)).date()
        observed.append(day.isoformat())
        for hour, kind in ((on, "on"), (off, "off")):
            when = datetime(day.year, day.month, day.day, hour, tzinfo=TZ)
            events.append({"ts": when.timestamp(), "kind": kind})
    return {
        "events": events,
        "observed_days": observed,
        "history_imported": True,
        "enabled": True,
    }


async def setup_entry(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    config: dict[str, Any],
    stored: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Set up a config entry, optionally with previously learned data."""
    entry = MockConfigEntry(domain=DOMAIN, title="TV", data=config, entry_id="tv")
    if stored is not None:
        key = f"{DOMAIN}.{entry.entry_id}"
        hass_storage[key] = {"version": 1, "key": key, "data": stored}
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def move_to(hass: HomeAssistant, freezer, when: datetime) -> None:
    """Advance the clock and let everything that is due run."""
    freezer.move_to(when)
    async_fire_time_changed(hass, when)
    await hass.async_block_till_done()
