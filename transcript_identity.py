"""Stable source/configuration identity and verifiable local outputs."""
from __future__ import annotations
import hashlib
import json
import tempfile
import os
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone


def digest(path):
    sha = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def source_id(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    parts = parsed.path.strip("/").split("/")
    identity = None
    if host in ("youtu.be", "www.youtu.be"):
        identity = parts[0]
    elif host == "youtube.com" or host.endswith(".youtube.com"):
        identity = parse_qs(parsed.query).get("v", [None])[0]
        if not identity and len(parts) == 2 and parts[0] in ("shorts", "embed", "live"):
            identity = parts[1]
    if identity and len(identity) <= 64 and all(c.isascii() and (c.isalnum() or c in "-_") for c in identity):
        return "youtube-" + identity
    return "url-" + hashlib.sha256(url.encode()).hexdigest()


def configuration(engine, **settings):
    packages = {}
    for name in ("openai-whisper", "yt-dlp", "torch"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = "unavailable"
    return {"schema": 1, "pipeline_revision": 2, "engine": engine, "packages": packages, **settings}


def output_stem(url, config):
    encoded = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    return source_id(url) + "__" + hashlib.sha256(encoded).hexdigest()[:16]


def atomic_json(path, data):
    atomic_write(lambda result, target: target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"), data, path)


def atomic_write(writer, result, dest):
    dest = Path(dest)
    fd, name = tempfile.mkstemp(prefix=dest.name + ".", suffix=".tmp", dir=dest.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        writer(result, temporary)
        temporary.replace(dest)
    finally:
        temporary.unlink(missing_ok=True)


def write_manifest(dest, url, title, config, retained=()):
    # Absolute paths support explicit media directories; records remain local.
    files = [{"path": str(Path(p).resolve()), "sha256": digest(p)} for p in (dest, *retained)]
    atomic_json(dest.with_suffix(".manifest.json"), {
        "schema": 1, "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_id": source_id(url), "source_url": url, "title": title,
        "configuration": config, "files": files,
    })


def verified_existing(dest, url, config):
    try:
        record = json.loads(dest.with_suffix(".manifest.json").read_text(encoding="utf-8"))
        files = record["files"]
        return (record["schema"] == 1 and record["source_id"] == source_id(url)
                and record["configuration"] == config and bool(files)
                and Path(files[0]["path"]).resolve() == dest.resolve()
                and all(digest(file["path"]) == file["sha256"] for file in files))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def subtitle_time(seconds):
    milliseconds = max(0, round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def finish_batch(output_dir, succeeded, skipped, failed):
    summary = {"succeeded": succeeded, "skipped": skipped, "failed": failed,
               "finished_at_utc": datetime.now(timezone.utc).isoformat()}
    atomic_json(output_dir / "last-run.json", summary)
    return summary
