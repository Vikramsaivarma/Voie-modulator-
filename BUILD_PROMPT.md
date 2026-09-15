# Build Prompt - Voice Control Desktop App

Copy the text below into any capable AI coding assistant to rebuild this exact app from scratch.

---

BUILD PROMPT START

Build a Windows desktop voice-control app as a single Python 3.11+ file, `voice_app.py`, with a dark-themed GUI using tkinter. It must satisfy the requirements below exactly.

## Stack & dependencies
- Hardware/OS: Windows 10/11, x64, real microphone.
- Python: 3.11+.
- Runtime GUI: tkinter (no extra install for the GUI itself).
- Dependencies (pinned, installable via `pip install -r requirements.txt`):
  - SpeechRecognition==3.10.4  (speech-to-text via Google Web Speech API)
  - pyttsx3==2.90              (offline TTS via Windows SAPI5)
  - comtypes==1.2.1            (COM support for pyttsx3)
  - PyAutoGUI==0.9.54          (keyboard/mouse automation)
  - PyAudio==0.2.14            (microphone capture, has cp311 wheels)
  - requests>=2.31.0           (Gemini HTTP API calls)
  - mediapipe==0.10.14         (hand tracking; bundles OpenCV; 0.10.21+ wheels fail DLL load on Windows)
  - Pillow>=10.0.0             (embedded camera preview in the GUI)

## App features (all required)
1. Dark-themed Tk GUI with a green terminal-style log area.
2. Voice control loop: press a START/STOP LISTENING toggle; when active, capture audio, transcribe via `speech_recognition`, log what was heard, speak short confirmations/answers aloud via pyttsx3 on a persistent background thread, and execute commands.
3. Commands:
   - Apps: open Chrome, Firefox, Notepad, Calculator, File Explorer, Task Manager, Settings (paths configurable via an APP_PATHS dict near the top).
   - Browser: new tab, close tab, next tab, previous tab, refresh, close window.
   - System: minimize, maximize, lock screen, volume up/down, mute, scroll up/down.
   - Clipboard/editing: copy, paste, cut, undo, select all, press Enter/Delete/Backspace/Escape.
   - Media: play/pause, next song, previous song.
   - Typing/search: "type the quick brown fox" types into the focused window; "search <query>" opens a Google search.
   - AI: any non-command phrase is sent to Google Gemini; answer is logged, spoken (shortened if long).
   - Lifecycle: start listening, stop listening, help (reads command list aloud), quit/exit.
4. Live voice-level meter (green bar) that turns amber near speech level and red while talking.
5. Microphone handling: enumerate all input devices, choose via dropdown, support TEST MIC, and an AUTO-BEST mode that measures the quietest (background-noise) level of every mic and auto-selects the quietest usable one.
6. Config persistence: `voc_config.json` next to the app stores device_index, language, voice_rate, gemini_api_key, gemini_model; the app loads it on start and saves it on change. API key may fall back to the GEMINI_API_KEY environment variable. Default model: gemini-3.6-flash.
7. Startup check: if dependencies are missing, show a friendly "Missing Dependencies" messagebox instead of crashing.
8. Entry point: `if __name__ == "__main__": main()`.
9. Threads: keep TTS and speech recognition off the main GUI thread; safe shutdown on close (stop recognizer thread and TTS queue).
10. Hand-gesture "air cursor" in a SECOND module `hand_cursor.py`: a `HandCursorEngine` class run in its own daemon thread using MediaPipe Hands + OpenCV through the webcam (mirrored, EMA-smoothed, small inference buffer for speed). Gestures: index finger up or open hand = move cursor, pinch (thumb+index) = left click, peace sign (index+middle) = scroll via vertical hand motion, THREE fingers (index+middle+ring = right click / right-drag), fist = hold left button (drag). Track all 5 fingers and show an extended/folded (T I M R P) finger panel on the preview overlay. It must forward non-GUI events (`started`, `stopped`, `error`, `log`, `hand_state`) and optional throttled preview frames to the host app through callbacks (never touch tkinter directly), and release the mouse buttons and camera on stop.
11. The main app must import the engine optionally (friendly log if MediaPipe/Pillow missing), add a HAND CURSOR card (START/STOP buttons, Cam: dropdown 0-5, Preview checkbox, live gesture+position label + colour LED, embedded camera preview via Pillow ImageTk throttled ~15 fps), auto-start the webcam when the app opens if `hand_mode` is "on", support voice commands "start hand cursor" / "stop hand cursor", and stop the engine in `on_close`. Config gains: hand_mode, camera_index, hand_sensitivity, hand_scroll_speed, show_preview.
12. Modern redesigned GUI (v4.0): dark GitHub-style theme, header with a live status badge (IDLE/LISTENING/SPEAKING/PAUSED), card-based sections for MICROPHONE & VOICE and HAND CURSOR, a LIVE voice-activity WAVEFORM canvas (green/amber/red bars with a threshold guide line), an ACTIVITY LOG panel, and a bottom status bar + hint line.

## Extra features (all required, v4.0)
13. Gemini SCREEN VISION: voice commands "analyze screen" / "what is on my screen" / "describe/read the screen" capture the full screen with PIL `ImageGrab`, send it with the user's question to the Gemini `generateContent` API using `inlineData` (base64 PNG, mimeType image/png), log the answer and speak a short version.
14. TIMERS/REMINDERS: "set a timer for 5 minutes", "remind me in 10 minutes to stretch" — a background runner thread fires the timer and logs + speaks "Timer finished." at expiry.
15. CLIPBOARD HISTORY: a GUI-thread poller remembers the last 8 text entries copied to the clipboard; "what did I copy" / "show my clipboard" prints them in the log.
16. Keep the Gemini model, threshold, device, language, and hand settings consistent; never store API keys in tracked files.

## Extra files to create in the same folder
- `requirements.txt` — exact pinned list above with short comments.
- `install.bat` — locates a Python launcher (`python`, else `py`), checks version >= 3.8, upgrades pip/setuptools/wheel, installs `-r requirements.txt`, verifies all four core imports, prints success, and shows how to launch.
- `QUICK_START.txt` — sections: requirements, install instructions, how to start (`python voice_app.py` or `py voice_app.py`), microphone selection guide, Gemini AI usage, full voice-command list, air-cursor (hand control) gesture guide, troubleshooting table, and file list.
- `BUILD_PROMPT.md` — this exact prompt.
- `.gitignore` — ignores `voc_config.json`, `__pycache__/`, `*.pyc`.

## Conventions
- No comments in code unless helpful; Windows-native, tkinter-native (no `customtkinter`).
- The Gemini call must target `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}`.
- Do not hardcode real API keys in code; read them from config/env only.
- Repo name: `Voie-modulator-`, default branch `main`.

BUILD PROMPT END