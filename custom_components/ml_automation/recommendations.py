"""Observe actions, expose recommendations, and execute exact learned calls."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import partial
import hashlib
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_CALL_SERVICE, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Context, Event, HomeAssistant, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import dt as dt_util

from .action_learner import (
    MAX_ACTIONS,
    MAX_OBSERVATIONS,
    MIN_SUPPORT_DAYS,
    SCHEDULE_WINDOW_MINUTES,
    WINDOW_DAYS,
    ActionRule,
    ActionSpec,
    Observation,
    learn_actions,
)
from .const import (
    CONF_CONTROL_ENTITY,
    DOMAIN,
    PENDING_OFF_MAX_HOURS,
    SAVE_DELAY_SECONDS,
    STATUS_POSTPONED,
    TARGET_OFF_STATES,
    signal_update,
)

MAX_SUGGESTIONS = 5
_MAX_CONTEXTS = 512
_FULL_DAY = (1 << 1440) - 1
_MIN_OBSERVED_MINUTES = 1440 * 0.8

type RecommendationConfigEntry = ConfigEntry[RecommendationManager]


@dataclass(frozen=True, slots=True)
class Recommendation:
    """An immutable, expiring proposal shared by every user interface."""

    id: str
    occurrence: str
    rule: ActionRule
    action: ActionSpec
    created: datetime
    expires: datetime
    title: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        """Return the presentation and exact action for dashboards and events."""
        return {
            "suggestion_id": self.id,
            "title": self.title,
            "reason": self.reason,
            "confidence": round(self.rule.confidence * 100),
            "support_days": self.rule.support_days,
            "kind": self.rule.kind,
            "expires_at": self.expires.isoformat(),
            **self.action.as_dict(),
        }


class RecommendationManager:
    """One bounded action recommender for the whole Home Assistant instance."""

    def __init__(self, hass: HomeAssistant, entry: RecommendationConfigEntry) -> None:
        """Initialize local learning, feedback, and explicit action permissions."""
        self.hass = hass
        self.entry = entry
        self._store: Store[dict[str, Any]] = Store(
            hass, 1, f"{DOMAIN}.{entry.entry_id}"
        )
        self.actions: dict[str, ActionSpec] = {}
        self.observations: list[Observation] = []
        self.coverage: dict[str, int] = {}
        self.rules: list[ActionRule] = []
        self.feedback: dict[str, dict[str, int]] = {}
        self.authorized: set[str] = set()
        self.automation_enabled = True
        self.last_result: dict[str, Any] | None = None
        self._handled: dict[str, float] = {}
        self._suggestions: dict[str, Recommendation] = {}
        self._sequences: dict[str, tuple[ActionRule, datetime]] = {}
        self._postponed: dict[str, tuple[ActionRule, datetime]] = {}
        self._blocked_contexts: dict[str, float] = {}
        self._seen_contexts: dict[str, float] = {}
        self._recent_executions: dict[str, tuple[str, float]] = {}
        self._unsubs: list[Callable[[], None]] = []
        self._refresh_lock = asyncio.Lock()
        self._today: date | None = None
        self._running = False
        self._save_scheduled = False
        self._entity_registry = er.async_get(hass)
        self._refresh_task: asyncio.Task[None] | None = None
        self._translations: dict[str, str] = {}

    @property
    def known_days(self) -> set[date]:
        """Return sufficiently observed, completed days; outages stay unknown."""
        today = dt_util.now().date()
        return {
            date.fromisoformat(day)
            for day, mask in self.coverage.items()
            if day < today.isoformat() and mask.bit_count() >= _MIN_OBSERVED_MINUTES
        }

    @property
    def suggestions(self) -> list[Recommendation]:
        """Return active proposals in deterministic display order."""
        now = dt_util.now()
        return sorted(
            (item for item in self._suggestions.values() if item.expires > now),
            key=lambda item: (-item.rule.confidence, item.created, item.id),
        )

    @property
    def suggestion(self) -> Recommendation | None:
        """Return the first active suggestion, if any."""
        return next(iter(self.suggestions), None)

    @property
    def status(self) -> str:
        """Explain whether observations, actions, or repeated patterns are missing."""
        if len(self.known_days) < MIN_SUPPORT_DAYS:
            return "collecting"
        if not self.actions:
            return "no_activity"
        if not self.rules:
            return "no_pattern"
        if self._postponed:
            return STATUS_POSTPONED
        return "controlling" if self.automation_enabled and self.authorized else "ready"

    async def async_start(self) -> None:
        """Restore bounded data and listen to actions and significant changes."""
        self._translations = await async_get_translations(
            self.hass, self.hass.config.language, "exceptions", {DOMAIN}
        )
        data = await self._store.async_load() or {}
        if data:
            for item in data.get("actions", [])[:MAX_ACTIONS]:
                if action := ActionSpec.from_call(
                    item["domain"], item["service"], item["data"]
                ):
                    self.actions[action.key] = action
            self.observations = [
                Observation(
                    datetime.fromisoformat(item["when"]),
                    item["trigger"],
                    item.get("action_key"),
                )
                for item in data.get("observations", [])[-MAX_OBSERVATIONS:]
            ]
            self.coverage = {
                day: int(mask, 16) & _FULL_DAY
                for day, mask in data.get("coverage", {}).items()
            }
            self.feedback = data.get("feedback", {})
            self.authorized = set(data.get("authorized", [])) & self.actions.keys()
            self.automation_enabled = data.get("automation_enabled", True)
            self._handled = data.get("handled", {})
            self._recent_executions = {
                key: (item[0], item[1])
                for key, item in data.get("recent_executions", {}).items()
                if key in self.actions
            }
        self._running = True
        self._today = dt_util.now().date()
        self._prune(dt_util.now())
        await self.async_learn()
        rules_by_key = {rule.key: rule for rule in self.rules}
        self._postponed = {
            occurrence: (rules_by_key[item[0]], datetime.fromisoformat(item[1]))
            for occurrence, item in data.get("postponed", {}).items()
            if item[0] in rules_by_key
        }
        self._unsubs = [
            self.hass.bus.async_listen(EVENT_CALL_SERVICE, self._async_observe_call),
            self.hass.bus.async_listen("state_changed", self._async_observe_state),
            self.hass.bus.async_listen(
                "automation_triggered", self._async_block_automation
            ),
            self.hass.bus.async_listen("script_started", self._async_block_automation),
            async_track_time_change(self.hass, self._async_tick, second=0),
        ]
        # Register sentence triggers without writing into the user's config.
        from .recommendation_assist import async_register_sentences

        self._unsubs.extend(async_register_sentences(self.hass, self))
        await self.async_refresh()

    async def async_stop(self) -> None:
        """Detach listeners and flush learning and feedback on unload."""
        self._running = False
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        async with self._refresh_lock:
            await self._store.async_save(self._data_to_save())

    async def async_remove_store(self) -> None:
        """Remove this entry's learned history."""
        await self._store.async_remove()

    def _data_to_save(self) -> dict[str, Any]:
        self._save_scheduled = False
        return {
            "actions": [
                {
                    "domain": action.domain,
                    "service": action.service,
                    "data": action.data,
                }
                for action in self.actions.values()
            ],
            "observations": [
                {
                    "when": event.when.isoformat(),
                    "trigger": event.trigger,
                    "action_key": event.action_key,
                }
                for event in self.observations
            ],
            "coverage": {day: hex(mask) for day, mask in self.coverage.items()},
            "feedback": self.feedback,
            "authorized": sorted(self.authorized),
            "automation_enabled": self.automation_enabled,
            "handled": self._handled,
            "recent_executions": self._recent_executions,
            "postponed": {
                occurrence: (rule.key, when.isoformat())
                for occurrence, (rule, when) in self._postponed.items()
            },
        }

    @callback
    def _save(self) -> None:
        if self._running and not self._save_scheduled:
            self._save_scheduled = True
            self._store.async_delay_save(self._data_to_save, SAVE_DELAY_SECONDS)

    @callback
    def _notify(self) -> None:
        async_dispatcher_send(self.hass, signal_update(self.entry.entry_id))

    def _prune(self, now: datetime) -> None:
        cutoff = now.date() - timedelta(days=WINDOW_DAYS)
        self.observations = sorted(
            [event for event in self.observations if event.when.date() >= cutoff],
            key=lambda event: event.when.timestamp(),
        )[-MAX_OBSERVATIONS:]
        self.coverage = {
            day: mask
            for day, mask in self.coverage.items()
            if day >= cutoff.isoformat()
        }
        used = {event.action_key for event in self.observations}
        self.actions = {
            key: action
            for key, action in self.actions.items()
            if key in used or key in self.authorized
        }
        self._handled = {
            key: timestamp
            for key, timestamp in self._handled.items()
            if timestamp >= now.timestamp() - WINDOW_DAYS * 86400
        }
        for cache in (self._blocked_contexts, self._seen_contexts):
            for key in list(cache):
                if cache[key] < now.timestamp() - 3600:
                    del cache[key]

    def _remember_context(self, cache: dict[str, float], context: Context) -> None:
        cache[context.id] = dt_util.utcnow().timestamp()
        while len(cache) > _MAX_CONTEXTS:
            del cache[next(iter(cache))]

    def _blocked(self, context: Context) -> bool:
        if (
            context.id in self._blocked_contexts
            or context.parent_id in self._blocked_contexts
        ):
            self._remember_context(self._blocked_contexts, context)
            return True
        return False

    @callback
    def _async_block_automation(self, event: Event) -> None:
        self._remember_context(self._blocked_contexts, event.context)

    @callback
    def _async_observe_call(self, event: Event) -> None:
        context = event.context
        if self._blocked(context):
            return
        # A service event says what was requested, not who wanted it. Only
        # direct, attributed user requests become labels; children are effects.
        if context.user_id is None or context.parent_id is not None:
            self._remember_context(self._blocked_contexts, context)
            return
        if event.data["domain"] in (DOMAIN, "conversation", "assist_pipeline"):
            return
        if context.id in self._seen_contexts:
            return
        self._remember_context(self._seen_contexts, context)
        action = ActionSpec.from_call(
            event.data["domain"], event.data["service"], event.data["service_data"]
        )
        if action is None:
            return
        if any(
            (target := self._entity_registry.async_get(entity_id)) is not None
            and target.platform == DOMAIN
            for entity_id in action.entity_ids
        ):
            return
        now = dt_util.now()
        self._record_undo(action, now)
        self._cancel_opposite_postponed(action)
        if action.key not in self.actions and len(self.actions) >= MAX_ACTIONS:
            return
        self.actions[action.key] = action
        self._observe(Observation(now, f"action:{action.key}", action.key))
        # Performing a proposed action manually also fulfils that occurrence.
        for item in list(self._suggestions.values()):
            if item.action.key == action.key:
                self._consume(item)
        self._fulfill(action.key, now)
        self._notify()

    def _record_undo(self, action: ActionSpec, now: datetime) -> None:
        """Treat a direct reversal or changed parameters as corrective feedback."""
        opposites = {
            ("turn_on", "turn_off"),
            ("turn_off", "turn_on"),
            ("open_cover", "close_cover"),
            ("close_cover", "open_cover"),
            ("media_play", "media_pause"),
            ("media_pause", "media_play"),
        }
        for key, (rule_key, timestamp) in list(self._recent_executions.items()):
            if now.timestamp() - timestamp > 5 * 60:
                del self._recent_executions[key]
                continue
            previous = self.actions.get(key)
            if (
                previous is None
                or previous.key == action.key
                or not set(previous.entity_ids).intersection(action.entity_ids)
            ):
                continue
            if (previous.service, action.service) not in opposites and not (
                previous.domain == action.domain and previous.service == action.service
            ):
                continue
            feedback = self.feedback.setdefault(rule_key, {})
            feedback["undone"] = feedback.get("undone", 0) + 1
            self.authorized.discard(key)
            del self._recent_executions[key]
            self._save()

    @callback
    def _async_observe_state(self, event: Event) -> None:
        if self._blocked(event.context):
            return
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if (
            old is None
            or new is None
            or old.state == new.state
            or new.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)
        ):
            return
        entity_id = event.data["entity_id"]
        registry_entry = self._entity_registry.async_get(entity_id)
        if registry_entry is not None and registry_entry.platform == DOMAIN:
            return
        if entity_id.startswith("event."):
            event_type = new.attributes.get("event_type")
            if isinstance(event_type, str) and len(event_type) <= 64:
                self._observe(
                    Observation(dt_util.now(), f"event:{entity_id}:{event_type}")
                )
            return
        # Continuous measurements and startup snapshots would drown useful
        # sequences; parameter changes are captured in the action calls.
        try:
            float(new.state)
            return
        except ValueError:
            pass
        if len(new.state) > 64:
            return
        if (
            new.state[:4].isdigit()
            and "T" in new.state
            and dt_util.parse_datetime(new.state) is not None
        ):
            return
        self._observe(Observation(dt_util.now(), f"state:{entity_id}:{new.state}"))

    def _observe(self, observation: Observation) -> None:
        self.observations.append(observation)
        if len(self.observations) > MAX_OBSERVATIONS:
            del self.observations[: len(self.observations) - MAX_OBSERVATIONS]
            # Evicting events also removes evidence of absence in that window.
            oldest = self.observations[0].when
            day = oldest.date().isoformat()
            self.coverage = {
                key: mask for key, mask in self.coverage.items() if key >= day
            }
            if day in self.coverage:
                self.coverage[day] &= _FULL_DAY ^ (
                    (1 << (oldest.hour * 60 + oldest.minute + 1)) - 1
                )
        for rule in self.rules:
            if rule.trigger == observation.trigger:
                occurrence = f"{rule.key}:{observation.when.timestamp()}"
                self._sequences[occurrence] = (
                    rule,
                    observation.when + timedelta(seconds=rule.delay_seconds),
                )
        while len(self._sequences) > 64:
            del self._sequences[next(iter(self._sequences))]
        self._save()
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = self.hass.async_create_task(self.async_refresh())

    async def async_learn(self) -> None:
        """Build rules from completed days off the event loop."""
        async with self._refresh_lock:
            self._prune(dt_util.now())
            self.rules = await self.hass.async_add_executor_job(
                partial(
                    learn_actions,
                    list(self.observations),
                    {day: self.coverage[day.isoformat()] for day in self.known_days},
                    today=dt_util.now().date(),
                )
            )
            keys = {rule.key for rule in self.rules}
            self.feedback = {
                key: value for key, value in self.feedback.items() if key in keys
            }
            self._notify()

    async def _async_tick(self, now: datetime) -> None:
        if not self._running:
            return
        now = dt_util.as_local(now)
        minute = now.hour * 60 + now.minute
        day = now.date().isoformat()
        self.coverage[day] = self.coverage.get(day, 0) | (1 << minute)
        if now.date() != self._today:
            self._today = now.date()
            await self.async_learn()
        self._save()
        await self.async_refresh()

    def _available(self, action: ActionSpec) -> bool:
        if not self.hass.services.has_service(action.domain, action.service):
            return False
        states = [
            self.hass.states.get(entity)
            for entity in action.entity_ids
            if entity != "all"
        ]
        if any(
            state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)
            for state in states
        ):
            return False
        if states and action.service == "turn_off":
            if all(state.state in TARGET_OFF_STATES for state in states):
                return False
        if (
            states
            and action.service == "turn_on"
            and not (
                action.data.keys()
                - {"entity_id", "device_id", "area_id", "floor_id", "label_id"}
            )
        ):
            if all(state.state == "on" for state in states):
                return False
        return True

    def _in_use(self, action: ActionSpec) -> bool:
        """Reuse existing switching patterns' evidence and idle grace periods."""
        if action.service not in ("turn_off", "close_cover"):
            return False
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            manager = getattr(entry, "runtime_data", None)
            if (
                entry.data.get(CONF_CONTROL_ENTITY) in action.entity_ids
                and manager is not None
                and (manager.in_use or manager.status == STATUS_POSTPONED)
            ):
                return True
        return False

    def _postpone(self, occurrence: str, rule: ActionRule, when: datetime) -> None:
        """Keep one pending stop per exact action, even after repeated triggers."""
        for pending, (previous, _when) in self._postponed.items():
            if previous.action_key == rule.action_key:
                if pending != occurrence:
                    self._mark_handled(occurrence)
                    self._sequences.pop(occurrence, None)
                return
        if len(self._postponed) < MAX_ACTIONS:
            self._postponed[occurrence] = (rule, when)
            self._save()

    def _cancel_opposite_postponed(self, action: ActionSpec) -> None:
        """A new start supersedes an old postponed stop on the same device."""
        opposite = {"turn_on": "turn_off", "open_cover": "close_cover"}.get(
            action.service
        )
        if opposite is None:
            return
        for occurrence, (rule, _when) in list(self._postponed.items()):
            pending = self.actions.get(rule.action_key)
            if (
                pending is not None
                and pending.service == opposite
                and set(pending.entity_ids).intersection(action.entity_ids)
            ):
                del self._postponed[occurrence]
                self._mark_handled(occurrence)
        self._save()

    def _text(self, key: str, **values: Any) -> str:
        return self._translations.get(
            f"component.{DOMAIN}.exceptions.{key}.message", key
        ).format(**values)

    def _title(self, action: ActionSpec) -> str:
        names = [
            state.name if (state := self.hass.states.get(entity)) else entity
            for entity in action.entity_ids
        ]
        label_key = f"label_{action.service}"
        label = self._text(label_key)
        if label == label_key:
            label = action.service.replace("_", " ").capitalize()
        return self._text(
            "action_title", action=label, target=", ".join(names) or action.domain
        )[:255]

    def _proposal(
        self, rule: ActionRule, occurrence: str, when: datetime
    ) -> Recommendation | None:
        action = self.actions.get(rule.action_key)
        feedback = self.feedback.get(rule.key, {})
        if (
            action is None
            or not self._available(action)
            or feedback.get("dismissed", 0) + feedback.get("undone", 0)
            >= max(3, feedback.get("accepted", 0) + 1)
        ):
            return None
        if rule.kind == "sequence":
            assert rule.trigger is not None
            trigger = rule.trigger
            if trigger.startswith("state:"):
                _kind, entity_id, state_value = trigger.split(":", 2)
                state = self.hass.states.get(entity_id)
                trigger = self._text(
                    "state_trigger",
                    target=state.name if state else entity_id,
                    state=state_value,
                )
            elif trigger.startswith("event:"):
                _kind, entity_id, event_type = trigger.split(":", 2)
                state = self.hass.states.get(entity_id)
                trigger = self._text(
                    "state_trigger",
                    target=state.name if state else entity_id,
                    state=event_type,
                )
            elif trigger.startswith("action:") and (
                prior := self.actions.get(trigger.removeprefix("action:"))
            ):
                trigger = self._title(prior)
            reason = self._text(
                "sequence_reason", trigger=trigger, days=rule.support_days
            )
        else:
            reason = self._text("schedule_reason", days=rule.support_days)
        # A released, postponed occurrence gets a new acceptance token. An old
        # dashboard or voice token must never accept a renewed proposal.
        identifier = hashlib.sha256(
            f"{occurrence}:{when.timestamp()}".encode()
        ).hexdigest()[:20]
        return Recommendation(
            identifier,
            occurrence,
            rule,
            action,
            when,
            when + timedelta(minutes=SCHEDULE_WINDOW_MINUTES),
            self._title(action),
            reason,
        )

    async def async_refresh(self) -> None:
        """Expire proposals, evaluate due rules, and act only with permission."""
        async with self._refresh_lock:
            if not self._running:
                return
            now = dt_util.now()
            for item in list(self._suggestions.values()):
                if self._in_use(item.action):
                    self._postpone(item.occurrence, item.rule, item.created)
                    self._suggestions.pop(item.id)
                    self._save()
                elif item.expires <= now or not self._available(item.action):
                    self._consume(item, fulfilled=False)
            due = {**self._sequences, **self._postponed}
            for rule in self.rules:
                if rule.kind != "schedule" or (
                    rule.weekday is not None and rule.weekday != now.weekday()
                ):
                    continue
                assert rule.minute is not None
                when = now.replace(
                    hour=rule.minute // 60,
                    minute=rule.minute % 60,
                    second=0,
                    microsecond=0,
                )
                due[f"{rule.key}:{now.date()}"] = (rule, when)
            for occurrence, (rule, when) in due.items():
                postponed = occurrence in self._postponed
                deadline = when + (
                    timedelta(hours=PENDING_OFF_MAX_HOURS)
                    if postponed
                    else timedelta(minutes=SCHEDULE_WINDOW_MINUTES)
                )
                if now >= deadline:
                    self._sequences.pop(occurrence, None)
                    self._postponed.pop(occurrence, None)
                    continue
                if now < when or occurrence in self._handled:
                    continue
                action = self.actions.get(rule.action_key)
                if action is not None and self._in_use(action):
                    if not postponed:
                        self._postpone(occurrence, rule, when)
                    continue
                if any(
                    item.action.key == rule.action_key
                    for item in self._suggestions.values()
                ):
                    continue
                proposal = self._proposal(rule, occurrence, now if postponed else when)
                if proposal is None:
                    self._postponed.pop(occurrence, None)
                    continue
                if self.automation_enabled and proposal.action.key in self.authorized:
                    try:
                        await self._execute(proposal, Context(), automatic=True)
                    except HomeAssistantError:
                        # One failing device must not stop the other suggestions.
                        pass
                elif (
                    proposal.id not in self._suggestions
                    and len(self._suggestions) < MAX_SUGGESTIONS
                ):
                    self._suggestions[proposal.id] = proposal
                    self.hass.bus.async_fire(
                        f"{DOMAIN}_action_suggestion",
                        {"entry_id": self.entry.entry_id, **proposal.as_dict()},
                    )
                    self._postponed.pop(occurrence, None)
            self._notify()

    def _fulfill(self, action_key: str, now: datetime) -> None:
        """One action fulfils overlapping predictions and already-seen triggers."""
        for occurrence, (rule, _when) in list(self._sequences.items()):
            if rule.action_key == action_key:
                self._mark_handled(occurrence)
                del self._sequences[occurrence]
        for occurrence, (rule, _when) in list(self._postponed.items()):
            if rule.action_key == action_key:
                self._mark_handled(occurrence)
                del self._postponed[occurrence]
        minute = now.hour * 60 + now.minute
        for rule in self.rules:
            if (
                rule.action_key == action_key
                and rule.kind == "schedule"
                and rule.weekday in (None, now.weekday())
                and rule.minute is not None
                and abs(minute - rule.minute) <= SCHEDULE_WINDOW_MINUTES
            ):
                self._mark_handled(f"{rule.key}:{now.date()}")

    def _mark_handled(self, occurrence: str) -> None:
        self._handled[occurrence] = dt_util.utcnow().timestamp()
        while len(self._handled) > MAX_OBSERVATIONS:
            del self._handled[next(iter(self._handled))]

    def _consume(self, proposal: Recommendation, *, fulfilled: bool = True) -> None:
        self._suggestions.pop(proposal.id, None)
        self._sequences.pop(proposal.occurrence, None)
        self._postponed.pop(proposal.occurrence, None)
        self._mark_handled(proposal.occurrence)
        if fulfilled:
            self._fulfill(proposal.action.key, dt_util.now())
        self._save()

    def _get(self, suggestion_id: str) -> Recommendation:
        proposal = self._suggestions.get(suggestion_id)
        if not self._running or proposal is None or proposal.expires <= dt_util.now():
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="suggestion_expired"
            )
        if not self._available(proposal.action) or self._in_use(proposal.action):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="action_unavailable"
            )
        return proposal

    async def async_apply(
        self, suggestion_id: str, context: Context, *, authorize: bool = False
    ) -> None:
        """Execute a specific active suggestion, optionally allowing future repeats."""
        async with self._refresh_lock:
            await self._execute(self._get(suggestion_id), context, authorize=authorize)
            self._notify()

    async def _execute(
        self,
        proposal: Recommendation,
        context: Context,
        *,
        authorize: bool = False,
        automatic: bool = False,
    ) -> None:
        # Consume before awaiting, so concurrent clicks cannot repeat an action.
        self._consume(proposal)
        execution_context = Context(parent_id=context.id, user_id=context.user_id)
        self._remember_context(self._blocked_contexts, execution_context)
        self.last_result = {
            "suggestion_id": proposal.id,
            "action": f"{proposal.action.domain}.{proposal.action.service}",
            "automatic": automatic,
            "status": "requested",
        }
        self._recent_executions[proposal.action.key] = (
            proposal.rule.key,
            dt_util.utcnow().timestamp(),
        )
        while len(self._recent_executions) > MAX_ACTIONS:
            del self._recent_executions[next(iter(self._recent_executions))]
        try:
            # Persist consumption before issuing a command. A restart after
            # acceptance must not replay the same occurrence.
            await self._store.async_save(self._data_to_save())
            if proposal.action.key not in self._recent_executions:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="action_unavailable"
                )
            await self.hass.services.async_call(
                proposal.action.domain,
                proposal.action.service,
                proposal.action.data,
                blocking=True,
                context=execution_context,
                return_response=self.hass.services.supports_response(
                    proposal.action.domain, proposal.action.service
                )
                is SupportsResponse.ONLY,
            )
        except Exception as err:
            self._recent_executions.pop(proposal.action.key, None)
            self.last_result["status"] = "failed"
            self._notify()
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="action_failed"
            ) from err
        # Completion of a service handler is not proof of a physical outcome.
        self.last_result["status"] = "accepted"
        self._cancel_opposite_postponed(proposal.action)
        if not automatic:
            feedback = self.feedback.setdefault(proposal.rule.key, {})
            feedback["accepted"] = feedback.get("accepted", 0) + 1
        if authorize and proposal.action.key in self._recent_executions:
            self.authorized.add(proposal.action.key)
        self._save()
        if authorize:
            await self._store.async_save(self._data_to_save())

    async def async_dismiss(self, suggestion_id: str) -> None:
        """Dismiss an occurrence and reduce repeated unwanted recommendations."""
        async with self._refresh_lock:
            proposal = self._get(suggestion_id)
            self._consume(proposal)
            feedback = self.feedback.setdefault(proposal.rule.key, {})
            feedback["dismissed"] = feedback.get("dismissed", 0) + 1
            self._save()
            await self._store.async_save(self._data_to_save())
            self._notify()

    async def async_set_enabled(self, enabled: bool) -> None:
        """Enable or pause previously authorized automatic actions."""
        async with self._refresh_lock:
            self.automation_enabled = enabled
            self._save()
            await self._store.async_save(self._data_to_save())
            self._notify()

    async def async_revoke(self, action_key: str) -> None:
        """Revoke automatic execution of an exact action and its parameters."""
        async with self._refresh_lock:
            self.authorized.discard(action_key)
            self._save()
            await self._store.async_save(self._data_to_save())
            self._notify()

    async def async_relearn(self) -> None:
        """Clear learned actions, history, feedback, and automatic permissions."""
        async with self._refresh_lock:
            self.actions.clear()
            self.observations.clear()
            self.coverage.clear()
            self.rules.clear()
            self.feedback.clear()
            self.authorized.clear()
            self._suggestions.clear()
            self._sequences.clear()
            self._postponed.clear()
            self._handled.clear()
            self._recent_executions.clear()
            self._save()
            await self._store.async_save(self._data_to_save())
            self._notify()
