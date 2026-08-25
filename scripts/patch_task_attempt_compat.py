"""Keep the durable task model compatible with databases created by PSKit 0.2.0."""

from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_task_attempt_compat.py PATH_TO_MODELS_PY")

    path = Path(sys.argv[1])
    source = path.read_text(encoding="utf-8")
    declaration = "    attempt_count: Mapped[int] = mapped_column(Integer, default=0)\n"
    if declaration in source:
        return

    anchor = "    progress: Mapped[float] = mapped_column(Float, default=0.0)\n"
    occurrences = source.count(anchor)
    if occurrences != 1:
        raise SystemExit(f"refusing patch: expected one Task.progress anchor, found {occurrences}")

    path.write_text(source.replace(anchor, anchor + declaration), encoding="utf-8")


if __name__ == "__main__":
    main()
