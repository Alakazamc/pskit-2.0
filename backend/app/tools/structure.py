from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from uuid import UUID

from Bio.PDB import MMCIFParser, PDBIO, PDBParser, Select
from Bio.PDB.Chain import Chain
from Bio.PDB.Residue import Residue
from sqlalchemy.orm import Session

from app.artifacts.service import (
    register_local_artifact,
    resolve_owned_local_artifact_reference,
    session_artifact_dir,
)
from app.db.models import User
from app.tools.external import ToolExecutionError


PROTEIN_RESIDUES = {
    "ALA",
    "ARG",
    "ASN",
    "ASP",
    "CYS",
    "GLN",
    "GLU",
    "GLY",
    "HIS",
    "ILE",
    "LEU",
    "LYS",
    "MET",
    "PHE",
    "PRO",
    "SER",
    "THR",
    "TRP",
    "TYR",
    "VAL",
    "SEC",
    "PYL",
}
DNA_RESIDUES = {"DA", "DC", "DG", "DT", "DI"}
RNA_RESIDUES = {"A", "C", "G", "U", "I"}
NUCLEIC_RESIDUES = DNA_RESIDUES | RNA_RESIDUES


def parse_structure(path: Path, file_format: str):
    fmt = file_format.lower().strip()
    if fmt == "cif":
        parser = MMCIFParser(QUIET=True)
    elif fmt == "pdb":
        parser = PDBParser(QUIET=True)
    else:
        raise ToolExecutionError("format must be cif or pdb")
    return parser.get_structure(path.stem, str(path))


def first_model(structure):
    return next(structure.get_models())


def residue_name(residue: Residue) -> str:
    return residue.get_resname().strip().upper()


def is_protein_residue(residue: Residue) -> bool:
    return residue_name(residue) in PROTEIN_RESIDUES


def is_nucleic_residue(residue: Residue) -> bool:
    return residue_name(residue) in NUCLEIC_RESIDUES


def chain_type(chain: Chain) -> str:
    protein = 0
    nucleic = 0
    for residue in chain:
        if is_protein_residue(residue):
            protein += 1
        elif is_nucleic_residue(residue):
            nucleic += 1
    if protein and not nucleic:
        return "protein"
    if nucleic and not protein:
        return "nucleic_acid"
    if protein and nucleic:
        return "mixed"
    return "other"


class ChainSelect(Select):
    def __init__(self, chain_id: str) -> None:
        self.chain_id = chain_id

    def accept_chain(self, chain: Chain) -> bool:
        return chain.id == self.chain_id


class MoleculeTypeSelect(Select):
    def __init__(self, molecule_type: str) -> None:
        self.molecule_type = molecule_type

    def accept_residue(self, residue: Residue) -> bool:
        if self.molecule_type == "protein":
            return is_protein_residue(residue)
        if self.molecule_type == "nucleic_acid":
            return is_nucleic_residue(residue)
        return False


class FragmentSelect(Select):
    def __init__(self, chain_id: str, start: int | None, end: int | None) -> None:
        self.chain_id = chain_id
        self.start = start
        self.end = end

    def accept_chain(self, chain: Chain) -> bool:
        return chain.id == self.chain_id

    def accept_residue(self, residue: Residue) -> bool:
        resseq = int(residue.id[1])
        if self.start is not None and resseq < self.start:
            return False
        if self.end is not None and resseq > self.end:
            return False
        return True


def save_selected(structure, output_file: Path, selector: Select) -> None:
    io = PDBIO()
    io.set_structure(structure)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    io.save(str(output_file), selector)


def residue_label(chain: Chain, residue: Residue) -> str:
    return f"{chain.id}-{residue.id[1]}-{residue_name(residue)}"


def residue_atoms(residue: Residue):
    return [atom for atom in residue.get_atoms() if atom.element != "H"]


def residue_point(residue: Residue) -> tuple[float, float, float] | None:
    preferred_atoms = ["CA", "C4'", "C4*", "P"]
    for name in preferred_atoms:
        if name in residue:
            coord = residue[name].get_coord()
            return float(coord[0]), float(coord[1]), float(coord[2])
    atoms = residue_atoms(residue)
    if not atoms:
        return None
    coords = [atom.get_coord() for atom in atoms]
    return (
        float(sum(coord[0] for coord in coords) / len(coords)),
        float(sum(coord[1] for coord in coords) / len(coords)),
        float(sum(coord[2] for coord in coords) / len(coords)),
    )


