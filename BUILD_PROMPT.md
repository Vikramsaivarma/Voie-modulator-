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

## Extra files to create in the same folder
- `requirements.txt` — exact pinned list above with short comments.
- `install.bat` — locates a Python launcher (`python`, else `py`), checks version >= 3.8, upgrades pip/setuptools/wheel, installs `-r requirements.txt`, verifies all four core imports, prints success, and shows how to launch.
- `QUICK_START.txt` — sections: requirements, install instructions, how to start (`python voice_app.py` or `py voice_app.py`), microphone selection guide, Gemini AI usage, full voice-command list, troubleshooting table, and file list.
- `BUILD_PROMPT.md` — this exact prompt.
- `.gitignore` — ignores `voc_config.json`, `__pycache__/`, `*.pyc`.

## Conventions
- No comments in code unless helpful; Windows-native, tkinter-native (no `customtkinter`).
- The Gemini call must target `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}`.
- Do not hardcode real API keys in code; read them from config/env only.
- Repo name: `Voie-modulator-`, default branch `main`.

BUILD PROMPT END