"""Tests for the pure learning logic."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from custom_components.ml_automation.learner import (
    KIND_OFF,
    KIND_ON,
    SLOT_MINUTES,
    SLOTS_PER_DAY,
    Habit,
    day_occupancy,
    extract_transitions,
    find_habits,
    train_forest,
    upcoming_actions,
)

MONDAY = date(2026, 3, 2)
SATURDAY = 5


def _day(*sessions: tuple[int, int]) -> tuple[bool, ...]:
    """Occupancy of a day that was active during the given hours."""
    slots = [False] * SLOTS_PER_DAY
    for start, end in sessions:
        for slot in range(start * 60 // SLOT_MINUTES, end * 60 // SLOT_MINUTES):
            slots[slot] = True
    return tuple(slots)


def _learn(days: list[tuple[bool, ...]], *, min_days: int = 3, threshold: float = 0.5):
    """Train on consecutive days starting on a Monday and return the habits."""
    occupancy = {
        MONDAY + timedelta(days=index): slots for index, slots in enumerate(days)
    }
    forest = train_forest(
        occupancy,
        today=MONDAY + timedelta(days=len(days)),
        half_life_days=14,
        min_days=min_days,
    )
    if forest is None:
        return []
    return find_habits(forest.weekly_profile(), threshold)


def _times(habits: list[Habit], weekday: int | None = None) -> set[tuple[str, int]]:
    return {
        (habit.kind, habit.minute)
        for habit in habits
        if weekday is None or habit.weekday == weekday
    }


def test_learns_daily_session() -> None:
    habits = _learn([_day((18, 19))] * 7)

    # Predicted for every day of the week.
    assert len(habits) == 14
    assert _times(habits) == {(KIND_ON, 18 * 60), (KIND_OFF, 19 * 60)}
    assert all(habit.confidence == 1.0 for habit in habits)


def test_needs_enough_days() -> None:
    assert _learn([_day((18, 19))] * 2) == []
    assert _learn([_day((18, 19))] * 2, min_days=2) != []


def test_irregular_behaviour_is_not_a_habit() -> None:
    days = [_day((18, 19))] * 3 + [_day()] * 7

    assert _learn(days) == []


def test_one_off_is_ignored() -> None:
    days = [_day((18, 19))] * 10
    days[4] = _day((9, 10), (18, 19))

    assert _times(_learn(days)) == {(KIND_ON, 18 * 60), (KIND_OFF, 19 * 60)}


def test_jitter_averages_out() -> None:
    days = []
    for shift in (-2, 1, 0, 2, -1, 1, 0, -1, 2, 0):
        slots = [False] * SLOTS_PER_DAY
        start = 18 * 60 // SLOT_MINUTES + shift
        for slot in range(start, start + 60 // SLOT_MINUTES):
            slots[slot] = True
        days.append(tuple(slots))

    habits = _learn(days)

    assert len(habits) == 14
    for habit in habits:
        expected = 18 * 60 if habit.kind == KIND_ON else 19 * 60
        assert abs(habit.minute - expected) <= 10


def test_two_sessions_per_day() -> None:
    habits = _learn([_day((7, 8), (20, 22))] * 7)

    assert _times(habits) == {
        (KIND_ON, 7 * 60),
        (KIND_OFF, 8 * 60),
        (KIND_ON, 20 * 60),
        (KIND_OFF, 22 * 60),
    }


def test_session_across_midnight() -> None:
    habits = _learn([_day((0, 1), (23, 24))] * 7)

    assert len(habits) == 14
    assert _times(habits) == {(KIND_ON, 23 * 60), (KIND_OFF, 60)}


def test_always_active_has_no_habits() -> None:
    assert _learn([_day((0, 24))] * 7) == []


def test_weekdays_and_weekend_are_told_apart() -> None:
    days = [
        _day((10, 11)) if index % 7 >= SATURDAY else _day((18, 19))
        for index in range(28)
    ]

    habits = _learn(days)

    for weekday in range(SATURDAY):
        assert _times(habits, weekday) == {(KIND_ON, 18 * 60), (KIND_OFF, 19 * 60)}
    for weekday in (SATURDAY, SATURDAY + 1):
        assert _times(habits, weekday) == {(KIND_ON, 10 * 60), (KIND_OFF, 11 * 60)}


def test_single_weekday_habit() -> None:
    days = [_day((20, 21)) if index % 7 == 2 else _day() for index in range(28)]

    habits = _learn(days)

    assert {habit.weekday for habit in habits} == {2}
    assert _times(habits) == {(KIND_ON, 20 * 60), (KIND_OFF, 21 * 60)}


def test_recent_days_count_more() -> None:
    days = [_day((18, 19))] * 14 + [_day((20, 21))] * 14

    assert _times(_learn(days)) == {(KIND_ON, 20 * 60), (KIND_OFF, 21 * 60)}


def test_training_is_deterministic() -> None:
    days = [_day((18, 19)) if index % 3 else _day((17, 20)) for index in range(20)]

    assert _learn(days) == _learn(days)


def test_find_habits_tidies_up() -> None:
    profile = [[0.0] * SLOTS_PER_DAY for _ in range(7)]
    monday = profile[0]
    # 10:00-11:00 with a 10 minute interruption, and a 5 minute blip at 15:00.
    for slot in range(120, 132):
        monday[slot] = 0.9
    monday[125] = monday[126] = 0.1
    monday[180] = 0.9

    habits = find_habits(profile, 0.5)

    assert [(habit.kind, habit.weekday, habit.minute) for habit in habits] == [
        (KIND_ON, 0, 10 * 60),
        (KIND_OFF, 0, 11 * 60),
    ]
    assert find_habits(profile, 0.95) == []


def test_find_habits_wraps_around_the_week() -> None:
    profile = [[0.0] * SLOTS_PER_DAY for _ in range(7)]
    for slot in range(SLOTS_PER_DAY - 12, SLOTS_PER_DAY):
        profile[6][slot] = 0.8
    for slot in range(12):
        profile[0][slot] = 0.8

    habits = find_habits(profile, 0.5)

    assert [(habit.kind, habit.weekday, habit.minute) for habit in habits] == [
        (KIND_OFF, 0, 60),
        (KIND_ON, 6, 23 * 60),
    ]


def test_day_occupancy() -> None:
    def ts(hour: int, minute: int = 0) -> float:
        return datetime(2026, 3, 2, hour, minute, tzinfo=timezone.utc).timestamp()

    occupancy = day_occupancy(
        MONDAY, timezone.utc, [ts(18), ts(19), ts(23, 30)], [True, False, True], False
    )

    assert occupancy == _day((18, 19))[: 23 * 12 + 6] + (True,) * 6
    # Without any transition the initial state holds all day.
    assert day_occupancy(MONDAY, timezone.utc, [], [], True) == _day((0, 24))


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
        Habit(KIND_ON, 0, 18 * 60, 1.0),
        Habit(KIND_OFF, 0, 23 * 60 + 30, 1.0),
        Habit(KIND_ON, 2, 7 * 60, 1.0),
    ]

    actions = upcoming_actions(
        habits,
        start_day=MONDAY,
        days=3,
        tz=timezone.utc,
        lead=timedelta(minutes=15),
        off_delay=timedelta(minutes=60),
    )

    assert [(action.kind, action.when) for action in actions] == [
        (KIND_ON, datetime(2026, 3, 2, 17, 45, tzinfo=timezone.utc)),
        # Crosses midnight
        (KIND_OFF, datetime(2026, 3, 3, 0, 30, tzinfo=timezone.utc)),
        (KIND_ON, datetime(2026, 3, 4, 6, 45, tzinfo=timezone.utc)),
    ]
    assert actions[0].habit_time == datetime(2026, 3, 2, 18, 0, tzinfo=timezone.utc)
