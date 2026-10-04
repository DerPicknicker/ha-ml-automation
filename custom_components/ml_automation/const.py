"""Constants for the ML Automation integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "ml_automation"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]

STORAGE_VERSION = 1

# --- Configuration keys -------------------------------------------------
CONF_SOURCE_ENTITY = "source_entity"
CONF_SOURCE_MODE = "source_mode"
CONF_ACTIVE_ABOVE = "active_above"
CONF_ACTIVE_STATES = "active_states"
CONF_DEBOUNCE_SECONDS = "debounce_seconds"

CONF_TARGET_ENTITIES = "target_entities"
CONF_CONTROL_ON = "control_on"
CONF_CONTROL_OFF = "control_off"
CONF_LEAD_MINUTES = "lead_minutes"
CONF_OFF_DELAY_MINUTES = "off_delay_minutes"
CONF_GUARD_ENTITY = "guard_entity"
CONF_GUARD_ABOVE = "guard_above"
CONF_GUARD_GRACE_MINUTES = "guard_grace_minutes"
CONF_CONDITION_ENTITY = "condition_entity"

CONF_WINDOW_DAYS = "window_days"
CONF_MIN_DAYS = "min_days"
CONF_MIN_CONFIDENCE = "min_confidence"
CONF_IMPORT_HISTORY = "import_history"

# --- Source modes -------------------------------------------------------
MODE_NUMERIC = "numeric"
MODE_STATE = "state"

# --- Defaults -----------------------------------------------------------
DEFAULT_ACTIVE_ABOVE = 20.0
DEFAULT_DEBOUNCE_SECONDS = 60
DEFAULT_LEAD_MINUTES = 15
DEFAULT_OFF_DELAY_MINUTES = 60
DEFAULT_GUARD_GRACE_MINUTES = 15
DEFAULT_WINDOW_DAYS = 28
DEFAULT_MIN_DAYS = 3
DEFAULT_MIN_CONFIDENCE = 50

# Domains whose state is normally a number.
NUMERIC_DOMAINS = ("sensor", "number", "input_number")

# Domains that `homeassistant.turn_on` / `homeassistant.turn_off` can drive.
TARGET_DOMAINS = [
    "switch",
    "light",
    "media_player",
    "fan",
    "input_boolean",
    "climate",
    "humidifier",
    "cover",
    "remote",
    "siren",
    "water_heater",
]

# Suggested "active" states per source domain (state mode).
DOMAIN_ACTIVE_STATES: dict[str, list[str]] = {
    "media_player": ["on", "playing", "paused", "idle", "buffering"],
    "person": ["home"],
    "device_tracker": ["home"],
    "cover": ["open"],
    "lock": ["unlocked"],
    "climate": ["heat", "cool", "heat_cool", "auto", "dry", "fan_only"],
    "vacuum": ["cleaning"],
}
FALLBACK_ACTIVE_STATES = ["on"]

# States that count as "in use" / "true" for guard and condition entities.
GENERIC_ACTIVE_STATES = frozenset({"on", "playing", "buffering", "home", "open"})

# Target states that mean "already switched off".
TARGET_OFF_STATES = frozenset({"off", "standby", "closed"})

# A source change this soon after one of our own actions was caused by us.
SELF_TRIGGER_SECONDS = 120
# Undoing one of our actions within this time counts as a rejection.
REVERT_WINDOW_SECONDS = 30 * 60
# Give up waiting for the guard to clear after this long.
PENDING_OFF_MAX_HOURS = 12

# --- Status values ------------------------------------------------------
STATUS_LEARNING = "learning"
STATUS_READY = "ready"
STATUS_CONTROLLING = "controlling"
STATUS_POSTPONED = "postponed"
STATUSES = [STATUS_LEARNING, STATUS_READY, STATUS_CONTROLLING, STATUS_POSTPONED]
