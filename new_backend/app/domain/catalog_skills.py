"""SkillRegistry operations on the catalog facade shared state."""

import hashlib
import json
import re
from datetime import UTC, datetime

from app.contracts.catalog import CatalogItem, SkillDetail, SkillFile
from app.domain.catalog_common import (
    ContextNotFound,
    SkillManifest,
    SkillVersionConflict,
)


class SkillRegistry:
    def _rebuild_skills(self) -> None:
        """Hide Skills whose declared tools are currently unavailable."""
        self._skill_specs = {
            skill_id: item for skill_id, item in self._all_skill_specs.items()
            if set(item["tools"]).issubset(self._available_tools)
        }
        if not self._overridden_skills:
            self._skills = [
                self._catalog_item(item)
                for item in self._skill_specs.values()
            ]


    def _catalog_item(self, item: dict, user_id: str | None = None) -> CatalogItem:
        owner_id = item.get("owner_id")
        return CatalogItem(
            id=item["id"],
            name=item["name"],
            description=item["description"],
            version=item["version"],
            visibility=item.get("visibility", "public"),
            owned_by_me=bool(user_id and owner_id == user_id),
            source=item.get("source", "builtin"),
            available=(
                item.get("visibility", "public") not in {"rejected", "disabled"}
                and set(item.get("tools", [])).issubset(self._available_tools)
            ),
        )


    @staticmethod
    def _visible_to(item: dict, user_id: str) -> bool:
        return item.get("visibility", "public") == "public" or item.get("owner_id") == user_id


    def _sync_registered_skills(self) -> None:
        """Load latest SQLite Skill versions and reject built-in ID conflicts."""
        if self.db is None:
            return
        rows = tuple(self.db.execute(
            "SELECT id,version,name,description,tools_json,instructions,owner_id,visibility,"
            "files_json,package_sha256,source_url,source_commit,license_name,review_reason "
            "FROM catalog_skill_versions ORDER BY id,version DESC"
        ).fetchall())
        if rows == self._registered_rows:
            return
        specs = dict(self._static_skill_specs)
        instructions: dict[str, str] = {}
        package_files: dict[str, list[SkillFile]] = {}
        for row in rows:
            (skill_id, version, name, description, tools_json, guidance, owner_id, visibility,
             files_json, package_sha256, source_url, source_commit, license_name,
             review_reason) = row
            if skill_id in specs:
                if skill_id in self._static_skill_specs:
                    raise ValueError(f"Registered Skill conflicts with built-in Skill: {skill_id}")
                continue
            specs[skill_id] = {
                "id": skill_id, "version": version, "name": name,
                "description": description, "tools": json.loads(tools_json),
                "owner_id": owner_id,
                "visibility": visibility,
                "source": "user" if owner_id else "upstream" if source_url else "builtin",
                "package_sha256": package_sha256,
                "source_url": source_url,
                "source_commit": source_commit,
                "license_name": license_name,
                "review_reason": review_reason,
            }
            instructions[skill_id] = guidance
            package_files[skill_id] = [
                SkillFile.model_validate(item) for item in json.loads(files_json or "[]")
            ]
        self._all_skill_specs = specs
        self._registered_instructions = instructions
        self._registered_files = package_files
        self._registered_rows = rows
        self._rebuild_skills()


    def register_skill_version(self, manifest: SkillManifest, instructions: str, *,
                               owner_id: str | None = None, visibility: str = "public",
                               files: list[SkillFile] | None = None,
                               package_sha256: str | None = None,
                               source_url: str | None = None,
                               source_commit: str | None = None,
                               license_name: str | None = None,
                               audit_callback=None, audit_actor='operator:legacy-admin-key',
                               audit_reason='Legacy operator Skill registration', audit_request_id=None,
                               expected_revision=None) -> CatalogItem:
        """Atomically register an immutable, increasing Skill version.

        Args:
            manifest: Validated Skill metadata and tool declarations.
            instructions: Nonempty Skill guidance stored in SQLite.

        Returns:
            Catalog entry for the registered version.

        Raises:
            RuntimeError: Persistent Skill storage is not configured.
            SkillVersionConflict: Built-in ID or existing version conflicts.
            ValueError: Instructions or declared tools are invalid.
        """
        if self.db is None:
            raise RuntimeError("Persistent Skill registry is unavailable")
        if manifest.id in self._static_skill_specs:
            raise SkillVersionConflict
        if (not manifest.name.strip() or not manifest.description.strip()
            or not instructions.strip() or len(instructions) > 65_536
            or len(set(manifest.tools)) != len(manifest.tools)
            or any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", tool)
                   for tool in manifest.tools)
            or (not self._defer_unknown_tool_validation
                and any(tool not in self._known_tools for tool in manifest.tools))):
            raise ValueError("Invalid Skill definition")
        if visibility not in {"private", "review_pending", "public", "rejected", "disabled"}:
            raise ValueError("Invalid Skill visibility")
        tools_json = json.dumps(manifest.tools)
        files_json = json.dumps(
            [item.model_dump() for item in files or []], ensure_ascii=False, separators=(",", ":")
        )
        self.db.execute("BEGIN IMMEDIATE")
        try:
            previous_version = self.db.execute(
                "SELECT COALESCE(MAX(version),0) FROM catalog_skill_versions WHERE id=?",
                (manifest.id,),
            ).fetchone()[0]
            if expected_revision is not None and previous_version != expected_revision:
                raise SkillVersionConflict
            existing = self.db.execute(
                "SELECT name,description,tools_json,instructions,owner_id,visibility,files_json,"
                "package_sha256,source_url,source_commit,license_name "
                "FROM catalog_skill_versions WHERE id=? AND version=?",
                (manifest.id, manifest.version),
            ).fetchone()
            if existing is not None:
                expected = (
                    manifest.name, manifest.description, tools_json, instructions, owner_id,
                    visibility, files_json, package_sha256, source_url, source_commit, license_name,
                )
                if existing != expected:
                    raise SkillVersionConflict
            else:
                latest = self.db.execute(
                    "SELECT MAX(version) FROM catalog_skill_versions WHERE id=?", (manifest.id,),
                ).fetchone()[0]
                if latest is not None and manifest.version <= latest:
                    raise SkillVersionConflict
                self.db.execute(
                    "INSERT INTO catalog_skill_versions "
                    "(id,version,name,description,tools_json,instructions,created_at,owner_id,"
                    "visibility,files_json,package_sha256,source_url,source_commit,license_name) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (manifest.id, manifest.version, manifest.name, manifest.description,
                     tools_json, instructions, datetime.now(UTC).isoformat(), owner_id,
                     visibility, files_json, package_sha256, source_url, source_commit,
                     license_name),
                )
            if audit_callback:
                audit_callback(audit_actor, 'skills:register', manifest.id, audit_reason,
                               {'version': previous_version},
                               {'version': manifest.version, 'tools': manifest.tools,
                                'instruction_sha256': hashlib.sha256(instructions.encode()).hexdigest()},
                               audit_request_id, connection=self.db)
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        self._registered_rows = None
        self._sync_registered_skills()
        return self._catalog_item({
            **manifest.model_dump(), "owner_id": owner_id, "visibility": visibility,
            "source": "user" if owner_id else "upstream" if source_url else "builtin",
        }, owner_id)


    def register_user_skill(self, user_id: str, package, *, request_public: bool) -> CatalogItem:
        """Create an immutable private version or a public review request."""
        if self.db is None:
            raise RuntimeError("Persistent Skill registry is unavailable")
        count, stored = self.db.execute(
            "SELECT COUNT(*),COALESCE(SUM(LENGTH(files_json)),0) "
            "FROM catalog_skill_versions WHERE owner_id=?",
            (user_id,),
        ).fetchone()
        incoming_size = sum(len((item.content or "").encode("utf-8")) for item in package.files)
        if count >= 50 or stored + incoming_size > 20 * 1024 * 1024:
            raise ValueError("Skill package quota exceeded")
        owner_prefix = hashlib.sha256(user_id.encode()).hexdigest()[:12]
        skill_id = f"custom-{owner_prefix}-{package.slug}"
        if self.db.execute(
            "SELECT 1 FROM catalog_skill_versions "
            "WHERE id=? AND visibility='review_pending' LIMIT 1",
            (skill_id,),
        ).fetchone():
            raise SkillVersionConflict
        row = self.db.execute(
            "SELECT COALESCE(MAX(version),0) FROM catalog_skill_versions WHERE id=?",
            (skill_id,),
        ).fetchone()
        version = int(row[0]) + 1
        return self.register_skill_version(
            SkillManifest(
                id=skill_id,
                version=version,
                name=package.name,
                description=package.description,
                tools=[],
            ),
            package.instructions,
            owner_id=user_id,
            visibility="review_pending" if request_public else "private",
            files=list(package.files),
            package_sha256=package.sha256,
        )


    def review_skill(
        self,
        skill_id: str,
        version: int,
        *,
        approved: bool,
        reason: str,
        audit_callback=None,
        audit_actor: str,
        audit_request_id: str | None,
    ) -> CatalogItem:
        """Publish or reject exactly one pending user Skill version."""
        if self.db is None:
            raise RuntimeError("Persistent Skill registry is unavailable")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT owner_id,visibility,name,description FROM catalog_skill_versions "
                "WHERE id=? AND version=?",
                (skill_id, version),
            ).fetchone()
            if row is None or row[0] is None:
                raise ContextNotFound
            if row[1] != "review_pending":
                raise SkillVersionConflict
            visibility = "public" if approved else "rejected"
            self.db.execute(
                "UPDATE catalog_skill_versions SET visibility=?,review_reason=? "
                "WHERE id=? AND version=? AND visibility='review_pending'",
                (visibility, reason, skill_id, version),
            )
            if audit_callback:
                audit_callback(
                    audit_actor,
                    "skills:review",
                    skill_id,
                    reason,
                    {"version": version, "visibility": "review_pending"},
                    {"version": version, "visibility": visibility},
                    audit_request_id,
                    connection=self.db,
                )
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        self._registered_rows = None
        self._sync_registered_skills()
        item = self._all_skill_specs[skill_id]
        return self._catalog_item(item, row[0])


    def pending_skill_reviews(self) -> list[SkillDetail]:
        """List current pending versions for an authorized administrator."""
        if self.db is None:
            return []
        self._sync_registered_skills()
        pending = [
            item for item in self._all_skill_specs.values()
            if item.get("visibility") == "review_pending"
        ]
        return [
            self.skill_detail_for(item["owner_id"], item["id"])
            for item in pending
            if item.get("owner_id")
        ]


    def skills(self) -> list[CatalogItem]:
        """List currently executable Skill versions without user filtering."""
        self._sync_registered_skills()
        return list(self._skills)


    def _allowed_skill_ids(self, user_id: str) -> set[str] | None:
        """Read explicit Skill grants, or ``None`` for unrestricted access."""
        if self.db:
            row = self.db.execute(
                "SELECT skill_ids_json FROM catalog_skill_grants WHERE user_id=?", (user_id,)
            ).fetchone()
            return set(json.loads(row[0])) if row else None
        return self._skill_grants.get(user_id)


    def skills_for(self, user_id: str) -> list[CatalogItem]:
        """List Skills allowed by grants, tool availability, and guest policy."""
        self._sync_registered_skills()
        allowed = self._allowed_skill_ids(user_id)
        return [self._catalog_item(skill, user_id) for skill in self._all_skill_specs.values()
                if self._visible_to(skill, user_id)
                and skill.get("visibility", "public") not in {"rejected", "disabled"}
                and (skill.get("owner_id") == user_id or allowed is None or skill["id"] in allowed)
                and (
                    not set(skill.get("tools", [])).issubset(self._available_tools)
                    or all(self.tool_allowed_for(user_id, tool) for tool in skill.get("tools", []))
                )]


    def skill_detail_for(self, user_id: str, skill_id: str) -> SkillDetail | None:
        """Return one authorized Skill version with its inert file tree."""
        self._sync_registered_skills()
        item = self._all_skill_specs.get(skill_id)
        if item is None or not self._visible_to(item, user_id):
            return None
        files = self._registered_files.get(skill_id)
        if files is None and skill_id in self._static_skill_specs:
            root = self._skill_root / skill_id
            files = [
                SkillFile(
                    path=str(path.relative_to(root)),
                    size=path.stat().st_size,
                    content=path.read_text(encoding="utf-8"),
                )
                for path in sorted(root.rglob("*"))
                if path.is_file() and path.suffix.lower() in {
                    ".md", ".json", ".txt", ".py", ".sh", ".yaml", ".yml", ".toml",
                    ".csv", ".tsv", ".bib",
                }
            ]
        return SkillDetail(
            **self._catalog_item(item, user_id).model_dump(),
            files=files or [],
            license_name=item.get("license_name"),
            source_url=item.get("source_url"),
            source_commit=item.get("source_commit"),
            review_reason=item.get("review_reason"),
        )


    def set_skill_grants(self, user_id: str, skill_ids: list[str], *,
                         audit_callback=None, audit_actor='operator:legacy-admin-key',
                         audit_reason='Legacy operator Skill grants', audit_request_id=None,
                         expected_revision=None) -> list[str]:
        """Replace a user's explicit Skill grants.

        Args:
            user_id: Owner of the grants.
            skill_ids: Unique IDs registered in the full Skill catalog.

        Returns:
            Saved Skill IDs in the submitted order.

        Raises:
            ValueError: An ID is unknown or duplicated.
        """
        self._sync_registered_skills()
        if len(set(skill_ids)) != len(skill_ids) or not set(skill_ids).issubset(self._all_skill_specs):
            raise ValueError("Unknown or duplicate Skill ID")
        if self.db:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self._require_account_active(user_id)
                before = self._allowed_skill_ids(user_id)
                revision = self.db.execute(
                    "SELECT revision FROM admin_revisions WHERE kind='skills' AND resource_id=?",
                    (user_id,),
                ).fetchone() if audit_callback else None
                current_revision = revision[0] if revision else 0
                if expected_revision is not None and current_revision != expected_revision:
                    from app.domain.admin.roles import RevisionConflict
                    raise RevisionConflict('REVISION_CONFLICT')
                self.db.execute(
                    "INSERT INTO catalog_skill_grants (user_id,skill_ids_json) VALUES (?,?) "
                    "ON CONFLICT(user_id) DO UPDATE SET skill_ids_json=excluded.skill_ids_json",
                    (user_id, json.dumps(skill_ids)),
                )
                if audit_callback:
                    self.db.execute("INSERT INTO admin_revisions VALUES ('skills',?,?) "
                                    "ON CONFLICT(kind,resource_id) DO UPDATE SET revision=excluded.revision",
                                    (user_id,current_revision+1))
                    audit_callback(audit_actor, 'skills:grants', user_id, audit_reason,
                                   {'skill_ids':sorted(before) if before is not None else None},
                                   {'skill_ids':skill_ids}, audit_request_id, connection=self.db)
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise
        else:
            self._skill_grants[user_id] = set(skill_ids)
        return skill_ids
