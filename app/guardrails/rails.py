import logfire
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.config import settings
from app.guardrails.colang_rules import ALLOW_MARKER, COLANG_CONTENT, RAIL_NAMES, yaml_content

_rails = None


class _NoLLM(FakeListChatModel):
    """
    The gate runs in embeddings-only mode, so NeMo must never call an LLM.
    If it ever tries, this raises and guard() fails open instead of spending tokens.
    """

    def _call(self, *args, **kwargs):
        raise RuntimeError("Guardrails attempted an LLM call in embeddings-only mode")


def initialize_rails() -> None:
    """Builds the NeMo LLMRails singleton (loads the local FastEmbed model once)."""
    global _rails
    if not settings.GUARDRAILS_ENABLED:
        logfire.info("🛡️ Guardrails disabled (GUARDRAILS_ENABLED=false).")
        return

    from nemoguardrails import LLMRails, RailsConfig

    config = RailsConfig.from_content(
        colang_content=COLANG_CONTENT,
        yaml_content=yaml_content(settings.GUARDRAILS_THRESHOLD),
    )
    _rails = LLMRails(config, llm=_NoLLM(responses=[]))
    # Warm-up: builds the intent index now instead of on the first user request
    _rails.generate(messages=[{"role": "user", "content": "hello"}])
    logfire.info(f"🛡️ NeMo Guardrails initialised (embeddings-only, threshold {settings.GUARDRAILS_THRESHOLD}).")


def guard(message: str) -> tuple[bool, str | None, str]:
    """
    Runs a user message through the guardrail gate.

    Returns (blocked, response, rail):
        (True,  canned_reply, rail_name) - a rail fired; reply immediately, skip the RAG pipeline
        (False, None, "pass")             - allowed into the RAG pipeline
        (False, None, "disabled"/"error") - gate off or failed (fails open)
    """
    if _rails is None:
        return False, None, "disabled"

    with logfire.span("🛡️ Guardrails Check"):
        try:
            result = _rails.generate(messages=[{"role": "user", "content": message}])
        except Exception as e:
            logfire.error(f"⚠️ Guardrails error, failing open: {e}")
            return False, None, "error"

        content = result.get("content", "") if isinstance(result, dict) else str(result)
        if ALLOW_MARKER in content:
            logfire.info("✅ Guardrails passed.")
            return False, None, "pass"

        rail = next((name for marker, name in RAIL_NAMES.items() if marker in content), "unknown")
        logfire.info(f"🛡️ Guardrails fired: {rail} | query='{message[:80]}'")
        return True, content, rail
