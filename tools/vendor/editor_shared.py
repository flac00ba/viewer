"""Minimal compatibility helpers required by the vendored DAT/SPR readers."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


def transactional_write_bytes(path: str | Path, data: bytes, *, do_backup: bool = False) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f"{target.name}.", suffix=".tmp", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        if do_backup and target.exists():
            shutil.copy2(target, target.with_suffix(f"{target.suffix}.bak"))
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
