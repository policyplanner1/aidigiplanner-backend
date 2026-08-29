import re

_INVALID_SLUG_CHARS = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    slug = _INVALID_SLUG_CHARS.sub("-", text.strip().lower()).strip("-")
    return slug or "item"
