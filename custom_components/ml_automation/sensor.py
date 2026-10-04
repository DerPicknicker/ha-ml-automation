"""Sensors exposing what ML Automation learned."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import STATUSES
from .entity import MLAutomationEntity
from .learner import KIND_OFF, KIND_ON
from .manager import MLAutomationConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MLAutomationConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensors."""
    async_add_entities(
        [
            StatusSensor(entry),
            NextActionSensor(entry, KIND_ON),
            NextActionSensor(entry, KIND_OFF),
            PatternsSensor(entry),
            DaysOfDataSensor(entry),
        ]
    )


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
            "source_entity": self.manager.source_entity,
            "target_entities": self.manager.target_entities,
            "in_use": self.manager.is_busy,
            "last_action": last["kind"] if last else None,
            "last_action_time": (
                dt_util.utc_from_timestamp(last["ts"]) if last else None
            ),
        }


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
    """The habits that were found."""

    def __init__(self, entry: MLAutomationConfigEntry) -> None:
        """Initialise the sensor."""
        super().__init__(entry, "patterns")

    @property
    def native_value(self) -> int:
        """Return the number of habits."""
        return len(self.manager.habits)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the habits."""
        return {
            "patterns": [
                {
                    "kind": habit.kind,
                    "days": habit.group,
                    "time": habit.time.strftime("%H:%M"),
                    "confidence": round(habit.confidence * 100),
                    "seen_on_days": habit.days,
                    "observed_days": habit.observed,
                }
                for habit in self.manager.habits
            ]
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
        return {
            "days_required": self.manager.min_occurrences,
            "transitions": self.manager.event_count,
        }
