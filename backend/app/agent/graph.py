from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, StateGraph

from app.agent.state import AgentState
from app.rag.retriever import retrieve


def retrieve_knowledge_node(state: AgentState) -> AgentState:
    query = state.get("latest_user_message", "")
    backend, chunks = retrieve(query, top_k=5)
    return {
        **state,
        "rag_backend": backend,
        "retrieved_knowledge": [
            {
                "source": chunk.source,
                "heading": chunk.heading,
                "score": chunk.score,
                "content": chunk.content,
            }
            for chunk in chunks
        ],
    }


@lru_cache(maxsize=1)
def build_agent_graph():
    graph = StateGraph(AgentState)
    graph.add_node("retrieve_knowledge", retrieve_knowledge_node)
    graph.set_entry_point("retrieve_knowledge")
    graph.add_edge("retrieve_knowledge", END)
    return graph.compile()


def run_agent_graph(state: AgentState) -> AgentState:
    return build_agent_graph().invoke(state)
