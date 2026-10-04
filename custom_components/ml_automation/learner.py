"""Pattern learning for ML Automation.

This module is deliberately free of Home Assistant imports and of third party
dependencies, so the learning logic can be reasoned about and tested on its
own, and installs on every Home Assistant system.

The model is a small random forest. Every observed day is cut into slots of a
few minutes, and each slot is labelled "active" or "not active". Decision trees
then learn to predict that label from the time of day and the day of the week.
Each tree is grown on a random selection of the observed days in which recent
days are more likely to be picked, so the forest follows changing routines.

Averaging the trees yields the probability of the entity being active for every
slot of the week. Where that probability crosses the confidence threshold, a
period of activity starts or ends; these crossings are the *habits* the
integration acts on.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
import random

SLOT_MINUTES = 5
SLOTS_PER_DAY = 24 * 60 // SLOT_MINUTES
DAYS_PER_WEEK = 7

KIND_ON = "on"
KIND_OFF = "off"

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# Forest hyperparameters. The problem is small (two features, at most a few
# thousand distinct inputs), so there is little to gain from exposing these.
TREES = 20
MAX_DEPTH = 10

# Predicted periods of activity are tidied up before they become habits:
# interruptions up to this long are bridged, ...
MAX_GAP_SLOTS = 2
# ... and periods shorter than this are dropped.
MIN_RUN_SLOTS = 2

_FEATURE_SLOT = 0
_FEATURE_WEEKDAY = 1
_MIN_GAIN = 1e-9

# slot, weekday, number of days, number of days on which the slot was active
type _Row = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class Habit:
    """A recurring switch-on or switch-off predicted by the model."""

    kind: str
    weekday: int
    minute: int
    confidence: float

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


def day_occupancy(
    day: date,
    tz: tzinfo,
    timestamps: Sequence[float],
    states: Sequence[bool],
    initial: bool,
) -> tuple[bool, ...]:
    """Label every slot of a day as active or not.

    `timestamps` and `states` describe the transitions in ascending order;
    `initial` is the state before the first of them. A slot gets the state
    that held at its midpoint.
    """
    occupancy = []
    for slot in range(SLOTS_PER_DAY):
        minute = slot * SLOT_MINUTES + SLOT_MINUTES // 2
        midpoint = datetime.combine(
            day, time(minute // 60, minute % 60, 30), tzinfo=tz
        ).timestamp()
        index = bisect_right(timestamps, midpoint)
        occupancy.append(states[index - 1] if index else initial)
    return tuple(occupancy)


class _Node:
    """A node of a decision tree; a leaf if it has no children."""

    __slots__ = ("feature", "left", "probability", "right", "threshold")

    def __init__(self, probability: float) -> None:
        self.probability = probability
        self.feature = -1
        self.threshold = 0
        self.left: _Node | None = None
        self.right: _Node | None = None


def _impurity(total: int, active: int) -> float:
    """Gini impurity of a set of samples, weighted by its size."""
    return 2 * active * (total - active) / total


def _grow(
    rows: list[_Row],
    depth: int,
    max_depth: int,
    min_days: int,
    days_per_weekday: Mapping[int, int],
) -> _Node:
    """Grow a classification tree by recursively picking the best split."""
    total = sum(row[2] for row in rows)
    active = sum(row[3] for row in rows)
    node = _Node(active / total)
    if depth >= max_depth or active in (0, total):
        return node

    parent = _impurity(total, active)
    best_gain = _MIN_GAIN
    best: tuple[int, int] | None = None
    for feature in (_FEATURE_SLOT, _FEATURE_WEEKDAY):
        sums: dict[int, list[int]] = {}
        for row in rows:
            if (entry := sums.get(row[feature])) is None:
                sums[row[feature]] = [row[2], row[3]]
            else:
                entry[0] += row[2]
                entry[1] += row[3]
        values = sorted(sums)
        all_days = left_days = 0
        if feature == _FEATURE_WEEKDAY:
            all_days = sum(days_per_weekday[value] for value in values)
        left_total = left_active = 0
        for value in values[:-1]:
            left_total += sums[value][0]
            left_active += sums[value][1]
            if feature == _FEATURE_WEEKDAY:
                # Telling weekdays apart needs evidence on both sides. Without
                # this a single unusual day would become a rule of its own.
                left_days += days_per_weekday[value]
                if left_days < min_days or all_days - left_days < min_days:
                    continue
            gain = (
                parent
                - _impurity(left_total, left_active)
                - _impurity(total - left_total, active - left_active)
            )
            if gain > best_gain:
                best_gain = gain
                best = (feature, value)

    if best is None:
        return node

    node.feature, node.threshold = best
    left = [row for row in rows if row[node.feature] <= node.threshold]
    right = [row for row in rows if row[node.feature] > node.threshold]
    node.left = _grow(left, depth + 1, max_depth, min_days, days_per_weekday)
    node.right = _grow(right, depth + 1, max_depth, min_days, days_per_weekday)
    return node


class Forest:
    """An ensemble of decision trees predicting activity."""

    def __init__(self, trees: list[_Node], days: int) -> None:
        """Initialise the forest."""
        self._trees = trees
        self.days = days

    def __len__(self) -> int:
        """Return the number of trees."""
        return len(self._trees)

    def probability(self, weekday: int, slot: int) -> float:
        """Return how likely the entity is active in a slot of the week."""
        features = (slot, weekday)
        total = 0.0
        for node in self._trees:
            while node.left is not None and node.right is not None:
                if features[node.feature] <= node.threshold:
                    node = node.left
                else:
                    node = node.right
            total += node.probability
        return total / len(self._trees)

    def weekly_profile(self) -> list[list[float]]:
        """Return the probability for every slot of every weekday."""
        return [
            [self.probability(weekday, slot) for slot in range(SLOTS_PER_DAY)]
            for weekday in range(DAYS_PER_WEEK)
        ]


def train_forest(
    occupancy: Mapping[date, Sequence[bool]],
    *,
    today: date,
    half_life_days: float,
    min_days: int,
    trees: int = TREES,
    max_depth: int = MAX_DEPTH,
    seed: int = 0,
) -> Forest | None:
    """Train a forest on the observed days, or return None if there are too few.

    A day's chance of being drawn for a tree halves every `half_life_days`.
    Weekdays are only told apart if at least `min_days` days back both sides.
    """
    days = sorted(occupancy)
    if not days or len(days) < min_days:
        return None

    weights = [0.5 ** ((today - day).days / half_life_days) for day in days]
    days_per_weekday = Counter(day.weekday() for day in days)
    active_slots = {
        day: [slot for slot, active in enumerate(occupancy[day]) if active]
        for day in days
    }

    rng = random.Random(seed)
    roots = []
    for _ in range(trees):
        count = [0] * DAYS_PER_WEEK
        active = [[0] * SLOTS_PER_DAY for _ in range(DAYS_PER_WEEK)]
        for day in rng.choices(days, weights=weights, k=len(days)):
            weekday = day.weekday()
            count[weekday] += 1
            for slot in active_slots[day]:
                active[weekday][slot] += 1
        rows = [
            (slot, weekday, count[weekday], active[weekday][slot])
            for weekday in range(DAYS_PER_WEEK)
            if count[weekday]
            for slot in range(SLOTS_PER_DAY)
        ]
        roots.append(_grow(rows, 0, max_depth, min_days, days_per_weekday))
    return Forest(roots, len(days))


def _flip_short_runs(values: list[bool], value: bool, max_length: int) -> list[bool]:
    """Invert runs of `value` that are at most `max_length` long.

    The list is treated as a circle, because the week wraps around.
    """
    size = len(values)
    boundaries = [index for index in range(size) if values[index] != values[index - 1]]
    result = list(values)
    for position, start in enumerate(boundaries):
        if values[start] != value:
            continue
        end = boundaries[(position + 1) % len(boundaries)]
        length = (end - start) % size
        if length <= max_length:
            for offset in range(length):
                result[(start + offset) % size] = not value
    return result


def find_habits(profile: Sequence[Sequence[float]], threshold: float) -> list[Habit]:
    """Turn a weekly probability profile into switch-on and switch-off habits."""
    flat = [probability for day in profile for probability in day]
    active = [probability >= threshold for probability in flat]
    active = _flip_short_runs(active, False, MAX_GAP_SLOTS)
    active = _flip_short_runs(active, True, MIN_RUN_SLOTS - 1)

    size = len(active)
    habits: list[Habit] = []
    for start in range(size):
        if not active[start] or active[start - 1]:
            continue
        end = start
        total = 0.0
        while active[end % size]:
            total += flat[end % size]
            end += 1
        confidence = total / (end - start)
        for kind, index in ((KIND_ON, start), (KIND_OFF, end % size)):
            habits.append(
                Habit(
                    kind=kind,
                    weekday=index // SLOTS_PER_DAY,
                    minute=index % SLOTS_PER_DAY * SLOT_MINUTES,
                    confidence=confidence,
                )
            )

    habits.sort(key=lambda habit: (habit.weekday, habit.minute, habit.kind))
    return habits


def learn(
    observed_days: Iterable[date],
    tz: tzinfo,
    timestamps: Sequence[float],
    states: Sequence[bool],
    initial: bool,
    *,
    today: date,
    half_life_days: float,
    min_days: int,
    threshold: float,
) -> tuple[Forest | None, list[Habit]]:
    """Train the model on the observed days and derive the habits from it."""
    occupancy = {
        day: day_occupancy(day, tz, timestamps, states, initial)
        for day in observed_days
    }
    forest = train_forest(
        occupancy, today=today, half_life_days=half_life_days, min_days=min_days
    )
    if forest is None:
        return None, []
    return forest, find_habits(forest.weekly_profile(), threshold)


def upcoming_actions(
    habits: Iterable[Habit],
    *,
    start_day: date,
    days: int,
    tz: tzinfo,
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
        for habit in habits:
            if habit.weekday != day.weekday():
                continue
            habit_time = datetime.combine(day, habit.time, tzinfo=tz)
            when = habit_time - lead if habit.kind == KIND_ON else habit_time + off_delay
            actions.append(
                Action(when=when, kind=habit.kind, habit=habit, habit_time=habit_time)
            )
    actions.sort(key=lambda action: action.when)
    return actions
