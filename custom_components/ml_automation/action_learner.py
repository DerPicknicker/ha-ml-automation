"""Bounded, deterministic learning of repeatable Home Assistant actions.

The model consumes observations, not Home Assistant objects. Unknown days are
not opportunities, and today's observations can trigger rules but never train
them. No language model or third-party numerical package is needed.
"""

from __future__ import annotations

from array import array
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
import hashlib
import json
import math
import re
from statistics import median
from typing import Any
from urllib.parse import parse_qsl, urlsplit

WINDOW_DAYS = 28
MAX_OBSERVATIONS = 5000
MAX_ACTIONS = 128
MAX_RULES = 256
MAX_TRIGGERS = 128
MAX_PAYLOAD_BYTES = 4096
MIN_SUPPORT_DAYS = 3
MIN_CONFIDENCE = 0.7
SEQUENCE_SECONDS = 10 * 60
SCHEDULE_WINDOW_MINUTES = 15
RECENT_DAYS = 14
_NAME = re.compile(r"^[a-z0-9_]+$")
_PRIVATE_KEYS = frozenset(
    {
        "password",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "authorization",
        "secret",
        "code",
        "pin",
    }
)


def _repeatable(value: Any, depth: int = 0) -> bool:
    """Reject opaque, private, oversized or templated historical arguments."""
    if depth > 8:
        return False
    if value is None or isinstance(value, (bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, str):
        if len(value) > MAX_PAYLOAD_BYTES or "{{" in value or "{%" in value:
            return False
        if "://" in value:
            try:
                url = urlsplit(value)
                if (
                    url.username
                    or url.password
                    or any(
                        key.lower() in _PRIVATE_KEYS for key, _ in parse_qsl(url.query)
                    )
                ):
                    return False
            except ValueError:
                return False
        return True
    if isinstance(value, (list, tuple)):
        return len(value) <= 64 and all(_repeatable(item, depth + 1) for item in value)
    if isinstance(value, Mapping):
        return len(value) <= 64 and all(
            isinstance(key, str)
            and key.lower() not in _PRIVATE_KEYS
            and _repeatable(item, depth + 1)
            for key, item in value.items()
        )
    return False


def _identifier(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:20]


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """An immutable action with canonical, JSON-compatible call arguments."""

    domain: str
    service: str
    payload: str

    @classmethod
    def from_call(
        cls, domain: str, service: str, data: Mapping[str, Any]
    ) -> ActionSpec | None:
        """Capture a repeatable call without retaining credentials or templates."""
        if (
            not _NAME.fullmatch(domain)
            or not _NAME.fullmatch(service)
            or not _repeatable(data)
        ):
            return None
        try:
            payload = json.dumps(
                dict(data), sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
        except (TypeError, ValueError, OverflowError):
            return None
        if len(payload.encode()) > MAX_PAYLOAD_BYTES:
            return None
        return cls(domain, service, payload)

    @property
    def key(self) -> str:
        """Return a stable identity including the action's parameters."""
        return _identifier(f"{self.domain}.{self.service}:{self.payload}")

    @property
    def data(self) -> dict[str, Any]:
        """Return a fresh copy so an executor cannot alter the learned action."""
        return json.loads(self.payload)

    @property
    def entity_ids(self) -> tuple[str, ...]:
        """Return explicit entity targets, where the action has any."""
        value = self.data.get("entity_id", ())
        if isinstance(value, str):
            value = [item.strip() for item in value.split(",")]
        if not isinstance(value, list):
            return ()
        return tuple(
            item
            for item in value
            if isinstance(item, str)
            and (item == "all" or re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", item))
        )

    def as_dict(self) -> dict[str, Any]:
        """Return the complete executable action."""
        return {"action": f"{self.domain}.{self.service}", "data": self.data}


@dataclass(frozen=True, slots=True)
class Observation:
    """An event, optionally containing a human-initiated action to learn."""

    when: datetime
    trigger: str
    action_key: str | None = None


@dataclass(frozen=True, slots=True)
class ActionRule:
    """A supported schedule or event-to-action relationship."""

    action_key: str
    kind: str
    confidence: float
    support_days: int
    trigger: str | None = None
    weekday: int | None = None
    minute: int | None = None
    delay_seconds: int = 0

    @property
    def key(self) -> str:
        """Return the rule identity independently of its changing evidence."""
        return _identifier(
            f"{self.action_key}:{self.kind}:{self.trigger}:{self.weekday}:{None if self.minute is None else self.minute // 30}"
        )


@dataclass(slots=True)
class _SequenceEvidence:
    """Aggregate matches without retaining an object for every event pair."""

    weight: float = 0.0
    days: int = 0
    delays: array = field(default_factory=lambda: array("I", [0]) * 61)

    def typical_delay(self) -> int:
        """Return the median delay, rounded to a ten-second bucket."""
        count = sum(self.delays)
        left, right = (count - 1) // 2, count // 2
        seen = 0
        middle = []
        for bucket, frequency in enumerate(self.delays):
            if seen <= left < seen + frequency:
                middle.append(bucket * 10)
            if seen <= right < seen + frequency:
                middle.append(bucket * 10)
            seen += frequency
        return sum(middle) // 2


def learn_actions(
    observations: Sequence[Observation],
    known_days: Collection[date] | Mapping[date, int],
    *,
    today: date,
) -> list[ActionRule]:
    """Learn schedules and short sequences from complete, observed days."""
    days = {day for day in known_days if 0 < (today - day).days <= WINDOW_DAYS}
    if len(days) < MIN_SUPPORT_DAYS:
        return []
    coverage = (
        known_days
        if isinstance(known_days, Mapping)
        else {day: (1 << 1440) - 1 for day in days}
    )
    weights = {day: 0.5 ** ((today - day).days / 7) for day in days}
    events = sorted(
        (event for event in observations if event.when.date() in days),
        key=lambda event: event.when.timestamp(),
    )
    schedules: dict[tuple[str, int | None, int], dict[date, list[int]]] = defaultdict(
        lambda: defaultdict(list)
    )
    latest: dict[str, date] = {}
    for event in events:
        if event.action_key is None:
            continue
        day = event.when.date()
        latest[event.action_key] = day
        minute = event.when.hour * 60 + event.when.minute
        for weekday in (None, day.weekday()):
            schedules[(event.action_key, weekday, minute // 30)][day].append(minute)

    rules: list[ActionRule] = []
    for (action, weekday, bucket), occurrences in sorted(
        schedules.items(), key=lambda item: str(item[0])
    ):
        opportunities = {
            day
            for day in days
            if (weekday is None or day.weekday() == weekday)
            and coverage[day] & (((1 << 30) - 1) << (bucket * 30))
            == (((1 << 30) - 1) << (bucket * 30))
        }
        occurrences = {
            day: minutes for day, minutes in occurrences.items() if day in opportunities
        }
        if not opportunities:
            continue
        confidence = math.fsum(weights[day] for day in occurrences) / math.fsum(
            weights[day] for day in opportunities
        )
        if (
            len(occurrences) < MIN_SUPPORT_DAYS
            or confidence < MIN_CONFIDENCE
            or (today - latest[action]).days > RECENT_DAYS
        ):
            continue
        # Repeated presses on one day must not dominate the learned time.
        minute = int(median(median(minutes) for minutes in occurrences.values()))
        rules.append(
            ActionRule(
                action,
                "schedule",
                confidence,
                len(occurrences),
                weekday=weekday,
                minute=minute,
            )
        )

    trigger_days: dict[str, set[date]] = defaultdict(set)
    for event in events:
        trigger_days[event.trigger].add(event.when.date())
    eligible_triggers = set(
        sorted(
            (
                trigger
                for trigger, seen in trigger_days.items()
                if len(seen) >= MIN_SUPPORT_DAYS
            ),
            key=lambda trigger: (-len(trigger_days[trigger]), trigger),
        )[:MAX_TRIGGERS]
    )
    opportunities: dict[str, float] = defaultdict(float)
    pairs: dict[tuple[str, str], _SequenceEvidence] = defaultdict(_SequenceEvidence)
    day_bits = {day: 1 << index for index, day in enumerate(sorted(days))}
    for index, event in enumerate(events):
        if event.trigger not in eligible_triggers:
            continue
        minute = event.when.hour * 60 + event.when.minute
        mask = ((1 << 11) - 1) << minute
        if minute + 10 >= 1440 or coverage[event.when.date()] & mask != mask:
            continue
        opportunities[event.trigger] += weights[event.when.date()]
        found: set[str] = set()
        for following in events[index + 1 : index + 65]:
            delay = following.when.timestamp() - event.when.timestamp()
            if following.when.date() != event.when.date() or delay > SEQUENCE_SECONDS:
                break
            action = following.action_key
            if action is None or action == event.action_key or action in found:
                continue
            found.add(action)
            evidence = pairs[(event.trigger, action)]
            evidence.weight += weights[event.when.date()]
            evidence.days |= day_bits[event.when.date()]
            evidence.delays[min(60, int((delay + 5) // 10))] += 1
    for (trigger, action), evidence in sorted(pairs.items()):
        supporting_days = evidence.days.bit_count()
        confidence = min(1.0, evidence.weight / opportunities[trigger])
        if (
            supporting_days < MIN_SUPPORT_DAYS
            or confidence < MIN_CONFIDENCE
            or (today - latest[action]).days > RECENT_DAYS
        ):
            continue
        rules.append(
            ActionRule(
                action,
                "sequence",
                confidence,
                supporting_days,
                trigger=trigger,
                delay_seconds=evidence.typical_delay(),
            )
        )
    return sorted(
        rules, key=lambda rule: (-rule.confidence, -rule.support_days, rule.key)
    )[:MAX_RULES]
