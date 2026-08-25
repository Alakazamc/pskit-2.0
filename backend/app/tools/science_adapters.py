from __future__ import annotations

import os
import shutil
from pathlib import Path, PurePath


HOMOLOGY_COLUMNS = ("query", "target", "evalue", "bits", "qcov", "tcov", "qaln", "taln")
BLAST_HOMOLOGY_COLUMNS = (
    "query",
    "target",
    "evalue",
    "bits",
    "qlen",
    "slen",
    "alignment_length",
    "qaln",
    "taln",
)
JACKHMMER_DOMTBL_COLUMNS = (
    "target",
    "target_accession",
    "target_length",
    "query",
    "query_accession",
    "query_length",
    "sequence_evalue",
    "sequence_bits",
    "sequence_bias",
    "domain_number",
    "domain_count",
    "conditional_evalue",
    "independent_evalue",
    "domain_bits",
    "domain_bias",
    "hmm_from",
    "hmm_to",
    "alignment_from",
    "alignment_to",
    "envelope_from",
    "envelope_to",
    "accuracy",
    "description",
)
AF3_OUTPUT_NORMALIZATION_SCRIPT = """
import os
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
uid = int(sys.argv[2])
gid = int(sys.argv[3])
for current_root, directory_names, file_names in os.walk(
    root,
    topdown=False,
    followlinks=False,
):
    current = Path(current_root)
    for name in [*file_names, *directory_names]:
        path = current / name
        if path.is_symlink():
            path.unlink()
        else:
            os.chown(path, uid, gid, follow_symlinks=False)
os.chown(root, uid, gid, follow_symlinks=False)
""".strip()


class ScientificToolDependencyError(RuntimeError):
    pass


def current_worker_user_spec() -> str:
    """返回用于科学工具容器的当前 POSIX Worker 身份。"""
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if not callable(getuid) or not callable(getgid):
        raise ScientificToolDependencyError(
            "jackhmmer-docker requires a POSIX worker identity"
        )
    return f"{getuid()}:{getgid()}"


def cleanup_scientific_scratch_dir(output_dir: Path, scratch_dir: Path) -> None:
    """删除任务隔离目录内不应登记为产物的科学工具临时工作区。"""
    output_root = output_dir.resolve()
    scratch_parent = scratch_dir.parent.resolve()
    if scratch_parent != output_root and output_root not in scratch_parent.parents:
        raise ScientificToolDependencyError(
            "scientific scratch directory escaped task output root"
        )
    if scratch_dir.is_symlink():
        scratch_dir.unlink()
        return
    if not scratch_dir.exists():
        return
    resolved = scratch_dir.resolve()
    if output_root not in resolved.parents:
        raise ScientificToolDependencyError(
            "scientific scratch directory escaped task output root"
        )
    try:
        shutil.rmtree(scratch_dir)
    except OSError as exc:
        raise ScientificToolDependencyError(
            f"failed to remove scientific scratch directory: {exc}"
        ) from exc


def build_foldseek_command(
    *,
    binary: PurePath,
    query_path: PurePath,
    database_path: PurePath,
    output_path: PurePath,
    temp_dir: PurePath,
    max_hits: int,
    evalue: float,
    sensitivity: float,
) -> list[str]:
    return [
        str(binary),
        "easy-search",
        str(query_path),
        str(database_path),
        str(output_path),
        str(temp_dir),
        "--format-output",
        ",".join(HOMOLOGY_COLUMNS),
        "--max-seqs",
        str(max_hits),
        "-e",
        str(evalue),
        "-s",
        str(sensitivity),
    ]


def build_af3_output_normalization_command(
    *,
    docker_binary: PurePath,
    container_image: str,
    output_dir: PurePath,
    worker_uid: int,
    worker_gid: int,
) -> list[str]:
    """构造隔离的 AF3 输出所有权与符号链接归一化命令。"""
    if not container_image.strip():
        raise ScientificToolDependencyError("AF3 output normalization requires an image")
    output_text = str(output_dir)
    if not output_text.startswith("/"):
        raise ScientificToolDependencyError(
            "AF3 output normalization requires an absolute output directory"
        )
    return [
        str(docker_binary),
        "run",
        "--rm",
        "--pull",
        "never",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--cap-add",
        "CHOWN",
        "--cap-add",
        "DAC_OVERRIDE",
        "--security-opt",
        "no-new-privileges",
        "--user",
        "0:0",
        "--volume",
        f"{output_text}:{output_text}:rw",
        "--entrypoint",
        "python3",
        container_image,
        "-c",
        AF3_OUTPUT_NORMALIZATION_SCRIPT,
        output_text,
        str(worker_uid),
        str(worker_gid),
    ]


