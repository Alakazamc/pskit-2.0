import json

from scripts.af3_receiver import (
    Journal,
    Receiver,
    SpoolAf3Engine,
    af3_command,
    execute_job,
    needs_data_pipeline,
)

CLAIM = {
    "id": "eebfc6dc-d74a-4f10-ab33-04d45881e7e4", "attempt": 1,
    "lease_token": "claim-token", "fold_input": {
        "name": "test", "modelSeeds": [1],
        "sequences": [{"protein": {"id": "A", "sequence": "AAAA"}}],
        "dialect": "alphafold3", "version": 4,
    },
}


class FakeComputeAPI:
    def __init__(self):
        self.owned_claims = []
        self.new_claims = [CLAIM]
        self.fail_result_once = True
        self.results = []
        self.uploads = []

    def owned(self, worker_id):
        return self.owned_claims

    def claim(self, worker_id, gpu_memory_mb):
        claimed, self.new_claims = self.new_claims, []
        self.owned_claims = claimed
        return claimed

    def heartbeat(self, claim, worker_id):
        return None

    def progress(self, claim, worker_id, progress):
        return None

    def upload(self, claim, artifact, content):
        self.uploads.append((artifact, content))

    def result(self, claim, payload):
        if self.fail_result_once:
            self.fail_result_once = False
            raise ConnectionError("server acknowledgement lost")
        self.results.append(payload)
        return {"status": payload["status"]}


class FakeEngine:
    def __init__(self):
        self.starts = 0

    def ensure_started(self, job_dir, claim):
        if (job_dir / "outcome.json").exists():
            return
        self.starts += 1
        output = job_dir / "output"
        output.mkdir()
        (output / "result.json").write_text('{"ok": true}')
        (job_dir / "outcome.json").write_text(json.dumps({
            "status": "completed", "actual_gpu_minutes": 1, "simulation": True,
        }))


def test_receiver_replays_local_result_until_backend_ack(tmp_path):
    journal = Journal(tmp_path / "journal.sqlite3")
    api = FakeComputeAPI()
    engine = FakeEngine()
    receiver = Receiver(api, journal, engine, worker_id="a6000-test",
                        spool_dir=tmp_path / "spool", gpu_memory_mb=49152)

    receiver.tick()
    assert journal.get(CLAIM["id"]) is not None
    assert engine.starts == 1
    assert api.uploads and not api.results

    restarted = Receiver(api, Journal(tmp_path / "journal.sqlite3"), engine,
                         worker_id="a6000-test", spool_dir=tmp_path / "spool",
                         gpu_memory_mb=49152)
    restarted.tick()
    assert restarted.journal.get(CLAIM["id"]) is None
    assert engine.starts == 1
    assert api.results[0]["status"] == "completed"


def test_receiver_recovers_claim_when_claim_response_was_lost(tmp_path):
    journal = Journal(tmp_path / "journal.sqlite3")
    api = FakeComputeAPI()
    api.new_claims = []
    api.owned_claims = [CLAIM]
    api.fail_result_once = False
    receiver = Receiver(api, journal, FakeEngine(), worker_id="a6000-test",
                        spool_dir=tmp_path / "spool", gpu_memory_mb=49152)

    receiver.tick()
    assert api.results[0]["attempt"] == 1
    assert journal.get(CLAIM["id"]) is None


def test_compute_spool_runs_without_receiver_and_retains_outcome(tmp_path):
    job_dir = tmp_path / CLAIM["id"]
    SpoolAf3Engine().ensure_started(job_dir, CLAIM)
    assert json.loads((job_dir / "input.json").read_text()) == CLAIM["fold_input"]

    execute_job(job_dir, "0", dry_run=True)

    assert json.loads((job_dir / "outcome.json").read_text()) == {
        "status": "completed", "actual_gpu_minutes": 0, "simulation": True,
    }
    assert (job_dir / "output" / "receiver_smoke.json").exists()


def test_receiver_does_not_start_unconfirmed_old_claim(tmp_path):
    class OfflineApi(FakeComputeAPI):
        def heartbeat(self, claim, worker_id):
            raise ConnectionError("backend unavailable")

    journal = Journal(tmp_path / "journal.sqlite3")
    journal.put(CLAIM)
    receiver = Receiver(OfflineApi(), journal, SpoolAf3Engine(),
                        worker_id="a6000-test", spool_dir=tmp_path / "spool",
                        gpu_memory_mb=49152)

    receiver.tick()

    assert journal.get(CLAIM["id"]) is not None
    assert not (tmp_path / "spool" / CLAIM["id"] / "input.json").exists()


def test_real_af3_command_uses_readable_mounts_and_skips_precomputed_pipeline(tmp_path):
    fold_input = {**CLAIM["fold_input"], "sequences": [{"protein": {
        "id": "A", "sequence": "AAAA", "unpairedMsa": "", "pairedMsa": "",
        "templates": [],
    }}]}
    assert needs_data_pipeline(CLAIM["fold_input"]) is True
    assert needs_data_pipeline(fold_input) is False
    command = af3_command(
        tmp_path, gpu_device="0", model_dir="/data/af3/models",
        db_dir="/data/af3/database", diffusion_samples=1,
        run_data_pipeline=needs_data_pipeline(fold_input),
    )
    assert "--model_dir=/data/af3/models" in command
    assert "--db_dir=/data/af3/database" in command
    assert "--run_data_pipeline=false" in command
    assert "--num_diffusion_samples=1" in command
