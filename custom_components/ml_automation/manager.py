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
from homeassistant.core import (
    CALLBACK_TYPE,
    Context,
    Event,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ACTIVE_ABOVE,
    CONF_ACTIVE_STATES,
    CONF_CONDITION_ENTITY,
    CONF_CONTROL_OFF,
    CONF_CONTROL_ON,
    CONF_DEBOUNCE_SECONDS,
    CONF_GUARD_ABOVE,
    CONF_GUARD_ENTITY,
    CONF_GUARD_GRACE_MINUTES,
    CONF_IMPORT_HISTORY,
    CONF_LEAD_MINUTES,
    CONF_MIN_CONFIDENCE,
    CONF_MIN_DAYS,
    CONF_OFF_DELAY_MINUTES,
    CONF_SOURCE_ENTITY,
    CONF_SOURCE_MODE,
    CONF_TARGET_ENTITIES,
    CONF_WINDOW_DAYS,
    DEFAULT_ACTIVE_ABOVE,
    DEFAULT_DEBOUNCE_SECONDS,
    DEFAULT_GUARD_GRACE_MINUTES,
    DEFAULT_LEAD_MINUTES,
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_MIN_DAYS,
    DEFAULT_OFF_DELAY_MINUTES,
    DEFAULT_WINDOW_DAYS,
    DOMAIN,
    FALLBACK_ACTIVE_STATES,
    GENERIC_ACTIVE_STATES,
    MODE_NUMERIC,
    PENDING_OFF_MAX_HOURS,
    REVERT_WINDOW_SECONDS,
    SELF_TRIGGER_SECONDS,
    STATUS_CONTROLLING,
    STATUS_LEARNING,
    STATUS_POSTPONED,
    STATUS_READY,
    STORAGE_VERSION,
    TARGET_OFF_STATES,
)
from .learner import (
    KIND_OFF,
    KIND_ON,
    SLOT_MINUTES,
    Action,
    Forest,
    Habit,
    extract_transitions,
    learn,
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


def evaluate_state(
    state: State | None, *, above: float | None, active_states: frozenset[str]
) -> bool | None:
    """Decide whether a state counts as active. None means "can't tell"."""
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    if above is not None:
        try:
            return float(state.state) > above
        except ValueError:
            return None
    if state.state in active_states:
        return True
    try:
        return float(state.state) > 0
    except ValueError:
        return False


class PatternManager:
    """Observes a source entity, learns its habits and acts on them."""

    def __init__(self, hass: HomeAssistant, entry: MLAutomationConfigEntry) -> None:
        """Initialise the manager."""
        self.hass = hass
        self.entry = entry
        self.conf: dict[str, Any] = {**entry.data, **entry.options}
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )

        # Persisted
        self._events: list[dict[str, Any]] = []
        self._observed: set[str] = set()
        self._history_imported = False
        self.enabled = True

        # Runtime
        self.forest: Forest | None = None
        self.habits: list[Habit] = []
        self.is_active: bool | None = None
        self.last_action: dict[str, Any] | None = None
        self._schedule: list[Action] = []
        self._today: date | None = None
        self._last_tick: datetime | None = None
        self._candidate_unsub: CALLBACK_TYPE | None = None
        self._pending_off: Action | None = None
        self._pending_off_since: datetime | None = None
        self._idle_since: datetime | None = None
        self._unsubs: list[Callable[[], None]] = []

    # --- Configuration --------------------------------------------------

    @property
    def source_entity(self) -> str:
        """Entity the pattern is learned from."""
        return self.conf[CONF_SOURCE_ENTITY]

    @property
    def target_entities(self) -> list[str]:
        """Entities that are switched according to the pattern."""
        return list(self.conf.get(CONF_TARGET_ENTITIES) or [])

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
    def _debounce(self) -> float:
        return float(self.conf.get(CONF_DEBOUNCE_SECONDS, DEFAULT_DEBOUNCE_SECONDS))

    @property
    def _window_days(self) -> int:
        return int(self.conf.get(CONF_WINDOW_DAYS, DEFAULT_WINDOW_DAYS))

    @property
    def min_days(self) -> int:
        """Complete days of data needed before the model is trusted."""
        return int(self.conf.get(CONF_MIN_DAYS, DEFAULT_MIN_DAYS))

    # --- Derived state --------------------------------------------------

    @property
    def status(self) -> str:
        """Summarise what the manager is currently doing."""
        if not self.habits:
            return STATUS_LEARNING
        if self._pending_off is not None:
            return STATUS_POSTPONED
        if self.enabled and (
            self._can_control(CONF_CONTROL_ON) or self._can_control(CONF_CONTROL_OFF)
        ):
            return STATUS_CONTROLLING
        return STATUS_READY

    @property
    def days_of_data(self) -> int:
        """Number of complete days the model is built from."""
        today = dt_util.now().date().isoformat()
        return sum(1 for day in self._observed if day < today)

    @property
    def event_count(self) -> int:
        """Number of stored transitions."""
        return len(self._events)

    @property
    def is_busy(self) -> bool:
        """Return True while the guard says the targets are still in use."""
        guard = self.conf.get(CONF_GUARD_ENTITY)
        if guard:
            value = evaluate_state(
                self.hass.states.get(guard),
                above=self.conf.get(CONF_GUARD_ABOVE),
                active_states=GENERIC_ACTIVE_STATES,
            )
            return bool(value)
        # Without an explicit guard the source itself is the guard, unless it
        # is one of the things we switch - then it would always block.
        if self.source_entity in self.target_entities:
            return False
        return bool(self._evaluate_source(self.hass.states.get(self.source_entity)))

    def last_due_action(self, now: datetime) -> Action | None:
        """Return the most recent action that was due."""
        for action in reversed(self._schedule):
            if action.when <= now:
                return action
        return None

    @property
    def predicted_active(self) -> bool | None:
        """Return whether the pattern expects the targets to be on right now."""
        if (action := self.last_due_action(dt_util.now())) is None:
            return None
        return action.kind == KIND_ON

    @property
    def probability(self) -> float | None:
        """Return how likely the model thinks the source is active right now."""
        if self.forest is None:
            return None
        now = dt_util.now()
        return self.forest.probability(
            now.weekday(), (now.hour * 60 + now.minute) // SLOT_MINUTES
        )

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
        if (data := await self._store.async_load()) is not None:
            self._events = list(data.get("events", []))
            self._observed = set(data.get("observed_days", []))
            self._history_imported = data.get("history_imported", False)
            self.enabled = data.get("enabled", True)

        now = dt_util.now()
        if not self._history_imported:
            self._history_imported = True
            if self.conf.get(CONF_IMPORT_HISTORY, True):
                await self._async_import_history(now)

        self._today = now.date()
        self._observed.add(self._today.isoformat())
        self._last_tick = now
        self.is_active = self._evaluate_source(self.hass.states.get(self.source_entity))
        self._prune(now)
        await self._async_rebuild_model(now)
        self._async_schedule_save()

        self._unsubs.append(
            async_track_state_change_event(
                self.hass, [self.source_entity], self._async_source_changed
            )
        )
        self._unsubs.append(
            async_track_time_change(self.hass, self._async_tick, second=0)
        )

    async def async_stop(self) -> None:
        """Stop observing and flush stored data."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._cancel_candidate()
        await self._store.async_save(self._data_to_save())

    async def async_remove_store(self) -> None:
        """Delete everything that was learned."""
        await self._store.async_remove()

    async def async_set_enabled(self, enabled: bool) -> None:
        """Allow or forbid switching the targets."""
        self.enabled = enabled
        if not enabled:
            self._clear_pending_off()
        self._async_schedule_save()
        self._notify()

    async def async_predict_now(self) -> None:
        """Re-evaluate the model and bring the targets in line with it now.

        Useful after a restart, after changing settings, or when the targets
        ended up in the wrong state. Honours the same rules as scheduled
        actions: the automation switch, the guard and the condition.
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
        self._events = []
        self._observed = {now.date().isoformat()}
        self._history_imported = True
        self.last_action = None
        self._clear_pending_off()
        await self._async_rebuild_model(now)
        await self._store.async_save(self._data_to_save())
        self._notify()

    # --- Persistence ----------------------------------------------------

    def _data_to_save(self) -> dict[str, Any]:
        return {
            "events": self._events,
            "observed_days": sorted(self._observed),
            "history_imported": self._history_imported,
            "enabled": self.enabled,
        }

    @callback
    def _async_schedule_save(self) -> None:
        self._store.async_delay_save(self._data_to_save, 10)

    def _prune(self, now: datetime) -> None:
        """Drop everything that fell out of the learning window."""
        first_day = now.date() - timedelta(days=self._window_days)
        cutoff = dt_util.start_of_local_day(first_day).timestamp()
        self._events = [event for event in self._events if event["ts"] >= cutoff]
        self._observed = {
            day for day in self._observed if day >= first_day.isoformat()
        }

    # --- Learning -------------------------------------------------------

    def _evaluate_source(self, state: State | None) -> bool | None:
        if self.conf.get(CONF_SOURCE_MODE) == MODE_NUMERIC:
            return evaluate_state(
                state,
                above=self.conf.get(CONF_ACTIVE_ABOVE, DEFAULT_ACTIVE_ABOVE),
                active_states=frozenset(),
            )
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        active_states = self.conf.get(CONF_ACTIVE_STATES) or FALLBACK_ACTIVE_STATES
        return state.state in active_states

    async def _async_rebuild_model(self, now: datetime) -> None:
        """Retrain the model on complete days and derive the schedule from it."""
        today = now.date()
        observed = [
            day for iso in self._observed if (day := date.fromisoformat(iso)) < today
        ]
        events = sorted(self._events, key=lambda event: event["ts"])
        states = [event["kind"] == KIND_ON for event in events]
        # Training is pure Python number crunching; keep it off the event loop.
        self.forest, self.habits = await self.hass.async_add_executor_job(
            partial(
                learn,
                observed,
                now.tzinfo,
                [event["ts"] for event in events],
                states,
                not states[0] if states else bool(self.is_active),
                today=today,
                half_life_days=self._window_days / 2,
                min_days=self.min_days,
                threshold=self.conf.get(CONF_MIN_CONFIDENCE, DEFAULT_MIN_CONFIDENCE)
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

    async def _async_import_history(self, now: datetime) -> None:
        """Seed the model from what the recorder already knows."""
        if "recorder" not in self.hass.config.components:
            return
        # pylint: disable-next=import-outside-toplevel
        from homeassistant.components.recorder import get_instance, history

        start = dt_util.start_of_local_day(now - timedelta(days=self._window_days))
        try:
            result = await get_instance(self.hass).async_add_executor_job(
                partial(
                    history.state_changes_during_period,
                    self.hass,
                    dt_util.as_utc(start),
                    None,
                    self.source_entity,
                    no_attributes=True,
                    include_start_time_state=True,
                )
            )
        except Exception:  # noqa: BLE001 - history is a bonus, never fatal
            _LOGGER.warning(
                "Could not import history for %s", self.source_entity, exc_info=True
            )
            return

        samples = [
            (max(dt_util.as_local(state.last_changed), start), self._evaluate_source(state))
            for state in result.get(self.source_entity, [])
        ]
        if not samples:
            return

        # Only count days we have seen from their very beginning.
        first = samples[0][0]
        day = first.date() if first <= start else first.date() + timedelta(days=1)
        while day < now.date():
            self._observed.add(day.isoformat())
            day += timedelta(days=1)

        for when, active in extract_transitions(samples, self._debounce):
            self._events.append(
                {"ts": when.timestamp(), "kind": KIND_ON if active else KIND_OFF}
            )
        _LOGGER.debug(
            "Imported %d transitions for %s from the recorder",
            len(self._events),
            self.source_entity,
        )

    # --- Observing ------------------------------------------------------

    @callback
    def _async_source_changed(self, event: Event) -> None:
        value = self._evaluate_source(event.data["new_state"])
        if value is None:
            return
        if self.is_active is None:
            self.is_active = value
            self._notify()
            return
        if value == self.is_active:
            # Flipped back before the debounce time was up.
            self._cancel_candidate()
            return
        if self._candidate_unsub is not None:
            return

        when = event.time_fired
        if self._debounce <= 0:
            self._async_confirm(value, when)
            return

        @callback
        def _confirm(_: datetime) -> None:
            self._candidate_unsub = None
            self._async_confirm(value, when)

        self._candidate_unsub = async_call_later(self.hass, self._debounce, _confirm)

    def _cancel_candidate(self) -> None:
        if self._candidate_unsub is not None:
            self._candidate_unsub()
            self._candidate_unsub = None

    @callback
    def _async_confirm(self, value: bool, when: datetime) -> None:
        """A change of the source held long enough to count."""
        self.is_active = value
        self._record_transition(KIND_ON if value else KIND_OFF, when.timestamp())
        self._async_schedule_save()
        self._notify()

    def _record_transition(self, kind: str, ts: float) -> None:
        last = self.last_action
        if last is not None:
            since_action = ts - last["ts"]
            if last["kind"] == kind and 0 <= since_action <= SELF_TRIGGER_SECONDS:
                # We caused this ourselves. Learning from it would drag the
                # habit towards our own action time, so count it as the habit
                # having happened at its learned time instead.
                if not last["reinforced"]:
                    last["reinforced"] = True
                    learned = last["habit_ts"]
                    latest = max((event["ts"] for event in self._events), default=0)
                    # Fall back to the real time if the learned time would
                    # rewrite history that was recorded since.
                    last["event_ts"] = (
                        learned if learned is not None and learned > latest else ts
                    )
                    self._events.append(
                        {"ts": last["event_ts"], "kind": kind, "auto": True}
                    )
                return
            if (
                last["kind"] != kind
                and last["reinforced"]
                and 0 <= since_action <= REVERT_WINDOW_SECONDS
            ):
                # The user undid our action, so it was not wanted today.
                last["reinforced"] = False
                self._events = [
                    event
                    for event in self._events
                    if not (event.get("auto") and event["ts"] == last["event_ts"])
                ]
                return
        self._events.append({"ts": ts, "kind": kind})

    # --- Acting ---------------------------------------------------------

    def _can_control(self, direction: str) -> bool:
        return bool(self.target_entities) and self.conf.get(direction, True)

    async def _async_tick(self, now: datetime) -> None:
        """Run once a minute: roll over days and execute what is due."""
        now = dt_util.as_local(now)
        if now.date() != self._today:
            self._today = now.date()
            self._observed.add(self._today.isoformat())
            self._prune(now)
            await self._async_rebuild_model(now)
            self._async_schedule_save()

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
            if not self._can_control(CONF_CONTROL_ON):
                return
            if (condition := self.conf.get(CONF_CONDITION_ENTITY)) and not evaluate_state(
                self.hass.states.get(condition),
                above=None,
                active_states=GENERIC_ACTIVE_STATES,
            ):
                _LOGGER.debug("Skipping switch-on, %s is not active", condition)
                return
            self._clear_pending_off()
            await self._async_switch(action)
            return

        if not self._can_control(CONF_CONTROL_OFF):
            return
        if self.is_busy:
            _LOGGER.debug("Postponing switch-off, targets are still in use")
            self._pending_off = action
            self._pending_off_since = now
            self._idle_since = None
            return
        await self._async_switch(action)

    async def _async_check_pending_off(self, now: datetime) -> None:
        """Switch off once the guard has been clear for the grace period."""
        if self._pending_off is None or self._pending_off_since is None:
            return
        if not self.enabled or now - self._pending_off_since > timedelta(
            hours=PENDING_OFF_MAX_HOURS
        ):
            self._clear_pending_off()
            return
        if self.is_busy:
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
            await self._async_switch(action, postponed=True)

    def _clear_pending_off(self) -> None:
        self._pending_off = None
        self._pending_off_since = None
        self._idle_since = None

    async def _async_switch(self, action: Action, *, postponed: bool = False) -> None:
        """Switch the targets that are not yet in the wanted state.

        A postponed switch-off happened late because the targets were really
        in use, so it must not be learned as having happened at the usual time.
        """
        targets = []
        for entity_id in self.target_entities:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                continue
            if (state.state in TARGET_OFF_STATES) == (action.kind == KIND_ON):
                targets.append(entity_id)
        if not targets:
            return

        _LOGGER.debug("Switching %s %s", action.kind, targets)
        self.last_action = {
            "kind": action.kind,
            "ts": dt_util.utcnow().timestamp(),
            "habit_ts": None if postponed else action.habit_time.timestamp(),
            "reinforced": False,
        }
        await self.hass.services.async_call(
            "homeassistant",
            SERVICE_TURN_ON if action.kind == KIND_ON else SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: targets},
            context=Context(),
        )

    @callback
    def _notify(self) -> None:
        async_dispatcher_send(self.hass, signal_update(self.entry.entry_id))
