import logfire
from app.agents.state import AgentState
from app.config import settings
from app.gateway import invoke_llm

DECLINE_MESSAGE = "The provided documentation does not cover this."


def generate_node(state: AgentState):
    """
    Synthesizes a response using both Documentation Context AND Conversation History.
    """
    query = state["current_query"]
    
    # Format the entire history for the LLM
    history_str = ""
    for msg in state["messages"][:-1]:
        role = "User" if msg["role"] == "user" else "Assistant"
        history_str += f"{role}: {msg['content']}\n"
    
    user_msg = state["messages"][-1]["content"] if state["messages"] else ""
    
    if query == "CONVERSATIONAL":
        logfire.info("Generating conversational response using memory.")
        prompt = f"""
        You are a friendly and helpful Enterprise AI Assistant.
        Answer the user's latest message using the CONVERSATION HISTORY below.
        
        CONVERSATION HISTORY:
        {history_str}
        
        LATEST MESSAGE:
        "{user_msg}"
        """
    else:
        # Technical RAG Logic with Token Safety
        logfire.info("Generating technical RAG response.")
        max_context_chars = 25000 
        full_context = ""
        
        for doc in state["documents"]:
            if len(full_context) + len(doc) < max_context_chars:
                full_context += doc + "\n\n"
            else:
                logfire.warning("Context truncated to fit Groq TPM limits.")
                break

        if settings.RESPONDER_PROMPT == "original":
            # Baseline prompt (kept for before/after evaluation)
            prompt = f"""
        You are a Senior Technical Architect.
        Answer the question using the TECHNICAL CONTEXT provided.

        TECHNICAL CONTEXT:
        {full_context}

        CONVERSATION HISTORY:
        {history_str}

        USER QUESTION:
        "{user_msg}"
        """
        else:
            # Grounded prompt: only facts from the context, decline otherwise, stay concise
            prompt = f"""
        You are a documentation assistant for an internal Kubernetes knowledge base.

        Rules:
        1. Answer ONLY with information stated in the TECHNICAL CONTEXT below. Do not add facts,
           numbers, commands, best practices or examples from your own knowledge.
        2. If the context does not contain the answer, reply exactly:
           "{DECLINE_MESSAGE}"
           and nothing else.
        3. If the context answers only part of the question, answer that part and say which part
           the documentation does not cover.
        4. Be concise: a few sentences, or a short list for steps. Quote commands and YAML
           exactly as they appear in the context.

        TECHNICAL CONTEXT:
        {full_context}

        CONVERSATION HISTORY:
        {history_str}

        USER QUESTION:
        "{user_msg}"
        """
    
    with logfire.span("✍️ LLM Synthesis"):
        try:
            answer, model = invoke_llm(prompt, temperature=0.1)
            logfire.info("Response synthesized successfully.")
            return {
                "final_answer": answer,
                "status": "Response generated.",
                "plan": state["plan"] + [f"Responder model: {model}"],
                "messages": [{"role": "assistant", "content": answer}]
            }
        except Exception as e:
            logfire.error(f"LLM Generation failed: {e}")
            raise e
