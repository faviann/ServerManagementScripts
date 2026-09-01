"""Interactive process helper local to the vault regression suite."""

from __future__ import annotations

from pathlib import Path

import pexpect


def run_vault_tty(
    repo: Path,
    env: dict[str, str],
    interactions: list[tuple[str, str]],
    *args: str,
) -> tuple[int, str]:
    """Run ``vault.sh`` on a pseudo-terminal and answer its prompts."""
    child = pexpect.spawn(
        str(repo / "vault.sh"),
        list(args),
        cwd=repo,
        env=env,
        encoding="utf-8",
        codec_errors="replace",
        timeout=10,
    )
    transcript: list[str] = []
    for prompt, response in interactions:
        if child.expect_exact([prompt, pexpect.EOF]) == 1:
            transcript.append(child.before)
            break
        transcript.extend((child.before, prompt))
        if "secret" in prompt.lower():
            if not child.waitnoecho():
                child.close(force=True)
                return 1, "".join(transcript)
        child.send(response)
    if not child.eof():
        child.expect(pexpect.EOF)
        transcript.append(child.before)
    child.close()
    returncode = child.exitstatus
    if returncode is None:
        returncode = 128 + (child.signalstatus or 0)
    return returncode, "".join(transcript)
