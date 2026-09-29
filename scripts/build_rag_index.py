from __future__ import annotations

from app.db.session import SessionLocal, init_db
from app.rag.qdrant_store import build_qdrant_index


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        result = build_qdrant_index(db)
        print(result)
    finally:
        db.close()


if __name__ == "__main__":
    main()
