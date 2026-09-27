#!/usr/bin/env python3
"""
yt-whisper
----------
Transcribe YouTube videos, playlists, or batches of URLs using
OpenAI Whisper locally — no API key and no usage fee.

Long videos are automatically split into smaller chunks before transcription
to avoid large single-pass RAM allocations on CPU-only systems. Chunk progress
is cached so interrupted jobs can resume without retranscribing finished chunks.

Install:
  pip install openai-whisper yt-dlp

Also install ffmpeg and make sure ffmpeg is available on PATH.

Usage:
  # Single video
  python3 yt_whisper.py --url "https://www.youtube.com/watch?v=VIDEO_ID"

  # Playlist
  python3 yt_whisper.py --url "https://www.youtube.com/playlist?list=PLAYLIST_ID"

  # Batch file, one URL per line
  python3 yt_whisper.py --file urls.txt

  # Your usual batch command
  python3 yt_whisper.py --file to-be-transcribed.txt --output ./transcripts --language en --skip-existing

  # Change automatic chunk size (default: 20 minutes)
  python3 yt_whisper.py --file urls.txt --chunk-minutes 15

  # Disable chunking and force the old single-pass behavior
  python3 yt_whisper.py --file urls.txt --no-chunk
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

MODEL_SIZES = ["tiny", "base", "small", "medium", "large", "turbo"]
OUTPUT_FORMATS = ["txt", "srt", "vtt", "tsv", "json"]
DEFAULT_CHUNK_MINUTES = 20

# Windows consoles commonly default to cp1252, which cannot print some
# legitimate YouTube title characters. Keep batch jobs running regardless of
# the console encoding.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def format_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s" if h else f"{m}m {s}s" if m else f"{s}s"


def safe_filename(s: str, max_len: int = 120) -> str:
    """Strip characters illegal on most filesystems."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:max_len] or "untitled"


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of a file in a memory-conscious way."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def record_path(path: Path, output_dir: Path) -> dict:
    """Describe a preserved file using a portable path where possible."""
    try:
        display_path = str(path.resolve().relative_to(output_dir.resolve()))
    except ValueError:
        display_path = str(path.resolve())
    return {
        "path": display_path,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def write_provenance_record(
    dest: Path,
    output_dir: Path,
    source_url: str,
    title: str,
    video_id: str,
    duration: float | None,
    model_name: str,
    language: str | None,
    output_format: str,
    audio_path: Path | None,
    video_path: Path | None,
) -> None:
    """Write a sidecar JSON record for later source and file verification."""
    files = {"transcript": record_path(dest, output_dir)}
    if audio_path is not None and audio_path.exists():
        files["audio"] = record_path(audio_path, output_dir)
    if video_path is not None and video_path.exists():
        files["video"] = record_path(video_path, output_dir)

    record = {
        "record_type": "yt_whisper_provenance",
        "record_version": 1,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "url": source_url,
            "title": title,
            "video_id": video_id,
            "duration_seconds": duration,
        },
        "processing": {
            "tool": "yt_whisper_chunked.py",
            "model": model_name,
            "language_hint": language,
            "transcript_format": output_format,
        },
        "files": files,
    }
    record_dest = output_dir / f"{dest.stem}.record.json"
    record_dest.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_urls_from_file(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [
        line.strip()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    ]


def require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        sys.exit(
            "\nError: ffmpeg was not found on PATH.\n"
            "Whisper and automatic chunking both require ffmpeg.\n"
        )


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def _srt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds % 1) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _vtt_time(seconds: float) -> str:
    return _srt_time(seconds).replace(",", ".")


def write_txt(result: dict, dest: Path) -> None:
    dest.write_text(result.get("text", "").strip() + "\n", encoding="utf-8")


