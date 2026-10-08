#!/usr/bin/env python3
"""Small local Docker volume plugin backed by bounded ext4 images.

The daemon is deliberately host-local and has no TCP listener. Docker reaches
it through a root-owned Unix socket. Each volume is a sparse ext4 image whose
filesystem size and inode table are fixed when the volume is created.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn, UnixStreamServer
from typing import Any

PLUGIN_VERSION = "1.0.0"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
SIZE_RE = re.compile(r"^([1-9][0-9]*)(KiB|MiB|GiB)$")
SIZE_MULTIPLIERS = {"KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}
MIN_SIZE = 64 * 1024**2
MAX_SIZE = 100 * 1024**3
MIN_INODES = 1024
MAX_INODES = 2_000_000


class DriverError(RuntimeError):
    """A volume operation was refused without exposing host details."""


class VolumeDriver:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.images = self.root / "images"
        self.mounts = self.root / "mounts"
        self.state = self.root / "state"
        for directory in (self.root, self.images, self.mounts, self.state):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)
        self.lock = threading.RLock()

    def create(self, name: str, opts: dict[str, Any]) -> None:
        name = self._name(name)
        size_text = str(opts.get("size", ""))
        inode_text = str(opts.get("inodes", ""))
        if set(opts) != {"size", "inodes"}:
            raise DriverError("only size and inodes options are accepted")
        size = self._size(size_text)
        try:
            inodes = int(inode_text)
        except ValueError as exc:
            raise DriverError("inodes must be an integer") from exc
        if not MIN_INODES <= inodes <= MAX_INODES:
            raise DriverError("inode limit is outside the approved range")

        image = self._image(name)
        metadata = self._metadata(name)
        with self.lock:
            if metadata.exists():
                existing = self._read_metadata(name)
                if existing.get("size") != size_text or existing.get("inodes") != inode_text:
                    raise DriverError("existing volume has different limits")
                return
            if image.exists():
                raise DriverError("untracked volume image already exists")
            temporary = image.with_suffix(".img.creating")
            try:
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                    0o600,
                )
                try:
                    os.ftruncate(descriptor, size)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                self._run(
                    [
                        "/usr/sbin/mkfs.ext4",
                        "-q",
                        "-F",
                        "-m",
                        "0",
                        "-N",
                        str(inodes),
                        "-E",
                        "lazy_itable_init=0,lazy_journal_init=0",
                        str(temporary),
                    ],
                    timeout=120,
                )
                os.replace(temporary, image)
                self._write_metadata(
                    name,
                    {"name": name, "size": size_text, "inodes": inode_text, "mount_ids": []},
                )
            except Exception:
                temporary.unlink(missing_ok=True)
                raise

    def remove(self, name: str) -> None:
        name = self._name(name)
        with self.lock:
            if self._mounted(name):
                raise DriverError("volume is mounted")
            mountpoint = self._mountpoint(name)
            if mountpoint.exists():
                mountpoint.rmdir()
            self._image(name).unlink(missing_ok=True)
            self._metadata(name).unlink(missing_ok=True)

    def mount(self, name: str, mount_id: str) -> str:
        name = self._name(name)
        if not mount_id or len(mount_id) > 256:
            raise DriverError("mount id is invalid")
        with self.lock:
            metadata = self._read_metadata(name)
            mountpoint = self._mountpoint(name)
            mountpoint.mkdir(mode=0o700, exist_ok=True)
            if not self._mounted(name):
                self._run(
                    [
                        "/usr/bin/mount",
                        "-t",
                        "ext4",
                        "-o",
                        "loop,nodev,nosuid,noexec,noatime",
                        str(self._image(name)),
                        str(mountpoint),
                    ],
                    timeout=30,
                )
            ids = {str(value) for value in metadata.get("mount_ids", [])}
            ids.add(mount_id)
            metadata["mount_ids"] = sorted(ids)
            self._write_metadata(name, metadata)
            return str(mountpoint)

    def unmount(self, name: str, mount_id: str) -> None:
        name = self._name(name)
        with self.lock:
            metadata = self._read_metadata(name)
            ids = {str(value) for value in metadata.get("mount_ids", [])}
            ids.discard(mount_id)
            if not ids and self._mounted(name):
                self._run(["/usr/bin/umount", str(self._mountpoint(name))], timeout=30)
            metadata["mount_ids"] = sorted(ids)
            self._write_metadata(name, metadata)

    def path(self, name: str) -> str:
        name = self._name(name)
        self._read_metadata(name)
        return str(self._mountpoint(name))

    def get(self, name: str) -> dict[str, Any]:
        name = self._name(name)
        metadata = self._read_metadata(name)
        return {
            "Name": name,
            "Mountpoint": str(self._mountpoint(name)),
            "Status": {
                "size": metadata["size"],
                "inodes": metadata["inodes"],
                "mounted": self._mounted(name),
            },
        }

    def list(self) -> list[dict[str, Any]]:
        volumes: list[dict[str, Any]] = []
        for metadata in sorted(self.state.glob("*.json")):
            name = metadata.stem
            try:
                volumes.append(self.get(name))
            except DriverError:
                continue
        return volumes

    def _name(self, value: str) -> str:
        if NAME_RE.fullmatch(value) is None:
            raise DriverError("volume name is invalid")
        return value

    @staticmethod
    def _size(value: str) -> int:
        match = SIZE_RE.fullmatch(value)
        if match is None:
            raise DriverError("size must use KiB, MiB, or GiB")
        size = int(match.group(1)) * SIZE_MULTIPLIERS[match.group(2)]
        if not MIN_SIZE <= size <= MAX_SIZE:
            raise DriverError("size is outside the approved range")
        return size

    def _image(self, name: str) -> Path:
        return self.images / f"{name}.img"

    def _mountpoint(self, name: str) -> Path:
        return self.mounts / name

    def _metadata(self, name: str) -> Path:
        return self.state / f"{name}.json"

    def _read_metadata(self, name: str) -> dict[str, Any]:
        path = self._metadata(name)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DriverError("volume does not exist") from exc
        if value.get("name") != name or not self._image(name).is_file():
            raise DriverError("volume metadata is inconsistent")
        return value

    def _write_metadata(self, name: str, value: dict[str, Any]) -> None:
        target = self._metadata(name)
        descriptor, temporary_name = tempfile.mkstemp(dir=self.state, prefix=f".{name}-")
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def _mounted(self, name: str) -> bool:
        target = str(self._mountpoint(name).resolve())
        try:
            lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise DriverError("mount state is unavailable") from exc
        return any(line.split(" - ", 1)[0].split()[4] == target for line in lines)

    @staticmethod
    def _run(command: list[str], *, timeout: int) -> None:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
        )
        if completed.returncode:
            raise DriverError(f"host filesystem operation failed ({command[0]})")


class PluginHandler(BaseHTTPRequestHandler):
    server_version = "pskit-quota-volume"
    protocol_version = "HTTP/1.1"

    @property
    def driver(self) -> VolumeDriver:
        return self.server.driver  # type: ignore[attr-defined,no-any-return]

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 64 * 1024:
                raise DriverError("request is too large")
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise DriverError("request body must be an object")
            response = self._dispatch(self.path, payload)
        except (DriverError, json.JSONDecodeError, ValueError) as exc:
            response = {"Err": str(exc)}
        self._json(response)

    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_error(404)
            return
        self._json({"status": "healthy", "version": PLUGIN_VERSION})

    def _dispatch(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if path == "/Plugin.Activate":
            return {"Implements": ["VolumeDriver"]}
        if path == "/VolumeDriver.Create":
            opts = payload.get("Opts") or {}
            if not isinstance(opts, dict):
                raise DriverError("volume options must be an object")
            self.driver.create(str(payload.get("Name", "")), opts)
            return {"Err": ""}
        if path == "/VolumeDriver.Remove":
            self.driver.remove(str(payload.get("Name", "")))
            return {"Err": ""}
        if path == "/VolumeDriver.Mount":
            mountpoint = self.driver.mount(
                str(payload.get("Name", "")), str(payload.get("ID", ""))
            )
            return {"Mountpoint": mountpoint, "Err": ""}
        if path == "/VolumeDriver.Unmount":
            self.driver.unmount(
                str(payload.get("Name", "")), str(payload.get("ID", ""))
            )
            return {"Err": ""}
        if path == "/VolumeDriver.Path":
            return {
                "Mountpoint": self.driver.path(str(payload.get("Name", ""))),
                "Err": "",
            }
        if path == "/VolumeDriver.Get":
            return {
                "Volume": self.driver.get(str(payload.get("Name", ""))),
                "Err": "",
            }
        if path == "/VolumeDriver.List":
            return {"Volumes": self.driver.list(), "Err": ""}
        if path == "/VolumeDriver.Capabilities":
            return {"Capabilities": {"Scope": "local"}}
        raise DriverError("unsupported plugin operation")

    def _json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class PluginServer(ThreadingMixIn, UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, socket_path: Path, driver: VolumeDriver) -> None:
        socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        socket_path.unlink(missing_ok=True)
        self.driver = driver
        super().__init__(str(socket_path), PluginHandler)
        os.chmod(socket_path, 0o600)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--socket", type=Path, required=True)
    args = parser.parse_args()
    for command in ("/usr/sbin/mkfs.ext4", "/usr/bin/mount", "/usr/bin/umount"):
        if not Path(command).is_file() or not os.access(command, os.X_OK):
            raise SystemExit(f"required host command is unavailable: {command}")
    driver = VolumeDriver(args.root)
    server = PluginServer(args.socket, driver)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        args.socket.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
