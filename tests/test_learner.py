"""Tests for the pure learning logic."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from custom_components.ml_automation.learner import (
    DAY_MODE_ALL,
    DAY_MODE_WEEKDAY,
    DAY_MODE_WORKDAY_WEEKEND,
    KIND_OFF,
    KIND_ON,
    Habit,
    Transition,
    extract_transitions,
    learn_habits,
    upcoming_actions,
)

MONDAY = date(2026, 3, 2)


def _days(count: int) -> list[date]:
    return [MONDAY + timedelta(days=offset) for offset in range(count)]


def _learn(transitions, observed, **overrides):
    params = {
        "day_mode": DAY_MODE_ALL,
        "tolerance_minutes": 45,
        "min_occurrences": 3,
        "min_confidence": 0.6,
    }
    return learn_habits(transitions, observed, **{**params, **overrides})


def test_learns_daily_on_and_off() -> None:
    days = _days(7)
    jitter = [-10, 5, 0, 12, -4, 8, -2]
    transitions = []
    for day, delta in zip(days, jitter, strict=True):
        transitions.append(Transition(day, 18 * 60 + delta, KIND_ON))
        transitions.append(Transition(day, 19 * 60 + delta, KIND_OFF))

    habits = _learn(transitions, days)

    assert [(habit.kind, habit.minute) for habit in habits] == [
        (KIND_ON, 18 * 60),
        (KIND_OFF, 19 * 60),
    ]
    assert all(habit.confidence == 1.0 for habit in habits)


def test_needs_enough_days() -> None:
    days = _days(2)
    transitions = [Transition(day, 18 * 60, KIND_ON) for day in days]

    assert _learn(transitions, days) == []


def test_ignores_irregular_behaviour() -> None:
    days = _days(10)
    # Only on 3 of 10 days: seen often enough, but not regular enough.
    transitions = [Transition(day, 18 * 60, KIND_ON) for day in days[:3]]

    assert _learn(transitions, days) == []
    assert len(_learn(transitions, days, min_confidence=0.3)) == 1


def test_scattered_times_are_not_a_habit() -> None:
    days = _days(6)
    minutes = [6 * 60, 9 * 60, 12 * 60, 15 * 60, 18 * 60, 21 * 60]
    transitions = [
        Transition(day, minute, KIND_ON)
        for day, minute in zip(days, minutes, strict=True)
    ]

    assert _learn(transitions, days) == []


def test_two_habits_per_day() -> None:
    days = _days(5)
    transitions = []
    for day in days:
        transitions.append(Transition(day, 7 * 60, KIND_ON))
        transitions.append(Transition(day, 20 * 60, KIND_ON))

    habits = _learn(transitions, days)

    assert [habit.minute for habit in habits] == [7 * 60, 20 * 60]


def test_habit_around_midnight() -> None:
    days = _days(4)
    minutes = [23 * 60 + 50, 5, 23 * 60 + 55, 10]
    transitions = [
        Transition(day, minute, KIND_OFF)
        for day, minute in zip(days, minutes, strict=True)
    ]

    habits = _learn(transitions, days)

    assert len(habits) == 1
    # Median of 23:50, 23:55, 00:05, 00:10
    assert habits[0].minute == 0


def test_flicker_uses_first_on_and_last_off() -> None:
    days = _days(3)
    transitions = []
    for day in days:
        transitions += [
            Transition(day, 18 * 60, KIND_ON),
            Transition(day, 18 * 60 + 20, KIND_ON),
            Transition(day, 19 * 60, KIND_OFF),
            Transition(day, 19 * 60 + 20, KIND_OFF),
        ]

    habits = {habit.kind: habit.minute for habit in _learn(transitions, days)}

    assert habits == {KIND_ON: 18 * 60, KIND_OFF: 19 * 60 + 20}


def test_workdays_and_weekend_are_separate() -> None:
    days = _days(14)
    transitions = [
        Transition(day, (18 if day.weekday() < 5 else 10) * 60, KIND_ON)
        for day in days
    ]

    habits = _learn(transitions, days, day_mode=DAY_MODE_WORKDAY_WEEKEND)

    assert {(habit.group, habit.minute) for habit in habits} == {
        ("workday", 18 * 60),
        ("weekend", 10 * 60),
    }


def test_single_weekday() -> None:
    days = _days(28)
    transitions = [
        Transition(day, 20 * 60, KIND_ON) for day in days if day.weekday() == 2
    ]

    habits = _learn(transitions, days, day_mode=DAY_MODE_WEEKDAY)

    assert [(habit.group, habit.minute, habit.days) for habit in habits] == [
        ("wed", 20 * 60, 4)
    ]


def test_unobserved_days_are_ignored() -> None:
    days = _days(5)
    transitions = [Transition(day, 18 * 60, KIND_ON) for day in days]

    # Transitions on days we were not watching must not count.
    assert _learn(transitions, days[:2]) == []


def test_extract_transitions_debounces() -> None:
    start = datetime(2026, 3, 2, 18, 0, tzinfo=timezone.utc)

    def at(seconds: int) -> datetime:
        return start + timedelta(seconds=seconds)

    samples = [
        (at(0), False),
        (at(100), True),  # 10 s spike
        (at(110), False),
        (at(200), None),  # unavailable
        (at(300), True),  # real switch-on
        (at(320), True),
        (at(1000), False),  # last change always counts
    ]

    assert extract_transitions(samples, 60) == [(at(300), True), (at(1000), False)]
    assert len(extract_transitions(samples, 0)) == 4
    assert extract_transitions([], 60) == []


def test_upcoming_actions_apply_lead_and_delay() -> None:
    habits = [
        Habit(KIND_ON, "all", 18 * 60, 5, 5),
        Habit(KIND_OFF, "all", 23 * 60 + 30, 5, 5),
    ]

    actions = upcoming_actions(
        habits,
        start_day=MONDAY,
        days=2,
        tz=timezone.utc,
        day_mode=DAY_MODE_ALL,
        lead=timedelta(minutes=15),
        off_delay=timedelta(minutes=60),
    )

    assert [(action.kind, action.when) for action in actions] == [
        (KIND_ON, datetime(2026, 3, 2, 17, 45, tzinfo=timezone.utc)),
        # Crosses midnight
        (KIND_OFF, datetime(2026, 3, 3, 0, 30, tzinfo=timezone.utc)),
        (KIND_ON, datetime(2026, 3, 3, 17, 45, tzinfo=timezone.utc)),
        (KIND_OFF, datetime(2026, 3, 4, 0, 30, tzinfo=timezone.utc)),
    ]
    assert actions[0].habit_time == datetime(2026, 3, 2, 18, 0, tzinfo=timezone.utc)
