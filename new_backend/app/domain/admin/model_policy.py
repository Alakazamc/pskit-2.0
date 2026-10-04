"""Fresh publication grants intersect cached gateway discovery on every call."""

from starlette.concurrency import run_in_threadpool

from app.contracts.admin import AdminModel, ModelDraft
from app.domain.admin.roles import RevisionConflict
from app.services.model_catalog import ModelOption


class ModelPolicy:
    def __init__(self, store, catalog, *, managed=False):
        self.store = store
        self.catalog = catalog
        self.managed = managed

    def policy(self, alias):
        row = self.store.db.execute(
            "SELECT revision,state,draft_json,published_json FROM admin_models WHERE alias=?",
            (alias,),
        ).fetchone()
        return row

    def permitted(self, user_id, policy, purpose):
        """An empty allowlist denies everybody; groups are controlled server data."""
        if purpose not in policy.purposes:
            return False
        if user_id in policy.allowed_user_ids:
            return True
        if policy.allowed_group_ids:
            groups = {
                r[0]
                for r in self.store.db.execute(
                    "SELECT group_id FROM admin_group_members WHERE user_id=?", (user_id,)
                )
            }
            return bool(groups & set(policy.allowed_group_ids))
        return False

    async def visible_for(self, user_id, purpose="chat"):
        gateway = await self.catalog.list_models(include_fallback=not self.managed)
        if not self.managed:
            return list(gateway)
        return await run_in_threadpool(self.filter_visible, user_id, purpose, gateway)

    def filter_visible(self, user_id, purpose, gateway):
        items = []
        for model in gateway:
            row = self.policy(model.id)
            if not row or row[1] != "published" or not row[3]:
                continue
            policy = ModelDraft.model_validate_json(row[3])
            if self.permitted(user_id, policy, purpose):
                items.append(
                    ModelOption(
                        id=model.id,
                        supports_images=model.supports_images and policy.supports_images,
                        reasoning_levels=[
                            level
                            for level in model.reasoning_levels
                            if level in policy.reasoning_levels
                        ],
                    )
                )
        return items

    async def authorize(self, user_id, alias, purpose="chat"):
        """Check current grants even when gateway metadata was cached before revocation."""
        if not self.managed:
            return await self.catalog.resolve(alias)
        for model in await self.visible_for(user_id, purpose):
            if model.id == alias:
                return model
        raise PermissionError("MODEL_FORBIDDEN")

    async def resolve(self, user_id, alias=None, purpose="chat"):
        if not self.managed:
            return await self.catalog.resolve(alias)
        visible = await self.visible_for(user_id, purpose)
        if alias:
            return await self.authorize(user_id, alias, purpose)
        for model in visible:
            row = await run_in_threadpool(self.policy, model.id)
            if purpose in ModelDraft.model_validate_json(row[3]).default_for_purposes:
                return model
        if visible:
            return visible[0]
        raise PermissionError("MODEL_FORBIDDEN")

    async def admin_list(self, *, limit=50, cursor=None):
        gateway = {m.id: m for m in await self.catalog.list_models(include_fallback=False)}
        return await run_in_threadpool(self.list_known, gateway, limit, cursor)

    def list_known(self, gateway, limit, cursor):
        ids = set(gateway) | {r[0] for r in self.store.db.execute("SELECT alias FROM admin_models")}
        selected = sorted(i for i in ids if i > (cursor or ""))[: limit + 1]
        items = [self.admin_item(alias, gateway.get(alias)) for alias in selected[:limit]]
        return {"items": items, "next_cursor": items[-1].id if len(selected) > limit else None}

    def admin_item(self, alias, gateway):
        row = self.policy(alias)
        return AdminModel(
            id=alias,
            revision=row[0] if row else 0,
            state=row[1] if row else "draft",
            gateway_available=gateway is not None,
            gateway=gateway or ModelOption(id=alias),
            draft=ModelDraft.model_validate_json(row[2]) if row and row[2] else None,
            published=ModelDraft.model_validate_json(row[3]) if row and row[3] else None,
        )

    async def available_gateway(self, alias):
        for model in await self.catalog.list_models(include_fallback=False):
            if model.id == alias:
                return model
        raise ValueError("MODEL_UNAVAILABLE")

    async def save(self, actor, alias, payload, *, request_id=None):
        gateway = await self.available_gateway(alias)
        if (
            payload.supports_images
            and not gateway.supports_images
            or any(level not in gateway.reasoning_levels for level in payload.reasoning_levels)
        ):
            raise ValueError("MODEL_CAPABILITY_UNSUPPORTED")
        if any(purpose not in payload.purposes for purpose in payload.default_for_purposes):
            raise ValueError("MODEL_DEFAULT_PURPOSE_UNAUTHORIZED")
        return await run_in_threadpool(
            self.save_current, actor, alias, payload, gateway, request_id
        )

    def save_current(self, actor, alias, payload, gateway, request_id):
        with self.store.transaction():
            row = self.policy(alias)
            revision = row[0] if row else 0
            if revision != payload.expected_revision:
                raise RevisionConflict("REVISION_CONFLICT")
            before = self.admin_item(alias, gateway).model_dump(mode="json")
            self.store.db.execute(
                "INSERT INTO admin_models VALUES (?,?,'draft',?,NULL) ON CONFLICT(alias) DO UPDATE SET revision=excluded.revision,draft_json=excluded.draft_json",
                (alias, revision + 1, payload.model_dump_json()),
            )
            after = self.admin_item(alias, gateway)
            self.store.audit(
                actor,
                "models:draft",
                alias,
                payload.reason,
                before,
                after.model_dump(mode="json"),
                request_id,
            )
            return after

    async def publish(self, actor, alias, payload, *, retire=False, request_id=None):
        gateway = (
            await self.available_gateway(alias)
            if not retire
            else next(
                (
                    m
                    for m in await self.catalog.list_models(include_fallback=False)
                    if m.id == alias
                ),
                None,
            )
        )
        return await run_in_threadpool(
            self.publish_current, actor, alias, payload, gateway, retire, request_id
        )

    def publish_current(self, actor, alias, payload, gateway, retire, request_id):
        with self.store.transaction():
            row = self.policy(alias)
            if not row:
                raise LookupError("MODEL_DRAFT_NOT_FOUND")
            if row[0] != payload.expected_revision:
                raise RevisionConflict("REVISION_CONFLICT")
            if not retire and not row[2]:
                raise ValueError("MODEL_DRAFT_REQUIRED")
            before = self.admin_item(alias, gateway).model_dump(mode="json")
            state = "retired" if retire else "published"
            self.store.db.execute(
                "UPDATE admin_models SET revision=?,state=?,published_json=? WHERE alias=?",
                (row[0] + 1, state, row[3] if retire else row[2], alias),
            )
            after = self.admin_item(alias, gateway)
            self.store.audit(
                actor,
                "models:retire" if retire else "models:publish",
                alias,
                payload.reason,
                before,
                after.model_dump(mode="json"),
                request_id,
            )
            return after
