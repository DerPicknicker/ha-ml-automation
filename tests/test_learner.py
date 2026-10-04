"""Tests for the pure learning logic."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from custom_components.ml_automation.learner import (
    KIND_NUMERIC,
    KIND_OFF,
    KIND_ON,
    KIND_STATE,
    SLOT_MINUTES,
    SLOTS_PER_DAY,
    Habit,
    find_habits,
    learn,
    learn_threshold,
    slots_from_timeline,
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


def test_unknown_slots_are_not_counted_as_idle() -> None:
    # Home Assistant was only running in the evening on most days.
    evening_only = tuple(
        value if 17 * 12 <= index < 20 * 12 else None
        for index, value in enumerate(_day((18, 19)))
    )
    days = [evening_only] * 6 + [_day((18, 19), (8, 9))]

    # The morning was seen once, and it was active then.
    assert _times(_learn(days, min_days=1)) == {
        (KIND_ON, 8 * 60),
        (KIND_OFF, 9 * 60),
        (KIND_ON, 18 * 60),
        (KIND_OFF, 19 * 60),
    }


def test_threshold_separates_standby_from_use() -> None:
    standby = [0.8, 1.0, 1.2] * 100
    watching = [70.0, 90.0, 110.0] * 10

    threshold = learn_threshold(standby + watching)

    assert threshold is not None
    assert 1.2 < threshold < 70.0


def test_threshold_picks_the_high_level_of_three() -> None:
    # Socket off, device in standby, device in use.
    values = [0.0] * 200 + [5.0] * 100 + [100.0] * 40

    assert learn_threshold(values) == 52.5


def test_no_threshold_without_two_levels() -> None:
    # Constant, too little data, or just drifting like a temperature.
    assert learn_threshold([1.0] * 500) is None
    assert learn_threshold([1.0, 90.0] * 5) is None
    assert learn_threshold([20.0 + index % 30 / 10 for index in range(900)]) is None


def test_slots_from_timeline() -> None:
    def ts(hour: int, minute: int = 0) -> float:
        return datetime(2026, 3, 2, hour, minute, tzinfo=timezone.utc).timestamp()

    slots = slots_from_timeline(
        MONDAY,
        timezone.utc,
        [ts(6), ts(18), ts(18, 2), ts(19), ts(20)],
        [1.0, 91.0, 101.0, None, 1.0],
        ts(21),
    )

    assert slots[5 * 12 + 11] is None  # nothing known before 06:00
    assert slots[6 * 12] == 1.0
    assert slots[18 * 12] == 97.0  # two minutes at 91, three at 101
    assert slots[19 * 12] is None  # unavailable
    assert slots[20 * 12] == 1.0
    assert slots[21 * 12] == 1.0  # 21:00 itself is the last sample
    assert slots[21 * 12 + 1] is None  # the future


def _recorded(**entities: tuple[bool, ...]) -> dict[str, list[float]]:
    """Slot values as they are recorded: watts for power, 0/1 for the rest."""
    return {
        entity: [
            (90.0 if active else 1.0) if entity == "power" else float(active)
            for active in slots
        ]
        for entity, slots in entities.items()
    }


def _run_learn(days, labels, ignored=None, kinds=None):
    return learn(
        {MONDAY + timedelta(days=index): data for index, data in enumerate(days)},
        kinds or {"power": KIND_NUMERIC, "player": KIND_STATE, "plug": KIND_STATE},
        labels,
        ignored or {},
        today=MONDAY + timedelta(days=len(days)),
        half_life_days=14,
        min_days=3,
        confidence=0.5,
    )


def test_learn_finds_threshold_and_habits() -> None:
    always_on = _day((0, 24))
    days = [_recorded(power=_day((18, 19)), plug=always_on)] * 7

    model = _run_learn(days, ["power"])

    assert model.thresholds == {"power": 45.5, "player": 0.5, "plug": 0.5}
    assert _times(model.habits) == {(KIND_ON, 18 * 60), (KIND_OFF, 19 * 60)}
    # Learning from the always-on plug instead finds nothing to act on.
    assert _run_learn(days, ["plug"]).habits == []


def test_learn_combines_entities() -> None:
    days = [_recorded(power=_day((18, 19)), player=_day((20, 21)))] * 7

    model = _run_learn(days, ["power", "player"])

    assert _times(model.habits) == {
        (KIND_ON, 18 * 60),
        (KIND_OFF, 19 * 60),
        (KIND_ON, 20 * 60),
        (KIND_OFF, 21 * 60),
    }


def test_learn_skips_ignored_slots_and_today() -> None:
    days = [_recorded(player=_day((17, 19)))] * 7
    ignored = {
        MONDAY + timedelta(days=index): range(17 * 12, 18 * 12) for index in range(7)
    }

    model = _run_learn(days, ["player"], ignored)

    assert _times(model.habits) == {(KIND_ON, 18 * 60), (KIND_OFF, 19 * 60)}

    # A day that is not over yet is not trained on.
    forest = learn(
        {MONDAY + timedelta(days=index): data for index, data in enumerate(days)},
        {"player": KIND_STATE},
        ["player"],
        {},
        today=MONDAY + timedelta(days=2),
        half_life_days=14,
        min_days=3,
        confidence=0.5,
    ).forest
    assert forest is None


def test_numeric_entity_without_threshold_is_never_active() -> None:
    days = [{"power": [21.0 + index % 7 / 10 for index in range(SLOTS_PER_DAY)]}] * 7

    model = _run_learn(days, ["power"], kinds={"power": KIND_NUMERIC})

    assert model.thresholds == {"power": None}
    assert model.habits == []


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
