#!/usr/bin/env python3
"""
yt-whisper
----------
Transcribe any YouTube video, playlist, or batch of URLs using
OpenAI Whisper — locally, with no API key and no cost.

Download audio and transcribe in a single command. Supports single
videos, full playlists, and text files containing one URL per line.

Install:
  pip install openai-whisper yt-dlp
  brew install ffmpeg        # macOS
  sudo apt install ffmpeg    # Linux

Usage:
  # Transcribe a single video:
  python3 yt_whisper.py --url "https://www.youtube.com/watch?v=VIDEO_ID"

  # Transcribe an entire playlist:
  python3 yt_whisper.py --url "https://www.youtube.com/playlist?list=PLAYLIST_ID"

  # Transcribe a batch of URLs from a text file (one URL per line):
  python3 yt_whisper.py --file urls.txt

  # Use a more accurate model and specify language:
  python3 yt_whisper.py --url "https://youtu.be/VIDEO_ID" --model medium --language en

  # Output as SRT subtitles:
  python3 yt_whisper.py --url "https://youtu.be/VIDEO_ID" --format srt

  # Keep the downloaded audio files after transcription:
  python3 yt_whisper.py --url "https://youtu.be/VIDEO_ID" --keep-audio
"""

from __future__ import annotations

from transcript_identity import (configuration, output_stem, verified_existing,
                                 atomic_write, write_manifest, finish_batch,
                                 subtitle_time)

import argparse
import os
import re
import sys
import tempfile
import time
from pathlib import Path

MODEL_SIZES   = ["tiny", "base", "small", "medium", "large", "turbo"]
OUTPUT_FORMATS = ["txt", "srt", "vtt", "tsv", "json"]


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
    return s[:max_len]


