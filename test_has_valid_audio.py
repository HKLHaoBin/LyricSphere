#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Audio validity / media URL decode contract tests."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))

import backend  # noqa: E402
from backend import (  # noqa: E402
    SONG_SEARCH_INDEX_VERSION,
    _extract_media_audio_file_param,
    _load_song_search_index_from_disk,
    _normalize_song_audio_reference,
    build_media_audio_url,
    has_valid_audio,
)


@pytest.fixture
def backend_ctx():
    yield SimpleNamespace(
        backend=backend,
        has_valid_audio=has_valid_audio,
        _extract_media_audio_file_param=_extract_media_audio_file_param,
        _normalize_song_audio_reference=_normalize_song_audio_reference,
        build_media_audio_url=build_media_audio_url,
    )


@pytest.fixture
def songs_tmp(backend_ctx, tmp_path, monkeypatch):
    songs_dir = tmp_path / "songs"
    songs_dir.mkdir()
    monkeypatch.setattr(backend_ctx.backend, "SONGS_DIR", songs_dir)
    monkeypatch.setitem(backend_ctx.backend.RESOURCE_DIRECTORIES, "songs", songs_dir)
    return songs_dir


def test_has_valid_audio_external_https(backend_ctx):
    assert backend_ctx.has_valid_audio("https://example.com/audio.mp3") is True
    assert backend_ctx.has_valid_audio("http://cdn.example.org/track") is True


def test_normalize_external_url_is_not_pseudo_local(backend_ctx):
    assert backend_ctx._normalize_song_audio_reference("https://example.com/audio.mp3") is None


def test_has_valid_audio_missing_local(backend_ctx):
    assert backend_ctx.has_valid_audio("songs/definitely-missing.mp3") is False
    assert backend_ctx.has_valid_audio("definitely-missing.mp3") is False


def test_has_valid_audio_placeholder(backend_ctx):
    assert backend_ctx.has_valid_audio("songs/音乐.mp3") is False
    assert backend_ctx.has_valid_audio("音乐.mp3") is False


def _signed(file_param: str) -> str:
    return f"http://127.0.0.1:5000/media/audio?file={file_param}&exp=1&token=abc"


def test_signed_file_param_once_decoded_literal_percent20(backend_ctx):
    assert (
        backend_ctx._extract_media_audio_file_param(_signed("100%2520song.mp3"))
        == "100%20song.mp3"
    )


def test_signed_file_param_once_decoded_hash(backend_ctx):
    assert (
        backend_ctx._extract_media_audio_file_param(_signed("track%2523one.mp3"))
        == "track%23one.mp3"
    )


def test_signed_file_param_utf8_chinese(backend_ctx):
    encoded = quote("音乐.mp3", safe="")
    assert backend_ctx._extract_media_audio_file_param(_signed(encoded)) == "音乐.mp3"


def test_signed_file_param_space_as_percent20(backend_ctx):
    assert (
        backend_ctx._extract_media_audio_file_param(_signed("100%20song.mp3"))
        == "100 song.mp3"
    )


def test_normalize_signed_literal_percent20(backend_ctx):
    assert (
        backend_ctx._normalize_song_audio_reference(_signed("100%2520song.mp3"))
        == "100%20song.mp3"
    )


def test_skip_index_env_blocks_startup_index_init():
    assert backend._song_search_index_should_initialize() is False


