"""Import a reviewed subset of Google DeepMind Science Skills into PSKit."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

UPSTREAM_URL = "https://github.com/google-deepmind/science-skills"
SELECTED = {
    "pubmed_database": ["pubmed_search"],
    "literature_search_europepmc": ["europepmc_search"],
    "ncbi_sequence_fetch": ["ncbi_sequence_fetch"],
    "uniprot_database": ["fetch_uniprot"],
    "pdb_database": ["search_pdb"],
    "alphafold_database_fetch_and_analyze": ["alphafold_database"],
    "clinvar_database": ["clinvar_lookup"],
    "gnomad_database": ["gnomad_lookup"],
    "chembl_database": ["chembl_search"],
    "pubchem_database": ["pubchem_lookup"],
    "reactome_database": ["reactome_analysis"],
    "string_database": ["string_network"],
    "opentargets_database": ["opentargets_search"],
    "interpro_database": ["interpro_lookup"],
}


def frontmatter(markdown: str) -> tuple[str, str]:
    """Extract the standard scalar name and description fields."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", markdown, re.DOTALL)
    if not match:
        raise ValueError("SKILL.md frontmatter is missing")
    lines = match.group(1).splitlines()
    values: dict[str, str] = {}
    index = 0
    while index < len(lines):
        field = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$", lines[index])
        if not field:
            index += 1
            continue
        key, value = field.group(1), field.group(2).strip()
        if value in {">", ">-", "|", "|-"}:
            continuation: list[str] = []
            index += 1
            while index < len(lines) and (
                not lines[index].strip() or lines[index][:1].isspace()
            ):
                continuation.append(lines[index].strip())
                index += 1
            values[key] = " ".join(part for part in continuation if part)
            continue
        values[key] = value.strip("\"'")
        index += 1
    if not values.get("name") or not values.get("description"):
        raise ValueError("SKILL.md name or description is missing")
    return values["name"].strip(), values["description"].strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    if not (args.source / "LICENSE").is_file() or not (args.source / "SKILL_LICENSES.md").is_file():
        raise SystemExit("Upstream license files are required")
    for source_name, tools in SELECTED.items():
        source = args.source / "skills" / source_name
        markdown = (source / "SKILL.md").read_text(encoding="utf-8")
        upstream_name, description = frontmatter(markdown)
        skill_id = upstream_name.replace("_", "-")
        if not re.fullmatch(r"[a-z][a-z0-9-]*", skill_id):
            raise SystemExit(f"Invalid upstream Skill name: {upstream_name}")
        target = args.destination / skill_id
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        (target / "manifest.json").write_text(
            json.dumps(
                {
                    "id": skill_id,
                    "version": 1,
                    "name": skill_id.replace("-", " ").title(),
                    "description": description,
                    "tools": tools,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        (target / "UPSTREAM.json").write_text(
            json.dumps(
                {
                    "repository": UPSTREAM_URL,
                    "commit": args.commit,
                    "path": f"skills/{source_name}",
                    "license": "Apache-2.0; database terms listed in SKILL_LICENSES.md",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    shutil.copy2(args.source / "LICENSE", args.destination / "UPSTREAM_LICENSE.txt")
    shutil.copy2(
        args.source / "SKILL_LICENSES.md",
        args.destination / "UPSTREAM_SKILL_LICENSES.md",
    )


if __name__ == "__main__":
    main()
