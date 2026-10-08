"""Tests of observed opportunities, sequences, and immutable action data."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.ml_automation.action_learner import (
    ActionSpec,
    Observation,
    learn_actions,
)

TZ = ZoneInfo("Europe/Berlin")
TODAY = date(2026, 3, 30)
PLAY = ActionSpec.from_call(
    "media_player",
    "play_media",
    {
        "entity_id": "media_player.speaker",
        "media_content_id": "station:xyz",
        "media_content_type": "music",
    },
)
assert PLAY is not None


def at(day: date, hour: int = 18, minute: int = 0) -> datetime:
    """Return the local wall time of an action."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def test_daily_action_preserves_parameters_and_is_deterministic() -> None:
    days = [TODAY - timedelta(days=index) for index in range(1, 8)]
    events = [
        Observation(at(day, minute=index % 3), "play", PLAY.key)
        for index, day in enumerate(days)
    ]
    rules = learn_actions(events, days, today=TODAY)
    schedules = [rule for rule in rules if rule.kind == "schedule"]
    assert len(schedules) == 1
    assert schedules[0].minute == 18 * 60 + 1
    assert schedules[0].confidence == 1
    assert learn_actions(events, days, today=TODAY) == rules
    assert PLAY.data["media_content_id"] == "station:xyz"
    data = PLAY.data
    data["media_content_id"] = "changed"
    assert PLAY.data["media_content_id"] == "station:xyz"


def test_weekly_action_needs_repeated_weekdays() -> None:
    days = [TODAY - timedelta(days=index) for index in range(1, 22)]
    events = [
        Observation(at(day), "play", PLAY.key) for day in days if day.weekday() == 4
    ]
    rules = learn_actions(events, days, today=TODAY)
    assert len(rules) == 1
    assert rules[0].weekday == 4
    assert rules[0].support_days == 3


def test_today_and_unobserved_days_are_not_training_data() -> None:
    days = [TODAY - timedelta(days=index) for index in range(1, 4)]
    events = [Observation(at(day), "play", PLAY.key) for day in days]
    assert learn_actions(events, days + [TODAY], today=TODAY)[0].confidence == 1
    assert learn_actions(
        events + [Observation(at(TODAY), "other", "new")], days, today=TODAY
    ) == learn_actions(events, days, today=TODAY)
    # Having no coverage is different from observing that an action did not occur.
    assert learn_actions(events, days[:2], today=TODAY) == []


def test_many_repeats_on_one_day_do_not_establish_a_habit() -> None:
    days = [TODAY - timedelta(days=index) for index in range(1, 4)]
    events = [
        Observation(at(days[0], minute=index), "play", PLAY.key) for index in range(20)
    ]
    assert learn_actions(events, days, today=TODAY) == []


def test_sequence_uses_trigger_opportunities_and_learns_delay() -> None:
    days = [TODAY - timedelta(days=index) for index in range(1, 5)]
    events = []
    for index, day in enumerate(days):
        events.append(
            Observation(at(day, 10 + index), "state:media_player.speaker:playing")
        )
        events.append(Observation(at(day, 10 + index, 2), "play", PLAY.key))
    sequences = [
        rule
        for rule in learn_actions(events, days, today=TODAY)
        if rule.kind == "sequence"
    ]
    assert len(sequences) == 1
    assert sequences[0].delay_seconds == 120
    assert sequences[0].confidence == 1
    # Busy sensors that usually have no corresponding action are poor triggers.
    for day in days:
        events.extend(
            Observation(at(day, hour), "state:media_player.speaker:playing")
            for hour in (1, 3, 5)
        )
    assert not any(
        rule.kind == "sequence" for rule in learn_actions(events, days, today=TODAY)
    )


def test_action_identity_includes_parameters_and_ignores_mapping_order() -> None:
    first = ActionSpec.from_call(
        "custom_domain", "custom_action", {"mode": "quiet", "value": 3}
    )
    same = ActionSpec.from_call(
        "custom_domain", "custom_action", {"value": 3, "mode": "quiet"}
    )
    different = ActionSpec.from_call(
        "custom_domain", "custom_action", {"mode": "loud", "value": 3}
    )
    assert first is not None and same is not None and different is not None
    assert first.key == same.key
    assert first.key != different.key


@pytest.mark.parametrize(
    "data",
    [
        {"password": "private"},
        {"nested": {"token": "private"}},
        {"media_content_id": "https://example.org/live?access_token=private"},
        {"value": "{{ unknown }}"},
        {"value": float("nan")},
        {"value": object()},
        {"value": "x" * 5000},
        {"value": 2**20000},
    ],
)
def test_non_repeatable_or_private_arguments_are_rejected(data: dict) -> None:
    assert ActionSpec.from_call("custom", "action", data) is None


def test_sequence_median_aggregates_delays_and_distinct_support_days() -> None:
    days = [TODAY - timedelta(days=index) for index in range(1, 4)]
    events = []
    for day, delay in zip(days, (60, 125, 240), strict=True):
        trigger = at(day, 10)
        events.append(Observation(trigger, "state:binary_sensor.motion:on"))
        events.append(Observation(trigger + timedelta(seconds=delay), "play", PLAY.key))
    rule = next(
        rule
        for rule in learn_actions(events, days, today=TODAY)
        if rule.kind == "sequence"
    )
    assert rule.support_days == 3
    assert rule.confidence == 1
    assert rule.delay_seconds == 130


def test_obsolete_action_patterns_are_not_proposed() -> None:
    days = [TODAY - timedelta(days=index) for index in (20, 21, 22)]
    events = [Observation(at(day), "play", PLAY.key) for day in days]
    assert learn_actions(events, days, today=TODAY) == []


def test_missing_time_windows_are_not_negative_schedule_evidence() -> None:
    days = [TODAY - timedelta(days=index) for index in range(1, 5)]
    events = [Observation(at(day), "play", PLAY.key) for day in days[1:]]
    full = (1 << 1440) - 1
    coverage = {day: full for day in days}
    coverage[days[0]] ^= ((1 << 30) - 1) << (18 * 60)
    rules = learn_actions(events, coverage, today=TODAY)
    assert rules and rules[0].confidence == 1
    assert (
        learn_actions(events, {day: full for day in days}, today=TODAY)[0].confidence
        < 1
    )


def test_missing_followup_window_is_not_a_failed_sequence() -> None:
    days = [TODAY - timedelta(days=index) for index in range(1, 5)]
    events = [
        Observation(at(day, 10 + index), "trigger") for index, day in enumerate(days)
    ]
    events.extend(
        Observation(at(day, 10 + index, 2), "play", PLAY.key)
        for index, day in enumerate(days)
        if index > 0
    )
    coverage = {day: (1 << 1440) - 1 for day in days}
    coverage[days[0]] ^= ((1 << 10) - 1) << (10 * 60 + 1)
    rules = learn_actions(events, coverage, today=TODAY)
    assert any(rule.kind == "sequence" and rule.confidence == 1 for rule in rules)
