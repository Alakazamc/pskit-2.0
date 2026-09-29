"""Offline delivery regressions; no Docker daemon or external services required."""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

PROJECT = Path(__file__).resolve().parents[1]
BASH = (
    "C:/Program Files/Git/bin/bash.exe"
    if os.name == "nt"
    else shutil.which("bash")
)


class DeliveryTests(unittest.TestCase):
    @unittest.skipUnless(BASH and Path(BASH).exists(), "Bash required for archive test")
    def test_archive_uses_source_allowlist_and_can_be_reexported(self):
        with tempfile.TemporaryDirectory(prefix="pskit-delivery-test-") as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir()
            shutil.copy(PROJECT / "scripts/export_offline_delivery.sh", root / "scripts")
            for name in ("README.md", "项目介绍.md", ".env.example", "backend/.env", "secrets/provider.json"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture", encoding="utf-8")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            (root / "private-notes.txt").write_text("untracked secret", encoding="utf-8")
            (root / "dist").mkdir()
            image_name = "pskit2-0.3.0-linux-amd64-images.tar.gz"
            image = root / "dist" / image_name
            image.write_bytes(b"image fixture")
            image.with_suffix(".gz.sha256").write_text(
                f"{hashlib.sha256(image.read_bytes()).hexdigest()}  {image_name}\n",
                encoding="utf-8", newline="\n",
            )
            environment = {**os.environ, "PSKIT_DELIVERY_REUSE_IMAGE_BUNDLE": "1"}
            for invocation in range(2):
                result = subprocess.run(
                    [BASH, "scripts/export_offline_delivery.sh"], cwd=root,
                    env=environment, capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                archive = root / "dist/pskit2-0.3.0-offline-delivery.tar"
                with tarfile.open(archive) as bundle:
                    names = bundle.getnames()
                    self.assertIn("pskit2-0.3.0/README.md", names)
                    self.assertIn("pskit2-0.3.0/项目介绍.md", names)
                    self.assertIn("pskit2-0.3.0/.env.example", names)
                    self.assertFalse(any("provider.json" in name for name in names))
                    self.assertFalse(any("backend/.env" in name for name in names))
                    self.assertFalse(any("private-notes" in name for name in names))
                    if invocation == 0:
                        manifest = bundle.extractfile("pskit2-0.3.0/SOURCE_MANIFEST.txt")
                        assert manifest is not None
                        (root / "SOURCE_MANIFEST.txt").write_bytes(manifest.read())
                if invocation == 0:
                    # Exercise re-exporting an unpacked delivery without Git metadata.
                    (root / ".git").rename(root / ".git-hidden")

    def run_snapshot(self, *, absent: bool = False, fail_download: bool = False):
        source = (PROJECT / "scripts/backup_runtime.sh").read_text(encoding="utf-8")
        marker = '> "$destination/qdrant.snapshot" <<\'PY\'\n'
        program = source.split(marker, 1)[1].split("\nPY\n", 1)[0]
        requests = []

        def respond(request):
            requests.append((request.method, request.url.path))
            if request.url.path == "/aliases":
                return httpx.Response(200, json={"result": {"aliases": []}})
            if request.url.path == "/collections/knowledge":
                return httpx.Response(404 if absent else 200, json={})
            if request.method == "POST":
                return httpx.Response(200, json={"result": {"name": "snapshot"}})
            if request.method == "GET":
                return httpx.Response(500 if fail_download else 200, content=b"snapshot-bytes")
            return httpx.Response(200, json={})

        client = httpx.Client(transport=httpx.MockTransport(respond))
        output = io.TextIOWrapper(io.BytesIO())
        with tempfile.TemporaryDirectory() as temporary:
            metadata = Path(temporary) / "metadata.json"
            with (
                patch.dict(os.environ, {"QDRANT_URL": "http://qdrant", "QDRANT_COLLECTION": "knowledge"}),
                patch.object(sys, "argv", ["-", str(metadata)]),
                patch.object(sys, "stdout", output),
                patch.object(httpx, "Client", return_value=client),
            ):
                try:
                    # Execute the checked-in snapshot program with a mocked transport.
                    exec(compile(program, "backup_runtime_snapshot", "exec"), {})  # noqa: S102
                except SystemExit as error:
                    self.assertEqual(error.code, 0)
                except httpx.HTTPStatusError:
                    if not fail_download:
                        raise
            if fail_download:
                self.assertIn(("DELETE", "/collections/knowledge/snapshots/snapshot"), requests)
                return
            self.assertEqual(json.loads(metadata.read_text())["present"], not absent)
            self.assertEqual(output.buffer.getvalue(), b"" if absent else b"snapshot-bytes")

    def test_snapshot_streams_to_host(self):
        self.run_snapshot()

    def test_missing_optional_collection_does_not_abort_backup(self):
        self.run_snapshot(absent=True)

    def test_failed_snapshot_download_cleans_server_snapshot(self):
        self.run_snapshot(fail_download=True)


if __name__ == "__main__":
    unittest.main()
