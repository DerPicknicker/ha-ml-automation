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
# Recorded data changes every few minutes; writing it that often would wear
# out SD cards. It is always written when Home Assistant stops.
SAVE_DELAY_SECONDS = 30 * 60

# --- Configuration keys -------------------------------------------------
CONF_CONTROL_ENTITY = "control_entity"
CONF_LEARN_ENTITIES = "learn_entities"

CONF_CONTROL_ON = "control_on"
CONF_CONTROL_OFF = "control_off"
CONF_LEAD_MINUTES = "lead_minutes"
CONF_OFF_DELAY_MINUTES = "off_delay_minutes"
CONF_GUARD_GRACE_MINUTES = "guard_grace_minutes"

CONF_WINDOW_DAYS = "window_days"
CONF_MIN_DAYS = "min_days"
CONF_MIN_CONFIDENCE = "min_confidence"

# --- Defaults -----------------------------------------------------------
DEFAULT_LEAD_MINUTES = 15
DEFAULT_OFF_DELAY_MINUTES = 60
DEFAULT_GUARD_GRACE_MINUTES = 15
DEFAULT_WINDOW_DAYS = 28
DEFAULT_MIN_DAYS = 3
DEFAULT_MIN_CONFIDENCE = 50

# Domains that can be switched on and off. Most have `turn_on` / `turn_off`
# services, which `homeassistant.turn_on` / `turn_off` dispatch to; the rest
# are listed in SWITCH_SERVICES.
CONTROL_DOMAINS = [
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

# Domains without turn_on / turn_off: (domain, on service, off service).
SWITCH_SERVICES = {"cover": ("cover", "open_cover", "close_cover")}

# Sensors of the controlled device that show whether it is really being used,
# best first. They are suggested as learning data during setup.
USAGE_DEVICE_CLASSES = ("power", "current")

# Non-numeric states that mean "nothing going on". Every other state counts as
# active, so nobody has to list the active states of an entity.
INACTIVE_STATES = frozenset(
    {
        "off",
        "standby",
        "idle",
        "closed",
        "not_home",
        "away",
        "locked",
        "docked",
        "below_horizon",
        "none",
        "",
    }
)

# States of the controlled entity that mean "already switched off".
TARGET_OFF_STATES = frozenset({"off", "standby", "closed"})

# Give up waiting for the controlled entity to become unused after this long.
PENDING_OFF_MAX_HOURS = 12

# --- Status values ------------------------------------------------------
STATUS_LEARNING = "learning"
STATUS_READY = "ready"
STATUS_CONTROLLING = "controlling"
STATUS_POSTPONED = "postponed"
STATUSES = [STATUS_LEARNING, STATUS_READY, STATUS_CONTROLLING, STATUS_POSTPONED]
