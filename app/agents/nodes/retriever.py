import logfire
from app.agents.state import AgentState
from app.services.retrieval.qdrant_service import search_enterprise_knowledge
from app.services.retrieval.ranking_service import rerank_documents
from app.config import settings

def retrieve_node(state: AgentState):
    """
    Performs vector search and semantic reranking for technical queries.
    """
    query = state["current_query"]
    
    
    # Standard Retrieval Logic
    with logfire.span("🔍 Knowledge Retrieval"):
        logfire.info(f"Searching Qdrant for: {query}")
        raw_results = search_enterprise_knowledge(query, limit=settings.RETRIEVAL_TOP_K)
        logfire.info(f"Retrieved {len(raw_results)} candidates from Vector DB")
        
        doc_contents = [doc['content'] for doc in raw_results]
        
        if settings.RERANK_ENABLED:
            with logfire.span("⚖️ Semantic Reranking"):
                reranked_contents = rerank_documents(query, doc_contents, top_n=settings.RERANK_TOP_N)
                logfire.info(f"Reranking complete. Kept top {settings.RERANK_TOP_N} most relevant chunks.")
        else:
            # Ablation: plain vector-search order
            reranked_contents = doc_contents[:settings.RERANK_TOP_N]
            
        formatted_docs = [f"CONTENT: {doc}" for doc in reranked_contents]
    
    return {
        "documents": formatted_docs,
        "status": f"Found technical context.",
        "plan": state["plan"] + ["Context Retrieved"]
    }
