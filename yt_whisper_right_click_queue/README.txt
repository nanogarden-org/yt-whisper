YT Whisper Right-Click Queue v0.1

What it adds:
  Right-click any web link -> Add to YT Whisper queue

The link is appended to:
  to-be-transcribed.txt

Duplicate links are ignored.

INSTALL

1. Put queue_server.py and "Start YT Whisper Queue.bat" in the same folder as:
     to-be-transcribed.txt

   For your current setup, that can simply be:
     C:\yt-whisper-main\

2. Double-click:
     Start YT Whisper Queue.bat

   Leave that small helper window open.

3. In Chrome or Edge, open the Extensions page.

4. Turn on Developer mode.

5. Click "Load unpacked".

6. Select the "extension" folder from this package.

7. Right-click a link in the browser and choose:
     Add to YT Whisper queue

FLOW

Browser right-click
  -> local helper at 127.0.0.1:8765
  -> appends URL to to-be-transcribed.txt
  -> your existing yt_whisper_chunked.py batch workflow

The helper uses only Python's standard library and communicates only over localhost.
