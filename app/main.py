# ============================================================
# CRITICAL: logfire MUST be configured before ALL other imports
# so that spans from all modules are captured from the start.
# ============================================================
import logfire
import os
from dotenv import load_dotenv

load_dotenv()
logfire.configure(token=os.getenv("LOGFIRE_TOKEN"), send_to_logfire="if-token-present")

# Now safe to import app modules - logfire is already active
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Response
from app.agents.graph import rag_agent
from app.gateway import warm_up
from app.guardrails import guard, initialize_rails
from app.services.gcp.redis_semantic_cache import check_cache, update_cache

from pydantic import BaseModel
from typing import Optional


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load the guardrails embedding index once, before serving traffic.
    # In a worker thread: NeMo's sync generate() refuses to run inside the event loop.
    await asyncio.to_thread(initialize_rails)
    # The first LLM call is slow; warm the gateway in the background without delaying startup
    warm_task = asyncio.create_task(asyncio.to_thread(warm_up))
    yield
    warm_task.cancel()


# Initialize FastAPI
app = FastAPI(title="Enterprise Agentic RAG API", lifespan=lifespan)

class QueryRequest(BaseModel):
    q: str
    thread_id: Optional[str] = "default_user"

# # Instrument FastAPI to link UI traces to backend traces in Logfire
# try:
#     logfire.instrument_fastapi(app)
#     print("✅ Logfire FastAPI instrumentation enabled.")
# except Exception as e:
#     print(f"⚠️ Logfire FastAPI instrumentation skipped: {e}")

@app.get("/")
def home():
    return {"message": "Enterprise LangGraph RAG API is live."}

@app.get("/graph")
def get_graph_image():
    """
    Returns the Mermaid image of the agent's workflow.
    """
    try:
        png_bytes = rag_agent.get_graph().draw_mermaid_png()
        return Response(content=png_bytes, media_type="image/png")
    except Exception as e:
        return {"error": f"Could not generate graph image: {e}"}

@app.post("/query")
def query(request: QueryRequest):
    """
    Executes the LangGraph RAG flow with memory using a POST request.
    """
    q = request.q
    thread_id = request.thread_id

    initial_state = {
        "messages": [{"role": "user", "content": q}],
        "current_query": q,
        "documents": [],
        "doc_meta": [],
        "plan": ["Start"],
        "status": "Initializing Graph..."
    }
    
    # Configuration for Memory (Thread ID)
    config = {"configurable": {"thread_id": thread_id}}
    
    try:
        # 0. Gate 1: Guardrails (NeMo, embeddings-only) - blocks jailbreaks / off-topic
        blocked, rail_response, rail = guard(q)
        if blocked:
            return {
                "question": q,
                "answer": rail_response,
                "thought_process": ["Intent: Guardrails Fired", f"Rail: {rail}", "Retrieval: Skipped"],
                "status": "Blocked by guardrails.",
                "sources": []
            }

        # 1. Check Semantic Cache first (Redis)
        cached_response = check_cache(q)
        if cached_response:
            return {
                "question": q,
                "answer": cached_response,
                "thought_process": ["Served from Semantic Cache (Redis)"],
                "status": "Cache HIT",
                "sources": []
            }

        # 2. Cache MISS: Run the full Agentic Graph
        # Run the graph synchronously to preserve Logfire context variables
        final_output = rag_agent.invoke(initial_state, config=config)
        answer = final_output.get("final_answer")
        
        # 3. Update Cache with the new answer
        if answer:
            update_cache(q, answer)
        
        return {
            "question": q,
            "answer": answer,
            "thought_process": final_output.get("plan"),
            "status": final_output.get("status"),
            "sources": final_output.get("documents", []),
            "source_meta": final_output.get("doc_meta", [])
        }
    except Exception as e:
        logfire.error(f"❌ Backend Execution Failed: {e}")
        return {
            "question": q,
            "answer": "I apologize, but I encountered an internal error while processing your request. Please try again later.",
            "thought_process": ["Error encountered during execution."],
            "status": "error",
            "sources": []
        }