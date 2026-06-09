from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.rag.retriever import retrieve


router = APIRouter(prefix="/api/rag", tags=["rag"])


class RagQueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)


@router.post("/query")
def query_rag(payload: RagQueryRequest, _user: User = Depends(get_current_user)):
    backend, chunks = retrieve(payload.query, top_k=payload.top_k)
    return {
        "backend": backend,
        "sources": [
            {
                "source": chunk.source,
                "heading": chunk.heading,
                "score": chunk.score,
                "content": chunk.content,
            }
            for chunk in chunks
        ],
    }