def write_srt(result: dict, dest: Path) -> None:
    segs = result.get("segments", [])
    lines = []
    for i, seg in enumerate(segs, 1):
        lines.append(
            f"{i}\n"
            f"{_srt_time(seg['start'])} --> {_srt_time(seg['end'])}\n"
            f"{seg['text'].strip()}\n"
        )
    dest.write_text("\n".join(lines), encoding="utf-8")


def write_vtt(result: dict, dest: Path) -> None:
    segs = result.get("segments", [])
    lines = ["WEBVTT\n"]
    for seg in segs:
        lines.append(
            f"{_vtt_time(seg['start'])} --> {_vtt_time(seg['end'])}\n"
            f"{seg['text'].strip()}\n"
        )
    dest.write_text("\n".join(lines), encoding="utf-8")


def write_tsv(result: dict, dest: Path) -> None:
    segs = result.get("segments", [])
    lines = ["start_ms\tend_ms\ttext"]
    for seg in segs:
        text = seg["text"].strip().replace("\t", " ")
        lines.append(
            f"{int(seg['start'] * 1000)}\t"
            f"{int(seg['end'] * 1000)}\t{text}"
        )
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_json(result: dict, dest: Path) -> None:
    dest.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


WRITERS = {
    "txt": write_txt,
    "srt": write_srt,
    "vtt": write_vtt,
    "tsv": write_tsv,
    "json": write_json,
}


# ---------------------------------------------------------------------------
# Download / URL resolution
# ---------------------------------------------------------------------------

def download_audio(
    url: str,
    work_dir: Path,
    audio_format: str = "mp3",
) -> tuple[Path | None, str, float | None, str]:
    """
    Download audio with yt-dlp.

    Returns:
      (audio_path, title_or_error, duration_seconds, video_id)

    audio_path is None on failure.
    """
    try:
        import yt_dlp
    except ImportError:
        sys.exit(
            "\nError: yt-dlp is not installed.\n"
            "Run:  pip install yt-dlp\n"
        )

    work_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(work_dir / "%(id)s.%(ext)s")

    postprocessor = {
        "key": "FFmpegExtractAudio",
        "preferredcodec": audio_format,
    }
    if audio_format == "mp3":
        postprocessor["preferredquality"] = "192"

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_template,
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [postprocessor],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        title = info.get("title", "unknown")
        video_id = str(info.get("id", "unknown"))
        duration_raw = info.get("duration")
        duration = float(duration_raw) if duration_raw is not None else None

        expected = work_dir / f"{video_id}.{audio_format}"
        if expected.exists():
            return expected, title, duration, video_id

        matches = list(work_dir.glob(f"{video_id}.*"))
        audio_path = matches[0] if matches else None
        if audio_path is None:
            return None, "yt-dlp finished but no audio file was found", duration, video_id

        return audio_path, title, duration, video_id

    except Exception as exc:  # noqa: BLE001
        return None, str(exc), None, "unknown"


