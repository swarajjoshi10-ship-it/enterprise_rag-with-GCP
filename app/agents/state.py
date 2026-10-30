from typing import TypedDict, List, Annotated
import operator


class AgentState(TypedDict):
    # Using Annotated with operator.add ensures that messages 
    # are appended to the history rather than replaced.
    messages: Annotated[List[dict], operator.add]
    current_query: str
    documents: List[str]
    # Parallel to documents: {"source", "source_type"} of each chunk (for evaluation, not shown to the LLM)
    doc_meta: List[dict]
    plan: List[str]
    status: str
    final_answer: str
