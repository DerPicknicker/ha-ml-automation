"""Runtime logic of one learned pattern: observe, learn, act."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from functools import partial
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Context, HomeAssistant, State, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CONTROL_ENTITY,
    CONF_CONTROL_OFF,
    CONF_CONTROL_ON,
    CONF_GUARD_GRACE_MINUTES,
    CONF_LEAD_MINUTES,
    CONF_LEARN_ENTITIES,
    CONF_MIN_CONFIDENCE,
    CONF_MIN_DAYS,
    CONF_OFF_DELAY_MINUTES,
    CONF_WINDOW_DAYS,
    DEFAULT_GUARD_GRACE_MINUTES,
    DEFAULT_LEAD_MINUTES,
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_MIN_DAYS,
    DEFAULT_OFF_DELAY_MINUTES,
    DEFAULT_WINDOW_DAYS,
    DOMAIN,
    INACTIVE_STATES,
    PENDING_OFF_MAX_HOURS,
    SAVE_DELAY_SECONDS,
    STATUS_CONTROLLING,
    STATUS_LEARNING,
    STATUS_POSTPONED,
    STATUS_READY,
    STORAGE_VERSION,
    TARGET_OFF_STATES,
)
from .learner import (
    KIND_NUMERIC,
    KIND_ON,
    KIND_STATE,
    SLOT_MINUTES,
    SLOTS_PER_DAY,
    Action,
    Habit,
    Model,
    learn,
    slots_from_timeline,
    upcoming_actions,
)

_LOGGER = logging.getLogger(__name__)

type MLAutomationConfigEntry = ConfigEntry[PatternManager]

# Projected actions are kept for a week in both directions: a weekday-only
# habit can be up to a week away, and the last one that was due tells us which
# state is expected right now.
_SCHEDULE_PAST_DAYS = 7
_SCHEDULE_DAYS = 2 * _SCHEDULE_PAST_DAYS + 1


def signal_update(entry_id: str) -> str:
    """Dispatcher signal sent whenever the manager's state changed."""
    return f"{DOMAIN}_{entry_id}_update"


def sample_state(state: State | None) -> tuple[float, str] | None:
    """Turn a state into a recordable value and its kind.

    Numeric states are recorded as they are. Everything else is on/off-like:
    1 unless the state is one of the known "nothing going on" states.
    """
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    try:
        return float(state.state), KIND_NUMERIC
    except ValueError:
        return float(state.state.lower() not in INACTIVE_STATES), KIND_STATE


def _slot_of(moment: datetime) -> int:
    return (moment.hour * 60 + moment.minute) // SLOT_MINUTES


