"""Fixtures for the ML Automation tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.components import recorder as _recorder
from homeassistant.components.homeassistant import ExposedEntities
from homeassistant.components.homeassistant.const import DATA_EXPOSED_ENTITIES
from homeassistant.components.recorder import migration as _migration, util as _util
from homeassistant.core import HomeAssistant
from homeassistant.helpers import recorder as _recorder_helper
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

# The recorder test fixture inspects the signatures of some recorder functions.
# On Python 3.14 that evaluates their annotations, which use names the modules
# only import for type checking. Make those names resolvable.
from sqlalchemy.orm.session import Session

from custom_components.ml_automation.const import (
    CONF_CONTROL_ENTITY,
    CONF_LEARN_ENTITIES,
    DOMAIN,
)
from custom_components.ml_automation.learner import SLOT_MINUTES, SLOTS_PER_DAY

for _module in (_migration, _util, _recorder_helper):
    for _name, _value in (("Recorder", _recorder.Recorder), ("Session", Session)):
        if not hasattr(_module, _name):
            setattr(_module, _name, _value)

TZ = ZoneInfo("Europe/Berlin")
# A Wednesday, with no daylight saving change in the days before it.
NOW = datetime(2026, 3, 11, 12, 0, tzinfo=TZ)

POWER = "sensor.tv_power"
PLUG = "switch.tv_plug"
LAMP = "light.lamp"

# A TV on a smart plug that is always on; its power shows when it is used.
TV_CONFIG = {CONF_CONTROL_ENTITY: PLUG, CONF_LEARN_ENTITIES: [PLUG, POWER]}
# A lamp with nothing to learn from but itself.
LAMP_CONFIG = {CONF_CONTROL_ENTITY: LAMP, CONF_LEARN_ENTITIES: [LAMP]}


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
    # The core integration is mocked by the HA fixtures. Assist needs the
    # exposed-entity store that a real core setup initializes.
    exposed = ExposedEntities(hass)
    await exposed.async_initialize()
    hass.data[DATA_EXPOSED_ENTITIES] = exposed


def local(hour: int, minute: int = 0, *, day: int = NOW.day) -> datetime:
    """Return a local time on the given day of the test month."""
    return datetime(NOW.year, NOW.month, day, hour, minute, tzinfo=TZ)


def slot(hour: int, minute: int = 0) -> int:
    """Return the slot a time of day falls into."""
    return (hour * 60 + minute) // SLOT_MINUTES


def daily_use(
    entity: str = POWER,
    *,
    idle: float = 1.0,
    active: float = 90.0,
    days: int = 10,
    on: int = 18,
    off: int = 19,
) -> dict[str, Any]:
    """Stored data of something that was used at the same time every day."""
    numeric = entity == POWER
    if not numeric:
        idle, active = 0.0, 1.0
    slots = [
        active if slot(on) <= index < slot(off) else idle
        for index in range(SLOTS_PER_DAY)
    ]
    recorded: dict[str, dict[str, list[float]]] = {}
    for offset in range(1, days + 1):
        day = (NOW - timedelta(days=offset)).date().isoformat()
        recorded[day] = {entity: list(slots)}
        if numeric:
            # The plug itself was on all the time.
            recorded[day][PLUG] = [1.0] * SLOTS_PER_DAY
    kinds = {POWER: "numeric", PLUG: "state"} if numeric else {entity: "state"}
    return {
        "days": recorded,
        "ignored": {},
        "kinds": kinds,
        "imported": sorted(kinds),
        "enabled": True,
    }


async def setup_entry(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    config: dict[str, Any],
    stored: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Set up a config entry, optionally with previously recorded data."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="TV",
        data=config,
        options=options or {},
        entry_id="tv",
        version=2,
    )
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
    # The minutely tick may retrain the model in the executor.
    await hass.async_block_till_done(wait_background_tasks=True)


async def run_minutes(
    hass: HomeAssistant, freezer, start: datetime, end: datetime
) -> None:
    """Let every minute from start to end (inclusive) tick."""
    when = start
    while when <= end:
        await move_to(hass, freezer, when)
        when += timedelta(minutes=1)
