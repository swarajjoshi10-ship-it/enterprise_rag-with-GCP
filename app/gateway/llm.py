import time

import logfire
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_groq import ChatGroq

from app.config import settings

# In-process LLM gateway: no proxy service, no extra account.
#   1. Vertex AI managed open model (settings.VERTEX_MODEL, e.g. gpt-oss-120b): billed to
#      GCP credits, no daily request cap
#   2. Groq models (settings.GROQ_MODEL, then settings.GROQ_FALLBACK_MODELS): free tier,
#      each model with its own quota, so a 429 on one does not block the others
#   3. Optional last resort: Gemini on Vertex AI (settings.GEMINI_FALLBACK_MODEL)
# The primary model's providers (1 and the GROQ_MODEL in 2) are retried in rounds before
# any weaker model is used.

_cache: dict[float, Runnable] = {}


class _GoogleAuth:
    """httpx auth hook: attaches a fresh Google OAuth token (they expire hourly)."""

    def __init__(self):
        import google.auth
        import google.auth.transport.requests

        self._creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        self._request = google.auth.transport.requests.Request()

    def __call__(self, request):
        if not self._creds.valid:
            self._creds.refresh(self._request)
        request.headers["Authorization"] = f"Bearer {self._creds.token}"
        return request


def _vertex_maas(model: str, temperature: float) -> BaseChatModel:
    import httpx
    from langchain_openai import ChatOpenAI

    location = settings.VERTEX_MAAS_LOCATION
    host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
    return ChatOpenAI(
        model=model,
        base_url=f"https://{host}/v1/projects/{settings.PROJECT_ID}/locations/{location}/endpoints/openapi",
        api_key="google-oauth",  # replaced per request by the auth hook
        http_client=httpx.Client(auth=_GoogleAuth()),
        # Per-request timeout: the OpenAI client overrides any timeout set on http_client
        timeout=settings.VERTEX_TIMEOUT_S,
        temperature=temperature,
        max_retries=0,  # a timeout means queueing: fall back now rather than queue again
    )


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


def _chain(temperature: float) -> tuple[list[BaseChatModel], list[BaseChatModel]]:
    """(primary tier, fallback tier): the primary model on each provider, then the weaker models."""
    primary = []
    if settings.VERTEX_MODEL:
        primary.append(_vertex_maas(settings.VERTEX_MODEL, temperature))
    if settings.GROQ_MODEL:
        primary.append(_groq(settings.GROQ_MODEL, temperature))
    fallbacks = [_groq(m, temperature) for m in settings.GROQ_FALLBACK_MODELS]
    if settings.GEMINI_FALLBACK_MODEL:
        fallbacks.append(_gemini(settings.GEMINI_FALLBACK_MODEL, temperature))
    return primary, fallbacks


def _primary_rounds(models: list[BaseChatModel]) -> Runnable:
    """
    Tries every provider of the primary model, up to PRIMARY_ROUNDS times with a growing pause.
    Rate limits are per minute, so waiting a few seconds usually keeps the answer on the
    primary model instead of handing it to a weaker one (which scored lower in evaluation).
    """

    def call(prompt):
        last_error = None
        for round_no in range(settings.PRIMARY_ROUNDS):
            if round_no:
                wait = settings.PRIMARY_BACKOFF_S * round_no
                logfire.warning(f"⏳ Primary model unavailable on all providers, retrying in {wait:.0f} s")
                time.sleep(wait)
            for model in models:
                try:
                    return model.invoke(prompt)
                except Exception as e:
                    last_error = e
        raise last_error

    return RunnableLambda(call)


def get_llm(temperature: float = 0) -> Runnable:
    """Returns the primary model (retried across providers) wrapped with the fallback chain."""
    if temperature not in _cache:
        primary, fallbacks = _chain(temperature)
        if not primary:
            primary, fallbacks = fallbacks[:1], fallbacks[1:]
        first = _primary_rounds(primary)
        _cache[temperature] = first.with_fallbacks(fallbacks) if fallbacks else first
    return _cache[temperature]


def warm_up() -> None:
    """The first Vertex AI call can take 20-60 s; pay that before real traffic."""
    try:
        invoke_llm("Reply with the single word OK.")
    except Exception as e:
        logfire.warning(f"⚠️ Gateway warm-up failed (non-fatal): {e}")


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
    if model != settings.PRIMARY_MODEL:
        logfire.warning(f"🔀 Gateway fallback: answered by {model} (primary {settings.PRIMARY_MODEL})")
    return message_text(response), model