def download_video(url: str, work_dir: Path) -> tuple[Path | None, str]:
    """Download the best available video and audio, merged into an MP4."""
    try:
        import yt_dlp
    except ImportError:
        sys.exit("\nError: yt-dlp is not installed.\nRun:  pip install yt-dlp\n")

    work_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(work_dir / "video_%(id)s.%(ext)s")
    ydl_opts = {
        "format": "bv*+ba/b",
        "outtmpl": out_template,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
        video_id = str(info.get("id", "unknown"))
        title = info.get("title", "unknown")
        expected = work_dir / f"video_{video_id}.mp4"
        if expected.exists():
            return expected, title
        return None, "yt-dlp finished but no MP4 video file was found"
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def resolve_urls(url: str) -> list[tuple[str, str]]:
    """
    Expand a URL into (url, title) pairs.
    Handles playlists without downloading them.
    """
    try:
        import yt_dlp
    except ImportError:
        sys.exit("\nError: yt-dlp is not installed.\nRun:  pip install yt-dlp\n")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if info is None:
            return []

        if "entries" in info:
            entries = []
            for entry in info["entries"]:
                if not entry:
                    continue
                entry_url = (
                    entry.get("url")
                    or f"https://www.youtube.com/watch?v={entry.get('id', '')}"
                )
                entries.append((entry_url, entry.get("title", "unknown")))
            return entries

        return [(url, info.get("title", "unknown"))]

    except Exception as exc:  # noqa: BLE001
        print(
            f"  Warning: could not resolve URL '{url}': {exc}",
            file=sys.stderr,
        )
        return [(url, "unknown")]


# ---------------------------------------------------------------------------
# Chunking / resume cache
# ---------------------------------------------------------------------------

def split_audio(
    audio_path: Path,
    chunk_dir: Path,
    chunk_seconds: int,
) -> list[Path]:
    """
    Split audio into fixed-length 16 kHz mono WAV chunks.

    WAV is intentionally used here because it is simple and reliable for
    Whisper/ffmpeg. Only one chunk is loaded by Whisper at a time.
    """
    chunk_dir.mkdir(parents=True, exist_ok=True)

    existing = sorted(chunk_dir.glob("chunk_*.wav"))
    if existing:
        return existing

    output_pattern = str(chunk_dir / "chunk_%04d.wav")

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(audio_path),
        "-ac", "1",
        "-ar", "16000",
        "-f", "segment",
        "-segment_time", str(chunk_seconds),
        "-reset_timestamps", "1",
        output_pattern,
    ]

    completed = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if completed.returncode != 0:
        raise RuntimeError(
            "ffmpeg could not split the audio:\n"
            + completed.stderr.strip()
        )

    chunks = sorted(chunk_dir.glob("chunk_*.wav"))
    if not chunks:
        raise RuntimeError("ffmpeg returned success but produced no chunks.")

    return chunks


def chunk_cache_path(cache_dir: Path, chunk_index: int) -> Path:
    return cache_dir / f"chunk_{chunk_index:04d}.json"


def save_chunk_result(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result, ensure_ascii=False),
        encoding="utf-8",
    )