class PatternManager:
    """Records the learning entities, learns their habits and acts on them."""

    def __init__(self, hass: HomeAssistant, entry: MLAutomationConfigEntry) -> None:
        """Initialise the manager."""
        self.hass = hass
        self.entry = entry
        self.conf: dict[str, Any] = {**entry.data, **entry.options}
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )

        # Persisted: slot values per ISO day and entity, the slots whose
        # activity was our own doing, what each entity looks like, and which
        # entities already got their recorder history imported.
        self._days: dict[str, dict[str, list[float | None]]] = {}
        self._ignored: dict[str, list[int]] = {}
        self._kinds: dict[str, str] = {}
        self._imported: set[str] = set()
        self.enabled = True

        # Runtime
        self.model = Model()
        self.days_of_data = 0
        self.last_action: dict[str, Any] | None = None
        self._schedule: list[Action] = []
        self._today: date | None = None
        self._last_tick: datetime | None = None
        self._slot: tuple[str, int] | None = None
        self._samples: dict[str, list[float]] = {}
        self._save_scheduled = False
        self._pending_off: Action | None = None
        self._pending_off_since: datetime | None = None
        self._idle_since: datetime | None = None
        self._unsubs: list[Callable[[], None]] = []

    # --- Configuration --------------------------------------------------

    @property
    def control_entity(self) -> str:
        """Entity that is switched according to the pattern."""
        return self.conf[CONF_CONTROL_ENTITY]

    @property
    def learn_entities(self) -> list[str]:
        """Entities whose activity is recorded."""
        return list(self.conf.get(CONF_LEARN_ENTITIES) or [self.control_entity])

    @property
    def evidence_entities(self) -> list[str]:
        """Learning entities that tell us something we did not cause ourselves.

        The controlled entity is on whenever we switched it on, so its state
        says nothing about whether it is actually being used.
        """
        return [
            entity for entity in self.learn_entities if entity != self.control_entity
        ]

    @property
    def label_entities(self) -> list[str]:
        """Entities whose activity the model learns the timing of.

        With nothing else to go by, the controlled entity itself is all there
        is, and what we switched ourselves has to be corrected for.
        """
        return self.evidence_entities or [self.control_entity]

    @property
    def lead(self) -> timedelta:
        """How long before a learned switch-on we act."""
        return timedelta(minutes=self.conf.get(CONF_LEAD_MINUTES, DEFAULT_LEAD_MINUTES))

    @property
    def off_delay(self) -> timedelta:
        """How long after a learned switch-off we act."""
        return timedelta(
            minutes=self.conf.get(CONF_OFF_DELAY_MINUTES, DEFAULT_OFF_DELAY_MINUTES)
        )

    @property
    def _window_days(self) -> int:
        return int(self.conf.get(CONF_WINDOW_DAYS, DEFAULT_WINDOW_DAYS))

    @property
    def min_days(self) -> int:
        """Complete days of data needed before the model is trusted."""
        return int(self.conf.get(CONF_MIN_DAYS, DEFAULT_MIN_DAYS))

    # --- Derived state --------------------------------------------------

    @property
    def habits(self) -> list[Habit]:
        """The habits the model predicts."""
        return self.model.habits

    @property
    def status(self) -> str:
        """Summarise what the manager is currently doing."""
        if not self.habits:
            return STATUS_LEARNING
        if self._pending_off is not None:
            return STATUS_POSTPONED
        if self.enabled and (
            self.conf.get(CONF_CONTROL_ON, True) or self.conf.get(CONF_CONTROL_OFF, True)
        ):
            return STATUS_CONTROLLING
        return STATUS_READY

    def entity_active(self, entity_id: str) -> bool | None:
        """Return whether a learning entity is active right now.

        None means it can't be told: the entity is unavailable, or it is
        numeric and no threshold could be learned for it yet.
        """
        if (sample := sample_state(self.hass.states.get(entity_id))) is None:
            return None
        value, kind = sample
        if kind == KIND_STATE:
            return value > 0
        if (threshold := self.model.thresholds.get(entity_id)) is None:
            return None
        return value >= threshold

    @property
    def is_active(self) -> bool | None:
        """Return whether what the model learns from is active right now."""
        states = [self.entity_active(entity) for entity in self.label_entities]
        if any(states):
            return True
        return None if all(state is None for state in states) else False

    @property
    def in_use(self) -> bool:
        """Return True while the controlled entity must not be switched off."""
        return any(self.entity_active(entity) for entity in self.evidence_entities)

    def last_due_action(self, now: datetime) -> Action | None:
        """Return the most recent action that was due."""
        for action in reversed(self._schedule):
            if action.when <= now:
                return action
        return None

    @property
    def predicted_active(self) -> bool | None:
        """Return whether the pattern expects the controlled entity to be on."""
        if (action := self.last_due_action(dt_util.now())) is None:
            return None
        return action.kind == KIND_ON

    @property
    def probability(self) -> float | None:
        """Return how likely the model thinks there is activity right now."""
        if self.model.forest is None:
            return None
        now = dt_util.now()
        return self.model.forest.probability(now.weekday(), _slot_of(now))

    def next_action(self, kind: str) -> Action | None:
        """Return the next scheduled action of the given kind."""
        now = dt_util.now()
        for action in self._schedule:
            if action.kind == kind and action.when > now:
                return action
        return None

    # --- Lifecycle ------------------------------------------------------

    async def async_start(self) -> None:
        """Load stored data and start observing."""
        data = await self._store.async_load()
        # Data written before the slot based recording has no "days".
        if data is not None and "days" in data:
            self._days = data["days"]
            self._ignored = data.get("ignored", {})
            self._kinds = data.get("kinds", {})
            self._imported = set(data.get("imported", []))
            self.enabled = data.get("enabled", True)

        now = dt_util.now()
        for entity in self.learn_entities:
            if entity not in self._imported:
                self._imported.add(entity)
                await self._async_import_history(entity, now)

        self._today = now.date()
        self._last_tick = now
        self._prune(now)
        await self._async_rebuild_model(now)
        self._async_schedule_save()

        self._unsubs.append(
            async_track_time_change(self.hass, self._async_tick, second=0)
        )

    async def async_stop(self) -> None:
        """Stop observing and flush stored data."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._flush_slot()
        await self._store.async_save(self._data_to_save())

    async def async_remove_store(self) -> None:
        """Delete everything that was learned."""
        await self._store.async_remove()

    async def async_set_enabled(self, enabled: bool) -> None:
        """Allow or forbid switching the controlled entity."""
        self.enabled = enabled
        if not enabled:
            self._clear_pending_off()
        self._async_schedule_save()
        self._notify()

    async def async_predict_now(self) -> None:
        """Re-evaluate the model and bring the controlled entity in line now.

        Useful after a restart, after changing settings, or when the entity
        ended up in the wrong state. Honours the same rules as scheduled
        actions: the automation switch and not switching off while in use.
        """
        now = dt_util.now()
        self._prune(now)
        await self._async_rebuild_model(now)
        if (action := self.last_due_action(now)) is not None:
            await self._async_execute(action, now)
        self._notify()

    async def async_relearn(self) -> None:
        """Forget everything and start learning from scratch."""
        now = dt_util.now()
        self._days = {}
        self._ignored = {}
        self._slot = None
        self._samples = {}
        self.last_action = None
        self._clear_pending_off()
        await self._async_rebuild_model(now)
        await self._store.async_save(self._data_to_save())
        self._notify()

    # --- Persistence ----------------------------------------------------

    def _data_to_save(self) -> dict[str, Any]:
        self._save_scheduled = False
        return {
            "days": self._days,
            "ignored": self._ignored,
            "kinds": self._kinds,
            "imported": sorted(self._imported),
            "enabled": self.enabled,
        }

    @callback
    def _async_schedule_save(self) -> None:
        # A new slot is recorded every few minutes. Scheduling a save resets
        # the delay, so only do it when none is pending.
        if self._save_scheduled:
            return
        self._save_scheduled = True
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY_SECONDS)

    def _prune(self, now: datetime) -> None:
        """Drop everything that fell out of the learning window or the config."""
        first_day = (now.date() - timedelta(days=self._window_days)).isoformat()
        entities = set(self.learn_entities)
        self._days = {
            day: {entity: slots for entity, slots in data.items() if entity in entities}
            for day, data in self._days.items()
            if day >= first_day
        }
        self._ignored = {
            day: slots for day, slots in self._ignored.items() if day >= first_day
        }
        self._kinds = {
            entity: kind for entity, kind in self._kinds.items() if entity in entities
        }
        self._imported &= entities

    # --- Recording ------------------------------------------------------

    def _day_slots(self, day: str, entity: str) -> list[float | None]:
        return self._days.setdefault(day, {}).setdefault(
            entity, [None] * SLOTS_PER_DAY
        )

    def _record(self, now: datetime) -> None:
        """Sample every learning entity; called once a minute."""
        slot = (now.date().isoformat(), _slot_of(now))
        if slot != self._slot:
            self._flush_slot()
            self._slot = slot
        for entity in self.learn_entities:
            if (sample := sample_state(self.hass.states.get(entity))) is None:
                continue
            value, kind = sample
            if self._kinds.setdefault(entity, kind) == kind:
                self._samples.setdefault(entity, []).append(value)

    def _flush_slot(self) -> None:
        """Store the mean of the samples taken in the current slot."""
        if self._slot is not None:
            day, slot = self._slot
            for entity, samples in self._samples.items():
                self._day_slots(day, entity)[slot] = sum(samples) / len(samples)
            if self._samples:
                self._async_schedule_save()
        self._samples = {}

    def _ignore(self, start: datetime, end: datetime) -> None:
        """Mark the slots from start up to, but not including, end."""
        moment = start.replace(second=0, microsecond=0)
        end = end.replace(second=0, microsecond=0)
        while moment < end:
            slots = self._ignored.setdefault(moment.date().isoformat(), [])
            if (slot := _slot_of(moment)) not in slots:
                slots.append(slot)
            moment += timedelta(minutes=SLOT_MINUTES)

    async def _async_import_history(self, entity: str, now: datetime) -> None:
        """Seed the recording of an entity from what the recorder knows."""
        if "recorder" not in self.hass.config.components:
            return
        # pylint: disable-next=import-outside-toplevel
        from homeassistant.components.recorder import get_instance, history

        first_day = now.date() - timedelta(days=self._window_days)
        start = dt_util.start_of_local_day(first_day)

        def _load() -> tuple[dict[str, list[float | None]], str | None]:
            states = history.state_changes_during_period(
                self.hass,
                dt_util.as_utc(start),
                None,
                entity,
                no_attributes=True,
                include_start_time_state=True,
            ).get(entity, [])
            timestamps: list[float] = []
            values: list[float | None] = []
            kind = None
            for state in states:
                sample = sample_state(state)
                if sample is not None and kind is None:
                    kind = sample[1]
                # A sensor that reported text for a while can't be compared
                # with its numeric values; treat those stretches as unknown.
                usable = sample is not None and sample[1] == kind
                timestamps.append(max(state.last_changed, start).timestamp())
                values.append(sample[0] if sample is not None and usable else None)
            days = {}
            day = first_day
            while day <= now.date():
                slots = slots_from_timeline(
                    day, now.tzinfo, timestamps, values, now.timestamp()
                )
                if any(value is not None for value in slots):
                    days[day.isoformat()] = slots
                day += timedelta(days=1)
            return days, kind

        try:
            days, kind = await get_instance(self.hass).async_add_executor_job(_load)
        except Exception:  # noqa: BLE001 - history is a bonus, never fatal
            _LOGGER.warning("Could not import history for %s", entity, exc_info=True)
            return

        if kind is not None:
            self._kinds.setdefault(entity, kind)
        for day, slots in days.items():
            recorded = self._day_slots(day, entity)
            for slot, value in enumerate(slots):
                if recorded[slot] is None:
                    recorded[slot] = value
        _LOGGER.debug("Imported %d days of history for %s", len(days), entity)

    # --- Learning -------------------------------------------------------

    async def _async_rebuild_model(self, now: datetime) -> None:
        """Retrain the model on complete days and derive the schedule from it."""
        today = now.date()
        days = {date.fromisoformat(day): data for day, data in self._days.items()}
        ignored = {
            date.fromisoformat(day): slots for day, slots in self._ignored.items()
        }
        labels = self.label_entities
        self.days_of_data = sum(
            1
            for day, data in days.items()
            if day < today
            and any(
                value is not None
                for entity in labels
                for value in data.get(entity, ())
            )
        )
        # Training is pure Python number crunching; keep it off the event loop.
        self.model = await self.hass.async_add_executor_job(
            partial(
                learn,
                days,
                dict(self._kinds),
                labels,
                ignored,
                today=today,
                half_life_days=self._window_days / 2,
                min_days=self.min_days,
                confidence=self.conf.get(CONF_MIN_CONFIDENCE, DEFAULT_MIN_CONFIDENCE)
                / 100,
            )
        )
        self._schedule = upcoming_actions(
            self.habits,
            start_day=today - timedelta(days=_SCHEDULE_PAST_DAYS),
            days=_SCHEDULE_DAYS,
            tz=now.tzinfo,
            lead=self.lead,
            off_delay=self.off_delay,
        )

    # --- Acting ---------------------------------------------------------

    async def _async_tick(self, now: datetime) -> None:
        """Run once a minute: record, roll over days and execute what is due."""
        now = dt_util.as_local(now)
        self._record(now)
        if now.date() != self._today:
            self._today = now.date()
            self._prune(now)
            await self._async_rebuild_model(now)

        last_tick = self._last_tick or now
        self._last_tick = now
        for action in self._schedule:
            if last_tick < action.when <= now:
                await self._async_execute(action, now)
        await self._async_check_pending_off(now)
        self._notify()

    async def _async_execute(self, action: Action, now: datetime) -> None:
        if not self.enabled:
            return
        if action.kind == KIND_ON:
            if not self.conf.get(CONF_CONTROL_ON, True):
                return
            self._clear_pending_off()
            await self._async_switch(action, now)
            return

        if not self.conf.get(CONF_CONTROL_OFF, True):
            return
        if self.in_use:
            _LOGGER.debug("Postponing switch-off, %s is in use", self.control_entity)
            self._pending_off = action
            self._pending_off_since = now
            self._idle_since = None
            return
        await self._async_switch(action, now)

    async def _async_check_pending_off(self, now: datetime) -> None:
        """Switch off once nothing was in use for the grace period."""
        if self._pending_off is None or self._pending_off_since is None:
            return
        if not self.enabled or now - self._pending_off_since > timedelta(
            hours=PENDING_OFF_MAX_HOURS
        ):
            self._clear_pending_off()
            return
        if self.in_use:
            self._idle_since = None
            return
        if self._idle_since is None:
            self._idle_since = now
        grace = timedelta(
            minutes=self.conf.get(CONF_GUARD_GRACE_MINUTES, DEFAULT_GUARD_GRACE_MINUTES)
        )
        if now - self._idle_since >= grace:
            action = self._pending_off
            self._clear_pending_off()
            await self._async_switch(action, now)

    def _clear_pending_off(self) -> None:
        self._pending_off = None
        self._pending_off_since = None
        self._idle_since = None

    async def _async_switch(self, action: Action, now: datetime) -> None:
        """Switch the controlled entity if it is not yet in the wanted state."""
        state = self.hass.states.get(self.control_entity)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return
        if (state.state in TARGET_OFF_STATES) != (action.kind == KIND_ON):
            return

        _LOGGER.debug("Switching %s %s", self.control_entity, action.kind)
        self._discount_own_action(action, now)
        self.last_action = {"kind": action.kind, "ts": dt_util.utcnow().timestamp()}
        await self.hass.services.async_call(
            "homeassistant",
            SERVICE_TURN_ON if action.kind == KIND_ON else SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: self.control_entity},
            context=Context(),
        )

    def _discount_own_action(self, action: Action, now: datetime) -> None:
        """Keep our own switching from being learned as a habit.

        We switch on before the learned time and off after it. Activity in
        between is our doing, not the user's: learning from it would drag the
        habit a bit earlier, or later, every single day.
        """
        if action.kind == KIND_ON:
            # Whatever comes on during the lead time may just be following us.
            self._ignore(now, action.habit_time)
        elif not self.evidence_entities and (
            now - action.habit_time <= self.off_delay + timedelta(minutes=SLOT_MINUTES)
        ):
            # Only the controlled entity is learned from, and it stayed on
            # past the learned time merely because we had not switched it off
            # yet. Later than that it was a deliberate catch-up, not a habit.
            self._ignore(action.habit_time, now)

    @callback
    def _notify(self) -> None:
        async_dispatcher_send(self.hass, signal_update(self.entry.entry_id))