def _file_sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_startup_rebuilds_stale_index_cache_subprocess(tmp_path):
    repo_root = Path(__file__).resolve().parent
    real_song_cache = repo_root / ".cache" / "song_search_index.json"
    real_artist_cache = repo_root / ".cache" / "artist_playlist_index.json"
    before_song_hash = _file_sha256(real_song_cache)
    before_artist_hash = _file_sha256(real_artist_cache)

    cache_file = tmp_path / "song_search_index.json"
    artist_cache_file = tmp_path / "artist_playlist_index.json"
    cache_file.write_text(
        json.dumps(
            {
                "version": SONG_SEARCH_INDEX_VERSION - 1,
                "revision": 1,
                "entries": {
                    "stale.json": {
                        "summary": {
                            "title": "stale",
                            "hasAudio": True,
                            "song": "missing.mp3",
                        },
                        "pool": "stale",
                        "pool_compact": "stale",
                        "mtime": 1.0,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("FAMYLIAM_SKIP_INDEX_INIT", None)
    env["FAMYLIAM_SONG_SEARCH_INDEX_FILE"] = str(cache_file)
    env["FAMYLIAM_ARTIST_PLAYLIST_INDEX_FILE"] = str(artist_cache_file)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    script = f"""
import json
import sys
from pathlib import Path

repo = Path({str(repo_root)!r})
cache = Path({str(cache_file)!r})
sys.path.insert(0, str(repo))
import backend

data = json.loads(cache.read_text(encoding="utf-8"))
if data.get("version") != backend.SONG_SEARCH_INDEX_VERSION:
    raise SystemExit(
        f"expected cache version {{backend.SONG_SEARCH_INDEX_VERSION}}, got {{data.get('version')}}"
    )
with backend._song_search_index_lock:
    count = len(backend._song_search_index)
if count == 0:
    raise SystemExit("search index empty after startup rebuild")
print("ok")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    combined = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 0, combined
    assert "startup init failed" not in combined.lower()
    assert "ok" in result.stdout
    assert _file_sha256(real_song_cache) == before_song_hash
    assert _file_sha256(real_artist_cache) == before_artist_hash
    assert artist_cache_file.is_file()


def test_load_song_search_index_rejects_stale_version(tmp_path, monkeypatch):
    cache = tmp_path / "song_search_index.json"
    cache.write_text(
        json.dumps(
            {
                "version": SONG_SEARCH_INDEX_VERSION - 1,
                "revision": 99,
                "entries": {
                    "stale.json": {
                        "summary": {
                            "title": "stale",
                            "hasAudio": True,
                        },
                        "pool": "stale",
                        "pool_compact": "stale",
                        "mtime": 1.0,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(backend, "SONG_SEARCH_INDEX_FILE", cache)
    assert _load_song_search_index_from_disk() is None


def test_load_song_search_index_accepts_current_version(tmp_path, monkeypatch):
    cache = tmp_path / "song_search_index.json"
    cache.write_text(
        json.dumps(
            {
                "version": SONG_SEARCH_INDEX_VERSION,
                "revision": 3,
                "entries": {
                    "current.json": {
                        "summary": {
                            "title": "current",
                            "hasAudio": False,
                        },
                        "pool": "current",
                        "pool_compact": "current",
                        "mtime": 2.0,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(backend, "SONG_SEARCH_INDEX_FILE", cache)
    loaded = _load_song_search_index_from_disk()
    assert loaded is not None
    entries, revision = loaded
    assert revision == 3
    assert entries["current.json"]["summary"]["hasAudio"] is False


@pytest.mark.parametrize(
    ("relative_path", "payload"),
    [
        ("100%20song.mp3", b"literal-percent20"),
        ("100 song.mp3", b"space-name"),
        ("track%23one.mp3", b"hash-name"),
        ("音乐.mp3", b"utf8-name"),
        ("nested/sub track.mp3", b"subdir"),
    ],
)
def test_media_audio_route_serves_signed_file(backend_ctx, songs_tmp, relative_path, payload):
    target = songs_tmp / Path(relative_path.replace("/", os.sep))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)

    signed_url = backend_ctx.build_media_audio_url(
        f"songs/{relative_path}",
        base_url="http://127.0.0.1:5000",
    )
    assert signed_url is not None
    assert signed_url.startswith("http://127.0.0.1:5000/media/audio?")
    assert backend_ctx._extract_media_audio_file_param(signed_url) == relative_path

    query = signed_url.split("?", 1)[1]
    with TestClient(backend_ctx.backend.app) as client:
        response = client.get(f"/media/audio?{query}")

    assert response.status_code == 200
    assert response.content == payload
