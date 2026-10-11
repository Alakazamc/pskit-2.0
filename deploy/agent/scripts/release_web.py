"""Private atomic Web publication and recovery; immutable assets stay available."""

from __future__ import annotations

from deploy.agent.scripts.release_files import (
    ReleaseError,
    atomic_at,
    directory_fd,
    publish_assets,
    read_at,
    relative_parent,
    snapshot_dist,
)


class _WebOperations:
    """Publish verified assets before the index and recover the registered index."""

    def _release_web(
        self,
        pending: dict,
        candidate: dict,
        old: dict,
        state: dict,
        *,
        version: str,
        digest: str,
        component: str,
    ) -> None:
        files = snapshot_dist(
            self.releases / version / "dist", candidate["frontend"]["sha256"]
        )
        old_files = snapshot_dist(
            self.releases / old["release_id"] / "dist", old["frontend"]["sha256"]
        )
        with directory_fd(self.static) as fd:
            for relative, content in old_files.items():
                with relative_parent(fd, relative) as (parent, name):
                    if read_at(parent, name) != content:
                        raise ReleaseError(
                            "Live web asset differs from registered rollback artifact"
                        )
            publish_assets(fd, files)
            self._write_json("pending.json", pending)
            try:
                atomic_at(fd, "index.html", files["index.html"])
                self._postflight()
                state[component] = {
                    "release_id": version,
                    "manifest_sha256": digest,
                }
                self._write_json("current.json", state)
                self._remove_pending()
            except BaseException:
                publish_assets(fd, old_files)
                atomic_at(fd, "index.html", old_files["index.html"])
                state[component] = pending["previous"]
                self._write_json("current.json", state)
                self._remove_pending()
                raise

    def _recover_web(self, pending: dict, old: dict, component: str) -> None:
        self._activity(self._current_manifest("api"))
        files = snapshot_dist(
            self.releases / old["release_id"] / "dist", old["frontend"]["sha256"]
        )
        with directory_fd(self.static) as fd:
            # Recovery must restore every verified immutable asset before switching the index.
            # Same-name mismatches remain fail closed; never overwrite a collision.
            publish_assets(fd, files)
            atomic_at(fd, "index.html", files["index.html"])
        self._postflight()
        state = self.current()
        state[component] = pending["previous"]
        self._write_json("current.json", state)
        self._remove_pending()
