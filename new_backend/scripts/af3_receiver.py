"""Persistent AF3 pull receiver for a long-lived compute container.

Uses only the Python standard library so it runs in the pinned AF3 image.
The spool directory must be bind mounted; it is retained until the API
acknowledges the terminal result. The receiver and AF3 compute loop run in
separate containers sharing the spool, so a receiver container crash does not
stop a running AF3 calculation.
"""

import argparse
import fcntl
import hashlib
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

LOG = logging.getLogger("af3_receiver")
MAX_ARTIFACT_BYTES = 20 * 1024 * 1024
JOB_ID = re.compile(r"^[0-9a-fA-F-]{36}$")


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class Journal:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS jobs ("
            "id TEXT PRIMARY KEY, claim_json TEXT NOT NULL, uploaded_json TEXT NOT NULL)"
        )
        self.db.commit()

    def put(self, claim: dict) -> None:
        if not JOB_ID.fullmatch(claim["id"]):
            raise ValueError("Invalid compute job ID")
        saved = self.get(claim["id"])
        if saved is not None and (saved["attempt"] != claim["attempt"]
                                  or saved["lease_token"] != claim["lease_token"]):
            raise ValueError("Compute claim changed while local result is pending")
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO jobs (id,claim_json,uploaded_json) VALUES (?,?,?)",
                (claim["id"], json.dumps(claim), "[]"),
            )

    def get(self, job_id: str) -> dict | None:
        row = self.db.execute("SELECT claim_json FROM jobs WHERE id=?", (job_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def all(self) -> list[dict]:
        rows = self.db.execute("SELECT claim_json FROM jobs ORDER BY rowid").fetchall()
        return [json.loads(row[0]) for row in rows]

    def uploaded(self, job_id: str) -> set[str]:
        row = self.db.execute("SELECT uploaded_json FROM jobs WHERE id=?", (job_id,)).fetchone()
        return set(json.loads(row[0])) if row else set()

    def mark_uploaded(self, job_id: str, artifact_id: str) -> None:
        uploaded = self.uploaded(job_id)
        uploaded.add(artifact_id)
        with self.db:
            self.db.execute("UPDATE jobs SET uploaded_json=? WHERE id=?",
                            (json.dumps(sorted(uploaded)), job_id))

    def delete(self, job_id: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM jobs WHERE id=?", (job_id,))


class ComputeAPI:
    def __init__(self, base_url: str, key: str, *, timeout: int = 15) -> None:
        self.base_url = base_url.rstrip("/")
        self.key = key
        self.timeout = timeout

    def _request(self, method: str, path: str, *, payload: dict | None = None,
                 body: bytes | None = None, headers: dict | None = None) -> object:
        content = json.dumps(payload).encode() if payload is not None else body
        request = urllib.request.Request(
            self.base_url + path, data=content, method=method,
            headers={"X-Compute-Key": self.key,
                     **({"Content-Type": "application/json"} if payload is not None else {}),
                     **(headers or {})},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.load(response)

    def owned(self, worker_id: str) -> list[dict]:
        path = "/internal/compute/af3/jobs/owned?" + urllib.parse.urlencode({
            "worker_id": worker_id})
        return self._request("GET", path)

    def claim(self, worker_id: str, gpu_memory_mb: int) -> list[dict]:
        return self._request("POST", "/internal/compute/af3/jobs/claim", payload={
            "worker_id": worker_id, "lease_seconds": 60, "max_jobs": 1,
            "resources": {"capabilities": ["af3"], "gpu_count": 1,
                          "gpu_memory_mb": gpu_memory_mb},
        })

    def heartbeat(self, claim: dict, worker_id: str) -> None:
        self._request("POST", f"/internal/compute/af3/jobs/{claim['id']}/heartbeat",
                      payload={"worker_id": worker_id, "lease_token": claim["lease_token"],
                               "lease_seconds": 60})

    def progress(self, claim: dict, worker_id: str, progress: int) -> None:
        self._request("POST", f"/internal/compute/af3/jobs/{claim['id']}/progress",
                      payload={"worker_id": worker_id, "lease_token": claim["lease_token"],
                               "attempt": claim["attempt"], "progress": progress})

    def upload(self, claim: dict, artifact: dict, content: bytes) -> None:
        params = urllib.parse.urlencode({
            "name": artifact["name"], "kind": artifact["kind"],
            "attempt": claim["attempt"],
        })
        self._request(
            "PUT", f"/internal/af3/jobs/{claim['id']}/artifacts/{artifact['id']}?{params}",
            body=content, headers={"X-Compute-Lease": claim["lease_token"],
                                   "Content-Type": "application/octet-stream"},
        )

    def result(self, claim: dict, payload: dict) -> dict:
        return self._request("POST", f"/internal/af3/jobs/{claim['id']}/result",
                             payload=payload)


class SpoolAf3Engine:
    def ensure_started(self, job_dir: Path, claim: dict) -> None:
        if (job_dir / "outcome.json").exists():
            return
        job_dir.mkdir(parents=True, exist_ok=True)
        if not (job_dir / "input.json").exists():
            atomic_json(job_dir / "input.json", claim["fold_input"])


class Receiver:
    def __init__(self, api: ComputeAPI, journal: Journal, engine: SpoolAf3Engine, *,
                 worker_id: str, spool_dir: Path, gpu_memory_mb: int) -> None:
        self.api = api
        self.journal = journal
        self.engine = engine
        self.worker_id = worker_id
        self.spool_dir = spool_dir
        self.gpu_memory_mb = gpu_memory_mb
        self.spool_dir.mkdir(parents=True, exist_ok=True)

    def tick(self) -> None:
        try:
            for claim in self.api.owned(self.worker_id):
                self.journal.put(claim)
            if not self.journal.all():
                for claim in self.api.claim(self.worker_id, self.gpu_memory_mb):
                    self.journal.put(claim)
        except (OSError, urllib.error.HTTPError, ValueError) as exc:
            LOG.warning("Compute control plane unavailable: %s", exc)
        for claim in self.journal.all():
            try:
                self._process(claim)
            except (OSError, urllib.error.HTTPError, ValueError) as exc:
                LOG.warning("Job %s remains in local journal: %s", claim["id"], exc)

    def _process(self, claim: dict) -> None:
        job_dir = self.spool_dir / claim["id"]
        job_dir.mkdir(parents=True, exist_ok=True)
        input_exists = (job_dir / "input.json").exists()
        if not input_exists:
            # An old local claim must be accepted by the backend before it
            # becomes a new GPU input after a long receiver outage.
            self.api.heartbeat(claim, self.worker_id)
        self.engine.ensure_started(job_dir, claim)
        outcome_path = job_dir / "outcome.json"
        try:
            if input_exists:
                self.api.heartbeat(claim, self.worker_id)
            self.api.progress(claim, self.worker_id, 90 if outcome_path.exists() else 5)
        except (OSError, urllib.error.HTTPError):
            pass  # The local engine continues; the next tick retries.
        if not outcome_path.exists():
            return
        outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
        artifacts = []
        if outcome["status"] == "completed":
            for path in sorted((job_dir / "output").rglob("*")):
                if not path.is_file() or path.suffix.lower() not in {".cif", ".json"}:
                    continue
                if path.stat().st_size > MAX_ARTIFACT_BYTES:
                    raise ValueError(f"Artifact exceeds 20 MiB: {path.name}")
                relative = path.relative_to(job_dir / "output").as_posix()
                artifact = {"id": hashlib.sha256(
                    (claim["id"] + "/" + relative).encode()).hexdigest()[:32],
                    "name": relative.replace("/", "__"),
                    "kind": "structure" if path.suffix.lower() == ".cif" else "data"}
                if artifact["id"] not in self.journal.uploaded(claim["id"]):
                    self.api.upload(claim, artifact, path.read_bytes())
                    self.journal.mark_uploaded(claim["id"], artifact["id"])
                artifacts.append(artifact)
        payload = {
            "status": outcome["status"],
            "actual_gpu_minutes": outcome["actual_gpu_minutes"],
            "simulation": outcome.get("simulation", False),
            "attempt": claim["attempt"], "lease_token": claim["lease_token"],
            "artifacts": artifacts,
        }
        acknowledged = self.api.result(claim, payload)
        if acknowledged["status"] != payload["status"]:
            raise ValueError("Backend result acknowledgement does not match local outcome")
        self.journal.delete(claim["id"])
        shutil.rmtree(job_dir)


def needs_data_pipeline(fold_input: dict) -> bool:
    """Use AF3 search unless every searchable entity provides its inputs."""
    for entity in fold_input.get("sequences", []):
        if "protein" in entity and not all(
            key in entity["protein"] for key in ("unpairedMsa", "pairedMsa", "templates")
        ):
            return True
        if "rna" in entity and "unpairedMsa" not in entity["rna"]:
            return True
    return False


def af3_command(
    job_dir: Path, *, gpu_device: str, model_dir: str, db_dir: str,
    diffusion_samples: int, run_data_pipeline: bool,
) -> list[str]:
    """Build an argument vector for the pinned AF3 image without a shell."""
    return [
        "python3", "/app/alphafold/run_alphafold.py",
        f"--json_path={job_dir / 'input.json'}",
        f"--output_dir={job_dir / 'output'}",
        f"--gpu_device={gpu_device}",
        f"--model_dir={model_dir}", f"--db_dir={db_dir}",
        f"--num_diffusion_samples={diffusion_samples}",
        f"--run_data_pipeline={'true' if run_data_pipeline else 'false'}",
    ]


def execute_job(job_dir: Path, gpu_device: str, dry_run: bool,
                dry_run_delay_seconds: float = 0, *,
                model_dir: str = "/data/af3/models",
                db_dir: str = "/data/af3/database",
                diffusion_samples: int = 5) -> None:
    with (job_dir / "engine.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        if (job_dir / "outcome.json").exists():
            return
        output = job_dir / "output"
        output.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        if dry_run:
            time.sleep(dry_run_delay_seconds)
            atomic_json(output / "receiver_smoke.json", {"simulation": True})
            status = "completed"
        else:
            fold_input = json.loads((job_dir / "input.json").read_text(encoding="utf-8"))
            command = af3_command(
                job_dir, gpu_device=gpu_device, model_dir=model_dir,
                db_dir=db_dir, diffusion_samples=diffusion_samples,
                run_data_pipeline=needs_data_pipeline(fold_input),
            )
            with (job_dir / "stdout.log").open("ab") as stdout, \
                 (job_dir / "stderr.log").open("ab") as stderr:
                code = subprocess.run(command, cwd="/app/alphafold", stdout=stdout,
                                      stderr=stderr, check=False).returncode
            status = "completed" if code == 0 else "failed"
        minutes = 0 if dry_run else math.ceil((time.monotonic() - started) / 60)
        atomic_json(job_dir / "outcome.json", {
            "status": status, "actual_gpu_minutes": min(minutes, 1440),
            "simulation": dry_run,
        })


def compute_loop(spool_dir: Path, gpu_device: str, dry_run: bool,
                 poll_seconds: float, dry_run_delay_seconds: float = 0, *,
                 model_dir: str = "/data/af3/models",
                 db_dir: str = "/data/af3/database",
                 diffusion_samples: int = 5) -> None:
    """Run persisted inputs independently of the receiver process."""
    spool_dir.mkdir(parents=True, exist_ok=True)
    while True:
        for input_path in sorted(spool_dir.glob("*/input.json")):
            job_dir = input_path.parent
            if not JOB_ID.fullmatch(job_dir.name) or (job_dir / "outcome.json").exists():
                continue
            try:
                execute_job(
                    job_dir, gpu_device, dry_run, dry_run_delay_seconds,
                    model_dir=model_dir, db_dir=db_dir,
                    diffusion_samples=diffusion_samples,
                )
            except OSError:
                LOG.exception("Could not run AF3 job %s", job_dir.name)
        time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Durable AF3 compute receiver")
    subcommands = parser.add_subparsers(dest="command", required=True)
    receive = subcommands.add_parser("receive")
    receive.add_argument("--api-url", required=True)
    receive.add_argument("--worker-id", required=True)
    receive.add_argument("--spool-dir", type=Path, default=Path("/var/lib/af3-receiver"))
    receive.add_argument("--gpu-memory-mb", type=int, default=49140)
    receive.add_argument("--poll-seconds", type=float, default=5)
    compute = subcommands.add_parser("compute-loop")
    compute.add_argument("--spool-dir", type=Path,
                         default=Path("/var/lib/af3-receiver/jobs"))
    compute.add_argument("--gpu-device", default="0")
    compute.add_argument("--poll-seconds", type=float, default=2)
    compute.add_argument("--dry-run", action="store_true")
    compute.add_argument("--dry-run-delay-seconds", type=float, default=0)
    compute.add_argument("--model-dir", default="/data/af3/models")
    compute.add_argument("--db-dir", default="/data/af3/database")
    compute.add_argument("--num-diffusion-samples", type=int, default=5)
    execute = subcommands.add_parser("execute-job")
    execute.add_argument("--job-dir", required=True, type=Path)
    execute.add_argument("--gpu-device", required=True)
    execute.add_argument("--dry-run", action="store_true")
    execute.add_argument("--dry-run-delay-seconds", type=float, default=0)
    execute.add_argument("--model-dir", default="/data/af3/models")
    execute.add_argument("--db-dir", default="/data/af3/database")
    execute.add_argument("--num-diffusion-samples", type=int, default=5)
    args = parser.parse_args()
    if args.command == "execute-job":
        if args.dry_run_delay_seconds < 0 or args.num_diffusion_samples < 1:
            parser.error("Invalid AF3 execution options")
        execute_job(args.job_dir, args.gpu_device, args.dry_run,
                    args.dry_run_delay_seconds, model_dir=args.model_dir,
                    db_dir=args.db_dir, diffusion_samples=args.num_diffusion_samples)
        return
    if args.command == "compute-loop":
        if (args.poll_seconds <= 0 or args.dry_run_delay_seconds < 0
                or args.num_diffusion_samples < 1
                or not re.fullmatch(r"[0-7]", args.gpu_device)):
            parser.error("Invalid compute loop configuration")
        logging.basicConfig(level=logging.INFO)
        compute_loop(args.spool_dir, args.gpu_device, args.dry_run,
                     args.poll_seconds, args.dry_run_delay_seconds,
                     model_dir=args.model_dir, db_dir=args.db_dir,
                     diffusion_samples=args.num_diffusion_samples)
        return
    if args.poll_seconds <= 0 or args.gpu_memory_mb < 0:
        parser.error("poll interval and GPU memory must be positive")
    key = os.environ.get("RESEARCH_AGENT_COMPUTE_CALLBACK_KEY")
    if not key:
        parser.error("RESEARCH_AGENT_COMPUTE_CALLBACK_KEY is required")
    logging.basicConfig(level=logging.INFO)
    journal = Journal(args.spool_dir / "journal.sqlite3")
    receiver = Receiver(
        ComputeAPI(args.api_url, key), journal, SpoolAf3Engine(),
        worker_id=args.worker_id, spool_dir=args.spool_dir / "jobs",
        gpu_memory_mb=args.gpu_memory_mb,
    )
    while True:
        receiver.tick()
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