def load_chunk_result(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def merge_chunk_results(
    results: list[dict],
    chunk_seconds: int,
) -> dict:
    """
    Merge Whisper results and shift each chunk's timestamps back onto
    the original video's timeline.
    """
    merged_text: list[str] = []
    merged_segments: list[dict] = []
    detected_language = "?"

    next_segment_id = 0

    for chunk_index, result in enumerate(results):
        text = result.get("text", "").strip()
        if text:
            merged_text.append(text)

        if detected_language == "?":
            detected_language = result.get("language", "?")

        offset = chunk_index * chunk_seconds

        for seg in result.get("segments", []):
            new_seg = dict(seg)
            new_seg["id"] = next_segment_id
            next_segment_id += 1
            new_seg["start"] = float(seg.get("start", 0.0)) + offset
            new_seg["end"] = float(seg.get("end", 0.0)) + offset
            merged_segments.append(new_seg)

    return {
        "text": " ".join(merged_text).strip(),
        "segments": merged_segments,
        "language": detected_language,
    }


def transcribe_chunked(
    model,
    audio_path: Path,
    work_dir: Path,
    language: str | None,
    verbose: bool,
    chunk_seconds: int,
) -> dict:
    chunk_dir = work_dir / "chunks"
    cache_dir = work_dir / "chunk_results"

    chunks = split_audio(audio_path, chunk_dir, chunk_seconds)
    print(
        f"         Split into {len(chunks)} chunk(s) "
        f"of up to {format_duration(chunk_seconds)}"
    )

    results: list[dict] = []

    for i, chunk_path in enumerate(chunks, 1):
        cache_path = chunk_cache_path(cache_dir, i - 1)

        if cache_path.exists():
            print(f"         [{i}/{len(chunks)}] Resume: cached chunk")
            result = load_chunk_result(cache_path)
            results.append(result)
            continue

        print(f"         [{i}/{len(chunks)}] Transcribing chunk...")
        chunk_start = time.time()

        options: dict = {}
        if language:
            options["language"] = language

        result = model.transcribe(
            str(chunk_path),
            verbose=verbose,
            **options,
        )

        save_chunk_result(cache_path, result)
        results.append(result)

        print(
            f"                 done in "
            f"{format_duration(time.time() - chunk_start)}"
        )

    return merge_chunk_results(results, chunk_seconds)


def clean_work_dir(work_dir: Path) -> None:
    try:
        shutil.rmtree(work_dir)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def transcribe_urls(
    urls: list[str],
    output_dir: Path,
    model_name: str,
    language: str | None,
    output_format: str,
    skip_existing: bool,
    keep_audio: bool,
    audio_dir: Path | None,
    verbose: bool,
    chunk_minutes: int,
    chunking_enabled: bool,
    audio_format: str,
    keep_video: bool,
    video_dir: Path | None,
) -> None:
    try:
        import whisper
    except ImportError:
        sys.exit(
            "\nError: openai-whisper is not installed.\n"
            "Run:  pip install openai-whisper\n"
        )

    require_ffmpeg()

    print("Resolving URLs...")
    all_videos: list[tuple[str, str]] = []
    for url in urls:
        all_videos.extend(resolve_urls(url))

    if not all_videos:
        sys.exit("No videos found to transcribe.")

    print(f"Found {len(all_videos)} video(s) to transcribe.\n")
    print(f"Loading Whisper model '{model_name}'...")
    model = whisper.load_model(model_name)
    print("Model loaded.\n")

    output_dir.mkdir(parents=True, exist_ok=True)

    # Persistent work folder is deliberate:
    # interrupted chunk jobs can resume on the next run.
    work_root = output_dir / ".yt_whisper_work"
    work_root.mkdir(parents=True, exist_ok=True)

    total_start = time.time()
    succeeded, skipped, failed = 0, 0, 0
    chunk_seconds = chunk_minutes * 60

    for idx, (video_url, raw_title) in enumerate(all_videos, 1):
        prefix = f"[{idx}/{len(all_videos)}]"
        safe_title = safe_filename(raw_title)
        dest = output_dir / f"{safe_title}.{output_format}"

        print(f"{prefix} {raw_title}")

        if skip_existing and dest.exists():
            print("         SKIP (already transcribed)\n")
            skipped += 1
            continue

        # One stable work directory per title. The short hash prevents two
        # videos with identical titles from colliding.
        import hashlib
        url_hash = hashlib.sha1(video_url.encode("utf-8")).hexdigest()[:10]
        video_work = work_root / f"{safe_title[:80]}__{url_hash}"
        video_work.mkdir(parents=True, exist_ok=True)

        print("         Downloading audio...")
        dl_start = time.time()

        audio_path, result_or_err, duration, video_id = download_audio(
            video_url,
            video_work,
            audio_format,
        )

        if audio_path is None:
            print(
                f"         ERROR downloading: {result_or_err}\n",
                file=sys.stderr,
            )
            failed += 1
            continue

        print(
            f"         Downloaded in "
            f"{format_duration(time.time() - dl_start)}"
        )

        if duration is not None:
            print(f"         Duration: {format_duration(duration)}")

        video_path = None
        if keep_video:
            print("         Downloading video (MP4)...")
            video_path, video_result_or_err = download_video(video_url, video_work)
            if video_path is None:
                print(
                    f"         ERROR downloading video: {video_result_or_err}\n",
                    file=sys.stderr,
                )
                failed += 1
                continue

        should_chunk = (
            chunking_enabled
            and duration is not None
            and duration > chunk_seconds
        )

        tr_start = time.time()

        try:
            if should_chunk:
                print(
                    f"         Long audio detected; using "
                    f"{chunk_minutes}-minute chunks."
                )
                result = transcribe_chunked(
                    model=model,
                    audio_path=audio_path,
                    work_dir=video_work,
                    language=language,
                    verbose=verbose,
                    chunk_seconds=chunk_seconds,
                )
            else:
                print(
                    f"         Transcribing with Whisper ({model_name})..."
                )
                options: dict = {}
                if language:
                    options["language"] = language
                result = model.transcribe(
                    str(audio_path),
                    verbose=verbose,
                    **options,
                )

            WRITERS[output_format](result, dest)

            tr_elapsed = time.time() - tr_start
            lang_detected = result.get("language", "?")
            print(
                f"         Done in {format_duration(tr_elapsed)} "
                f"| lang: {lang_detected}"
            )
            print(f"         -> {dest}\n")
            succeeded += 1

            preserved_audio_path = None
            preserved_video_path = None

            # Successful final transcript means the resume cache is no longer
            # needed. Keep downloaded audio only when explicitly requested.
            if keep_audio:
                target_audio_dir = audio_dir or (output_dir / "audio")
                target_audio_dir.mkdir(parents=True, exist_ok=True)
                kept_audio = target_audio_dir / (
                    f"{safe_title}{audio_path.suffix}"
                )
                if kept_audio.resolve() != audio_path.resolve():
                    shutil.copy2(audio_path, kept_audio)
                preserved_audio_path = kept_audio

            if keep_video and video_path is not None:
                target_video_dir = video_dir or (output_dir / "video")
                target_video_dir.mkdir(parents=True, exist_ok=True)
                kept_video = target_video_dir / f"{safe_title}.mp4"
                if kept_video.resolve() != video_path.resolve():
                    shutil.copy2(video_path, kept_video)
                preserved_video_path = kept_video

            write_provenance_record(
                dest=dest,
                output_dir=output_dir,
                source_url=video_url,
                title=raw_title,
                video_id=video_id,
                duration=duration,
                model_name=model_name,
                language=language,
                output_format=output_format,
                audio_path=preserved_audio_path,
                video_path=preserved_video_path,
            )
            print(f"         Record -> {output_dir / f'{dest.stem}.record.json'}\n")

            clean_work_dir(video_work)

        except MemoryError:
            print(
                "         ERROR: Python ran out of memory.\n"
                "         Try a smaller --chunk-minutes value, "
                "for example --chunk-minutes 10.\n",
                file=sys.stderr,
            )
            failed += 1

        except Exception as exc:  # noqa: BLE001
            print(
                f"         ERROR transcribing: {exc}\n",
                file=sys.stderr,
            )
            print(
                "         Progress for chunked jobs has been kept in:\n"
                f"         {video_work}\n"
                "         Run the same command again to resume.\n",
                file=sys.stderr,
            )
            failed += 1

    # Remove work root if all per-video work dirs were cleaned.
    try:
        work_root.rmdir()
    except OSError:
        pass

    total_elapsed = time.time() - total_start
    print("=" * 55)
    print(f"Finished in {format_duration(total_elapsed)}")
    print(f"  Transcribed : {succeeded}")
    print(f"  Skipped     : {skipped}")
    print(f"  Failed      : {failed}")
    print(f"  Output dir  : {output_dir}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="yt_whisper",
        description=(
            "Transcribe YouTube videos or playlists locally with "
            "OpenAI Whisper. Long videos are automatically chunked."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--url", "-u",
        help="YouTube video or playlist URL to transcribe.",
    )
    source.add_argument(
        "--file", "-f",
        help="Text file containing one YouTube URL per line.",
    )

    p.add_argument(
        "--output", "-o",
        default="transcripts",
        help="Folder to write transcripts into. (default: ./transcripts)",
    )
    p.add_argument(
        "--model", "-m",
        default="base",
        choices=MODEL_SIZES,
        help=(
            "Whisper model size. Larger = more accurate, slower, more RAM. "
            "tiny | base (default) | small | medium | large | turbo"
        ),
    )
    p.add_argument(
        "--language", "-l",
        default=None,
        help=(
            "Language hint, e.g. en, es, fr, ja. "
            "Omit to auto-detect."
        ),
    )
    p.add_argument(
        "--format",
        default="txt",
        choices=OUTPUT_FORMATS,
        dest="output_format",
        help="Output format: txt (default), srt, vtt, tsv, json.",
    )
    p.add_argument(
        "--keep-audio",
        action="store_true",
        default=False,
        help="Keep downloaded audio after successful transcription.",
    )
    p.add_argument(
        "--audio-format",
        choices=["mp3", "wav"],
        default="mp3",
        help="Saved audio format: mp3 (default) or wav.",
    )
    p.add_argument(
        "--audio-dir",
        default=None,
        help=(
            "Folder for kept audio files. "
            "Implies --keep-audio."
        ),
    )
    p.add_argument(
        "--keep-video",
        action="store_true",
        default=False,
        help="Download and keep the video as an MP4 file.",
    )
    p.add_argument(
        "--video-dir",
        default=None,
        help="Folder for downloaded MP4 video files. Implies --keep-video.",
    )
    p.add_argument(
        "--skip-existing",
        action="store_true",
        default=False,
        help="Skip videos that already have a final transcript.",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Print Whisper segment-level output.",
    )
    p.add_argument(
        "--chunk-minutes",
        type=int,
        default=DEFAULT_CHUNK_MINUTES,
        metavar="MINUTES",
        help=(
            "Automatically split longer audio into chunks of this size. "
            f"(default: {DEFAULT_CHUNK_MINUTES})"
        ),
    )
    p.add_argument(
        "--no-chunk",
        action="store_true",
        default=False,
        help="Disable automatic chunking and use one-pass transcription.",
    )

    return p


def main() -> None:
    args = build_parser().parse_args()

    if args.chunk_minutes < 1:
        sys.exit("Error: --chunk-minutes must be at least 1.")

    if args.url:
        urls = [args.url]
    else:
        file_path = Path(args.file).expanduser().resolve()
        if not file_path.is_file():
            sys.exit(f"Error: '{file_path}' is not a file.")
        urls = load_urls_from_file(file_path)
        if not urls:
            sys.exit(f"No URLs found in: {file_path}")

    output_dir = Path(args.output).expanduser().resolve()

    audio_dir = None
    keep_audio = args.keep_audio
    if args.audio_dir:
        audio_dir = Path(args.audio_dir).expanduser().resolve()
        audio_dir.mkdir(parents=True, exist_ok=True)
        keep_audio = True

    video_dir = None
    keep_video = args.keep_video
    if args.video_dir:
        video_dir = Path(args.video_dir).expanduser().resolve()
        video_dir.mkdir(parents=True, exist_ok=True)
        keep_video = True

    print(f"\nModel        : {args.model}")
    print(f"Language     : {args.language or 'auto-detect'}")
    print(f"Format       : {args.output_format}")
    print(f"Output       : {output_dir}")
    print(f"Keep audio   : {keep_audio}")
    print(f"Audio format : {args.audio_format}")
    print(f"Keep video   : {keep_video}")
    print(f"Skip existing: {args.skip_existing}")
    print(
        f"Chunking     : "
        f"{'off' if args.no_chunk else f'auto ({args.chunk_minutes} min)'}"
        "\n"
    )

    transcribe_urls(
        urls=urls,
        output_dir=output_dir,
        model_name=args.model,
        language=args.language,
        output_format=args.output_format,
        skip_existing=args.skip_existing,
        keep_audio=keep_audio,
        audio_dir=audio_dir,
        verbose=args.verbose,
        chunk_minutes=args.chunk_minutes,
        chunking_enabled=not args.no_chunk,
        audio_format=args.audio_format,
        keep_video=keep_video,
        video_dir=video_dir,
    )


if __name__ == "__main__":
    main()
