import logfire
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_groq import ChatGroq

from app.config import settings

# In-process LLM gateway: no proxy service, no extra account.
#   1. Primary Groq model (settings.GROQ_MODEL)
#   2. Other Groq models (settings.GROQ_FALLBACK_MODELS): each has its own free-tier
#      quota, so a 429 on one model does not block the others
#   3. Optional last resort: Gemini on Vertex AI (settings.GEMINI_FALLBACK_MODEL),
#      billed to GCP credits, only reached when every Groq model fails

_cache: dict[float, Runnable] = {}


def _groq(model: str, temperature: float) -> BaseChatModel:
    # max_retries=1: fail over to the next model quickly instead of
    # retrying a rate-limited model with long backoffs
    return ChatGroq(
        api_key=settings.GROQ_API_KEY,
        model=model,
        temperature=temperature,
        max_retries=1,
        timeout=60,
    )


def _gemini(model: str, temperature: float) -> BaseChatModel:
    from langchain_google_vertexai import ChatVertexAI

    # Current Gemini models are served from the "global" location only
    return ChatVertexAI(
        model=model,
        project=settings.PROJECT_ID,
        location="global",
        temperature=temperature,
        max_retries=1,
    )


def get_llm(temperature: float = 0) -> Runnable:
    """Returns the primary model wrapped with the configured fallback chain."""
    if temperature not in _cache:
        primary = _groq(settings.GROQ_MODEL, temperature)
        fallbacks = [_groq(m, temperature) for m in settings.GROQ_FALLBACK_MODELS]
        if settings.GEMINI_FALLBACK_MODEL:
            fallbacks.append(_gemini(settings.GEMINI_FALLBACK_MODEL, temperature))
        _cache[temperature] = primary.with_fallbacks(fallbacks) if fallbacks else primary
    return _cache[temperature]


def message_text(message) -> str:
    """Plain text of a chat message; Gemini returns a list of content parts."""
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)


def invoke_llm(prompt: str, temperature: float = 0) -> tuple[str, str]:
    """
    Calls the gateway and returns (text, model_name) so callers can record
    which model actually answered (fallback rate is an evaluation metric).
    """
    response = get_llm(temperature).invoke(prompt)
    model = response.response_metadata.get("model_name", "unknown")
    if model != settings.GROQ_MODEL:
        logfire.warning(f"🔀 Gateway fallback: answered by {model} (primary {settings.GROQ_MODEL})")
    return message_text(response), model
