# LangGraph Agent Design

## Agent Responsibilities

The Agent must:

- Understand user intent.
- Retrieve PSKit knowledge with RAG.
- Select a skill/workflow.
- Check required dependencies before calling tools.
- Call tools with valid arguments.
- Submit long tasks when needed.
- Track generated artifacts.
- Diagnose failures.
- Return a useful answer with source citations and result files.

## Agent State

```python
class AgentState(TypedDict):
    user_id: str
    session_id: str
    messages: list[dict]
    latest_user_message: str
    selected_skill: dict | None
    retrieved_knowledge: list[dict]
    llm_response: dict | None
    tool_calls: list[dict]
    tool_results: list[dict]
    active_task_ids: list[str]
    artifacts: list[dict]
    diagnostics: list[dict]
    final_answer: str | None
```

## Graph Nodes

```text
load_session_context
retrieve_knowledge
select_skill
preflight
call_llm
route_tool_calls
execute_fast_tool
enqueue_long_task
inspect_tool_result
diagnose_error
compose_final_answer
persist_turn
```

## Routing Rules

Fast synchronous tools:

- RCSB search/fetch/download.
- UniProt lookup.
- RNAcentral lookup.
- read_result_file.
- small structure split/extract/contact-map jobs.

Long queued tools:

- AlphaFold 3.
- binding site prediction.
- sequence interaction prediction.
- empirical feature extraction.
- report generation.
- RAG index rebuild.

## Preflight Checks

### Binding Site Prediction

Check:

- Input file ownership.
- Input file existence.
- mmCIF/PDB parser can read file.
- Protein chain exists.
- ligand_type is `DNA` or `RNA`.
- Matching INABe weight exists.
- ESM2 650M exists.
- SaProt 650M exists.
- Foldseek exists and is executable.

### AlphaFold 3

Check:

- User explicitly requested AF3.
- Sequence is valid.
- GPU is visible.
- AF3 Docker image exists.
- AF3 DB dir exists.
- AF3 model dir exists.
- Output directory is writable.

### Remote RNA Expert

Check:

- PDB ID format.
- Chain exists and is protein.
- MCP SSE endpoint reachable.

## Structured Errors

All tools should return structured errors:

```json
{
  "error_type": "missing_model_weight",
  "dependency": "INABe_RNA.pth",
  "suggestion": "Check PSKIT_MODEL_PARAMETERS and rerun doctor."
}
```

Supported error types:

- `missing_dependency`
- `missing_model_weight`
- `invalid_input_structure`
- `invalid_chain_type`
- `external_api_unreachable`
- `external_api_auth_failed`
- `task_failed`
- `task_timeout`
- `permission_denied`
- `artifact_not_found`

## Streaming Events

SSE events:

```json
{"type": "knowledge_sources", "sources": []}
{"type": "message_delta", "delta": "..."}
{"type": "tool_call_started", "name": "...", "args": {}}
{"type": "tool_call_finished", "name": "...", "result": {}}
{"type": "task_created", "task_id": "...", "task_type": "..."}
{"type": "task_update", "task_id": "...", "status": "running", "progress": 0.3}
{"type": "artifact_created", "artifact": {}}
{"type": "error", "error": {}}
{"type": "done"}
```

