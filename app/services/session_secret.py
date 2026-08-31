import secrets
from pathlib import Path

from app.local_config import ensure_local_directories, load_local_config


def _read_session_secret(path):
    try:
        value = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError(f"Impossible de lire le secret de session local : {path}") from exc

    if len(value) < 32:
        raise RuntimeError(
            "Le secret de session local existe mais il est invalide ; "
            "Aurelia refuse de le remplacer automatiquement"
        )
    return value


def get_or_create_session_secret(config=None):
    """Load the installation secret, creating it atomically when absent.

    The secret deliberately has no environment or in-memory fallback. Tests
    isolate it by supplying a LocalConfig whose data directory is temporary.
    """

    config = ensure_local_directories(config or load_local_config())
    path = Path(config.session_secret_path)
    existing = _read_session_secret(path)
    if existing is not None:
        return existing

    value = secrets.token_urlsafe(48)
    try:
        with path.open("x", encoding="ascii") as handle:
            handle.write(value)
            handle.flush()
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return value
    except FileExistsError:
        existing = _read_session_secret(path)
        if existing is None:
            raise RuntimeError("Le secret de session local a disparu pendant sa création")
        return existing
    except OSError as exc:
        raise RuntimeError(f"Impossible de créer le secret de session local : {path}") from exc
