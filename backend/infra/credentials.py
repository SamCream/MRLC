from __future__ import annotations

import os
import stat
from pathlib import Path

CREDENTIALS_FILE_ENV = "MRLC_CREDENTIALS_FILE"
ALLOWED_KEYS = frozenset({"CCR_API_KEY", "MRLC_STRATA_API_KEY", "MRLC_CCR_BASE_URL", "MRLC_STRATA_URL"})


class CredentialFileError(RuntimeError):
    pass


def credentials_path() -> Path:
    explicit = (os.environ.get(CREDENTIALS_FILE_ENV) or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    base = (os.environ.get("XDG_CONFIG_HOME") or "").strip() or str(Path.home() / ".config")
    return Path(base) / "mrlc" / "credentials.env"


def _parse(text: str, path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or name not in ALLOWED_KEYS:
            raise CredentialFileError(f"{path}:{number}: unsupported entry")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if value:
            values[name] = value
    return values


def load_credentials(path: Path | None = None, environ=None) -> list[str]:
    """Fill missing gateway settings from a private local file; existing environment wins.

    Returns the names that were loaded, never their values.
    """
    environ = os.environ if environ is None else environ
    path = path or credentials_path()
    try:
        info = path.stat()
    except FileNotFoundError:
        return []
    if not stat.S_ISREG(info.st_mode):
        raise CredentialFileError(f"{path}: not a regular file")
    if info.st_uid != os.getuid():
        raise CredentialFileError(f"{path}: must be owned by the service account")
    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise CredentialFileError(f"{path}: permissions must be 0600")
    loaded = []
    for name, value in _parse(path.read_text(encoding="utf-8"), path).items():
        if not (environ.get(name) or "").strip():
            environ[name] = value
            loaded.append(name)
    return sorted(loaded)
