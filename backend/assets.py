"""Cache-busting for the static frontend.

The JS/CSS are baked into the image and fronted by Cloudflare. With no versioned
URL a browser (or the edge) can keep serving a *stale* ``app.js`` after a deploy
— the new ``index.html`` loads but an old script runs, so newly added handlers
silently do nothing. We stamp every internal asset URL with a short content hash
(``?v=<hash>``) so a changed file always gets a brand-new URL, and serve the HTML
shells with ``no-cache`` so the current hash is always seen. Changing a file
changes the hash, which changes every URL — breaking any cached copy at once.
"""
import hashlib
from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# Files whose content should drive the cache-busting hash (everything we serve
# from /static and reference with ?v=). Order is fixed for a stable digest.
_HASHED = (
    "css/style.css",
    "js/app.js",
    "js/webauthn.js",
    "js/login.js",
    "js/banks-callback.js",
)

# Placeholder embedded in the HTML shells; replaced at serve time.
PLACEHOLDER = "__ASSET_V__"


def compute_version(frontend_dir: Path = FRONTEND_DIR) -> str:
    """Short content hash over all cache-busted assets (stable across restarts
    unless a file changes)."""
    h = hashlib.sha256()
    for rel in _HASHED:
        try:
            h.update((frontend_dir / rel).read_bytes())
        except OSError:
            # A missing optional asset shouldn't break page rendering.
            h.update(b"\x00")
    return h.hexdigest()[:10]


# Computed once at import; the baked image is immutable per deploy.
ASSET_VERSION = compute_version()


def render(filename: str, version: str = None, frontend_dir: Path = FRONTEND_DIR) -> str:
    """Read an HTML shell and stamp the asset-version placeholder."""
    html = (frontend_dir / filename).read_text(encoding="utf-8")
    return html.replace(PLACEHOLDER, version or ASSET_VERSION)
