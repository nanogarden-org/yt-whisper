import importlib
import io
from contextlib import redirect_stdout, redirect_stderr
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock
from transcript_identity import configuration, output_stem, write_manifest, verified_existing, source_id, atomic_write


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self.output = redirect_stdout(io.StringIO())
        self.errors = redirect_stderr(io.StringIO())
        self.output.__enter__()
        self.errors.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)
        self.addCleanup(self.errors.__exit__, None, None, None)

    def test_failed_writer_preserves_previous_output(self):
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "out.txt"
            dest.write_text("previous")
            def broken(result, target):
                target.write_text("partial")
                raise OSError("disk failure")
            with self.assertRaises(OSError):
                atomic_write(broken, {}, dest)
            self.assertEqual(dest.read_text(), "previous")
            self.assertEqual(list(dest.parent.glob("*.tmp")), [])

    def test_source_alias_and_config_identity(self):
        a = "https://youtu.be/abc123?t=30"
        b = "https://www.youtube.com/watch?v=abc123"
        self.assertEqual(source_id(a), source_id(b))
        config = configuration("test", model="tiny", language="en")
        self.assertEqual(output_stem(a, config), output_stem(b, config))
        self.assertNotEqual(output_stem(a, config), output_stem("https://youtu.be/xyz", config))
        self.assertNotEqual(output_stem(a, config), output_stem(a, {**config, "language": "fr"}))

    def test_skip_requires_manifest_configuration_and_all_hashes(self):
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "transcript.txt"
            media = Path(folder) / "media.mp3"
            dest.write_text("transcript")
            media.write_bytes(b"audio")
            config = configuration("test", model="tiny")
            url = "https://youtu.be/abc"
            self.assertFalse(verified_existing(dest, url, config))
            write_manifest(dest, url, "title", config, [media])
            self.assertTrue(verified_existing(dest, url, config))
            self.assertFalse(verified_existing(dest, url, {**config, "model": "base"}))
            dest.write_text("changed")
            self.assertFalse(verified_existing(dest, url, config))
            dest.write_text("transcript")
            media.unlink()
            self.assertFalse(verified_existing(dest, url, config))

    def test_subtitle_carry_both_engines(self):
        for name in ("yt_whisper", "yt_whisper_chunked"):
            module = importlib.import_module(name)
            self.assertEqual(module._srt_time(59.9996), "00:01:00,000")
            self.assertEqual(module._vtt_time(3599.9996), "01:00:00.000")

    def test_batch_engines_same_titles_skip_and_partial_failure(self):
        for name in ("yt_whisper", "yt_whisper_chunked"):
            module = importlib.import_module(name)
            with self.subTest(engine=name), tempfile.TemporaryDirectory() as folder:
                output = Path(folder)
                audio = output / "input.mp3"
                audio.write_bytes(b"audio")
                urls = [("https://youtu.be/one", "same"), ("https://youtu.be/two", "same"), ("https://youtu.be/bad", "same")]
                model = Mock()
                model.transcribe.return_value = {"text": "test", "segments": [], "language": "en"}
                whisper = SimpleNamespace(load_model=Mock(return_value=model))
                def download(url, *args):
                    if url.endswith("bad"):
                        return (None, "failed", None, "bad") if name.endswith("chunked") else (None, "failed")
                    return (audio, "same", 1, url.rsplit("/", 1)[-1]) if name.endswith("chunked") else (audio, "same")
                kwargs = dict(urls=["playlist"], output_dir=output, model_name="tiny", language="en", output_format="txt", skip_existing=True, keep_audio=True, audio_dir=output, verbose=False)
                if name.endswith("chunked"):
                    kwargs.update(chunk_minutes=20, chunking_enabled=True, audio_format="mp3", keep_video=False, video_dir=None)
                with patch.dict(sys.modules, {"whisper": whisper}), patch.object(module, "resolve_urls", return_value=urls), patch.object(module, "download_audio", side_effect=download), patch.object(module, "require_ffmpeg", create=True):
                    summary = module.transcribe_urls(**kwargs)
                    self.assertEqual((summary["succeeded"], summary["failed"]), (2, 1))
                    self.assertEqual(len(list(output.glob("*.txt"))), 2)
                    self.assertEqual(json.loads((output / "last-run.json").read_text())["failed"], 1)
                    # Title updates must not cause duplicate output or inference.
                    with patch.object(module, "resolve_urls", return_value=[(url, "new title") for url, _ in urls[:2]]):
                        summary = module.transcribe_urls(**kwargs)
                    self.assertEqual(summary["skipped"], 2)
                    self.assertEqual(model.transcribe.call_count, 2)
                    # Corruption forces a new transcription.
                    next(output.glob("*.txt")).write_text("damaged")
                    with patch.object(module, "resolve_urls", return_value=urls[:2]):
                        summary = module.transcribe_urls(**kwargs)
                    self.assertEqual(summary["succeeded"], 1)

    def test_chunk_cache_invalidated_for_changed_audio(self):
        import yt_whisper_chunked as module
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            audio = work / "input.mp3"
            audio.write_bytes(b"first")
            chunk = work / "chunk.mp3"
            chunk.write_bytes(b"chunk")
            model = Mock()
            model.transcribe.return_value = {"text": "test", "segments": []}
            with patch.object(module, "split_audio", return_value=[chunk]):
                module.transcribe_chunked(model, audio, work, "en", False, 60)
                module.transcribe_chunked(model, audio, work, "en", False, 60)
                self.assertEqual(model.transcribe.call_count, 1)
                audio.write_bytes(b"second")
                module.transcribe_chunked(model, audio, work, "en", False, 60)
                self.assertEqual(model.transcribe.call_count, 2)

    def test_cli_returns_nonzero_for_partial_failure(self):
        for name in ("yt_whisper", "yt_whisper_chunked"):
            module = importlib.import_module(name)
            with patch.object(sys, "argv", [name, "--url", "https://youtu.be/abc"]), patch.object(module, "transcribe_urls", return_value={"failed": 1}):
                with self.assertRaises(SystemExit) as raised:
                    module.main()
                self.assertEqual(raised.exception.code, 1)
