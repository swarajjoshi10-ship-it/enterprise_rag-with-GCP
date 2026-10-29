import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class Settings:
    # --- GCP CONFIG ---
    PROJECT_ID = os.getenv("PROJECT_ID")
    LOCATION = os.getenv("LOCATION", "us-central1")
    GCP_DOC_AI_LOCATION = os.getenv("GCP_DOC_AI_LOCATION", "us")
    GCP_DOC_AI_PROCESSOR_ID = os.getenv("GCP_DOC_AI_PROCESSOR_ID")
    PDF_PARSER = os.getenv("PDF_PARSER", "pypdf").lower()  # "pypdf" (free) or "docai" (billed per page)
    RAW_BUCKET = os.getenv("GCP_RAW_BUCKET", "rag-data-raw")
    PROCESSED_BUCKET = os.getenv("GCP_PROCESSED_BUCKET", "rag-data-processed")

    # --- VECTOR DB (QDRANT) ---
    QDRANT_URL = os.getenv("QDRANT_CLUSTER_ENDPOINT")
    QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
    QDRANT_COLLECTION = "enterprise_rag"

    # --- REASONING ENGINE (GROQ) ---
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

    # --- LLM GATEWAY (fallback chain) ---
    # Each Groq model has its own free-tier quota; tried in order after GROQ_MODEL fails
    GROQ_FALLBACK_MODELS = [m.strip() for m in os.getenv("GROQ_FALLBACK_MODELS", "qwen/qwen3.8-27b,openai/gpt-oss-20b").split(",") if m.strip()]
    # Last resort on Vertex AI (GCP credits); set empty to disable
    GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")

    # --- GUARDRAILS (NeMo, embeddings-only: no LLM calls) ---
    GUARDRAILS_ENABLED = os.getenv("GUARDRAILS_ENABLED", "true").lower() == "true"
    # Minimum similarity to match a rule; below it the message passes through (fail open)
    GUARDRAILS_THRESHOLD = float(os.getenv("GUARDRAILS_THRESHOLD", "0.5"))

    # --- DATABASE & CACHE ---
    DB_USER = os.getenv("DB_USER", "rag_admin")
    DB_PASS = os.getenv("DB_PASS")
    DB_NAME = os.getenv("DB_NAME", "rag_memory")
    DB_CONNECTION_NAME = os.getenv("DB_CONNECTION_NAME")
    
    REDIS_HOST = os.getenv("REDIS_HOST")
    REDIS_PORT = os.getenv("REDIS_PORT", "6379")

    # --- ENVIRONMENT MODE ---
    # Set to "true" in your local .env to bypass Cloud SQL/Redis
    LOCAL_MODE = os.getenv("LOCAL_MODE", "false").lower() == "true"

    # --- OBSERVABILITY ---
    LOGFIRE_TOKEN = os.getenv("LOGFIRE_TOKEN")
    LANGSMITH_TRACING = os.getenv("LANGSMITH_TRACING", "true")
    LANGSMITH_API_KEY = os.getenv("LANGSMITH_API_KEY")
    LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT", "entreprise_rag")
    LANGSMITH_ENDPOINT = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")

# Apply LangChain environment variables for automatic tracing
os.environ["LANGCHAIN_TRACING_V2"] = os.getenv("LANGSMITH_TRACING", "true")
os.environ["LANGCHAIN_API_KEY"] = os.getenv("LANGSMITH_API_KEY", "")
os.environ["LANGCHAIN_PROJECT"] = os.getenv("LANGSMITH_PROJECT", "rag_scale_test")
os.environ["LANGCHAIN_ENDPOINT"] = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")

settings = Settings()
