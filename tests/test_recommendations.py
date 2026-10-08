"""Behaviour of general recommendations inside Home Assistant."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.ml_automation.action_learner import (
    MAX_ACTIONS,
    MAX_OBSERVATIONS,
    ActionSpec,
)
from custom_components.ml_automation.const import CONF_RECOMMENDATIONS, DOMAIN
from custom_components.ml_automation.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import NOW, local, move_to

pytestmark = pytest.mark.usefixtures("setup_env")
SPEAKER = "media_player.speaker"
LIGHT = "light.living_room"
DATA = {
    "entity_id": SPEAKER,
    "media_content_id": "station:xyz",
    "media_content_type": "music",
}
SENSOR = "sensor.action_recommendations_action_suggestion"


def recorded_actions(
    *,
    sequence: bool = False,
    domain: str = "media_player",
    service: str = "play_media",
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return genuinely observed complete days with exact historical calls."""
    action = ActionSpec.from_call(domain, service, data or DATA)
    assert action is not None
    observations = []
    coverage = {}
    for index in range(1, 4):
        when = NOW - timedelta(days=index)
        coverage[when.date().isoformat()] = hex((1 << 1440) - 1)
        if sequence:
            when = when.replace(hour=12 + index)
            observations.append(
                {
                    "when": when.isoformat(),
                    "trigger": f"state:{SPEAKER}:playing",
                    "action_key": None,
                }
            )
            when += timedelta(minutes=2)
        observations.append(
            {
                "when": when.isoformat(),
                "trigger": f"action:{action.key}",
                "action_key": action.key,
            }
        )
    return {
        "actions": [{"domain": domain, "service": service, "data": action.data}],
        "observations": observations,
        "coverage": coverage,
    }


async def setup_recommender(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    stored: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Set up the zero-field entry and its real platforms and Assist triggers."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Action recommendations",
        data={CONF_RECOMMENDATIONS: True},
        version=3,
        unique_id="action_recommendations",
        entry_id="recommendations",
    )
    if stored is not None:
        key = f"{DOMAIN}.{entry.entry_id}"
        hass_storage[key] = {"version": 1, "key": key, "data": stored}
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_dashboard_suggestion_replays_exact_media_call(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass.states.async_set(SPEAKER, "idle", {"friendly_name": "Kitchen speaker"})
    calls = async_mock_service(hass, "media_player", "play_media")
    entry = await setup_recommender(hass, hass_storage, recorded_actions())
    state = hass.states.get(SENSOR)
    assert state is not None and "Kitchen speaker" in state.state
    assert state.attributes["data"] == DATA
    assert state.attributes["confidence"] == 100
    assert not calls
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.action_recommendations_apply_action_suggestion"},
        blocking=True,
    )
    assert len(calls) == 1 and calls[0].data == DATA
    assert entry.runtime_data.suggestion is None
    assert entry.runtime_data.last_result["status"] == "accepted"
    assert len(entry.runtime_data.observations) == 3


