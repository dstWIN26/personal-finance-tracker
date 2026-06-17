"""Cache-busting of the static frontend (backend/assets.py).

Guards the fix for stale app.js after a deploy: every internal asset URL must be
content-versioned so a changed file gets a fresh URL the browser/edge can't have
cached. A bare /static/... reference would silently reintroduce the bug.
"""
import re

import pytest

from backend import assets


def test_version_is_short_stable_hex():
    v = assets.compute_version()
    assert re.fullmatch(r"[0-9a-f]{10}", v)
    assert v == assets.compute_version()            # deterministic


def test_version_changes_when_an_asset_changes(tmp_path):
    (tmp_path / "css").mkdir()
    (tmp_path / "js").mkdir()
    for rel in ("css/style.css", "js/app.js", "js/webauthn.js",
                "js/login.js", "js/banks-callback.js"):
        (tmp_path / rel).write_text("/* a */")
    v1 = assets.compute_version(tmp_path)
    (tmp_path / "js/app.js").write_text("/* changed */")
    v2 = assets.compute_version(tmp_path)
    assert v1 != v2


def test_render_replaces_placeholder():
    html = assets.render("index.html")
    assert assets.PLACEHOLDER not in html           # no leftover __ASSET_V__
    assert f"/static/js/app.js?v={assets.ASSET_VERSION}" in html


@pytest.mark.parametrize("page", ["index.html", "login.html", "banks-callback.html"])
def test_every_internal_asset_is_versioned(page):
    html = assets.render(page)
    # Every /static/... reference must carry ?v=<current version> — a bare one
    # would let the browser/edge serve a stale copy after a deploy.
    for ref in re.findall(r"/static/\S+", html):
        ref = ref.rstrip('">\'')
        assert f"?v={assets.ASSET_VERSION}" in ref, f"un-versioned asset in {page}: {ref}"