def build_sequence_search_command(
    *,
    backend: str,
    binary: PurePath,
    query_path: PurePath,
    database_path: PurePath,
    output_path: PurePath,
    temp_dir: PurePath,
    max_hits: int,
    evalue: float,
    sensitivity: float,
    container_image: str | None = None,
) -> list[str]:
    normalized_backend = backend.strip().lower()
    if normalized_backend == "mmseqs":
        return [
            str(binary),
            "easy-search",
            str(query_path),
            str(database_path),
            str(output_path),
            str(temp_dir),
            "--format-output",
            ",".join(HOMOLOGY_COLUMNS),
            "--max-seqs",
            str(max_hits),
            "-e",
            str(evalue),
            "-s",
            str(sensitivity),
        ]
    if normalized_backend == "blastp":
        return [
            str(binary),
            "-query",
            str(query_path),
            "-db",
            str(database_path),
            "-out",
            str(output_path),
            "-outfmt",
            "6 qseqid sseqid evalue bitscore qlen slen length qseq sseq",
            "-max_target_seqs",
            str(max_hits),
            "-evalue",
            str(evalue),
        ]
    if normalized_backend == "jackhmmer-docker":
        if not container_image:
            raise ScientificToolDependencyError(
                "jackhmmer-docker requires PSKIT_AF3_IMAGE"
            )
        # wzf：Worker 与宿主机对科研输入、输出使用同一绝对路径，
        # 因此 Docker 守护进程可以只读挂载数据库，并回写本轮隔离输出目录。
        work_root = output_path.parent
        database_root = database_path.parent
        volumes = [
            "--volume",
            f"{work_root}:{work_root}:rw",
        ]
        if database_root != work_root:
            volumes.extend(
                [
                    "--volume",
                    f"{database_root}:{database_root}:ro",
                ]
            )
        return [
            str(binary),
            "run",
            "--rm",
            "--pull",
            "never",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            # wzf：容器沿用当前 Worker 身份，确保能写入 Worker 创建的隔离目录，
            # 同时避免通过 root 或放宽宿主机目录权限绕过访问控制。
            current_worker_user_spec(),
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            *volumes,
            "--workdir",
            str(work_root),
            "--entrypoint",
            "/hmmer/bin/jackhmmer",
            container_image,
            "--noali",
            "--cpu",
            "4",
            "-N",
            "1",
            "-E",
            str(evalue),
            "--domE",
            str(evalue),
            "--domtblout",
            str(output_path),
            str(query_path),
            str(database_path),
        ]
    raise ScientificToolDependencyError(
        "Unsupported sequence search backend: "
        f"{backend}. Expected mmseqs, blastp, or jackhmmer-docker."
    )


def _parse_jackhmmer_hits(path: Path, *, max_hits: int | None) -> list[dict]:
    best_by_target: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        values = line.split(maxsplit=len(JACKHMMER_DOMTBL_COLUMNS) - 1)
        if len(values) == len(JACKHMMER_DOMTBL_COLUMNS) - 1:
            values.append("")
        if len(values) < len(JACKHMMER_DOMTBL_COLUMNS):
            raise ScientificToolDependencyError(
                "JackHMMER domtblout has "
                f"{len(values)} columns; expected {len(JACKHMMER_DOMTBL_COLUMNS)}"
            )
        raw = dict(zip(JACKHMMER_DOMTBL_COLUMNS, values, strict=True))
        try:
            query_length = int(raw["query_length"])
            target_length = int(raw["target_length"])
            hmm_from = int(raw["hmm_from"])
            hmm_to = int(raw["hmm_to"])
            alignment_from = int(raw["alignment_from"])
            alignment_to = int(raw["alignment_to"])
            if query_length <= 0 or target_length <= 0:
                raise ValueError("sequence length must be positive")
            hit = {
                "query": raw["query"],
                "target": raw["target"],
                "evalue": float(raw["sequence_evalue"]),
                "bits": float(raw["sequence_bits"]),
                "qcov": min(1.0, (hmm_to - hmm_from + 1) / query_length),
                "tcov": min(
                    1.0,
                    (alignment_to - alignment_from + 1) / target_length,
                ),
                "qaln": f"{hmm_from}-{hmm_to}",
                "taln": f"{alignment_from}-{alignment_to}",
                "domain_evalue": float(raw["independent_evalue"]),
                "domain_bits": float(raw["domain_bits"]),
                "accuracy": float(raw["accuracy"]),
                "description": raw["description"],
            }
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            raise ScientificToolDependencyError(
                f"JackHMMER domtblout contains invalid numeric data: {exc}"
            ) from exc
        previous = best_by_target.get(str(hit["target"]))
        if previous is None or (
            float(hit["evalue"]),
            -float(hit["bits"]),
        ) < (
            float(previous["evalue"]),
            -float(previous["bits"]),
        ):
            best_by_target[str(hit["target"])] = hit
    hits = sorted(
        best_by_target.values(),
        key=lambda item: (float(item["evalue"]), -float(item["bits"])),
    )
    return hits[:max_hits] if max_hits is not None else hits