async def test_generic_custom_action_and_parameters(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    data = {"profile": "quiet", "values": [1, 2], "nested": {"enabled": True}}
    calls = async_mock_service(hass, "my_integration", "my_action")
    entry = await setup_recommender(
        hass,
        hass_storage,
        recorded_actions(domain="my_integration", service="my_action", data=data),
    )
    proposal = entry.runtime_data.suggestion
    response = await hass.services.async_call(
        DOMAIN, "list_suggestions", {}, blocking=True, return_response=True
    )
    assert response["suggestions"][0]["suggestion_id"] == proposal.id
    await hass.services.async_call(
        DOMAIN, "apply_action_suggestion", {"suggestion_id": proposal.id}, blocking=True
    )
    assert calls[0].data == data
    with pytest.raises(ServiceValidationError):
        await entry.runtime_data.async_apply(proposal.id, Context())


async def test_event_sequence_suggests_after_learned_delay(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(SPEAKER, "idle")
    hass.states.async_set(LIGHT, "off")
    calls = async_mock_service(hass, "light", "turn_on")
    entry = await setup_recommender(
        hass,
        hass_storage,
        recorded_actions(
            sequence=True,
            domain="light",
            service="turn_on",
            data={"entity_id": LIGHT, "brightness_pct": 35},
        ),
    )
    assert entry.runtime_data.suggestion is None
    hass.states.async_set(SPEAKER, "playing")
    await hass.async_block_till_done()
    assert entry.runtime_data.suggestion is None
    await move_to(hass, freezer, local(12, 2))
    proposal = entry.runtime_data.suggestion
    assert proposal is not None and proposal.rule.kind == "sequence"
    await entry.runtime_data.async_apply(proposal.id, Context())
    assert calls[0].data == {"entity_id": LIGHT, "brightness_pct": 35}


async def test_manual_followup_fulfils_pending_sequence(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(SPEAKER, "idle")
    hass.states.async_set(LIGHT, "off")
    async_mock_service(hass, "light", "turn_on")
    data = {"entity_id": LIGHT, "brightness_pct": 35}
    entry = await setup_recommender(
        hass,
        hass_storage,
        recorded_actions(sequence=True, domain="light", service="turn_on", data=data),
    )
    hass.states.async_set(SPEAKER, "playing")
    await hass.async_block_till_done()
    await hass.services.async_call(
        "light", "turn_on", data, context=Context(user_id="human"), blocking=True
    )
    await hass.async_block_till_done()
    await move_to(hass, freezer, local(12, 2))
    assert entry.runtime_data.suggestion is None


async def test_only_root_human_actions_are_labels(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    async_mock_service(hass, "media_player", "play_media")
    entry = await setup_recommender(hass, hass_storage)
    for context in (
        Context(),
        Context(user_id="human", parent_id="automation"),
        Context(user_id="human"),
    ):
        await hass.services.async_call(
            "media_player", "play_media", DATA, context=context, blocking=True
        )
    await hass.async_block_till_done()
    assert len(entry.runtime_data.actions) == 1
    assert len(entry.runtime_data.observations) == 1


async def test_acceptance_buttons_do_not_become_learned_actions(
    hass: HomeAssistant, hass_storage: dict[str, Any], hass_admin_user
) -> None:
    hass.states.async_set(SPEAKER, "idle")
    calls = async_mock_service(hass, "media_player", "play_media")
    entry = await setup_recommender(hass, hass_storage, recorded_actions())
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.action_recommendations_apply_action_suggestion"},
        context=Context(user_id=hass_admin_user.id),
        blocking=True,
    )
    await hass.async_block_till_done()
    assert len(calls) == 1
    assert len(entry.runtime_data.actions) == 1
    assert len(entry.runtime_data.observations) == 3


async def test_automation_state_changes_are_not_new_triggers(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(SPEAKER, "idle")
    hass.states.async_set(LIGHT, "off")
    async_mock_service(hass, "light", "turn_on")
    entry = await setup_recommender(
        hass,
        hass_storage,
        recorded_actions(
            sequence=True, domain="light", service="turn_on", data={"entity_id": LIGHT}
        ),
    )
    context = Context()
    hass.bus.async_fire("automation_triggered", {}, context=context)
    await hass.async_block_till_done()
    hass.states.async_set(SPEAKER, "playing", context=Context(parent_id=context.id))
    await hass.async_block_till_done()
    await move_to(hass, freezer, local(12, 2))
    assert entry.runtime_data.suggestion is None


async def test_expired_id_cannot_apply_a_replacement(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(SPEAKER, "idle")
    calls = async_mock_service(hass, "media_player", "play_media")
    entry = await setup_recommender(hass, hass_storage, recorded_actions())
    identifier = entry.runtime_data.suggestion.id
    await move_to(hass, freezer, local(12, 16))
    with pytest.raises(ServiceValidationError):
        await entry.runtime_data.async_apply(identifier, Context())
    assert not calls


async def test_dismissal_and_handled_occurrences_survive_reload(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass.states.async_set(SPEAKER, "idle")
    calls = async_mock_service(hass, "media_player", "play_media")
    entry = await setup_recommender(hass, hass_storage, recorded_actions())
    proposal = entry.runtime_data.suggestion
    await entry.runtime_data.async_dismiss(proposal.id)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.suggestion is None
    assert entry.runtime_data.feedback[proposal.rule.key]["dismissed"] == 1
    assert not calls


async def test_autonomy_is_per_exact_action_and_master_switch(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(SPEAKER, "idle")
    calls = async_mock_service(hass, "media_player", "play_media")
    entry = await setup_recommender(hass, hass_storage, recorded_actions())
    manager = entry.runtime_data
    proposal = manager.suggestion
    await manager.async_apply(proposal.id, Context(), authorize=True)
    assert len(calls) == 1 and proposal.action.key in manager.authorized
    await manager.async_refresh()
    assert len(calls) == 1
    await move_to(hass, freezer, local(12, day=12))
    assert len(calls) == 2 and manager.suggestion is None
    await manager.async_set_enabled(False)
    await move_to(hass, freezer, local(12, day=13))
    assert len(calls) == 2 and manager.suggestion is not None
    await manager.async_revoke(proposal.action.key)
    await manager.async_set_enabled(True)
    await manager.async_refresh()
    assert len(calls) == 2


async def test_assist_reads_then_applies_the_same_id(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass.states.async_set(SPEAKER, "idle", {"friendly_name": "Kitchen speaker"})
    calls = async_mock_service(hass, "media_player", "play_media")
    await setup_recommender(hass, hass_storage, recorded_actions())
    context = Context()
    answer = await conversation.async_converse(
        hass, "what do you suggest", "recommendation-chat", context, language="en"
    )
    assert "Kitchen speaker" in answer.response.speech["plain"]["speech"]
    answer = await conversation.async_converse(
        hass, "apply the suggestion", "recommendation-chat", context, language="en"
    )
    assert answer.response.speech["plain"]["speech"] == "Action applied."
    assert len(calls) == 1 and calls[0].data == DATA


async def test_service_failure_is_not_reported_as_success(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass.states.async_set(SPEAKER, "idle")

    async def fail(call) -> None:
        raise HomeAssistantError("device failed")

    hass.services.async_register("media_player", "play_media", fail)
    entry = await setup_recommender(hass, hass_storage, recorded_actions())
    proposal = entry.runtime_data.suggestion
    with pytest.raises(HomeAssistantError):
        await entry.runtime_data.async_apply(proposal.id, Context(), authorize=True)
    assert proposal.action.key not in entry.runtime_data.authorized
    assert entry.runtime_data.last_result["status"] == "failed"


async def test_coverage_outages_and_bounded_data(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    entry = await setup_recommender(hass, hass_storage)
    await move_to(hass, freezer, local(12, day=12))
    assert not entry.runtime_data.known_days
    assert len(entry.runtime_data.observations) <= MAX_OBSERVATIONS
    assert len(entry.runtime_data.actions) <= MAX_ACTIONS
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert "coverage" not in diagnostics and "actions" in diagnostics
    assert diagnostics["status"] == "collecting"


async def test_duplicate_concurrent_clicks_execute_once(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass.states.async_set(SPEAKER, "idle")
    calls = async_mock_service(hass, "media_player", "play_media")
    entry = await setup_recommender(hass, hass_storage, recorded_actions())
    identifier = entry.runtime_data.suggestion.id
    results = await asyncio.gather(
        entry.runtime_data.async_apply(identifier, Context()),
        entry.runtime_data.async_apply(identifier, Context()),
        return_exceptions=True,
    )
    assert len(calls) == 1
    assert sum(isinstance(result, ServiceValidationError) for result in results) == 1


async def test_consumed_occurrence_is_saved_before_the_device_call(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass.states.async_set(SPEAKER, "idle")
    handled = []

    async def handler(call: ServiceCall) -> None:
        handled.extend(hass_storage[f"{DOMAIN}.recommendations"]["data"]["handled"])

    hass.services.async_register("media_player", "play_media", handler)
    entry = await setup_recommender(hass, hass_storage, recorded_actions())
    proposal = entry.runtime_data.suggestion
    await entry.runtime_data.async_apply(proposal.id, Context())
    assert proposal.occurrence in handled


async def test_continuous_ticks_do_not_defer_history_writes_forever(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    async_mock_service(hass, "media_player", "play_media")
    await setup_recommender(hass, hass_storage)
    await hass.services.async_call(
        "media_player",
        "play_media",
        DATA,
        context=Context(user_id="human"),
        blocking=True,
    )
    await hass.async_block_till_done()
    for minute in range(1, 32):
        await move_to(hass, freezer, local(12, minute))
    saved = hass_storage[f"{DOMAIN}.recommendations"]["data"]
    assert len(saved["observations"]) == 1
    assert saved["coverage"]


async def test_correction_during_execution_cannot_restore_autonomy(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass.states.async_set(SPEAKER, "idle")

    async def handler(call: ServiceCall) -> None:
        if call.data["media_content_id"] != DATA["media_content_id"]:
            return
        await hass.services.async_call(
            "media_player",
            "play_media",
            {**DATA, "media_content_id": "different station"},
            context=Context(user_id="human"),
            blocking=True,
        )
        await asyncio.sleep(0)

    hass.services.async_register("media_player", "play_media", handler)
    entry = await setup_recommender(hass, hass_storage, recorded_actions())
    proposal = entry.runtime_data.suggestion
    await entry.runtime_data.async_apply(proposal.id, Context(), authorize=True)
    await hass.async_block_till_done()
    assert proposal.action.key not in entry.runtime_data.authorized
    assert entry.runtime_data.feedback[proposal.rule.key]["undone"] == 1


async def test_changed_parameters_revoke_automatic_repetition(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass.states.async_set(SPEAKER, "idle")
    calls = async_mock_service(hass, "media_player", "play_media")
    entry = await setup_recommender(hass, hass_storage, recorded_actions())
    proposal = entry.runtime_data.suggestion
    await entry.runtime_data.async_apply(proposal.id, Context(), authorize=True)
    await hass.services.async_call(
        "media_player",
        "play_media",
        {**DATA, "media_content_id": "a different station"},
        context=Context(user_id="human"),
        blocking=True,
    )
    await hass.async_block_till_done()
    assert len(calls) == 2
    assert proposal.action.key not in entry.runtime_data.authorized
    assert entry.runtime_data.feedback[proposal.rule.key]["undone"] == 1


async def test_unavailable_target_is_rechecked_at_acceptance(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass.states.async_set(SPEAKER, "idle")
    calls = async_mock_service(hass, "media_player", "play_media")
    entry = await setup_recommender(hass, hass_storage, recorded_actions())
    identifier = entry.runtime_data.suggestion.id
    hass.states.async_set(SPEAKER, "unavailable")
    with pytest.raises(ServiceValidationError):
        await entry.runtime_data.async_apply(identifier, Context())
    assert not calls


async def test_observation_and_action_limits_under_event_load(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass.states.async_set(SPEAKER, "idle")
    async_mock_service(hass, "media_player", "volume_set")
    entry = await setup_recommender(hass, hass_storage)
    for index in range(MAX_ACTIONS + 5):
        await hass.services.async_call(
            "media_player",
            "volume_set",
            {"entity_id": SPEAKER, "volume_level": index / 1000},
            context=Context(user_id="human"),
            blocking=True,
        )
    await hass.async_block_till_done()
    assert len(entry.runtime_data.actions) == MAX_ACTIONS
    for index in range(MAX_OBSERVATIONS + 10):
        hass.states.async_set(SPEAKER, "playing" if index % 2 else "idle")
    await hass.async_block_till_done()
    assert len(entry.runtime_data.observations) == MAX_OBSERVATIONS


async def test_assist_rejects_expired_spoken_proposal(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    hass.states.async_set(SPEAKER, "idle")
    calls = async_mock_service(hass, "media_player", "play_media")
    await setup_recommender(hass, hass_storage, recorded_actions())
    await conversation.async_converse(
        hass, "what do you suggest", "chat", Context(), language="en"
    )
    await move_to(hass, freezer, local(12, 16))
    response = await conversation.async_converse(
        hass, "apply the suggestion", "chat", Context(), language="en"
    )
    assert "expired" in response.response.speech["plain"]["speech"]
    assert not calls


async def test_german_assist_commands(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass.states.async_set(SPEAKER, "idle")
    calls = async_mock_service(hass, "media_player", "play_media")
    await setup_recommender(hass, hass_storage, recorded_actions())
    await conversation.async_converse(
        hass, "was schlägst du vor", "chat", Context(), language="de"
    )
    response = await conversation.async_converse(
        hass, "wende den vorschlag an", "chat", Context(), language="de"
    )
    assert response.response.speech["plain"]["speech"] == "Aktion ausgeführt."
    assert len(calls) == 1


async def test_response_only_actions_execute_through_the_same_path(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    from homeassistant.core import SupportsResponse

    calls = []

    async def handler(call: ServiceCall) -> dict[str, Any]:
        calls.append(call)
        return {"result": "done"}

    hass.services.async_register(
        "custom", "respond", handler, supports_response=SupportsResponse.ONLY
    )
    entry = await setup_recommender(
        hass,
        hass_storage,
        recorded_actions(domain="custom", service="respond", data={"mode": "usual"}),
    )
    await entry.runtime_data.async_apply(entry.runtime_data.suggestion.id, Context())
    assert calls[0].return_response is True


async def test_unload_detaches_assist_and_observers(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    from homeassistant.components.conversation.agent_manager import get_agent_manager

    async_mock_service(hass, "media_player", "play_media")
    entry = await setup_recommender(hass, hass_storage)
    manager = entry.runtime_data
    assert get_agent_manager(hass).trigger_sentences
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.services.async_call(
        "media_player",
        "play_media",
        DATA,
        context=Context(user_id="human"),
        blocking=True,
    )
    await hass.async_block_till_done()
    assert not manager.observations
    assert not get_agent_manager(hass).trigger_sentences


async def test_in_use_stop_survives_restart_and_releases_with_a_new_id(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer
) -> None:
    """An old acceptance token cannot execute a stop after renewed use."""
    hass.states.async_set(LIGHT, "on")
    calls = async_mock_service(hass, "light", "turn_off")
    guard = MockConfigEntry(domain=DOMAIN, data={"control_entity": LIGHT})
    guard.add_to_hass(hass)
    guard.runtime_data = SimpleNamespace(in_use=False, status="ready")
    entry = await setup_recommender(
        hass,
        hass_storage,
        recorded_actions(domain="light", service="turn_off", data={"entity_id": LIGHT}),
    )
    old_id = entry.runtime_data.suggestion.id
    guard.runtime_data.in_use = True
    await entry.runtime_data.async_refresh()
    assert entry.runtime_data.suggestion is None
    assert entry.runtime_data.status == "postponed"
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data._postponed
    await move_to(hass, freezer, local(12, 20))
    assert not calls and entry.runtime_data.suggestion is None
    guard.runtime_data.in_use = False
    await entry.runtime_data.async_refresh()
    assert entry.runtime_data.suggestion.id != old_id
    with pytest.raises(ServiceValidationError):
        await entry.runtime_data.async_apply(old_id, Context())
    await entry.runtime_data.async_apply(entry.runtime_data.suggestion.id, Context())
    assert len(calls) == 1


async def test_postponed_stop_is_cancelled_by_a_new_manual_start(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass.states.async_set(LIGHT, "on")
    calls = async_mock_service(hass, "light", "turn_off")
    async_mock_service(hass, "light", "turn_on")
    guard = MockConfigEntry(domain=DOMAIN, data={"control_entity": LIGHT})
    guard.add_to_hass(hass)
    guard.runtime_data = SimpleNamespace(in_use=True, status="ready")
    entry = await setup_recommender(
        hass,
        hass_storage,
        recorded_actions(domain="light", service="turn_off", data={"entity_id": LIGHT}),
    )
    assert entry.runtime_data._postponed
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": LIGHT},
        context=Context(user_id="human"),
        blocking=True,
    )
    await hass.async_block_till_done()
    guard.runtime_data.in_use = False
    await entry.runtime_data.async_refresh()
    assert not entry.runtime_data._postponed
    assert not calls and entry.runtime_data.suggestion is None


async def test_event_entities_use_event_type_and_eviction_removes_coverage(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass.states.async_set(
        "event.remote", "2026-10-11T10:00:00+00:00", {"event_type": "pressed"}
    )
    entry = await setup_recommender(hass, hass_storage, recorded_actions())
    manager = entry.runtime_data
    hass.states.async_set(
        "event.remote", "2026-10-11T10:01:00+00:00", {"event_type": "pressed"}
    )
    await hass.async_block_till_done()
    assert manager.observations[-1].trigger == "event:event.remote:pressed"
    for index in range(MAX_OBSERVATIONS):
        hass.states.async_set("binary_sensor.busy", "on" if index % 2 else "off")
    await hass.async_block_till_done()
    assert not manager.known_days


async def test_legacy_assist_registration_fallback(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Use the pre-AgentManager trigger registration contract."""
    from homeassistant.components.conversation.agent_manager import get_agent_manager

    from custom_components.ml_automation import recommendation_assist

    hass.states.async_set(SPEAKER, "idle")
    calls = async_mock_service(hass, "media_player", "play_media")
    real_manager = get_agent_manager(hass)
    with (
        patch.object(
            recommendation_assist, "get_agent_manager", return_value=SimpleNamespace()
        ),
        patch.object(
            recommendation_assist,
            "async_get_agent",
            return_value=SimpleNamespace(
                register_trigger=real_manager.register_trigger
            ),
        ),
    ):
        await setup_recommender(hass, hass_storage, recorded_actions())
    await conversation.async_converse(
        hass, "what do you suggest", "legacy", Context(), language="en"
    )
    await conversation.async_converse(
        hass, "apply the suggestion", "legacy", Context(), language="en"
    )
    assert len(calls) == 1
