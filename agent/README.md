# Agent

Target Agent framework:

```text
LangGraph
```

Core graph:

```text
load_context
retrieve_knowledge
select_skill
preflight
call_llm
route_tools
execute_or_enqueue
diagnose_error
compose_answer
persist_turn
```

The Agent must not be treated as a normal chat completion endpoint. It is a stateful workflow runtime that coordinates RAG, tools, tasks, artifacts, and final answer generation.

