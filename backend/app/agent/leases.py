from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import event, update
from sqlalchemy.orm import Session

from app.db.models import AgentTurn, now_utc


_AGENT_COMMIT_FENCE_KEY = "pskit_agent_commit_fence"


class AgentLeaseRevoked(RuntimeError):
    """当前 Agent 执行器已不再持有该轮次的写入资格。"""


def install_agent_commit_fence(
    db: Session,
    turn_id: UUID,
    lease_token: str,
) -> None:
    """让该 Session 的每次提交都在同一事务内核验 Agent 租约。"""

    db.info[_AGENT_COMMIT_FENCE_KEY] = {
        "turn_id": turn_id,
        "lease_token": lease_token,
    }


def clear_agent_commit_fence(db: Session) -> None:
    db.info.pop(_AGENT_COMMIT_FENCE_KEY, None)


@event.listens_for(Session, "before_commit")
def _fence_agent_side_effect_commit(db: Session) -> None:
    """wzf：把租约校验和工具副作用放进同一事务，阻止旧执行器提交结果。"""

    fence: dict[str, Any] | None = db.info.get(_AGENT_COMMIT_FENCE_KEY)
    if not fence:
        return
    guarded = db.execute(
        update(AgentTurn)
        .where(
            AgentTurn.id == fence["turn_id"],
            AgentTurn.status == "running",
            AgentTurn.lease_token == fence["lease_token"],
            AgentTurn.lease_expires_at.is_not(None),
            AgentTurn.lease_expires_at > now_utc(),
        )
        .values(updated_at=AgentTurn.updated_at)
    )
    if guarded.rowcount != 1:
        raise AgentLeaseRevoked("Agent 轮次执行租约已失效，拒绝提交工具副作用")