def parse_homology_hits(
    path: Path,
    *,
    backend: str = "standard",
    max_hits: int | None = None,
) -> list[dict]:
    hits: list[dict] = []
    if not path.exists():
        raise ScientificToolDependencyError(f"Homology search produced no result file: {path}")
    if backend.strip().lower() == "jackhmmer-docker":
        return _parse_jackhmmer_hits(path, max_hits=max_hits)
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        values = line.split("\t")
        columns = (
            BLAST_HOMOLOGY_COLUMNS
            if backend.strip().lower() == "blastp"
            else HOMOLOGY_COLUMNS
        )
        if len(values) < len(columns):
            raise ScientificToolDependencyError(
                f"Homology result has {len(values)} columns; expected {len(columns)}"
            )
        hit: dict[str, object] = dict(zip(columns, values, strict=False))
        if columns == BLAST_HOMOLOGY_COLUMNS:
            try:
                qlen = float(hit["qlen"])
                slen = float(hit["slen"])
                alignment_length = float(hit["alignment_length"])
                if qlen <= 0 or slen <= 0:
                    raise ValueError("sequence length must be positive")
                # wzf：BLAST 没有直接提供 target coverage；由比对长度分别除以 query/subject 长度。
                hit["qcov"] = min(1.0, alignment_length / qlen)
                hit["tcov"] = min(1.0, alignment_length / slen)
            except (TypeError, ValueError, ZeroDivisionError) as exc:
                raise ScientificToolDependencyError(
                    f"BLAST result has invalid coverage lengths: {exc}"
                ) from exc
        for key in ("evalue", "bits", "qcov", "tcov"):
            try:
                hit[key] = float(hit[key])
            except (TypeError, ValueError):
                pass
        hits.append(hit)
    return hits[:max_hits] if max_hits is not None else hits


def _normalize_candidate(item: object, index: int) -> dict:
    if isinstance(item, str):
        sequence = "".join(item.split()).upper()
        if not sequence:
            raise ScientificToolDependencyError(f"Candidate {index} has an empty sequence")
        return {"external_id": None, "sequence": sequence, "metrics": {}, "metadata": {}}
    if not isinstance(item, dict):
        raise ScientificToolDependencyError(
            f"Candidate {index} must be a sequence string or an object"
        )
    raw = dict(item)
    sequence_value = (
        raw.pop("sequence", None)
        or raw.pop("seq", None)
        or raw.pop("rna_sequence", None)
        or raw.pop("peptide_sequence", None)
    )
    sequence = "".join(str(sequence_value or "").split()).upper()
    if not sequence:
        raise ScientificToolDependencyError(f"Candidate {index} has no sequence")
    external_id = raw.pop("id", None) or raw.pop("candidate_id", None)
    metrics = raw.pop("metrics", {})
    if not isinstance(metrics, dict):
        metrics = {"value": metrics}
    metadata: dict[str, object] = {}
    for key, value in raw.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            metrics.setdefault(key, value)
        else:
            metadata[key] = value
    return {
        "external_id": str(external_id) if external_id is not None else None,
        "sequence": sequence,
        "metrics": metrics,
        "metadata": metadata,
    }


def normalize_candidate_result(
    raw_result: object,
    *,
    source_label: str = "Candidate generator",
) -> dict:
    if isinstance(raw_result, list):
        raw_candidates: list[object] = raw_result
        metadata = {}
    elif isinstance(raw_result, dict):
        if raw_result.get("error"):
            # wzf：服务把推理故障包装成成功传输的 JSON 时，必须按应用层错误处理；
            # 不回显第三方错误正文，避免路径、代理或凭证进入用户错误信息。
            raise ScientificToolDependencyError(
                f"{source_label} returned an application-level error"
            )
        candidate_source = raw_result
        nested_result = raw_result.get("result")
        if isinstance(nested_result, dict):
            if nested_result.get("error"):
                raise ScientificToolDependencyError(
                    f"{source_label} returned an application-level error"
                )
            candidate_source = nested_result
        candidate_value = (
            candidate_source.get("candidates")
            or candidate_source.get("generated_candidates")
            or candidate_source.get("generated_rnas")
            or candidate_source.get("generated_peptides")
            or candidate_source.get("peptides")
            or candidate_source.get("sequences")
        )
        raw_candidates = candidate_value if isinstance(candidate_value, list) else []
        candidate_keys = {
            "candidates",
            "generated_candidates",
            "generated_rnas",
            "generated_peptides",
            "peptides",
            "sequences",
        }
        metadata = {
            key: value
            for key, value in raw_result.items()
            if key not in candidate_keys | {"result"}
        }
        if candidate_source is not raw_result:
            metadata["result_metadata"] = {
                key: value
                for key, value in candidate_source.items()
                if key not in candidate_keys
            }
    else:
        raise ScientificToolDependencyError(f"{source_label} returned an unsupported result type")
    if not raw_candidates:
        raise ScientificToolDependencyError(f"{source_label} returned no candidates")
    return {
        "candidates": [
            _normalize_candidate(item, index)
            for index, item in enumerate(raw_candidates, start=1)
        ],
        "metadata": metadata,
    }
