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
        meta_by_text = {doc['content']: {"source": doc['source'], "source_type": doc['source_type']} for doc in raw_results}
        
        if settings.RERANK_ENABLED:
            with logfire.span("⚖️ Semantic Reranking"):
                ranked = rerank_documents(query, doc_contents, top_n=len(doc_contents))
                # The cross-encoder sometimes drops a chunk dense search ranked first (e.g. a YAML
                # manifest), so the top dense hits always stay; the re-ranker fills the other slots
                keep = doc_contents[:settings.RERANK_KEEP_DENSE]
                rest = [doc for doc in ranked if doc not in keep][:max(0, settings.RERANK_TOP_N - len(keep))]
                selected = set(keep + rest)
                reranked_contents = [doc for doc in ranked if doc in selected]  # re-ranker order
                logfire.info(f"Reranking complete. Kept {len(reranked_contents)} chunks "
                             f"({settings.RERANK_KEEP_DENSE} pinned from dense search).")
        else:
            # Ablation: plain vector-search order
            reranked_contents = doc_contents[:settings.RERANK_TOP_N]
            
        formatted_docs = [f"CONTENT: {doc}" for doc in reranked_contents]
        doc_meta = [meta_by_text.get(doc, {"source": "unknown", "source_type": "unknown"}) for doc in reranked_contents]
    
    return {
        "documents": formatted_docs,
        "doc_meta": doc_meta,
        "status": f"Found technical context.",
        "plan": state["plan"] + ["Context Retrieved"]
    }
