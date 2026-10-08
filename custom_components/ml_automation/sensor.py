"""Sensors exposing what ML Automation learned."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    CONF_RECOMMENDATIONS,
    STATUSES,
    SUGGESTION_NONE,
    SUGGESTION_OFF,
    SUGGESTION_ON,
    SUGGESTIONS,
)
from .entity import MLAutomationEntity, RecommendationEntity
from .learner import KIND_OFF, KIND_ON, WEEKDAYS, Habit
from .manager import MLAutomationConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MLAutomationConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensors."""
    if entry.data.get(CONF_RECOMMENDATIONS):
        async_add_entities(
            [
                ActionSuggestionSensor(entry),
                ActionStatusSensor(entry),
                ActionPatternsSensor(entry),
            ]
        )
        return
    async_add_entities(
        [
            StatusSensor(entry),
            SuggestionSensor(entry),
            ProbabilitySensor(entry),
            NextActionSensor(entry, KIND_ON),
            NextActionSensor(entry, KIND_OFF),
            PatternsSensor(entry),
            DaysOfDataSensor(entry),
        ]
    )


class ActionSuggestionSensor(RecommendationEntity, SensorEntity):
    """Display a recommendation in any card that displays entity states."""

    def __init__(self, entry: MLAutomationConfigEntry) -> None:
        """Initialize the recommendation feed."""
        super().__init__(entry, "action_suggestion")

    @property
    def native_value(self) -> str | None:
        """Return a readable title rather than a device-specific enum."""
        return proposal.title if (proposal := self.manager.suggestion) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return stable IDs, evidence, and a bounded queue for other frontends."""
        return {
            **(proposal.as_dict() if (proposal := self.manager.suggestion) else {}),
            "suggestions": [item.as_dict() for item in self.manager.suggestions],
        }


class ActionStatusSensor(RecommendationEntity, SensorEntity):
    """Explain learning progress and report command acceptance honestly."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATUSES

    def __init__(self, entry: MLAutomationConfigEntry) -> None:
        """Initialize the status sensor."""
        super().__init__(entry, "action_status")

    @property
    def native_value(self) -> str:
        """Return the reason for the current learning state."""
        return self.manager.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return observation coverage, permissions, and the last execution."""
        return {
            "days_of_data": len(self.manager.known_days),
            "observations": len(self.manager.observations),
            "actions": len(self.manager.actions),
            "authorized_actions": [
                {"action_key": key, **self.manager.actions[key].as_dict()}
                for key in sorted(self.manager.authorized)
                if key in self.manager.actions
            ],
            "last_result": self.manager.last_result,
        }


class ActionPatternsSensor(RecommendationEntity, SensorEntity):
    """Show discovered schedules and event-to-action patterns."""

    def __init__(self, entry: MLAutomationConfigEntry) -> None:
        """Initialize the learned action count."""
        super().__init__(entry, "action_patterns")

    @property
    def native_value(self) -> int:
        """Return the number of supported rules."""
        return len(self.manager.rules)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose learned relationships without copying the observation log."""
        return {
            "model": "action_frequencies",
            "patterns": [
                {
                    "rule_id": rule.key,
                    "action_key": rule.action_key,
                    "kind": rule.kind,
                    "trigger": rule.trigger,
                    "weekday": rule.weekday,
                    "minute": rule.minute,
                    "delay_seconds": rule.delay_seconds,
                    "confidence": round(rule.confidence * 100),
                    "support_days": rule.support_days,
                }
                for rule in self.manager.rules
            ],
        }


class StatusSensor(MLAutomationEntity, SensorEntity):
    """What the pattern is currently doing."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATUSES

    def __init__(self, entry: MLAutomationConfigEntry) -> None:
        """Initialise the sensor."""
        super().__init__(entry, "status")

    @property
    def native_value(self) -> str:
        """Return the status."""
        return self.manager.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return details about the last action."""
        last = self.manager.last_action
        return {
            "control_entity": self.manager.control_entity,
            "learn_entities": self.manager.learn_entities,
            "in_use": self.manager.in_use,
            "days_of_data": self.manager.days_of_data,
            "days_required": self.manager.min_days,
            "last_action": last["kind"] if last else None,
            "last_action_time": (
                dt_util.utc_from_timestamp(last["ts"]) if last else None
            ),
        }


