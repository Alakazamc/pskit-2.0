"""Inventory missing Python function docstrings in the backend application."""

import argparse
import ast
from pathlib import Path


def missing_functions(path: Path) -> list[str]:
    """List functions without a docstring in a Python source file.

    Args:
        path: Python source file to inspect.

    Returns:
        Qualified function names that have no AST docstring.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    missing: list[str] = []

    def walk(node: ast.AST, parents: tuple[str, ...]) -> None:
        """Visit nested definitions and retain their qualified names."""
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = (*parents, child.name)
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and not ast.get_docstring(child):
                    missing.append(".".join(name))
                walk(child, name)
            else:
                walk(child, parents)

    walk(tree, ())
    return missing


def main(argv: list[str] | None = None) -> int:
    """Print missing counts and optionally fail when any function lacks a docstring.

    Args:
        argv: Optional command-line arguments; defaults to the process arguments.

    Returns:
        Zero for inventory mode or a complete check; one for an incomplete check.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1] / "app")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    total = 0
    for path in sorted(args.root.rglob("*.py")):
        missing = missing_functions(path)
        if missing:
            print(f"{path.relative_to(args.root)}: {len(missing)} missing")
            total += len(missing)
    print(f"Total missing docstrings: {total}")
    return 1 if args.check and total else 0


if __name__ == "__main__":
    raise SystemExit(main())
