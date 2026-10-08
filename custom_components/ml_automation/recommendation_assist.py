"""Use learned recommendations from Assist without a language model."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from hassil.recognize import RecognizeResult
from homeassistant.components.conversation import ConversationInput
from homeassistant.components.conversation.agent_manager import (
    async_get_agent,
    get_agent_manager,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.translation import async_get_translations

from .const import DOMAIN

if TYPE_CHECKING:
    from .recommendations import RecommendationManager


@callback
def async_register_sentences(
    hass: HomeAssistant, manager: RecommendationManager
) -> list[Callable[[], None]]:
    """Register local sentence triggers and return their cleanup callbacks."""
    # A spoken acceptance applies what was actually read back in that context,
    # even if another recommendation has since become first on the dashboard.
    pending: dict[tuple[str | None, str | None, str | None], str] = {}
    sentences = {
        "show": [
            "what do you suggest",
            "show [me ](your|the) (suggestions|recommendations)",
            "was schlägst du vor",
            "zeige [mir ](die|deine) (vorschläge|empfehlungen)",
        ],
        "apply": [
            "(apply|accept) [the ](suggestion|recommendation)",
            "(wende den vorschlag an|führe den vorschlag aus)",
        ],
        "dismiss": [
            "dismiss [the ](suggestion|recommendation)",
            "(verwirf|verwerfe) [den ]vorschlag",
        ],
        "authorize": [
            "always apply [this ](suggestion|recommendation)",
            "führe diesen vorschlag immer aus",
        ],
    }

    def make_handler(
        operation: str,
    ) -> Callable[[ConversationInput, RecognizeResult], Awaitable[str | None]]:
        async def handle(user_input: ConversationInput, result: RecognizeResult) -> str:
            translations = await async_get_translations(
                hass, user_input.language, "exceptions", {DOMAIN}
            )

            def text(key: str) -> str:
                return translations[f"component.{DOMAIN}.exceptions.{key}.message"]

            key = (
                user_input.context.user_id,
                user_input.device_id,
                user_input.conversation_id,
            )
            if operation == "show":
                if (proposal := manager.suggestion) is None:
                    pending.pop(key, None)
                    return text("assist_no_suggestion")
                pending[key] = proposal.id
                while len(pending) > 32:
                    del pending[next(iter(pending))]
                return f"{proposal.title}. {proposal.reason}"
            if (suggestion_id := pending.pop(key, None)) is None:
                return text("assist_ask_first")
            try:
                if operation == "dismiss":
                    await manager.async_dismiss(suggestion_id)
                    return text("assist_dismissed")
                await manager.async_apply(
                    suggestion_id,
                    user_input.context,
                    authorize=operation == "authorize",
                )
            except HomeAssistantError:
                return text("assist_failed")
            return text("assist_applied")

        return handle

    agent_manager = get_agent_manager(hass)
    # Older supported Home Assistant versions register on the default agent.
    register = getattr(agent_manager, "register_trigger", None)
    if register is None:
        register = async_get_agent(hass).register_trigger
    return [
        register(phrases, make_handler(operation))
        for operation, phrases in sentences.items()
    ]
