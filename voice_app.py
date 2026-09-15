"""
================================================================================
 voice_app.py
 A Windows desktop voice control application built with Python + tkinter.

 FEATURES
 --------
 * Dark-themed GUI (600x560 px) with terminal-style green text.
 * Microphone DEVICE SELECTOR (fixes "not listening" on noisy or wrong mics).
 * Recognition LANGUAGE selector.
 * Start / Stop listening buttons + a "Test Microphone" button.
 * HAND-CURSOR air mouse: webcam hand tracking that moves the cursor
   (index finger), clicks/drags (pinch / fist) and scrolls (peace sign).
 * Real-time scrolling command log with live status.
 * Stabilised voice-activity detection (VAD) that does not drift away
   from normal speech levels in a noisy room.

 SPEECH-TO-TEXT: Google Web Speech API (free, needs internet).
 TEXT-TO-SPEECH: pyttsx3 (offline, Windows SAPI5 voice).

 GEMINI ANSWERS
 --------------
 * Ask any question by saying:
        "Ask <question>"  /  "Question <question>"
   or simply say something that is not a known command - it is sent to
   Google Gemini and the answer is displayed in the app log (and a short
   version is spoken aloud).
 * Requires a Gemini API key in voc_config.json (or the GEMINI_API_KEY
   environment variable) and internet access.

 VOICE COMMANDS
 --------------
   App launching:  Open Chrome | Open Firefox | Open Notepad | Open Calculator
                   | Open File Explorer | Open Task Manager | Open Settings
   Browser:        New Tab | Close Tab | Next Tab | Previous Tab | Refresh
                   | Close Window
   Window:         Minimize | Maximize
   System:         Lock Screen | Volume Up | Volume Down | Mute
                   | Copy | Paste | Cut | Undo | Select All
                   | Enter | Delete | Backspace | Escape | Scroll Up/Down
                   | Play/Pause | Next Song | Previous Song
   Text/Search:    Type <text> | Search <query>
   AI:             Ask <question> | any unrecognised phrase
   Lifecycle:      Stop Listening | Start Listening | Help | Quit

 THREADING & ERROR HANDLING
 --------------------------
 * A worker thread runs the microphone/recognition/Gemini loop and pushes
   results to the GUI through a thread-safe queue. The window never freezes.
 * Errors for network, microphone and timeouts are caught and logged.

 HOW TO RUN
 ----------
   1. Install dependencies:   pip install -r requirements.txt
   2. Start the app:          python voice_app.py

 VERSION   : 3.2.0
================================================================================
"""

import array
import ctypes
import json
import os
import queue
import re
import subprocess
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import scrolledtext, messagebox
from urllib.parse import quote_plus

# ----------------------------------------------------------------------
# Third-party imports are wrapped so the app can display a friendly error
# dialog instead of crashing with an ImportError if a dependency is missing.
# ----------------------------------------------------------------------
try:
    import pyautogui                 # keyboard / mouse automation
    import pyttsx3                   # offline text-to-speech
    import requests                  # HTTP calls (Gemini API)
    import speech_recognition as sr  # speech-to-text (Google API)
    DEPENDENCIES_OK = True
except ImportError:
    DEPENDENCIES_OK = False
    pyautogui = pyttsx3 = requests = sr = None

# Hand-gesture "air cursor" engine + camera preview support. These are kept
# optional: the voice app still runs if MediaPipe/Pillow are not installed.
try:
    from hand_cursor import (HAND_DEPS_OK, HandCursorEngine, bgr_frame_to_pil)
except ImportError:
    HAND_DEPS_OK = False
    HandCursorEngine = bgr_frame_to_pil = None

try:
    from PIL import ImageTk
    PIL_OK = True
except ImportError:
    ImageTk = None
    PIL_OK = False


# ----------------------------------------------------------------------
# Configuration constants (theme colours, paths, hotkeys)
# ----------------------------------------------------------------------
BG_COLOR          = "#0d1117"
FG_COLOR          = "#33ff33"
LOG_BG_COLOR      = "#010409"
LOG_FG_COLOR      = "#00ff66"
BTN_BG_COLOR      = "#1f6f43"
BTN_STOP_BG_COLOR = "#6e2020"
BTN_TEST_BG_COLOR = "#1f5380"
BTN_FG_COLOR      = "#ffffff"
HLIGHT_COLOR      = "#238636"

FONT_NAME   = "Consolas"
LOG_FONT    = (FONT_NAME, 10)
TITLE_FONT  = (FONT_NAME, 15, "bold")
BTN_FONT    = (FONT_NAME, 10, "bold")
SMALL_FONT  = (FONT_NAME, 9)

# Common install locations of applications we can launch.
APP_PATHS = {
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ],
    "firefox": [
        r"C:\Program Files\Mozilla Firefox\firefox.exe",
        r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
    ],
    "notepad": [
        r"C:\Windows\System32\notepad.exe",
        r"C:\Windows\notepad.exe",
    ],
    "calculator": [
        r"C:\Windows\System32\calc.exe",
        r"C:\Windows\calc.exe",
    ],
    "file explorer": [
        r"C:\Windows\explorer.exe",
        r"C:\Windows\System32\explorer.exe",
    ],
    "paint": [
        r"C:\Windows\System32\mspaint.exe",
    ],
}

# Apps launched through the Windows "start" command (PATH / registry).
STARTABLE_APPS = {
    "task manager": "taskmgr",
    "settings":     "ms-settings:",
    "control panel": "control",
}

# Process image names used when the user says "Close <app>".
CLOSE_PROCESSES = {
    "chrome":        ["chrome.exe"],
    "firefox":       ["firefox.exe"],
    "notepad":       ["notepad.exe"],
    "file explorer": ["explorer.exe"],
    "paint":         ["mspaint.exe"],
    "task manager":  ["Taskmgr.exe"],
    "calculator":    ["CalculatorApp.exe", "Calculator.exe"],
}

# Common install locations used when a browser must be opened explicitly.
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]

# Aliases normalising what the user says to the keys above.
APP_ALIASES = {
    "google chrome": "chrome",
    "the chrome":    "chrome",
    "mozilla firefox": "firefox",
    "the firefox":   "firefox",
    "explorer":      "file explorer",
    "file manager":  "file explorer",
    "files":         "file explorer",
    "my files":      "file explorer",
    "calc":          "calculator",
    "the calculator": "calculator",
}

# Speech-recognition language options shown in the GUI.
LANGUAGES = [
    "en-US", "en-GB", "en-IN", "es-ES", "fr-FR", "de-DE", "pt-BR",
    "it-IT", "nl-NL", "hi-IN", "zh-CN", "ja-JP", "ko-KR", "ru-RU", "ar-SA",
]

# Virtual-key codes used for media/volume keys that pyautogui lacks.
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_MEDIA_NEXT       = 0xB0
VK_MEDIA_PREV       = 0xB1
VK_VOLUME_UP        = 0xAF
VK_VOLUME_DOWN      = 0xAE
VK_VOLUME_MUTE      = 0xAD

# Keystrokes that pyautogui can press directly.
PRESSPRESS_KEYS = {
    "enter": "enter", "return": "enter", "delete": "delete", "del": "delete",
    "backspace": "backspace", "space": "space", "spacebar": "space",
    "escape": "escape", "esc": "escape", "tab": "tab",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "home": "home", "end": "end", "page up": "pageup", "page down": "pagedown",
}

# Words used to filter out polite trailing words from typed text.
TRAILING_FILLER = re.compile(
    r"\s*(please|thanks|thank you|thank you very much)\s*$", re.I
)

