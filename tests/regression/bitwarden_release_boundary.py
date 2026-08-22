#!/usr/bin/env python3
"""Local Bitwarden CLI release boundary for workstation regression fixtures.

The `config/lxc_workstation_baseline` role resolves release metadata from
`workstation_bw_release_api_url` and fetches the archive from
`workstation_bw_download_url`. Regression fixtures that include the whole role
point both at this loopback boundary so no test reaches the live GitHub
release, while the production metadata shape, checksum selection, extraction,
and install path stay exercised.
"""

from __future__ import annotations

import functools
import http.server
import json
import os
import tempfile
import threading
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path


ARCHIVE_NAME = "bw-linux-test.zip"
RELEASE_METADATA_NAME = "release.json"


class _QuietHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


def _build_release_assets(asset_root: Path, *, digest_matches: bool) -> None:
    """Write a fake `bw` archive plus release metadata naming its sha256 digest."""
    archive_path = asset_root / ARCHIVE_NAME
    binary_path = asset_root / "bw"
    binary_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(binary_path, 0o755)

    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.write(binary_path, arcname="bw")

    digest = sha256(archive_path.read_bytes()).hexdigest()
    if not digest_matches:
        # Serve bytes that no longer hash to the published digest.
        with zipfile.ZipFile(archive_path, "a") as archive:
            archive.comment = b"tampered"

    release_payload = {"assets": [{"name": ARCHIVE_NAME, "digest": f"sha256:{digest}"}]}
    (asset_root / RELEASE_METADATA_NAME).write_text(
        json.dumps(release_payload), encoding="utf-8"
    )


@contextmanager
def _serve_directory(directory: Path) -> Iterator[str]:
    handler = functools.partial(_QuietHTTPRequestHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join()


@contextmanager
def bitwarden_release_boundary(*, digest_matches: bool = True) -> Iterator[dict[str, str]]:
    """Serve a Bitwarden release over loopback and yield the role var overrides.

    With `digest_matches=False` the served archive no longer hashes to the digest
    the served metadata publishes, so the role's checksum validation must fail.
    """
    with tempfile.TemporaryDirectory(prefix="bitwarden-release-boundary-") as asset_root:
        _build_release_assets(Path(asset_root), digest_matches=digest_matches)
        with _serve_directory(Path(asset_root)) as base_url:
            yield {
                "workstation_bw_download_url": f"{base_url}/{ARCHIVE_NAME}",
                "workstation_bw_release_api_url": f"{base_url}/{RELEASE_METADATA_NAME}",
            }
