#!/usr/bin/env python3
from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "YT Whisper — Local Transcriber"
ENGINE_NAME = "yt_whisper_chunked.py"
MODELS = ["tiny", "base", "small", "medium", "large", "turbo"]
FORMATS = ["txt", "srt", "vtt", "tsv", "json"]


class YTWhisperGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("900x600")
        self.minsize(760, 520)

        self.process: subprocess.Popen | None = None
        self.output_queue: queue.Queue[str] = queue.Queue()
        self.script_dir = Path(__file__).resolve().parent
        self.engine_path = self.script_dir / ENGINE_NAME

        self.source_mode = tk.StringVar(value="file")
        self.url_var = tk.StringVar()
        self.file_var = tk.StringVar()
        self.output_var = tk.StringVar(value=str(self.script_dir / "transcripts"))
        self.model_var = tk.StringVar(value="base")
        self.language_var = tk.StringVar(value="en")
        self.format_var = tk.StringVar(value="txt")
        self.chunk_var = tk.StringVar(value="20")
        self.skip_existing_var = tk.BooleanVar(value=True)
        self.keep_audio_var = tk.BooleanVar(value=False)
        self.audio_format_var = tk.StringVar(value="mp3")
        self.keep_video_var = tk.BooleanVar(value=False)
        self.verbose_var = tk.BooleanVar(value=False)
        self.no_chunk_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Ready")
        self.video_progress_var = tk.StringVar()
        self.chunk_progress_var = tk.StringVar()

        self._build_ui()
        self._source_mode_changed()
        self.after(100, self._drain_output_queue)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text="YT Whisper", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(root, text="Local YouTube transcription — no terminal required once launched").pack(anchor="w", pady=(0, 12))

        source_box = ttk.LabelFrame(root, text="Source", padding=10)
        source_box.pack(fill="x", pady=(0, 10))

        mode_row = ttk.Frame(source_box)
        mode_row.pack(fill="x", pady=(0, 8))
        ttk.Radiobutton(mode_row, text="Batch file", value="file", variable=self.source_mode,
                        command=self._source_mode_changed).pack(side="left")
        ttk.Radiobutton(mode_row, text="Single URL / playlist", value="url", variable=self.source_mode,
                        command=self._source_mode_changed).pack(side="left", padx=(16, 0))

        self.file_row = ttk.Frame(source_box)
        self.file_row.pack(fill="x")
        ttk.Label(self.file_row, text="URL list:", width=12).pack(side="left")
        self.file_entry = ttk.Entry(self.file_row, textvariable=self.file_var)
        self.file_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.file_button = ttk.Button(self.file_row, text="Browse…", command=self._browse_file)
        self.file_button.pack(side="left")

        self.url_row = ttk.Frame(source_box)
        self.url_row.pack(fill="x", pady=(8, 0))
        ttk.Label(self.url_row, text="YouTube URL:", width=12).pack(side="left")
        self.url_entry = ttk.Entry(self.url_row, textvariable=self.url_var)
        self.url_entry.pack(side="left", fill="x", expand=True)

        options = ttk.LabelFrame(root, text="Options", padding=10)
        options.pack(fill="x", pady=(0, 10))

        out_row = ttk.Frame(options)
        out_row.pack(fill="x", pady=(0, 8))
        ttk.Label(out_row, text="Output folder:", width=14).pack(side="left")
        ttk.Entry(out_row, textvariable=self.output_var).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(out_row, text="Browse…", command=self._browse_output).pack(side="left")

        grid = ttk.Frame(options)
        grid.pack(fill="x")
        ttk.Label(grid, text="Model:").grid(row=0, column=0, sticky="w")
        ttk.Combobox(grid, textvariable=self.model_var, values=MODELS, state="readonly", width=12).grid(row=0, column=1, sticky="w", padx=(6, 18))
        ttk.Label(grid, text="Language:").grid(row=0, column=2, sticky="w")
        ttk.Entry(grid, textvariable=self.language_var, width=10).grid(row=0, column=3, sticky="w", padx=(6, 18))
        ttk.Label(grid, text="Format:").grid(row=0, column=4, sticky="w")
        ttk.Combobox(grid, textvariable=self.format_var, values=FORMATS, state="readonly", width=10).grid(row=0, column=5, sticky="w", padx=(6, 18))
        ttk.Label(grid, text="Chunk min:").grid(row=0, column=6, sticky="w")
        ttk.Spinbox(grid, from_=1, to=120, textvariable=self.chunk_var, width=7).grid(row=0, column=7, sticky="w", padx=(6, 0))

        media_row = ttk.Frame(root)
        media_row.pack(fill="x", pady=(0, 10))

        audio_box = ttk.LabelFrame(media_row, text="Audio", padding=10)
        audio_box.pack(side="left", fill="x", expand=True, padx=(0, 5))
        ttk.Checkbutton(audio_box, text="Save audio", variable=self.keep_audio_var).pack(side="left")
        ttk.Label(audio_box, text="Format:").pack(side="left", padx=(18, 6))
        ttk.Combobox(
            audio_box, textvariable=self.audio_format_var,
            values=["mp3", "wav"], state="readonly", width=8,
        ).pack(side="left")

        video_box = ttk.LabelFrame(media_row, text="Video", padding=10)
        video_box.pack(side="left", fill="x", expand=True, padx=(5, 0))
        ttk.Checkbutton(video_box, text="Download video", variable=self.keep_video_var).pack(side="left")
        ttk.Label(video_box, text="Format: MP4").pack(side="left", padx=(18, 0))

        flags = ttk.Frame(root)
        flags.pack(fill="x", pady=(0, 10))
        ttk.Checkbutton(flags, text="Skip existing", variable=self.skip_existing_var).pack(side="left")
        ttk.Checkbutton(flags, text="Verbose", variable=self.verbose_var).pack(side="left", padx=(14, 0))
        ttk.Checkbutton(flags, text="Disable chunking", variable=self.no_chunk_var).pack(side="left", padx=(14, 0))

        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=(0, 8))
        self.start_button = ttk.Button(actions, text="Start Transcription", command=self._start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="Stop", command=self._stop, state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Open Output Folder", command=self._open_output_folder).pack(side="left", padx=(8, 0))
        ttk.Label(actions, textvariable=self.status_var).pack(side="right")

        self.progress = ttk.Progressbar(root, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 6))

        pr = ttk.Frame(root)
        pr.pack(fill="x", pady=(0, 8))
        ttk.Label(pr, textvariable=self.video_progress_var).pack(side="left")
        ttk.Label(pr, textvariable=self.chunk_progress_var).pack(side="right")

        log_box = ttk.LabelFrame(root, text="Live log", padding=6)
        log_box.pack(fill="x")
        self.log = tk.Text(
            log_box, height=3, wrap="word", state="disabled", font=("Consolas", 10),
        )
        self.log.pack(side="left", fill="x", expand=True)
        scroll = ttk.Scrollbar(log_box, orient="vertical", command=self.log.yview)
        scroll.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scroll.set)

    def _source_mode_changed(self) -> None:
        use_file = self.source_mode.get() == "file"
        self.file_entry.configure(state="normal" if use_file else "disabled")
        self.file_button.configure(state="normal" if use_file else "disabled")
        self.url_entry.configure(state="disabled" if use_file else "normal")

    def _browse_file(self) -> None:
        path = filedialog.askopenfilename(title="Choose URL list", filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if path:
            self.file_var.set(path)

    def _browse_output(self) -> None:
        path = filedialog.askdirectory(title="Choose transcript output folder")
        if path:
            self.output_var.set(path)

    def _open_output_folder(self) -> None:
        path = Path(self.output_var.get()).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showerror("Could not open folder", str(exc))

    def _build_command(self) -> list[str]:
        if not self.engine_path.is_file():
            raise FileNotFoundError(
                f"Could not find {ENGINE_NAME}.\n\nPlace this GUI in the same folder as the engine."
            )

        cmd = [sys.executable, "-u", str(self.engine_path)]
        if self.source_mode.get() == "file":
            source = self.file_var.get().strip()
            if not source or not Path(source).is_file():
                raise ValueError("Choose a valid text file containing YouTube URLs.")
            cmd += ["--file", source]
        else:
            url = self.url_var.get().strip()
            if not url:
                raise ValueError("Enter a YouTube video or playlist URL.")
            cmd += ["--url", url]

        output = self.output_var.get().strip()
        if not output:
            raise ValueError("Choose an output folder.")

        try:
            chunk_minutes = int(self.chunk_var.get())
        except ValueError as exc:
            raise ValueError("Chunk minutes must be a whole number.") from exc
        if chunk_minutes < 1:
            raise ValueError("Chunk minutes must be at least 1.")

        cmd += ["--output", output, "--model", self.model_var.get(), "--format", self.format_var.get(),
                "--chunk-minutes", str(chunk_minutes)]
        language = self.language_var.get().strip()
        if language:
            cmd += ["--language", language]
        if self.skip_existing_var.get():
            cmd.append("--skip-existing")
        if self.keep_audio_var.get():
            cmd.append("--keep-audio")
        cmd += ["--audio-format", self.audio_format_var.get()]
        if self.keep_video_var.get():
            cmd.append("--keep-video")
        if self.verbose_var.get():
            cmd.append("--verbose")
        if self.no_chunk_var.get():
            cmd.append("--no-chunk")
        return cmd

    def _start(self) -> None:
        if self.process is not None:
            return
        try:
            cmd = self._build_command()
        except Exception as exc:
            messagebox.showerror("Cannot start", str(exc))
            return

        self._clear_log()
        self._append_log("Starting:\n" + " ".join(f'\"{x}\"' if " " in x else x for x in cmd) + "\n\n")
        self.status_var.set("Running")
        self.video_progress_var.set("")
        self.chunk_progress_var.set("")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.progress.start(10)

        flags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0
        try:
            self.process = subprocess.Popen(
                cmd,
                cwd=str(self.script_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                universal_newlines=True,
                creationflags=flags,
            )
        except Exception as exc:
            self.process = None
            self._finished_ui()
            messagebox.showerror("Launch failed", str(exc))
            return

        threading.Thread(target=self._read_output, daemon=True).start()

    def _read_output(self) -> None:
        proc = self.process
        if proc is None or proc.stdout is None:
            return
        try:
            for line in iter(proc.stdout.readline, ""):
                if not line:
                    break
                self.output_queue.put(line)
            proc.stdout.close()
            code = proc.wait()
            self.output_queue.put(f"__PROCESS_FINISHED__:{code}\n")
        except Exception as exc:
            self.output_queue.put(f"GUI reader error: {exc}\n")
            self.output_queue.put("__PROCESS_FINISHED__:1\n")

    def _stop(self) -> None:
        if self.process is None:
            return
        if not messagebox.askyesno(
            "Stop transcription?",
            "Stop the current run?\n\nCompleted chunk cache will remain so the job can resume later.",
        ):
            return
        self.status_var.set("Stopping…")
        try:
            self.process.terminate()
        except Exception:
            pass

    def _drain_output_queue(self) -> None:
        try:
            while True:
                line = self.output_queue.get_nowait()
                m = re.match(r"__PROCESS_FINISHED__:(-?\d+)", line)
                if m:
                    self._process_finished(int(m.group(1)))
                    continue
                self._append_log(line)
                self._update_progress(line)
        except queue.Empty:
            pass
        self.after(100, self._drain_output_queue)

    def _update_progress(self, line: str) -> None:
        m = re.search(r"\[(\d+)/(\d+)\]\s+(.+)", line)
        if m and "Transcribing chunk" not in line and "Resume: cached chunk" not in line:
            cur, total, title = m.groups()
            self.video_progress_var.set(f"Video {cur}/{total}: {title[:60]}")

        c = re.search(r"\[(\d+)/(\d+)\]\s+(?:Transcribing chunk|Resume: cached chunk)", line)
        if c:
            cur, total = c.groups()
            self.chunk_progress_var.set(f"Chunk {cur}/{total}")

        if "Downloading audio" in line or "Downloading video" in line:
            self.status_var.set("Downloading")
        elif "Transcribing chunk" in line or "Transcribing with Whisper" in line:
            self.status_var.set("Transcribing")
        elif "SKIP (already transcribed)" in line:
            self.status_var.set("Skipping existing")

    def _process_finished(self, code: int) -> None:
        self.status_var.set("Finished" if code == 0 else f"Stopped / error ({code})")
        self.process = None
        self._finished_ui()

    def _finished_ui(self) -> None:
        self.progress.stop()
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")


def main() -> None:
    YTWhisperGUI().mainloop()


if __name__ == "__main__":
    main()