CONFIG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "voc_config.json"
)
DEFAULT_CONFIG = {
    "device_index": None,   # None = system default microphone
    "language": "en-US",
    "voice_rate": 180,
    "gemini_api_key": "",
    "gemini_model": "gemini-3.6-flash",
    # Hand-gesture air-cursor settings.
    "hand_mode": "on",          # "on" = webcam starts with the app
    "camera_index": 0,          # which webcam to use
    "hand_sensitivity": 1.0,    # 1.0 = full frame maps to the screen
    "hand_scroll_speed": 1.0,   # wheel sensitivity while making a peace sign
    "show_preview": True,       # embed the camera view in the GUI
}

# Windows user32 functions used for window control and virtual keys.
user32 = ctypes.windll.user32


# ----------------------------------------------------------------------
# Small helper functions
# ----------------------------------------------------------------------
def load_config():
    """Load settings from voc_config.json, falling back to defaults."""
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        for key in DEFAULT_CONFIG:
            if key in saved:
                cfg[key] = saved[key]
    except (OSError, ValueError):
        pass  # no config yet - use defaults
    return cfg


def save_config(cfg):
    """Persist settings to voc_config.json next to the app."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
    except OSError as exc:
        print(f"[warning] could not save config: {exc}")


def compute_rms(frame_bytes, sample_width=2):
    """Compute an approximate RMS amplitude of raw PCM bytes in pure Python
    (16-bit samples are decoded with a C-speed `array` unpack)."""
    n = len(frame_bytes) // sample_width
    if n == 0:
        return 0.0
    if sample_width == 2:
        samples = array.array("h")
        samples.frombytes(frame_bytes[:n * sample_width])
        total = 0.0
        for sample in samples:
            total += sample * sample
        return (total / n) ** 0.5
    step = max(1, n // 20000)          # sample a subset for speed
    total = 0.0
    picked = 0
    for i in range(0, n, step):
        off = i * sample_width
        sample = int.from_bytes(
            frame_bytes[off:off + sample_width], "little", signed=True
        )
        total += sample * sample
        picked += 1
    return (total / picked) ** 0.5


def describe_noise(rms_value):
    """Turn an RMS value into a human readable description."""
    if rms_value < 120:
        return "quiet"
    if rms_value < 800:
        return "moderate"
    if rms_value < 2500:
        return "noisy"
    return "very noisy"


class VoiceControlApp:
    """
    The main application class.

    Threading model:
      * The *worker thread* owns the microphone loop, speech recognition,
        command execution and Gemini requests. It never touches the GUI.
      * The *main (GUI) thread* polls a thread-safe queue with `after()`
        and applies updates. This keeps the window fluid.
    """

    HELP_TEXT = (
        "I can open and close applications like Chrome, Firefox, Notepad, "
        "Calculator, File Explorer, Task Manager and Settings. I control "
        "the browser with new tab, close tab, next tab and refresh. I can "
        "minimize or maximize windows, lock the screen, change volume, copy "
        "and paste, type text after the word type, and search after the "
        "word search. I answer questions after the word ask, and questions "
        "open a Chrome search as well. Use AUTO-BEST in the app to pick the "
        "best microphone. Say stop listening to pause, start listening to "
        "resume, and help for this message. I also control the mouse cursor "
        "with your hand: raise your index finger or open hand to move, pinch to "
        "click, make a peace sign to scroll, and make a fist to drag. Say "
        "start hand cursor or stop hand cursor to switch it on or off."
    )

    GREETINGS = {"hello", "hi", "hey", "good morning", "good afternoon",
                 "good evening", "wake up", "ok google", "hey google"}

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Voice Control - Speech Application")
        self.root.geometry("630x890")
        self.root.configure(bg=BG_COLOR)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Hand-cursor engine state (engine itself is created lazily).
        self._hand_engine = None
        self._hand_preview_photo = None
        self._preview_warning_done = False

        self.config = load_config()
        self._ready = False  # blocks control callbacks until the GUI is built
        self._meter_threshold = 500  # live VAD threshold for the meter/red zone

        # Worker thread control.
        self._listening_event = threading.Event()
        self._suspended = False        # paused via voice (still hears wake word)
        self._thread = None

        # Thread-safe message queue between worker and GUI threads.
        self._queue = queue.Queue()

        # Dedicated TTS thread owns a single persistent pyttsx3 engine, so
        # speech starts instantly instead of re-initialising SAPI5 each time.
        self._tts_queue = queue.Queue()
        self._tts_thread = threading.Thread(
            target=self._tts_loop, name="tts", daemon=True)
        self._tts_thread.start()

        # Allow a couple of Google recognition calls to run in parallel with
        # live capture (barge-in) without flooding the API.
        self._recognize_sem = threading.BoundedSemaphore(2)

        # Reused HTTP session keeps the Gemini connection alive between calls.
        self._http = requests.Session()

        self._build_gui()

        # Auto-start the webcam hand control when the app opens.
        if HAND_DEPS_OK and self.config.get("hand_mode", "on") == "on":
            self.root.after(300, self.start_hand_cursor)

    # ------------------------------------------------------------------
    # GUI construction
    # ------------------------------------------------------------------
    def _build_gui(self):
        """Lay out title, controls, log, buttons and status bar."""
        # --- Title ------------------------------------------------------
        tk.Label(
            self.root, text="[ VOICE CONTROL ]", font=TITLE_FONT,
            bg=BG_COLOR, fg=FG_COLOR,
        ).pack(pady=(10, 4))

        # --- Settings row (microphone + language pickers) ---------------
        settings = tk.Frame(self.root, bg=BG_COLOR)
        settings.pack(fill=tk.X, padx=12, pady=4)

        tk.Label(settings, text="Microphone:", font=SMALL_FONT,
                 bg=BG_COLOR, fg="#8b949e").pack(side=tk.LEFT)
        self.device_var = tk.StringVar(value="")
        device_choices = self._list_input_devices()
        self.device_menu = tk.OptionMenu(
            settings, self.device_var, *device_choices,
            command=self._on_device_change,
        )
        self.device_menu.config(bg=BG_COLOR, fg=FG_COLOR,
                                highlightthickness=1, highlightbackground="#30363d")
        self.device_menu.pack(side=tk.LEFT, padx=(4, 4))

        self.auto_btn = tk.Button(
            settings, text="AUTO-BEST", font=(FONT_NAME, 8, "bold"),
            bg=BTN_TEST_BG_COLOR, fg=BTN_FG_COLOR,
            activebackground="#2c6496", activeforeground=BTN_FG_COLOR,
            relief=tk.FLAT, cursor="hand2", padx=6, pady=2,
            command=self._auto_select,
        )
        self.auto_btn.pack(side=tk.LEFT, padx=(0, 14))

        tk.Label(settings, text="Language:", font=SMALL_FONT,
                 bg=BG_COLOR, fg="#8b949e").pack(side=tk.LEFT)
        self.lang_var = tk.StringVar(value=self.config["language"])
        self.lang_menu = tk.OptionMenu(
            settings, self.lang_var, *LANGUAGES,
            command=self._on_language_change,
        )
        self.lang_menu.config(bg=BG_COLOR, fg=FG_COLOR,
                              highlightthickness=1, highlightbackground="#30363d")
        self.lang_menu.pack(side=tk.LEFT, padx=(4, 0))

        # Make the device picker show the configured choice.
        self._apply_device_selection(device_choices)

        # --- Live voice-level meter --------------------------------------
        meter_row = tk.Frame(self.root, bg=BG_COLOR)
        meter_row.pack(fill=tk.X, padx=12, pady=(4, 0))

        self.level_var = tk.StringVar(value="LEVEL: --  (not listening)")
        tk.Label(meter_row, textvariable=self.level_var, font=(FONT_NAME, 8),
                 bg=BG_COLOR, fg="#8b949e").pack(side=tk.LEFT)

        self.threshold_var = tk.StringVar(value="speech threshold: --")
        tk.Label(meter_row, textvariable=self.threshold_var, font=(FONT_NAME, 8),
                 bg=BG_COLOR, fg="#8b949e").pack(side=tk.RIGHT)

        self.meter_canvas = tk.Canvas(
            self.root, height=16, bg=LOG_BG_COLOR,
            highlightthickness=1, highlightbackground="#30363d",
        )
        self.meter_canvas.pack(fill=tk.X, padx=12, pady=(2, 2))
        self._meter_fill = None
        self._meter_color = "#238636"

        # --- Hand cursor (air mouse / touch) section --------------------
        hand_row = tk.Frame(self.root, bg=BG_COLOR)
        hand_row.pack(fill=tk.X, padx=12, pady=(6, 0))

        tk.Label(hand_row, text="HAND CURSOR:", font=(FONT_NAME, 9, "bold"),
                 bg=BG_COLOR, fg="#58a6ff").pack(side=tk.LEFT)

        self.hand_state_var = tk.StringVar(value="hand: off")
        tk.Label(hand_row, textvariable=self.hand_state_var,
                 font=(FONT_NAME, 8), bg=BG_COLOR, fg=LOG_FG_COLOR,
                 anchor="w").pack(side=tk.LEFT, padx=(6, 0))

        self.hand_stop_btn = tk.Button(
            hand_row, text="STOP", font=(FONT_NAME, 8, "bold"),
            bg=BTN_STOP_BG_COLOR, fg=BTN_FG_COLOR,
            activebackground="#8b2c2c", activeforeground=BTN_FG_COLOR,
            relief=tk.FLAT, cursor="hand2", padx=10, pady=2,
            command=self.stop_hand_cursor, state="disabled",
        )
        self.hand_stop_btn.pack(side=tk.RIGHT)

        self.hand_btn = tk.Button(
            hand_row, text="START", font=(FONT_NAME, 8, "bold"),
            bg=BTN_TEST_BG_COLOR, fg=BTN_FG_COLOR,
            activebackground="#2c6496", activeforeground=BTN_FG_COLOR,
            relief=tk.FLAT, cursor="hand2", padx=10, pady=2,
            command=self._hand_start_btn,
        )
        self.hand_btn.pack(side=tk.RIGHT, padx=(0, 4))

        tk.Label(hand_row, text="Cam:", font=(FONT_NAME, 8),
                 bg=BG_COLOR, fg="#8b949e").pack(side=tk.RIGHT, padx=(10, 2))
        self.camera_var = tk.StringVar(
            value=str(self.config.get("camera_index", 0)))
        cam_menu = tk.OptionMenu(
            hand_row, self.camera_var, "0", "1", "2", "3", "4", "5",
            command=self._on_camera_change,
        )
        cam_menu.config(bg=BG_COLOR, fg=FG_COLOR, highlightthickness=1,
                        highlightbackground="#30363d")
        cam_menu.pack(side=tk.RIGHT)

        self.preview_var = tk.BooleanVar(
            value=bool(self.config.get("show_preview", True)))
        tk.Checkbutton(
            hand_row, text="Preview", variable=self.preview_var,
            font=(FONT_NAME, 8), bg=BG_COLOR, fg="#8b949e",
            selectcolor=BG_COLOR, activebackground=BG_COLOR,
            activeforeground="#8b949e", highlightthickness=0, bd=0,
            command=self._on_preview_toggle,
        ).pack(side=tk.RIGHT, padx=(0, 8))

        self.preview_canvas = tk.Label(
            self.root, bg=LOG_BG_COLOR, text="(preview turned off)",
            font=(FONT_NAME, 8), fg="#8b949e",
            highlightthickness=1, highlightbackground="#30363d",
        )
        self.preview_canvas.pack(fill=tk.X, padx=12, pady=(4, 0),
                                 ipadx=6, ipady=6)

        # --- Live "what the app heard" display ----------------------------
        self.heard_var = tk.StringVar(value="Heard: (nothing yet)")
        tk.Label(self.root, textvariable=self.heard_var,
                 font=(FONT_NAME, 10, "bold"), bg=BG_COLOR, fg="#e6edf3",
                 anchor="w", justify=tk.LEFT, wraplength=560,
                 ).pack(fill=tk.X, padx=12, pady=(2, 0))

        # --- Scrolling command log (read-only) --------------------------
        self.log_text = scrolledtext.ScrolledText(
            self.root,
            bg=LOG_BG_COLOR, fg=LOG_FG_COLOR, font=LOG_FONT,
            insertbackground=LOG_FG_COLOR, relief=tk.FLAT,
            highlightthickness=1, highlightbackground="#30363d",
            state="disabled", wrap=tk.WORD,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)

        # --- Button row --------------------------------------------------
        btn_frame = tk.Frame(self.root, bg=BG_COLOR)
        btn_frame.pack(pady=8)

        self.start_btn = tk.Button(
            btn_frame, text="START LISTENING", font=BTN_FONT,
            bg=BTN_BG_COLOR, fg=BTN_FG_COLOR,
            activebackground=HLIGHT_COLOR, activeforeground=BTN_FG_COLOR,
            relief=tk.FLAT, cursor="hand2", padx=16, pady=5,
            command=self.start_listening,
        )
        self.start_btn.grid(row=0, column=0, padx=6)

        self.stop_btn = tk.Button(
            btn_frame, text="STOP LISTENING", font=BTN_FONT,
            bg=BTN_STOP_BG_COLOR, fg=BTN_FG_COLOR,
            activebackground="#8b2c2c", activeforeground=BTN_FG_COLOR,
            relief=tk.FLAT, cursor="hand2", padx=16, pady=5,
            command=self.stop_listening, state="disabled",
        )
        self.stop_btn.grid(row=0, column=1, padx=6)

        self.test_btn = tk.Button(
            btn_frame, text="TEST MIC", font=BTN_FONT,
            bg=BTN_TEST_BG_COLOR, fg=BTN_FG_COLOR,
            activebackground="#2c6496", activeforeground=BTN_FG_COLOR,
            relief=tk.FLAT, cursor="hand2", padx=16, pady=5,
            command=self.test_mic,
        )
        self.test_btn.grid(row=0, column=2, padx=6)

        # --- Status bar + hint -------------------------------------------
        self.status_var = tk.StringVar(value="Idle. Press START LISTENING.")
        tk.Label(self.root, textvariable=self.status_var, font=SMALL_FONT,
                 bg=BG_COLOR, fg="#8b949e").pack(side=tk.BOTTOM, pady=(0, 2))

        tk.Label(
            self.root,
            text="Say: Open <app> | Type <text> | Search <query> | "
                 "Ask <question> | New/Close/Next Tab | Refresh | "
                 "Minimize | Lock Screen | Stop Listening | Help | "
                 "Start/Stop Hand Cursor",
            font=(FONT_NAME, 8), bg=BG_COLOR, fg="#58a6ff",
            wraplength=570, justify=tk.LEFT,
        ).pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=4)

        self._ready = True

        # Start the queue poller (runs forever on the GUI thread).
        self.root.after(30, self._poll_queue)

    # ------------------------------------------------------------------
    # Device / language helpers (GUI thread)
    # ------------------------------------------------------------------
    def _list_input_devices(self):
        """Return labels for all input devices (with a 'System default' entry)."""
        labels = ["System default (use Windows setting)"]
        try:
            pa = __import__("pyaudio")
            p = pa.PyAudio()
            try:
                for i in range(p.get_device_count()):
                    info = p.get_device_info_by_index(i)
                    if info.get("maxInputChannels", 0) > 0:
                        name = str(info.get("name", "?")).strip()
                        labels.append(f"[{i}] {name}")
            finally:
                p.terminate()
        except Exception:
            pass
        return labels

    def _apply_device_selection(self, choices):
        """Set the picker to the configured device index, or 'System default'."""
        idx = self.config.get("device_index")
        if idx is None:
            # Prefer 'System default' when the config has no explicit device.
            self.device_var.set(choices[0] if choices else "")
            return
        desired = f"[{idx}] "
        for label in choices:
            if label.startswith(desired):
                self.device_var.set(label)
                return
        self.device_var.set(choices[0] if choices else "")

    def _selected_device_index(self):
        """Convert the picker's current label to a device index (or None)."""
        label = self.device_var.get()
        if label.startswith("System default"):
            return None
        match = re.match(r"^\[(\d+)\]", label)
        return int(match.group(1)) if match else None

    def _device_label_text(self):
        """Human-readable label for the currently selected device."""
        return self.device_var.get() or "system default"

    def _on_device_change(self, _event=None):
        if not getattr(self, "_ready", False):
            return  # ignore the automatic set during GUI construction
        self.config["device_index"] = self._selected_device_index()
        save_config(self.config)
        self._append_log(
            f"Microphone set to: {self._device_label_text()}"
        )

    def _on_language_change(self, _event=None):
        if not getattr(self, "_ready", False):
            return
        lang = self.lang_var.get()
        self.config["language"] = lang
        save_config(self.config)
        self._append_log(f"Recognition language set to: {lang}")

    # ------------------------------------------------------------------
    # AUTO-BEST microphone selection
    # ------------------------------------------------------------------
    def _auto_select(self):
        """Measure all input devices and pick the quietest usable one."""
        if (self._thread is not None and self._thread.is_alive()):
            self._append_log("Stop listening first, then use AUTO-BEST.")
            return
        self.test_btn.config(state="disabled")
        self.auto_btn.config(state="disabled")
        self._append_log("Scanning all microphones ...")
        threading.Thread(target=self._auto_select_job, daemon=True).start()

    def _auto_select_job(self):
        """Worker: sample RMS on every input device and pick the best."""
        try:
            pa = __import__("pyaudio")
            p = pa.PyAudio()
            indices = []
            try:
                for i in range(p.get_device_count()):
                    info = p.get_device_info_by_index(i)
                    if info.get("maxInputChannels", 0) > 0:
                        indices.append(i)
            finally:
                p.terminate()

            results = []
            for i in indices:
                try:
                    with sr.Microphone(device_index=i) as source:
                        stream = source.stream
                        samples = []
                        end = time.time() + 0.9
                        while time.time() < end:
                            buf = stream.read(2048)
                            if buf:
                                samples.append(
                                    compute_rms(buf, source.SAMPLE_WIDTH))
                    rms = sum(samples) / len(samples) if samples else None
                    results.append((i, rms))
                except Exception:
                    results.append((i, None))

            for i, rms in results:
                if rms is None:
                    self._post("log", f"[auto] device [{i}] unusable")
                else:
                    self._post("log", f"[auto] device [{i}] quiet-level "
                                      f"{rms:.0f} ({describe_noise(rms)})")

            live = [r for r in results if r[1] is not None]
            if not live:
                self._post("log",
                           "[auto] No usable microphones were found.")
            else:
                # Prefer the quietest device that is still 'alive'.
                sweet = [r for r in live if 50 <= r[1] <= 800]
                pool = sweet or live
                best = min(pool, key=lambda r: r[1])
                self._post("log",
                           f"[auto] Best match: device [{best[0]}] "
                           f"(quiet-level {best[1]:.0f}).")
                self._post("autoselect", best[0])
        except Exception as exc:
            self._post("log", f"[!] Auto-select error: {exc}")
        finally:
            self._post("autodone", None)

    # ------------------------------------------------------------------
    # Live meter + heard display (GUI thread)
    # ------------------------------------------------------------------
    def _on_level(self, rms_value):
        """Update the live voice-level meter from a queue message."""
        self.level_var.set(
            f"LEVEL: {rms_value}  ({describe_noise(rms_value)})")
        try:
            width = self.meter_canvas.winfo_width() or 400
            fraction = min(rms_value / 4000.0, 1.0)
            fill = int(width * fraction)

            # Colour decides whether the sound is close to speech level.
            threshold = self._meter_threshold or 500
            if rms_value >= threshold:
                color = "#f85149"          # at / above speech level (red)
            elif rms_value >= threshold * 0.6:
                color = "#d29922"           # approaching speech level (amber)
            else:
                color = "#238636"           # below speech level (green)

            if self._meter_fill is not None:
                self.meter_canvas.delete(self._meter_fill)
            if self._meter_color != color:
                self.meter_canvas.configure(bg=LOG_BG_COLOR)
                self._meter_color = color
            self._meter_fill = self.meter_canvas.create_rectangle(
                0, 0, fill, 18, fill=color, outline="")
        except tk.TclError:
            pass  # window closing

    def _on_heard(self, text):
        """Show the latest recognized phrase (or an error hint)."""
        shown = str(text).strip()
        if not shown:
            shown = "(listening ...)"
        self.heard_var.set(f"Heard: {shown}")

    # ------------------------------------------------------------------
    # Queue plumbing (GUI thread)
    # ------------------------------------------------------------------
    def _post(self, kind, data):
        """Send a message to the GUI thread via the safe queue.

        kind: "log" | "status" | "running" | "level" | "heard" |
              "threshold" | "autoselect" | "autodone" | "testdone" |
              "hand_state" | "hand_preview" | "started" | "stopped" |
              "error" | "quit"
        """
        self._queue.put((kind, data))

    def _poll_queue(self):
        """Drain the queue and apply updates to the GUI."""
        while True:
            try:
                kind, data = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                if kind == "log":
                    self._append_log(data)
                elif kind == "status":
                    self.status_var.set(data)
                elif kind == "running":
                    self._set_running(data)
                elif kind == "level":
                    self._on_level(int(data))
                elif kind == "heard":
                    self._on_heard(data)
                elif kind == "threshold":
                    self._meter_threshold = data
                    self.threshold_var.set(f"speech threshold: {data:.0f}")
                elif kind == "hand_state":
                    self.hand_state_var.set(data)
                elif kind == "hand_preview":
                    self._show_preview(data)
                elif kind == "started":
                    self._set_hand_ui(True, "hand: ON")
                    self._append_log(data)
                elif kind == "stopped":
                    self._set_hand_ui(False, "hand: off")
                elif kind == "error":
                    self._set_hand_ui(False, "hand: error")
                    self._append_log(data)
                elif kind == "autoselect":
                    self._apply_autoselect(data)
                elif kind == "testdone" or kind == "autodone":
                    self.test_btn.config(state="normal")
                    self.auto_btn.config(state="normal")
                elif kind == "quit":
                    self.on_close()
                    return
            except tk.TclError:
                pass  # window is closing - drop the message
            except Exception as exc:
                # Never let one bad message kill the whole GUI poller.
                self._append_log(f"[!] Message error ({kind}): {exc}")
        self.root.after(30, self._poll_queue)

    def _apply_autoselect(self, index):
        """Select the device chosen by AUTO-BEST on the GUI thread."""
        for label in self._list_input_devices():
            if label.startswith(f"[{index}] "):
                # `.set()` fires the dropdown callback, which saves the
                # config and logs the new microphone automatically.
                self.device_var.set(label)
                self._append_log(f"Auto-selected microphone: {label}")
                return
        self._append_log(f"Could not find device [{index}] in the list.")

    def _append_log(self, line):
        """Insert a timestamped line (or multi-line block) into the log."""
        timestamp = time.strftime("%H:%M:%S")
        body = line if isinstance(line, str) else str(line)
        self.log_text.configure(state="normal")
        for part in body.split("\n"):
            self.log_text.insert(tk.END, f"[{timestamp}] {part}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def _set_running(self, running):
        """Enable/disable buttons to match listening state."""
        if running:
            self.start_btn.config(state="disabled")
            self.stop_btn.config(state="normal")
            self.test_btn.config(state="disabled")
            self.auto_btn.config(state="disabled")
        else:
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")
            self.test_btn.config(state="normal")
            self.auto_btn.config(state="normal")
            self.threshold_var.set("speech threshold: --")

    # ------------------------------------------------------------------
    # Public controls (called by the buttons)
    # ------------------------------------------------------------------
    def start_listening(self):
        """Spin up the worker thread that runs the microphone loop."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._suspended = False
        self._listening_event.set()
        self._set_running(True)
        self._append_log("Starting microphone...")
        self.status_var.set("Calibrating microphone...")
        # IMPORTANT: read the tkinter variable on the GUI thread only -
        # tkinter is not thread safe and crashes if read from the worker.
        dev_label = self.device_var.get() or "system default"
        self._thread = threading.Thread(
            target=self._listening_loop,
            args=(dev_label,),
            daemon=True,
        )
        self._thread.start()

    def stop_listening(self):
        """Fully stop the worker loop (from the GUI Stop button)."""
        self._listening_event.clear()
        self._set_running(False)
        self.status_var.set("Idle.")
        self._append_log("Voice control stopped.")

    def test_mic(self):
        """Run a microphone test in a background thread."""
        if self._thread is not None and self._thread.is_alive():
            return  # busy listening already
        self.test_btn.config(state="disabled")
        threading.Thread(target=self._mic_test_job, daemon=True).start()

    # ------------------------------------------------------------------
    # The microphone / recognition loop (worker thread)
    # ------------------------------------------------------------------
    def _get_microphone(self):
        """Build a Microphone for the configured device index."""
        idx = self.config.get("device_index")
        try:
            return sr.Microphone(device_index=idx)
        except Exception:
            return sr.Microphone()

    def _listening_loop(self, dev_label):
        """
        Stream audio continuously from the microphone.

        For every chunk this loop:
          * posts the live RMS level to the GUI meter,
          * runs a simple voice-activity detector that collects a phrase,
          * sends complete phrases to the Google Web Speech API,
          * shows 'what was heard' and dispatches commands.

        The noise floor is measured once and then slowly re-adapted while
        the user is silent, so the threshold never jumps up into normal
        speech levels. NEVER touches tkinter widgets here.
        """
        language = self.config.get("language", "en-US")
        device_index = self.config.get("device_index")

        CHUNK = 2048                 # frames per read
        PAUSE_LIMIT = 0.5            # seconds of silence that ends a phrase
        PHRASE_LIMIT = 10.0          # max phrase length in seconds
        CALIBRATION = 0.5            # initial noise calibration seconds

        dev_label = dev_label or "system default"

        try:
            mic = sr.Microphone(device_index=device_index)
        except Exception as exc:
            self._post("log", f"[!] Microphone error: {exc}")
            self._post("running", False)
            self._speak("Microphone error. Check your microphone.")
            return

        try:
            with mic as source:
                sample_rate = source.SAMPLE_RATE
                sample_width = source.SAMPLE_WIDTH
                stream = source.stream

                # --- Initial ambient-noise calibration -------------------
                floor_samples = []
                calib_end = time.time() + CALIBRATION
                while time.time() < calib_end:
                    buf = stream.read(CHUNK)
                    if buf:
                        floor_samples.append(
                            compute_rms(buf, sample_width))
                floor_samples.sort()
                noise_floor = (floor_samples[len(floor_samples) // 2]
                               if floor_samples else 150)
                noise_floor = max(noise_floor, 40.0)
                threshold = min(max(noise_floor * 1.45, 150.0), 3500.0)

                self._post("log",
                           f"[i] Microphone: {dev_label} | ambient noise: "
                           f"{noise_floor:.0f} ({describe_noise(noise_floor)})"
                           f" | threshold: {threshold:.0f}")
                self._post("threshold", threshold)
                if noise_floor > 2500:
                    self._post("log",
                               "[i] High background noise. Try AUTO-BEST "
                               "or a quieter spot for best results.")
                self._speak("Voice control ready.")
                self._post("status", "Listening...")

                while self._listening_event.is_set():
                    phrase_frames = []
                    active = False
                    phrase_start = None
                    last_speech = None
                    last_level = 0.0
                    last_heartbeat = 0.0

                    while self._listening_event.is_set():
                        try:
                            buf = stream.read(CHUNK)
                        except (OSError, IOError) as exc:
                            self._post("log",
                                       f"[!] Microphone error: {exc}")
                            self._post("running", False)
                            self._speak(
                                "Microphone error. The app has stopped.")
                            return
                        if len(buf) == 0:
                            time.sleep(0.02)
                            continue

                        now = time.time()
                        rms = compute_rms(buf, sample_width)

                        # Live meter (throttled to ~10 updates/second).
                        if now - last_level >= 0.1:
                            self._post("level", int(rms))
                            last_level = now

                        if rms > threshold:
                            if not active:
                                active = True
                                phrase_start = now
                                phrase_frames = []
                                self._post("status",
                                           "Speech detected - capturing...")
                            phrase_frames.append(buf)
                            last_speech = now
                        else:
                            if active:
                                # Keep a short silence tail on the phrase.
                                phrase_frames.append(buf)
                                if last_speech is not None and \
                                        (now - last_speech) > PAUSE_LIMIT:
                                    break          # phrase finished
                            else:
                                # Slow re-adaptation of the noise floor.
                                noise_floor = (0.04 * rms +
                                               0.96 * noise_floor)
                                noise_floor = max(noise_floor, 40.0)
                                threshold = min(max(noise_floor * 1.45,
                                                    150.0), 3500.0)
                                if now - last_heartbeat > 5.0:
                                    self._post("status",
                                               "No speech detected. "
                                               "Listening...")
                                    last_heartbeat = now

                        if active and (now - phrase_start) > PHRASE_LIMIT:
                            break

                    # ---- A phrase was captured: transcribe it ------------
                    # Recognition + command dispatch run on a helper thread
                    # so the microphone never stops capturing while the
                    # Google (or Gemini) call is in flight.
                    if active and phrase_frames:
                        self._post("status", "Recognizing ...")
                        audio = sr.AudioData(
                            b"".join(phrase_frames),
                            sample_rate, sample_width)
                        threading.Thread(
                            target=self._process_phrase,
                            args=(audio, language),
                            daemon=True,
                        ).start()

                    # Outer loop repeats -> next phrase.
        except OSError as exc:
            self._post("log", f"[!] Microphone error: {exc}")
            self._post("running", False)
            self._speak("Microphone error. The app has stopped.")
        except Exception as exc:
            self._post("log", f"[!] Listening error: {exc}")
            self._post("running", False)

        self._post("level", 0)
        self._post("status", "Stopped.")

    def _process_phrase(self, audio, language):
        """Recognize a captured phrase and run its command on a helper
        thread, never blocking the microphone capture loop."""
        with self._recognize_sem:
            recognizer = sr.Recognizer()
            recognizer.operation_timeout = 15
            try:
                phrase = recognizer.recognize_google(audio,
                                                     language=language)
            except sr.RequestError as exc:
                self._post("log", f"[!] Network error: {exc}")
                self._speak("Network error. Check your internet "
                            "connection.")
                self._post("heard",
                           "(network error - could not reach the speech "
                           "service)")
                return
            except sr.UnknownValueError:
                self._post("heard", "[could not make out the speech]")
                self._post("log", "[!] Could not recognize that speech.")
                return
            except Exception as exc:
                self._post("log", f"[!] Recognition error: {exc}")
                return

            self._post("heard", phrase)
            self._post("log", f"You said: {phrase}")
            try:
                self.handle_command(phrase)
            except Exception as exc:
                self._post("log", f"[!] Command error: {exc}")
            self._post("status", "Listening...")

    # ------------------------------------------------------------------
    # Microphone test (worker thread)
    # ------------------------------------------------------------------
    def _mic_test_job(self):
        """Record a few seconds and report the levels + transcribe if possible."""
        try:
            mic = self._get_microphone()
            recognizer = sr.Recognizer()
            recognizer.dynamic_energy_threshold = False
            self._post("status", "Testing microphone - speak now...")
            with mic as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.7)
                audio = recognizer.record(source, duration=4.0)
            level = compute_rms(audio.frame_data)
            self._post("log",
                        f"[i] Mic test: capture level {level:.0f} "
                        f"({describe_noise(level)}).")
            if level < 60:
                self._speak("Very little sound detected. "
                            "Please check your microphone and speak louder.")
            else:
                try:
                    text = recognizer.recognize_google(
                        audio, language=self.config.get("language", "en-US")
                    )
                    self._post("log", f"[i] Mic test transcription: {text}")
                    self._post("heard", text)
                    self._speak(f"Microphone test passed. You said: {text}")
                except sr.UnknownValueError:
                    self._speak("I heard sound, but could not make out words.")
                except sr.RequestError as exc:
                    self._post("log", f"[!] Network error during test: {exc}")
                    self._speak("Could not reach the speech service.")
        except Exception as exc:
            self._post("log", f"[!] Mic test error: {exc}")
            self._speak("Microphone test failed.")
        finally:
            self._post("testdone", None)

    # ------------------------------------------------------------------
    # Command parsing and execution (worker thread)
    # ------------------------------------------------------------------
    def handle_command(self, phrase):
        """
        Match a spoken phrase against known commands.

        Unknown (but sufficiently long) phrases are forwarded to Gemini
        and the answer is shown in the log and spoken aloud.
        """
        lowered = phrase.lower().strip()
        original = phrase.strip()
        if not lowered:
            return

        # ============ Paused / suspended state ==========================
        if self._suspended:
            if re.search(r"(?:^|\b)(?:please\s+|now\s+|okay\s+)?start "
                         r"listening\b", lowered) or lowered in (
                "resume", "resume listening", "wake up", "continue"):
                self._suspended = False
                self._post("status", "Listening...")
                self._post("log", "Voice control resumed.")
                self._speak("Resumed. Go ahead.")
            elif lowered in ("quit", "exit", "quit the app", "exit the app",
                             "close the app", "close this app"):
                self._post("quit", None)
            elif "help" in lowered or "commands" in lowered:
                self._speak(self.HELP_TEXT)
            else:
                self._post("log", "(paused) The app is waiting for the "
                                   "command: Start listening.")
            return

        # ============ Lifecycle commands ================================
        if lowered in ("stop listening", "stop", "pause", "go to sleep"):
            self._suspended = True
            self._post("status", "Paused.")
            self._post("log", "Voice control paused. Say 'start listening' "
                               "to resume.")
            self._speak("Paused. Say start listening to resume.")
            return

        if lowered in ("quit", "exit", "quit the app", "exit the app",
                       "close the app", "close this app"):
            self._post("log", "Shutting down - goodbye.")
            self._speak("Goodbye.")
            self._post("quit", None)
            return

        if re.search(r"(?:^|\b)(?:please\s+|now\s+|okay\s+)?start "
                     r"listening\b", lowered):
            self._post("log", "Already listening.")
            return

        if "help" in lowered or "commands" in lowered:
            self._speak(self.HELP_TEXT)
            self._post("log", "[i] Spoke the command list.")
            return

        if lowered in self.GREETINGS:
            self._speak("Hello. How can I help you?")
            self._post("log", "[i] Greeting returned.")
            return

        # ---- Hand cursor (air mouse / touch) ------------------------------
        if re.search(r"\b(hand cursor|hand mode|hand control|air cursor|"
                     r"air mouse)\b", lowered):
            if re.search(r"\b(start|turn on|enable|activate|begin)\b", lowered):
                self.start_hand_cursor()
                self._post("log", "[i] Starting hand cursor.")
                self._speak("Starting hand cursor.")
            else:
                self.stop_hand_cursor()
                self._post("log", "[i] Stopping hand cursor.")
                self._speak("Stopping hand cursor.")
            return

        # ============ Discrete command keywords ==========================
        if "close window" in lowered or "close the window" in lowered:
            self._safe_keys(lambda: pyautogui.hotkey("alt", "f4"))
            self._speak("Closing the window.")
            return

        if "new tab" in lowered:
            self._safe_keys(lambda: pyautogui.hotkey("ctrl", "t"))
            self._speak("Opening a new tab.")
            return

        if "next tab" in lowered:
            self._safe_keys(lambda: pyautogui.hotkey("ctrl", "tab"))
            self._speak("Next tab.")
            return

        if "previous tab" in lowered:
            self._safe_keys(lambda: pyautogui.hotkey("shift", "ctrl", "tab"))
            self._speak("Previous tab.")
            return

        if "close tab" in lowered or "close the tab" in lowered:
            self._safe_keys(lambda: pyautogui.hotkey("ctrl", "w"))
            self._speak("Closing the tab.")
            return

        close_match = re.match(
            r"(?:^|\b)(?:close|close down|kill|shut down|exit)\s+"
            r"(?:the\s+|down\s+|down the\s+)?([a-z0-9 .'-]+)", lowered
        )
        if close_match:
            target = re.sub(r"\s*[,.;!?]$", "",
                            close_match.group(1).strip()).strip()
            target = TRAILING_FILLER.sub("", target).strip()
            norm = APP_ALIASES.get(target, target)
            if norm in CLOSE_PROCESSES:
                self._post("log", f"[i] Closing {target}...")
                self._close_app(norm)
                return
            # not a known app - fall through (question may need Gemini)

        if "refresh" in lowered:
            self._safe_keys(lambda: pyautogui.press("f5"))
            self._post("log", "[i] Refreshed page.")
            self._speak("Refreshed.")
            return

        if "minimize" in lowered or "minimise" in lowered:
            self._window_action("minimize")
            self._speak("Minimized the window.")
            return

        if "maximize" in lowered or "maximise" in lowered:
            self._window_action("maximize")
            self._speak("Maximized the window.")
            return

        if "lock" in lowered:  # "lock screen / lock the computer"
            self._safe_keys(lambda: pyautogui.hotkey("win", "l"))
            self._speak("Locking the screen.")
            return

        # ---- Volume -----------------------------------------------------
        if any(w in lowered for w in ("volume up", "increase volume",
                                      "up the volume", "louder")):
            self._press_vk(VK_VOLUME_UP, times=3)
            self._speak("Volume up.")
            return
        if any(w in lowered for w in ("volume down", "decrease volume",
                                      "down the volume", "quieter")):
            self._press_vk(VK_VOLUME_DOWN, times=3)
            self._speak("Volume down.")
            return
        if "mute" in lowered or "silence" in lowered:
            self._press_vk(VK_VOLUME_MUTE, times=1)
            self._speak("Mute toggle.")
            return

        # ---- Media -------------------------------------------------------
        if any(w in lowered for w in ("play or pause", "play pause",
                                      "pause", "play music", "stop music")):
            self._press_vk(VK_MEDIA_PLAY_PAUSE, times=1)
            self._speak("Play pause.")
            return
        if any(w in lowered for w in ("next song", "next track")):
            self._press_vk(VK_MEDIA_NEXT, times=1)
            self._speak("Next track.")
            return
        if any(w in lowered for w in ("previous song", "previous track",
                                      "previous songs")):
            self._press_vk(VK_MEDIA_PREV, times=1)
            self._speak("Previous track.")
            return

        # ---- Clipboard ---------------------------------------------------
        if "select all" in lowered:
            self._safe_keys(lambda: pyautogui.hotkey("ctrl", "a"))
            self._speak("Select all.")
            return
        if "copy" in lowered:
            self._safe_keys(lambda: pyautogui.hotkey("ctrl", "c"))
            self._speak("Copied.")
            return
        if "paste" in lowered:
            self._safe_keys(lambda: pyautogui.hotkey("ctrl", "v"))
            self._speak("Pasted.")
            return
        if "cut" in lowered:
            self._safe_keys(lambda: pyautogui.hotkey("ctrl", "x"))
            self._speak("Cut.")
            return
        if "undo" in lowered:
            self._safe_keys(lambda: pyautogui.hotkey("ctrl", "z"))
            self._speak("Undone.")
            return

        # ---- Scrolling ---------------------------------------------------
        if "scroll down" in lowered:
            self._safe_keys(lambda: pyautogui.press("pagedown"))
            self._speak("Scrolled down.")
            return
        if "scroll up" in lowered:
            self._safe_keys(lambda: pyautogui.press("pageup"))
            self._speak("Scrolled up.")
            return

        # ---- Press a key -------------------------------------------------
        press = re.match(
            r"(?:^|\b)(?:press|hit|click)\s+(.+)", lowered
        )
        if press:
            key_text = press.group(1).replace("key", "").strip()
            mapped = PRESSPRESS_KEYS.get(key_text)
            if not mapped:
                # maybe "the enter key"
                for candidate, key in PRESSPRESS_KEYS.items():
                    if candidate in key_text:
                        mapped = key
                        break
            if mapped:
                self._safe_keys(lambda k=mapped: pyautogui.press(k))
                self._speak(f"Pressed {key_text}.")
                return
            # fall through to unknown handling otherwise

        # ============ "Open <app>" =======================================
        m = re.search(r"(?:^|\b)(?:open|launch|start|run|load)\s+"
                      r"([a-z0-9 .'-]+)", lowered)
        if m:
            target = m.group(1).strip()
            target = re.sub(r"\s*[,.;!?]$", "", target)
            target = TRAILING_FILLER.sub("", target).strip()
            norm = APP_ALIASES.get(target, target)

            if norm in APP_PATHS:
                ok = self._launch_app(norm)
                self._post("log", f"[i] Opening {target}...")
                self._speak(f"Opening {target}." if ok else
                            f"Could not find {target}.")
            elif norm in STARTABLE_APPS:
                ok = self._launch_startable(norm)
                self._post("log", f"[i] Opening {target}...")
                self._speak(f"Opening {target}." if ok else
                            f"Could not open {target}.")
            else:
                self._post("log", f"[i] I do not know how to open "
                                   f"{target}.")
                self._speak(f"I don't know how to open {target}. "
                            "Try Chrome, Firefox, Notepad, Calculator, "
                            "File Explorer, Task Manager or Settings.")
            return

        # ============ "Type <text>" ======================================
        m = re.search(r"(?:^|\b)type(?: out)?[\s:,]+(.*)", original, re.I)
        if m:
            text = TRAILING_FILLER.sub("", m.group(1))
            if text:
                self._post("log", f"[i] Typing: {text}")
                self._type_text(text)
                self._speak("Typed.")
            else:
                self._speak("What should I type?")
            return

        # ============ "Search <query>" ===================================
        m = re.search(r"(?:^|\b)search(?: for)?[\s:,]+(.*)", original, re.I)
        if m:
            query = TRAILING_FILLER.sub("", m.group(1))
            if query:
                url = "https://www.google.com/search?q=" + quote_plus(query)
                self._post("log", f"[i] Searching for: {query}")
                self._open_browser(url)
                self._speak(f"Searching for {query}.")
            else:
                self._speak("What should I search for?")
            return

        # ============ "Ask <question>" or fallback to Gemini =============
        m = re.search(r"(?:^|\b)(?:ask|question)[\s:,]+(.*)", original, re.I)
        question = m.group(1) if m else original
        question = TRAILING_FILLER.sub("", question)

        if len(question.strip()) >= 4:
            self._post("log", f"[i] Asking Gemini: {question}")
            answer, error = self._ask_gemini(question)
            if error:
                self._post("log", f"[!] {error}")
                self._speak("I could not get an answer from Gemini.")
            else:
                self._post("log", f"\n[ANSWER]\n{answer}")
                self._speak(self._shorten(answer))
            # Also open the browser and search the same question.
            url = "https://www.google.com/search?q=" + quote_plus(question)
            self._post("log", f"[i] Opening Chrome to search: {question}")
            self._open_browser(url)
            return

        # ============ Unknown short phrase ===============================
        self._post("log", f"[!] Not understood: {lowered}")
        self._speak("Sorry, I did not understand that. Say help to hear "
                    "the list of commands.")

    # ------------------------------------------------------------------
    # Gemini / TTS / automation helpers (worker thread)
    # ------------------------------------------------------------------
    def _ask_gemini(self, question):
        """
        Send a text prompt to the Gemini API and return (answer, error).

        Returns (answer_text, None) on success or (None, error_message).
        """
        api_key = (self.config.get("gemini_api_key") or
                   os.environ.get("GEMINI_API_KEY", ""))
        if not api_key:
            return None, ("Gemini is not configured. Add your API key to "
                          "voc_config.json or the GEMINI_API_KEY variable.")
        model = self.config.get("gemini_model", "gemini-2.0-flash")
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={api_key}")
        try:
            resp = self._http.post(
                url,
                json={"contents": [{"parts": [{"text": question}]}]},
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
            data = resp.json()
            if resp.status_code != 200:
                message = data.get("error", {}).get("message", resp.reason)
                return None, f"Gemini error ({resp.status_code}): {message}"
            parts = data.get("candidates", [{}])[0].get("content", {}).get(
                "parts", [])
            texts = [p.get("text", "") for p in parts if p.get("text")]
            if not texts:
                return None, "Gemini returned an empty answer."
            return texts[0].strip(), None
        except requests.exceptions.RequestException as exc:
            return None, f"Network error contacting Gemini: {exc}"
        except (ValueError, KeyError, IndexError) as exc:
            return None, f"Unexpected Gemini response: {exc}"

    def _shorten(self, text, limit=220):
        """Return the first sentence(s) of text up to `limit` characters."""
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) <= limit:
            return text
        cut = text[:limit]
        last_period = cut.rfind(". ")
        if last_period > 40:
            return cut[:last_period + 1]
        return cut.rsplit(" ", 1)[0] + "."

    def _launch_app(self, name):
        """Launch an app by known file path."""
        for path in APP_PATHS.get(name, []):
            if os.path.exists(path):
                try:
                    subprocess.Popen([path])
                    return True
                except Exception:
                    continue
        return False

    def _close_app(self, name):
        """Close an app by terminating its process(es)."""
        images = CLOSE_PROCESSES.get(name, [name + ".exe"])
        for image in images:
            try:
                proc = subprocess.run(
                    ["taskkill", "/IM", image, "/F"],
                    capture_output=True, text=True, timeout=10,
                )
                if proc.returncode == 0:
                    self._post("log", f"[i] Closed {name}.")
                    self._speak(f"Closed {name}.")
                    return
            except Exception as exc:
                self._post("log", f"[!] Could not close {name}: {exc}")
                self._speak(f"Could not close {name}.")
                return
        self._post("log", f"[i] {name} is not currently running.")
        self._speak(f"{name} is not running.")

    def _open_browser(self, url):
        """Open a URL in Google Chrome when available, otherwise the OS
        default browser."""
        for path in CHROME_CANDIDATES:
            if os.path.exists(path):
                try:
                    subprocess.Popen([path, url])
                    return True
                except Exception:
                    break
        try:
            webbrowser.open(url)
            return True
        except Exception as exc:
            self._post("log", f"[!] Could not open browser: {exc}")
            return False

    def _launch_startable(self, name):
        """Launch an app via the Windows 'start' command."""
        command = STARTABLE_APPS.get(name)
        if not command:
            return False
        try:
            subprocess.Popen(["start", command], shell=True)
            return True
        except Exception:
            return False

    def _type_text(self, text):
        """Type a string into the currently focused window."""
        try:
            pyautogui.write(text, interval=0.02)
        except Exception as exc:
            self._post("log", f"[!] Could not type: {exc}")

    def _safe_keys(self, action):
        """Run a pyautogui key action and log any failure."""
        try:
            action()
        except Exception as exc:
            self._post("log", f"[!] Keyboard action error: {exc}")

    def _window_action(self, action):
        """Minimize / maximize the currently focused window via Win32."""
        codes = {"minimize": 6, "maximize": 3}
        try:
            hwnd = user32.GetForegroundWindow()
            user32.ShowWindow(hwnd, codes.get(action, 6))
        except Exception as exc:
            self._post("log", f"[!] Window action error: {exc}")

    def _press_vk(self, code, times=1):
        """Press a virtual-key code directly (volume / media keys)."""
        try:
            for _ in range(times):
                user32.keybd_event(code, 0, 0, 0)
                user32.keybd_event(code, 0, 2, 0)
                time.sleep(0.02)
        except Exception as exc:
            self._post("log", f"[!] Key press error: {exc}")

    def _tts_loop(self):
        """Dedicated thread that owns a single persistent pyttsx3 engine.

        The engine is created lazily on first use and kept alive for the
        whole session, which removes ~half a second of SAPI5 re-init delay
        from every spoken reply.
        """
        engine = None
        rate = int(self.config.get("voice_rate", 180))
        while True:
            text = self._tts_queue.get()
            if text is None:
                break
            try:
                if engine is None:
                    engine = pyttsx3.init()
                    engine.setProperty("rate", rate)
                    engine.setProperty("volume", 1.0)
                engine.say(text)
                engine.runAndWait()
            except Exception as exc:
                self._post("log", f"[!] Text-to-speech error: {exc}")
                try:
                    if engine is not None:
                        engine.stop()
                except Exception:
                    pass
                engine = None

    def _speak(self, text, max_len=None):
        """Queue text for the persistent TTS thread (never blocks)."""
        if max_len is not None:
            text = self._shorten(text, max_len)
        if not text:
            return
        try:
            self._tts_queue.put(text)
        except Exception as exc:
            self._post("log", f"[!] Text-to-speech error: {exc}")

    # ------------------------------------------------------------------
    # Hand cursor (air mouse / touch) - GUI tie-in
    # ------------------------------------------------------------------
    def _hand_start_btn(self):
        """GUI button: store the chosen camera, then start the engine."""
        try:
            self.config["camera_index"] = int(self.camera_var.get())
        except (TypeError, ValueError):
            pass
        self.start_hand_cursor()

    def _on_camera_change(self, _choice):
        try:
            self.config["camera_index"] = int(self.camera_var.get())
        except (TypeError, ValueError):
            pass

    def _on_preview_toggle(self):
        if self._hand_engine is not None:
            self._hand_engine.set_emit_preview(bool(self.preview_var.get()))
        if not self.preview_var.get():
            self.preview_canvas.configure(text="(preview turned off)")

    def start_hand_cursor(self):
        """Create the engine (if needed) and start it. GUI/thread-safe."""
        if (self._hand_engine is not None and
                self._hand_engine.is_running()):
            return
        if not HAND_DEPS_OK or HandCursorEngine is None:
            self._post("log", "[!] Hand-tracking packages missing - run: "
                              "pip install mediapipe pillow")
            self._post("status", "Hand cursor: dependencies missing")
            return
        try:
            engine = HandCursorEngine(
                camera_index=int(self.config.get("camera_index", 0)),
                sensitivity=float(self.config.get("hand_sensitivity", 1.0)),
                scroll_speed=float(self.config.get("hand_scroll_speed", 1.0)),
                emit_preview=bool(self.preview_var.get() and PIL_OK),
                on_event=self._hand_event,
                on_preview=self._hand_preview,
            )
        except Exception as exc:
            self._post("log", f"[!] Hand cursor failed to start: {exc}")
            return
        self._hand_engine = engine
        engine.start()

    def stop_hand_cursor(self):
        """Stop the engine; the 'stopped' event resets the GUI. Thread-safe."""
        engine = getattr(self, "_hand_engine", None)
        if engine is not None:
            try:
                engine.stop()
            except Exception as exc:
                self._post("log", f"[!] {exc}")
        self._hand_engine = None

    def _hand_event(self, kind, data):
        """Callback from the engine thread -> forward into the GUI queue."""
        self._post(kind, data)

    def _hand_preview(self, frame):
        """Callback from the engine thread -> queue the camera frame."""
        self._post("hand_preview", frame)

    def _set_hand_ui(self, running, text):
        """Update hand-cursor button states and status label (GUI thread)."""
        try:
            self.hand_state_var.set(text)
            self.hand_btn.config(
                state="disabled" if running else "normal")
            self.hand_stop_btn.config(
                state="normal" if running else "disabled")
        except tk.TclError:
            pass  # window closing

    def _show_preview(self, frame):
        """Display a camera frame in the preview area (GUI thread)."""
        if not PIL_OK or not self.preview_var.get():
            return
        try:
            img = bgr_frame_to_pil(frame)
            if img is None:
                return
            img.thumbnail((600, 200))
            self._hand_preview_photo = ImageTk.PhotoImage(img)
            self.preview_canvas.configure(image=self._hand_preview_photo,
                                          text="")
        except Exception as exc:
            if not self._preview_warning_done:
                self._preview_warning_done = True
                self._append_log(f"[!] Camera preview unavailable: {exc}")

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    def on_close(self):
        """Clean up threads and close the window."""
        self._listening_event.clear()
        save_config(self.config)
        if getattr(self, "_hand_engine", None) is not None:
            try:
                self._hand_engine.stop()
            except Exception:
                pass
        try:
            pyautogui.FAILSAFE = False
        except Exception:
            pass
        try:
            self._tts_queue.put(None)  # stop the persistent TTS thread
        except Exception:
            pass
        self.root.destroy()


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------
def main():
    """Application entry point with a friendly missing-dependency dialog."""
    if not DEPENDENCIES_OK:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Missing Dependencies",
            "One or more required packages are not installed.\n\n"
            "Install them with:\n"
            "    pip install -r requirements.txt\n\n"
            "Then run this script again.",
        )
        return

    app = VoiceControlApp()
    app.root.mainloop()


if __name__ == "__main__":
    main()