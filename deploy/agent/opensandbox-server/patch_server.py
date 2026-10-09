"""Apply the reviewed PSKit hardening patches to OpenSandbox 1.1.0.

The build fails if the pinned upstream source no longer matches. This keeps the
derived image auditable and prevents a silent patch against another release.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

UPSTREAM_COMMIT = "b1a29cf93a823a95913f7943010febb3f29de05c"


def replace_once(path: Path, before: str, after: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(before) != 1:
        digest = hashlib.sha256(text.encode()).hexdigest()
        raise SystemExit(f"refusing to patch unexpected {path.name} ({digest})")
    path.write_text(text.replace(before, after, 1), encoding="utf-8")


def main(root: Path) -> None:
    port_allocator = root / "services/docker/port_allocator.py"
    replace_once(
        port_allocator,
        'DOCKER_PUBLISH_HOST = "0.0.0.0"\n',
        'DOCKER_PUBLISH_HOST = "127.0.0.1"\n',
    )

    diagnostics = root / "services/docker/docker_diagnostics.py"
    replace_once(
        diagnostics,
        "        lines.append(f\"Status:         {state.get('Status', 'unknown')}\")\n",
        "        lines.append(f\"Status:         {state.get('Status', 'unknown')}\")\n"
        "        lines.append(f\"OCI Runtime:    {host_config.get('Runtime', 'N/A')}\")\n",
    )

    volumes = root / "services/docker/volumes.py"
    before = """                    self.docker_client.api.create_volume(
                        name=volume_name,
                        labels={SANDBOX_MANAGED_VOLUMES_LABEL: \"server\"},
                    )
"""
    after = """                    driver = os.environ.get(
                        \"PSKIT_WORKSPACE_VOLUME_DRIVER\", \"\"
                    ).strip()
                    expected_size = os.environ.get(
                        \"PSKIT_WORKSPACE_VOLUME_SIZE\", \"\"
                    ).strip()
                    inode_limit = os.environ.get(
                        \"PSKIT_WORKSPACE_VOLUME_INODES\", \"\"
                    ).strip()
                    requested_size = (volume.pvc.storage or \"\").strip()
                    if (
                        not driver
                        or driver == \"local\"
                        or not expected_size
                        or requested_size != expected_size
                        or not inode_limit.isdigit()
                        or int(inode_limit) <= 0
                    ):
                        raise HTTPException(
                            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail={
                                \"code\": SandboxErrorCodes.PVC_VOLUME_INSPECT_FAILED,
                                \"message\": \"PSKit quota volume policy is unavailable.\",
                            },
                        )
                    self.docker_client.api.create_volume(
                        name=volume_name,
                        driver=driver,
                        driver_opts={\"size\": expected_size, \"inodes\": inode_limit},
                        labels={SANDBOX_MANAGED_VOLUMES_LABEL: \"server\"},
                    )
"""
    replace_once(volumes, before, after)

    validation_anchor = """        if volume.sub_path:
            driver = vol_info.get(\"Driver\", \"\")
"""
    validation = """        required_driver = os.environ.get(
            \"PSKIT_WORKSPACE_VOLUME_DRIVER\", \"\"
        ).strip()
        required_size = os.environ.get(
            \"PSKIT_WORKSPACE_VOLUME_SIZE\", \"\"
        ).strip()
        required_inodes = os.environ.get(
            \"PSKIT_WORKSPACE_VOLUME_INODES\", \"\"
        ).strip()
        actual_options = vol_info.get(\"Options\", {}) or {}
        if (
            not required_driver
            or required_driver == \"local\"
            or vol_info.get(\"Driver\") != required_driver
            or actual_options.get(\"size\") != required_size
            or actual_options.get(\"inodes\") != required_inodes
        ):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    \"code\": SandboxErrorCodes.PVC_VOLUME_INSPECT_FAILED,
                    \"message\": \"Docker volume does not satisfy PSKit quota policy.\",
                },
            )

        if volume.sub_path:
            driver = vol_info.get(\"Driver\", \"\")
"""
    replace_once(volumes, validation_anchor, validation)

    docker_service = root / "services/docker/docker_service.py"
    replace_once(
        docker_service,
        '                cap_add.add("SYS_ADMIN")\n',
        '                cap_add.update(\n'
        '                    {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETPCAP", "SETUID", "SYS_ADMIN"}\n'
        '                )\n',
    )
    replace_once(
        docker_service,
        '                host_config_kwargs["cap_add"] = sorted(cap_add)\n',
        '                host_config_kwargs["cap_add"] = sorted(cap_add)\n'
        '                host_config_kwargs["pskit_bootstrap_as_root"] = True\n',
    )
    container_ops = root / "services/docker/container_ops.py"
    replace_once(
        container_ops,
        "        host_config = self.docker_client.api.create_host_config(**host_config_kwargs)\n",
        "        host_config_kwargs = dict(host_config_kwargs)\n"
        "        bootstrap_as_root = bool(\n"
        "            host_config_kwargs.pop(\"pskit_bootstrap_as_root\", False)\n"
        "        )\n"
        "        host_config = self.docker_client.api.create_host_config(**host_config_kwargs)\n",
    )
    replace_once(
        container_ops,
        '                if not requested_windows_platform:\n'
        '                    container_kwargs["entrypoint"] = [BOOTSTRAP_PATH]\n',
        '                if not requested_windows_platform:\n'
        '                    container_kwargs["entrypoint"] = [BOOTSTRAP_PATH]\n'
        '                if bootstrap_as_root:\n'
        '                    container_kwargs["user"] = "0:0"\n',
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_server.py OPEN_SANDBOX_PACKAGE_ROOT")
    main(Path(sys.argv[1]))
