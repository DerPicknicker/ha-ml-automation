"""Pattern learning for ML Automation.

This module is deliberately free of Home Assistant imports so the learning
logic can be reasoned about and tested on its own.

The model is simple and explainable: every observed on/off transition is
reduced to "minute of the day" on a given calendar day. For each group of days
(all days, workdays/weekend, or each weekday) we look for a time window that
contains a transition on most of the observed days. Such a window is a *habit*,
and its median time is what the integration acts on.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from statistics import median

MINUTES_PER_DAY = 24 * 60

KIND_ON = "on"
KIND_OFF = "off"

DAY_MODE_ALL = "all"
DAY_MODE_WORKDAY_WEEKEND = "workday_weekend"
DAY_MODE_WEEKDAY = "weekday"
DAY_MODES = [DAY_MODE_WORKDAY_WEEKEND, DAY_MODE_ALL, DAY_MODE_WEEKDAY]

GROUP_ALL = "all"
GROUP_WORKDAY = "workday"
GROUP_WEEKEND = "weekend"
WEEKDAY_GROUPS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass(frozen=True, slots=True)
class Transition:
    """A single observed on/off transition, in local time."""

    day: date
    minute: int
    kind: str


@dataclass(frozen=True, slots=True)
class Habit:
    """A recurring transition found in the data."""

    kind: str
    group: str
    minute: int
    days: int
    observed: int

    @property
    def confidence(self) -> float:
        """Share of the observed days on which this habit occurred."""
        return self.days / self.observed if self.observed else 0.0

    @property
    def time(self) -> time:
        """Return the habit as a wall clock time."""
        return time(self.minute // 60, self.minute % 60)


@dataclass(frozen=True, slots=True)
class Action:
    """A point in time at which a habit should be acted on."""

    when: datetime
    kind: str
    habit: Habit
    habit_time: datetime


def group_for(day: date, day_mode: str) -> str:
    """Return the day group a calendar day belongs to."""
    if day_mode == DAY_MODE_WEEKDAY:
        return WEEKDAY_GROUPS[day.weekday()]
    if day_mode == DAY_MODE_WORKDAY_WEEKEND:
        return GROUP_WORKDAY if day.weekday() < 5 else GROUP_WEEKEND
    return GROUP_ALL


def extract_transitions(
    samples: Sequence[tuple[datetime, bool | None]], debounce_seconds: float
) -> list[tuple[datetime, bool]]:
    """Turn a series of (time, active) samples into debounced transitions.

    A change only counts if the new value held for at least `debounce_seconds`.
    Samples with an unknown value (None) are skipped. The value of the first
    sample is the starting point and does not produce a transition.
    """
    runs: list[tuple[datetime, bool]] = []
    for when, value in samples:
        if value is None:
            continue
        if not runs or runs[-1][1] != value:
            runs.append((when, value))

    if not runs:
        return []

    transitions: list[tuple[datetime, bool]] = []
    confirmed = runs[0][1]
    for index, (start, value) in enumerate(runs[1:], start=1):
        if value == confirmed:
            continue
        if index + 1 < len(runs):
            duration = (runs[index + 1][0] - start).total_seconds()
            if duration < debounce_seconds:
                continue
        confirmed = value
        transitions.append((start, value))
    return transitions


def _best_window(
    points: list[tuple[int, date]], width: int
) -> tuple[int, dict[date, list[int]]] | None:
    """Find the window of `width` minutes covering the most distinct days.

    The day is treated as a circle so habits around midnight are found too.
    Returns the window start and, per day, the offsets of its points from that
    start. Ties are broken in favour of the window holding the most points,
    then the tightest, so one habit is not split into several.
    """
    best: tuple[int, dict[date, list[int]]] | None = None
    best_key: tuple[int, int, int] | None = None
    for start in sorted({minute for minute, _ in points}):
        per_day: dict[date, list[int]] = defaultdict(list)
        for minute, day in points:
            offset = (minute - start) % MINUTES_PER_DAY
            if offset <= width:
                per_day[day].append(offset)
        spread = max(max(offsets) for offsets in per_day.values())
        count = sum(len(offsets) for offsets in per_day.values())
        key = (len(per_day), count, -spread)
        if best_key is None or key > best_key:
            best_key = key
            best = (start, dict(per_day))
    return best


def learn_habits(
    transitions: Iterable[Transition],
    observed_days: Iterable[date],
    *,
    day_mode: str,
    tolerance_minutes: int,
    min_occurrences: int,
    min_confidence: float,
) -> list[Habit]:
    """Find recurring transitions.

    `observed_days` are the days on which we were watching at all; they are
    the denominator for a habit's confidence. A habit needs to show up on at
    least `min_occurrences` days and on at least `min_confidence` (0..1) of
    the observed days of its group, within +/- `tolerance_minutes`.
    """
    observed_per_group: dict[str, int] = defaultdict(int)
    observed = set(observed_days)
    for day in observed:
        observed_per_group[group_for(day, day_mode)] += 1

    points: dict[tuple[str, str], list[tuple[int, date]]] = defaultdict(list)
    for transition in transitions:
        if transition.day not in observed:
            continue
        group = group_for(transition.day, day_mode)
        points[(group, transition.kind)].append((transition.minute, transition.day))

    width = min(2 * tolerance_minutes, MINUTES_PER_DAY - 1)
    habits: list[Habit] = []
    for (group, kind), remaining in points.items():
        observed_count = observed_per_group[group]
        if observed_count < min_occurrences:
            continue
        while remaining:
            window = _best_window(remaining, width)
            if window is None:
                break
            start, per_day = window
            day_count = len(per_day)
            if day_count < min_occurrences:
                break
            if day_count / observed_count < min_confidence:
                break
            # One representative per day: the first switch-on, the last
            # switch-off. That keeps a flickering device from skewing the time.
            pick = min if kind == KIND_ON else max
            offset = round(median(pick(offsets) for offsets in per_day.values()))
            habits.append(
                Habit(
                    kind=kind,
                    group=group,
                    minute=(start + offset) % MINUTES_PER_DAY,
                    days=day_count,
                    observed=observed_count,
                )
            )
            remaining = [
                (minute, day)
                for minute, day in remaining
                if (minute - start) % MINUTES_PER_DAY > width
            ]

    habits.sort(key=lambda habit: (habit.group, habit.minute, habit.kind))
    return habits


def upcoming_actions(
    habits: Iterable[Habit],
    *,
    start_day: date,
    days: int,
    tz: tzinfo,
    day_mode: str,
    lead: timedelta,
    off_delay: timedelta,
) -> list[Action]:
    """Project habits onto the calendar.

    Switch-on habits are moved earlier by `lead`, switch-off habits later by
    `off_delay`. The result covers `days` days from `start_day`, sorted by time.
    """
    habits = list(habits)
    actions: list[Action] = []
    for offset in range(days):
        day = start_day + timedelta(days=offset)
        group = group_for(day, day_mode)
        for habit in habits:
            if habit.group != group:
                continue
            habit_time = datetime.combine(day, habit.time, tzinfo=tz)
            when = habit_time - lead if habit.kind == KIND_ON else habit_time + off_delay
            actions.append(
                Action(when=when, kind=habit.kind, habit=habit, habit_time=habit_time)
            )
    actions.sort(key=lambda action: action.when)
    return actions