def distance(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.sqrt(sum((a[index] - b[index]) ** 2 for index in range(3)))


def min_residue_distance(a: Residue, b: Residue) -> float:
    min_distance = float("inf")
    for atom_a in residue_atoms(a):
        coord_a = atom_a.get_coord()
        for atom_b in residue_atoms(b):
            coord_b = atom_b.get_coord()
            dist = float(math.sqrt(sum((coord_a[index] - coord_b[index]) ** 2 for index in range(3))))
            if dist < min_distance:
                min_distance = dist
    return min_distance


def resolve_input_structure_path(db: Session, user: User, path: str) -> Path:
    try:
        return resolve_owned_local_artifact_reference(
            db,
            user,
            file_path=path,
        )
    except (FileNotFoundError, NotImplementedError, ValueError) as exc:
        raise ToolExecutionError(
            "Input structure must be a registered artifact owned by the current user"
        ) from exc


def split_pdb_by_chain(
    db: Session,
    user: User,
    session_id: UUID,
    tool_call_id: str,
    pdb_path: str,
    file_format: str,
) -> dict:
    input_path = resolve_input_structure_path(db, user, pdb_path)
    structure = parse_structure(input_path, file_format)
    out_dir = session_artifact_dir(user.id, session_id, tool_call_id)
    artifacts = []
    for chain in first_model(structure):
        chain_id = chain.id.strip() or "blank"
        output_file = out_dir / f"{input_path.stem}_chain_{chain_id}.pdb"
        save_selected(structure, output_file, ChainSelect(chain.id))
        artifact = register_local_artifact(db, user, session_id, None, output_file, "structure", "chemical/x-pdb")
        artifacts.append(
            {
                "chain": chain.id,
                "chain_type": chain_type(chain),
                "artifact_id": str(artifact.id),
                "filename": artifact.filename,
                "download_url": f"/api/files/{artifact.id}/download",
                "path": str(output_file),
            }
        )
    return {"input": str(input_path), "chains": artifacts, "count": len(artifacts)}


def split_complex(
    db: Session,
    user: User,
    session_id: UUID,
    tool_call_id: str,
    pdb_path: str,
    file_format: str,
) -> dict:
    input_path = resolve_input_structure_path(db, user, pdb_path)
    structure = parse_structure(input_path, file_format)
    out_dir = session_artifact_dir(user.id, session_id, tool_call_id)
    outputs = []
    for molecule_type, filename in [
        ("protein", f"{input_path.stem}_protein.pdb"),
        ("nucleic_acid", f"{input_path.stem}_nucleic_acid.pdb"),
    ]:
        output_file = out_dir / filename
        save_selected(structure, output_file, MoleculeTypeSelect(molecule_type))
        if output_file.exists() and output_file.stat().st_size > 0:
            artifact = register_local_artifact(db, user, session_id, None, output_file, "structure", "chemical/x-pdb")
            outputs.append(
                {
                    "molecule_type": molecule_type,
                    "artifact_id": str(artifact.id),
                    "filename": artifact.filename,
                    "download_url": f"/api/files/{artifact.id}/download",
                    "path": str(output_file),
                }
            )
    return {"input": str(input_path), "parts": outputs, "count": len(outputs)}


def extract_fragment(
    db: Session,
    user: User,
    session_id: UUID,
    tool_call_id: str,
    pdb_path: str,
    chain: str,
    file_format: str,
    start: int | None = None,
    end: int | None = None,
) -> dict:
    input_path = resolve_input_structure_path(db, user, pdb_path)
    structure = parse_structure(input_path, file_format)
    chain_ids = {item.id for item in first_model(structure)}
    if chain not in chain_ids:
        raise ToolExecutionError(f"Chain {chain} not found. Available chains: {sorted(chain_ids)}")
    out_dir = session_artifact_dir(user.id, session_id, tool_call_id)
    range_suffix = f"_{start or 'start'}_{end or 'end'}"
    output_file = out_dir / f"{input_path.stem}_chain_{chain}{range_suffix}.pdb"
    save_selected(structure, output_file, FragmentSelect(chain, start, end))
    artifact = register_local_artifact(db, user, session_id, None, output_file, "structure", "chemical/x-pdb")
    return {
        "input": str(input_path),
        "chain": chain,
        "start": start,
        "end": end,
        "artifact_id": str(artifact.id),
        "filename": artifact.filename,
        "download_url": f"/api/files/{artifact.id}/download",
        "path": str(output_file),
    }


def calculate_contact_map(
    db: Session,
    user: User,
    session_id: UUID,
    tool_call_id: str,
    pdb_path: str,
    file_format: str,
    chain: str | None = None,
    mode: str = "d",
    k: int | None = None,
) -> dict:
    input_path = resolve_input_structure_path(db, user, pdb_path)
    structure = parse_structure(input_path, file_format)
    residues = []
    for chain_obj in first_model(structure):
        if chain and chain_obj.id != chain:
            continue
        for residue in chain_obj:
            if is_protein_residue(residue) or is_nucleic_residue(residue):
                point = residue_point(residue)
                if point is not None:
                    residues.append((chain_obj, residue, point))
    axis = [residue_label(chain_obj, residue) for chain_obj, residue, _point in residues]
    values = []
    mode = (mode or "d").lower()
    if mode == "knn":
        neighbors = max(1, int(k or 8))
        contact_pairs = set()
        for i, item in enumerate(residues):
            distances = []
            for j, other in enumerate(residues):
                if i == j:
                    continue
                distances.append((distance(item[2], other[2]), j))
            for _dist, j in sorted(distances)[:neighbors]:
                contact_pairs.add(tuple(sorted((i, j))))
        for i in range(len(residues)):
            for j in range(i + 1, len(residues)):
                values.append(1 if (i, j) in contact_pairs else 0)
    else:
        cutoff = 8.0
        for i in range(len(residues)):
            for j in range(i + 1, len(residues)):
                values.append(1 if distance(residues[i][2], residues[j][2]) <= cutoff else 0)

    out_dir = session_artifact_dir(user.id, session_id, tool_call_id)
    output_file = out_dir / f"contact_map_{input_path.stem}.json"
    output = {"axis": axis, "values": values, "mode": mode, "chain": chain}
    output_file.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    artifact = register_local_artifact(db, user, session_id, None, output_file, "json", "application/json")
    return {
        "input": str(input_path),
        "residue_count": len(axis),
        "artifact_id": str(artifact.id),
        "filename": artifact.filename,
        "download_url": f"/api/files/{artifact.id}/download",
        "path": str(output_file),
    }


def annotate_binding_pairs(
    db: Session,
    user: User,
    session_id: UUID,
    tool_call_id: str,
    pdb_path: str,
    file_format: str,
    cutoff: float = 3.5,
) -> dict:
    input_path = resolve_input_structure_path(db, user, pdb_path)
    structure = parse_structure(input_path, file_format)
    protein_residues = []
    nucleic_residues = []
    for chain_obj in first_model(structure):
        for residue in chain_obj:
            if is_protein_residue(residue):
                protein_residues.append((chain_obj, residue))
            elif is_nucleic_residue(residue):
                nucleic_residues.append((chain_obj, residue))

    pairs = []
    for protein_chain, protein_residue in protein_residues:
        for nucleic_chain, nucleic_residue in nucleic_residues:
            dist = min_residue_distance(protein_residue, nucleic_residue)
            if dist <= cutoff:
                pairs.append(
                    {
                        "pair": f"{residue_label(protein_chain, protein_residue)}_{residue_label(nucleic_chain, nucleic_residue)}",
                        "distance": round(dist, 3),
                    }
                )
    pairs.sort(key=lambda item: item["distance"])

    out_dir = session_artifact_dir(user.id, session_id, tool_call_id)
    output_file = out_dir / f"{input_path.stem}_binding_pairs.csv"
    with output_file.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["pair", "distance"])
        writer.writeheader()
        writer.writerows(pairs)
    artifact = register_local_artifact(db, user, session_id, None, output_file, "table", "text/csv")
    return {
        "input": str(input_path),
        "cutoff": cutoff,
        "pair_count": len(pairs),
        "top_pairs": pairs[:20],
        "artifact_id": str(artifact.id),
        "filename": artifact.filename,
        "download_url": f"/api/files/{artifact.id}/download",
        "path": str(output_file),
    }