def load_urls_from_file(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def _srt_time(seconds: float) -> str:
    return subtitle_time(seconds)


def _vtt_time(seconds: float) -> str:
    return _srt_time(seconds).replace(",", ".")


def write_txt(result: dict, dest: Path) -> None:
    dest.write_text(result.get("text", "").strip() + "\n", encoding="utf-8")


def write_srt(result: dict, dest: Path) -> None:
    segs = result.get("segments", [])
    lines = []
    for i, seg in enumerate(segs, 1):
        lines.append(f"{i}\n{_srt_time(seg['start'])} --> {_srt_time(seg['end'])}\n{seg['text'].strip()}\n")
    dest.write_text("\n".join(lines), encoding="utf-8")


def write_vtt(result: dict, dest: Path) -> None:
    segs = result.get("segments", [])
    lines = ["WEBVTT\n"]
    for seg in segs:
        lines.append(f"{_vtt_time(seg['start'])} --> {_vtt_time(seg['end'])}\n{seg['text'].strip()}\n")
    dest.write_text("\n".join(lines), encoding="utf-8")


def write_tsv(result: dict, dest: Path) -> None:
    segs = result.get("segments", [])
    lines = ["start_ms\tend_ms\ttext"]
    for seg in segs:
        text = seg["text"].strip().replace("\t", " ")
        lines.append(f"{int(seg['start']*1000)}\t{int(seg['end']*1000)}\t{text}")
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_json(result: dict, dest: Path) -> None:
    import json
    dest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


WRITERS = {"txt": write_txt, "srt": write_srt, "vtt": write_vtt,
           "tsv": write_tsv, "json": write_json}


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_audio(url: str, tmp_dir: Path) -> tuple[Path | None, str]:
    """
    Download audio from a YouTube URL using yt-dlp.
    Returns (audio_path, video_title) or (None, error_message).
    """
    try:
        import yt_dlp
    except ImportError:
        sys.exit(
            "\nError: yt-dlp is not installed.\n"
            "Run:  pip install yt-dlp\n"
        )

    out_template = str(tmp_dir / "%(id)s.%(ext)s")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_template,
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "unknown")
            video_id = info.get("id", "unknown")
            audio_path = tmp_dir / f"{video_id}.mp3"
            if not audio_path.exists():
                # Fallback: find any mp3 in tmp_dir matching the id
                matches = list(tmp_dir.glob(f"{video_id}.*"))
                audio_path = matches[0] if matches else None
            return audio_path, title
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def resolve_urls(url: str) -> list[tuple[str, str]]:
    """
    Expand a URL into a list of (url, title) pairs.
    Handles playlists by enumerating all entries without downloading.
    """
    try:
        import yt_dlp
    except ImportError:
        sys.exit("\nError: yt-dlp is not installed.\nRun:  pip install yt-dlp\n")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,   # Don't download, just list entries
        "skip_download": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info is None:
                return []
            if "entries" in info:
                # Playlist
                entries = []
                for entry in info["entries"]:
                    if entry:
                        entry_url = entry.get("url") or f"https://www.youtube.com/watch?v={entry.get('id', '')}"
                        entries.append((entry_url, entry.get("title", "unknown")))
                return entries
            else:
                return [(url, info.get("title", "unknown"))]
    except Exception as e:  # noqa: BLE001
        print(f"  Warning: could not resolve URL '{url}': {e}", file=sys.stderr)
        return [(url, "unknown")]


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
) -> dict:
    try:
        import whisper
    except ImportError:
        sys.exit(
            "\nError: openai-whisper is not installed.\n"
            "Run:  pip install openai-whisper\n"
            "Also install ffmpeg:\n"
            "  macOS:  brew install ffmpeg\n"
            "  Linux:  sudo apt install ffmpeg\n"
        )

    # Resolve playlists / singles → flat list of (url, title)
    print("Resolving URLs...")
    all_videos: list[tuple[str, str]] = []
    for url in urls:
        resolved = resolve_urls(url)
        all_videos.extend(resolved)

    if not all_videos:
        sys.exit("No videos found to transcribe.")

    print(f"Found {len(all_videos)} video(s) to transcribe.\n")
    print(f"Loading Whisper model '{model_name}'...")
    model = whisper.load_model(model_name)
    print(f"Model loaded.\n")

    output_dir.mkdir(parents=True, exist_ok=True)
    tmp_root = audio_dir or Path(tempfile.mkdtemp(prefix="yt_whisper_"))

    config = configuration("yt_whisper.py", model=model_name, language=language, output_format=output_format,
                           keep_audio=keep_audio, audio_dir=str(audio_dir.resolve()) if audio_dir else None)

    total_start = time.time()
    succeeded, skipped, failed = 0, 0, 0

    for idx, (video_url, raw_title) in enumerate(all_videos, 1):
        prefix = f"[{idx}/{len(all_videos)}]"
        stem = output_stem(video_url, config)
        dest = output_dir / f"{stem}.{output_format}"

        print(f"{prefix} {raw_title}")

        if skip_existing and verified_existing(dest, video_url, config):
            print(f"         SKIP (configuration and output hashes verified)\n")
            skipped += 1
            continue

        # Download audio
        print(f"         Downloading audio...")
        dl_start = time.time()
        audio_path, result_or_err = download_audio(video_url, tmp_root)
        if audio_path is None:
            print(f"         ERROR downloading: {result_or_err}\n", file=sys.stderr)
            failed += 1
            continue
        dl_elapsed = time.time() - dl_start
        print(f"         Downloaded in {format_duration(dl_elapsed)}")

        # Transcribe
        print(f"         Transcribing with Whisper ({model_name})...")
        tr_start = time.time()
        try:
            options: dict = {}
            if language:
                options["language"] = language
            result = model.transcribe(str(audio_path), verbose=verbose, **options)
            atomic_write(WRITERS[output_format], result, dest)
            tr_elapsed = time.time() - tr_start
            lang_detected = result.get("language", "?")
            print(f"         Done in {format_duration(tr_elapsed)} | lang: {lang_detected}")
            print(f"         -> {dest}\n")
            write_manifest(dest, video_url, raw_title, config, [audio_path] if keep_audio else [])
            succeeded += 1
        except Exception as e:  # noqa: BLE001
            print(f"         ERROR transcribing: {e}\n", file=sys.stderr)
            failed += 1
        finally:
            if not keep_audio and audio_path and audio_path.exists():
                audio_path.unlink()

    total_elapsed = time.time() - total_start
    print(f"{'='*55}")
    print(f"Finished in {format_duration(total_elapsed)}")
    print(f"  Transcribed : {succeeded}")
    print(f"  Skipped     : {skipped}")
    print(f"  Failed      : {failed}")
    print(f"  Output dir  : {output_dir}\n")

    # Clean up temp dir if we created it and aren't keeping audio
    if not keep_audio and not audio_dir:
        try:
            tmp_root.rmdir()
        except OSError:
            pass

    return finish_batch(output_dir, succeeded, skipped, failed)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="yt_whisper",
        description=(
            "Transcribe YouTube videos or playlists to text using "
            "OpenAI Whisper — locally, no API key, no cost."
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
        help="Path to a text file containing one YouTube URL per line.",
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
            "Language hint for faster, more accurate results. "
            "e.g. 'en', 'es', 'fr', 'ja'. Omit to auto-detect."
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
        help="Keep downloaded audio files after transcription.",
    )
    p.add_argument(
        "--audio-dir",
        default=None,
        help=(
            "Where to save downloaded audio files. "
            "Implies --keep-audio. Defaults to a temp folder."
        ),
    )
    p.add_argument(
        "--skip-existing",
        action="store_true",
        default=False,
        help="Skip videos that already have a transcript in the output folder.",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Print Whisper's segment-level output as it transcribes.",
    )
    return p


def main() -> None:
    args = build_parser().parse_args()

    # Collect URLs
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

    print(f"\nModel        : {args.model}")
    print(f"Language     : {args.language or 'auto-detect'}")
    print(f"Format       : {args.output_format}")
    print(f"Output       : {output_dir}")
    print(f"Keep audio   : {keep_audio}")
    print(f"Skip existing: {args.skip_existing}\n")

    summary = transcribe_urls(
        urls=urls,
        output_dir=output_dir,
        model_name=args.model,
        language=args.language,
        output_format=args.output_format,
        skip_existing=args.skip_existing,
        keep_audio=keep_audio,
        audio_dir=audio_dir,
        verbose=args.verbose,
    )

    if summary["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
