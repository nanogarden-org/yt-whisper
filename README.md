# yt-whisper

Transcribe any YouTube video or playlist to text using OpenAI Whisper — **locally, with no API key and no cost.**

One command. Paste a URL. Get a transcript.

python3 yt_whisper.py --file to-be-transcribed.txt --output ./transcripts --language en --skip-existing
---

## Why this exists

Whisper is the best free transcription model available. yt-dlp is the best YouTube downloader. But using them together means running two separate commands, managing temp files, and wiring things up yourself — every time.

This script collapses the entire workflow into a single command. It handles download, transcription, and cleanup automatically.

Works on **any** YouTube video — even ones with no captions at all.

---

## Features

- **Single video, full playlist, or batch file** — one URL or thousands
- **100% local** — Whisper runs on your machine; no API key, no internet needed after setup, no audio uploaded anywhere
- **5 output formats** — `txt`, `srt`, `vtt`, `tsv`, `json`
- **99 languages** — auto-detected or specify one for faster results
- **6 Whisper model sizes** — from `tiny` (fast) to `large`/`turbo` (near-human accuracy)
- **Auto-cleanup** — downloaded audio is deleted after transcription by default (use `--keep-audio` to retain it)
- **Skip existing** — safely re-run without re-transcribing already-done videos
- **Named by video title** — output files are named after the video, not the ID

---

## Install

```bash
pip install openai-whisper yt-dlp
```

```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt install ffmpeg

# Windows — download from https://ffmpeg.org/download.html and add to PATH
```

---

## Usage

### Transcribe a single video
```bash
python3 yt_whisper.py --url "https://www.youtube.com/watch?v=VIDEO_ID"
```
Transcript saved to `transcripts/<video title>.txt`.

---

### Transcribe a full playlist
```bash
python3 yt_whisper.py --url "https://www.youtube.com/playlist?list=PLAYLIST_ID"
```
All videos in the playlist are transcribed and saved to `transcripts/`.

---

### Transcribe a batch of URLs
Create a text file with one URL per line:
```
# urls.txt
https://www.youtube.com/watch?v=VIDEO_ID_1
https://www.youtube.com/watch?v=VIDEO_ID_2
https://youtu.be/VIDEO_ID_3
```
Then run:
```bash
python3 yt_whisper.py --file urls.txt
```

---

### Use a more accurate model
```bash
python3 yt_whisper.py --url "https://youtu.be/VIDEO_ID" --model medium
```

| Model | Speed | Accuracy | VRAM |
|---|---|---|---|
| `tiny` | Fastest | Lowest | ~1 GB |
| `base` | Fast | Good *(default)* | ~1 GB |
| `small` | Moderate | Better | ~2 GB |
| `medium` | Slow | Great | ~5 GB |
| `large` | Very slow | Best | ~10 GB |
| `turbo` | Fast | Near-large | ~6 GB |

---

### Specify language (faster + more accurate)
```bash
python3 yt_whisper.py --url "https://youtu.be/VIDEO_ID" --language en
```
Use standard language codes: `en`, `es`, `fr`, `de`, `ja`, `zh`, `pt`, `ar`, etc.

---

### Output as SRT subtitles
```bash
python3 yt_whisper.py --url "https://youtu.be/VIDEO_ID" --format srt
```
Also supports `vtt`, `tsv`, `json`.

---

### Keep the downloaded audio
```bash
python3 yt_whisper.py --url "https://youtu.be/VIDEO_ID" --keep-audio --audio-dir ./audio
```

---

### Skip already-transcribed videos
```bash
python3 yt_whisper.py --url "https://www.youtube.com/playlist?list=PLAYLIST_ID" --skip-existing
```
Safe to re-run. Only processes new videos.

---

### Full example
```bash
python3 yt_whisper.py \
  --url "https://www.youtube.com/playlist?list=PLAYLIST_ID" \
  --output ./transcripts \
  --model medium \
  --language en \
  --format srt \
  --skip-existing
```

---

## Example output

```
Model        : base
Language     : auto-detect
Format       : txt
Output       : /Users/you/transcripts
Keep audio   : False
Skip existing: False

Resolving URLs...
Found 3 video(s) to transcribe.

Loading Whisper model 'base'...
Model loaded.

[1/3] How to Learn Python Fast
         Downloading audio...
         Downloaded in 8s
         Transcribing with Whisper (base)...
         Done in 1m 42s | lang: en
         -> /Users/you/transcripts/How to Learn Python Fast.txt

[2/3] Python Projects for Beginners
         Downloading audio...
         Downloaded in 12s
         Transcribing with Whisper (base)...
         Done in 2m 11s | lang: en
         -> /Users/you/transcripts/Python Projects for Beginners.txt

[3/3] Build a REST API in Python
         Downloading audio...
         Downloaded in 9s
         Transcribing with Whisper (base)...
         Done in 1m 58s | lang: en
         -> /Users/you/transcripts/Build a REST API in Python.txt

=======================================================
Finished in 6m 20s
  Transcribed : 3
  Skipped     : 0
  Failed      : 0
  Output dir  : /Users/you/transcripts
```

---

## All flags

| Flag | Short | Default | Description |
|---|---|---|---|
| `--url` | `-u` | — | YouTube video or playlist URL |
| `--file` | `-f` | — | Text file of URLs (one per line) |
| `--output` | `-o` | `./transcripts` | Folder to write transcripts into |
| `--model` | `-m` | `base` | Whisper model size |
| `--language` | `-l` | auto-detect | Language code hint |
| `--format` | | `txt` | Output format: `txt` `srt` `vtt` `tsv` `json` |
| `--keep-audio` | | `False` | Keep downloaded audio after transcription |
| `--audio-dir` | | temp folder | Where to save audio (implies `--keep-audio`) |
| `--skip-existing` | | `False` | Skip already-transcribed videos |
| `--verbose` | | `False` | Print Whisper's segment-level output |

`--url` and `--file` are mutually exclusive — use one or the other.

---

## Use cases

- 🎙️ **Researchers** — transcribe interviews, lectures, or talks without paying for a transcription service
- 📚 **Students** — get searchable transcripts of educational YouTube content
- 📰 **Journalists** — transcribe sources and press conferences locally (nothing leaves your machine)
- 🎧 **Podcast listeners** — create transcripts of YouTube-hosted podcast episodes
- 🌍 **Non-English speakers** — transcribe content in any of 99 supported languages
- 🔒 **Privacy-first users** — your audio is never uploaded anywhere; Whisper runs entirely on your machine
- 📝 **Content creators** — generate SRT/VTT subtitle files for your own YouTube uploads

---

## Tips

- **First run downloads the Whisper model** — `base` is ~140 MB, cached after the first run
- **GPU acceleration** — NVIDIA GPUs are used automatically via CUDA; dramatically faster
- **Apple Silicon (M1/M2/M3/M4)** — works natively; PyTorch uses the Metal backend
- **`--skip-existing` is your friend** — use it when running on a large playlist you'll want to update later
- **Combine with vtt-to-txt** — if the video already has YouTube captions and you just need clean text, [vtt-to-txt](https://github.com/nord342/vtt-to-txt) may be faster than re-transcribing

---

## License

[MIT](LICENSE) — free to use, modify, and distribute.

---

## Contributing

Issues and PRs welcome. If a URL format isn't being handled, or you'd like to see a new feature (speaker diarization, translation mode, auto-chapter generation), open an issue.