class SuggestionSensor(MLAutomationEntity, SensorEntity):
    """What the user probably wants done right now."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = SUGGESTIONS

    def __init__(self, entry: MLAutomationConfigEntry) -> None:
        """Initialise the sensor."""
        super().__init__(entry, "suggestion")

    @property
    def native_value(self) -> str:
        """Return the suggestion."""
        if (action := self.manager.suggestion) is None:
            return SUGGESTION_NONE
        return SUGGESTION_ON if action.kind == KIND_ON else SUGGESTION_OFF

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return what the suggestion is about and how sure the model is."""
        action = self.manager.suggestion
        return {
            "entity_id": self.manager.control_entity,
            "confidence": (
                round(self.manager.suggestion_confidence(action) * 100)
                if action
                else None
            ),
        }


class ProbabilitySensor(MLAutomationEntity, SensorEntity):
    """How likely the model thinks there is use right now.

    The raw prediction as an entity of its own, so dashboard cards can show
    it without digging into attributes.
    """

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, entry: MLAutomationConfigEntry) -> None:
        """Initialise the sensor."""
        super().__init__(entry, "probability")

    @property
    def native_value(self) -> int | None:
        """Return the probability in percent."""
        probability = self.manager.probability
        return None if probability is None else round(probability * 100)


class NextActionSensor(MLAutomationEntity, SensorEntity):
    """When the targets will be switched next."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, entry: MLAutomationConfigEntry, kind: str) -> None:
        """Initialise the sensor."""
        super().__init__(entry, f"next_{kind}")
        self._kind = kind

    @property
    def native_value(self) -> datetime | None:
        """Return the time of the next action."""
        action = self.manager.next_action(self._kind)
        return action.when if action else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the habit behind the next action."""
        if (action := self.manager.next_action(self._kind)) is None:
            return {}
        return {
            "learned_time": action.habit_time,
            "confidence": round(action.habit.confidence * 100),
        }


class PatternsSensor(MLAutomationEntity, SensorEntity):
    """The habits the model predicts.

    The model predicts every weekday on its own; habits that happen at the
    same time on several days are shown as one.
    """

    def __init__(self, entry: MLAutomationConfigEntry) -> None:
        """Initialise the sensor."""
        super().__init__(entry, "patterns")

    def _grouped(self) -> dict[tuple[int, str], list[Habit]]:
        groups: dict[tuple[int, str], list[Habit]] = {}
        for habit in self.manager.habits:
            groups.setdefault((habit.minute, habit.kind), []).append(habit)
        return dict(sorted(groups.items()))

    @property
    def native_value(self) -> int:
        """Return the number of distinct habits."""
        return len(self._grouped())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the habits and what the model was trained on."""
        forest = self.manager.model.forest
        return {
            "patterns": [
                {
                    "kind": kind,
                    "time": habits[0].time.strftime("%H:%M"),
                    "days": [WEEKDAYS[habit.weekday] for habit in habits],
                    "confidence": round(
                        sum(habit.confidence for habit in habits) / len(habits) * 100
                    ),
                }
                for (_, kind), habits in self._grouped().items()
            ],
            "model": "random_forest",
            "trees": len(forest) if forest else 0,
            "trained_on_days": forest.days if forest else 0,
        }


class DaysOfDataSensor(MLAutomationEntity, SensorEntity):
    """How much data the model is built from."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = UnitOfTime.DAYS

    def __init__(self, entry: MLAutomationConfigEntry) -> None:
        """Initialise the sensor."""
        super().__init__(entry, "days_of_data")

    @property
    def native_value(self) -> int:
        """Return the number of complete days observed."""
        return self.manager.days_of_data

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return how much data is needed."""
        return {"days_required": self.manager.min_days}
