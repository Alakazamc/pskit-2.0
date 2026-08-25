from pathlib import Path

from app.config import get_settings


def check_file_exists(path: str) -> dict | None:
    file_path = Path(path)
    if not file_path.exists():
        return {
            "error_type": "artifact_not_found",
            "message": f"Input file does not exist: {path}",
        }
    return None


def preflight_binding_site(ligand_type: str, pdb_path: str) -> list[dict]:
    settings = get_settings()
    errors: list[dict] = []
    missing_file = check_file_exists(pdb_path)
    if missing_file:
        errors.append(missing_file)

    if ligand_type not in {"DNA", "RNA"}:
        errors.append(
            {
                "error_type": "invalid_input",
                "message": "ligand_type must be DNA or RNA",
            }
        )

    root = settings.pskit_model_parameters
    if root is None:
        errors.append(
            {
                "error_type": "missing_model_weight",
                "dependency": "PSKIT_MODEL_PARAMETERS",
                "message": "PSKIT_MODEL_PARAMETERS is not configured",
            }
        )
    else:
        weight_name = "INABe_RNA.pth" if ligand_type == "RNA" else "INABe_DNA.pth"
        for relative in [weight_name, "esm2_650M", "SaProt_650M_PDB"]:
            if not (root / relative).exists():
                errors.append(
                    {
                        "error_type": "missing_model_weight",
                        "dependency": relative,
                        "message": f"Missing model dependency: {root / relative}",
                    }
                )

    if settings.pskit_foldseek is None or not settings.pskit_foldseek.exists():
        errors.append(
            {
                "error_type": "missing_dependency",
                "dependency": "Foldseek",
                "message": "PSKIT_FOLDSEEK is missing or invalid",
            }
        )
    return errors
