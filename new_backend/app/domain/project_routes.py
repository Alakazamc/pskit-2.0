import re
import unicodedata
import uuid


def new_project_id(name: str) -> str:
    """Create a unique project ID with a readable name suffix.

    Args:
        name: Project display name used for the ASCII slug.

    Returns:
        ID containing a UUID and a normalized suffix.
    """
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")[:40].strip("-") or "project"
    return f"project-{uuid.uuid4().hex}-{slug}"


def project_id_from_key(key: str) -> str | None:
    """Expand a short ``g-p-`` route key to its project ID.

    Args:
        key: Project key from a public route.

    Returns:
        Project ID, or ``None`` for a malformed key.
    """
    if not key.startswith("g-p-") or len(key) <= 4:
        return None
    return f"project-{key[4:]}"
