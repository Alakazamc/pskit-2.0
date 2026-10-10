"""Agent business tools projected from the existing immutable product releases."""

from __future__ import annotations

import re

from app.contracts.compute import ComputeJobRequest
from app.contracts.tool_products import ToolProductDraft
from app.domain.compute.common import admission_lock, payload_hash
from app.domain.tool_products.registry import ToolProductNotAvailable
from app.domain.tool_products.runs import ToolRunGateway


class AgentComputeTools:
    def __init__(self, database, repository, compute_jobs):
        self.database = database
        self.repository = repository
        self.compute_jobs = compute_jobs

    @staticmethod
    def _tool_name(product, action):
        # Keep provider-safe readable names; punctuation and truncation never
        # determine identity. A release change produces a new opaque tool name.
        identity = payload_hash({"product": product.product_id,
                                 "release": product.release_id, "action": action.id})
        slug = re.sub(r"[^a-zA-Z0-9_]", "_", product.slug)[:10]
        action_name = re.sub(r"[^a-zA-Z0-9_]", "_", action.id)[:10]
        return f"pskit_{slug}_{action_name}_{identity[:24]}"

    @staticmethod
    def _eligible(action):
        # Workflows keep the Tools UI engine's final-result semantics. Correlating
        # only the first compute step to an Agent call would resume too early.
        return (action.kind == "capability" and len(action.binding_ids) == 1
                and action.input_schema.get("type") == "object")

    def for_user(self, user_id):
        if not user_id:
            return []
        return [{
            "name": self._tool_name(product, action),
            "description": (
                f"{product.title.en}: {action.label.en}. "
                f"{action.description.en if action.description else product.description.en} "
                "Submit a durable background job. Stop and wait; the completed job resumes this session."
            ),
            "input_schema": action.input_schema,
            "release_id": product.release_id,
            "action_id": action.id,
        } for product in self.repository.list_visible(user_id)
            for action in product.actions if self._eligible(action)]

    def submit(self, user_id, run_id, tool_call_id, tool_name, release_id, action_id, arguments):
        if not user_id or not run_id or not tool_call_id:
            raise ValueError("INVALID_AGENT_COMPUTE_CONTEXT")
        with self.database.transaction() as connection:
            admission_lock(connection)
            # Lock the release before the product, matching suspend's lock order.
            # Publication/visibility changes cannot race a successful admission.
            state = connection.execute(
                "SELECT state FROM tool_product_releases WHERE release_id=%s FOR SHARE",
                (release_id,),
            ).fetchone()
            if state is None or state[0] != "published":
                raise ToolProductNotAvailable("TOOL_PRODUCT_NOT_AVAILABLE")
            release = self.repository.release_snapshot(release_id, connection=connection)
            draft = ToolProductDraft.model_validate(release["draft"])
            active = connection.execute(
                "SELECT active_release_id FROM tool_products WHERE product_id=%s FOR SHARE",
                (draft.product_id,),
            ).fetchone()
            visibility = draft.visibility
            visible = (visibility.audience in {"public", "members"}
                       or user_id in visibility.allowed_user_ids)
            if not visible or active is None or active[0] != release_id:
                raise ToolProductNotAvailable("TOOL_PRODUCT_NOT_AVAILABLE")
            product = self.repository._published(release_id, connection=connection)
            action = next((item for item in product.actions if item.id == action_id), None)
            if (action is None or not self._eligible(action)
                    or self._tool_name(product, action) != tool_name):
                raise ToolProductNotAvailable("TOOL_PRODUCT_ACTION_NOT_AVAILABLE")
            binding = next(item for item in release["draft"]["bindings"]
                           if item["binding_id"] == action.binding_ids[0])
            found = ToolRunGateway._released_capability(binding, action.input_schema)
            capability = found[1]
            execution_binding = ToolRunGateway._execution_binding(binding, connection)
            return self.compute_jobs.submit(
                user_id,
                ComputeJobRequest(capability_id=capability.id, version=capability.version,
                                  arguments=arguments, budget=capability.max_budget),
                f"agent:{run_id}:{tool_call_id}",
                run_id=run_id, tool_call_id=tool_call_id,
                execution_binding=execution_binding,
                provenance={"release_id": product.release_id, "action_id": action.id},
                resolved_capability=found, connection=connection,
            )
