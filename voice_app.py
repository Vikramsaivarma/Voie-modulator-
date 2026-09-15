"""
================================================================================
 voice_app.py
 A Windows desktop voice control application built with Python + tkinter.

 FEATURES
 --------
 * Modern dark-themed GUI with a LIVE voice-activity WAVEFORM that shows
   every time you speak (plus colour-coded LISTENING / SPEAKING states).
 * Microphone DEVICE SELECTOR (fixes "not listening" on noisy or wrong mics).
 * Recognition LANGUAGE selector.
 * Start / Stop listening buttons + a "Test Microphone" button.
 * HAND-CURSOR air mouse: webcam hand tracking that moves the cursor
   (index finger), clicks/drags (pinch / fist / three fingers = right click)
   and scrolls (peace sign). A 5-finger panel shows each finger live.
 * SCREEN VISION: "analyze screen / what is on my screen" sends a
   screenshot to Gemini and reads the answer aloud (vision model).
 * CURSOR VISION: "what is under my cursor" grabs the region around the
   mouse pointer and Gemini describes it.
 * GEMINI MEMORY: the app keeps the last few Q&A turns in a session
   context, so follow-up questions ("and this one?") have context.
   "clear my memory / forget" resets that context.
 * TIMER / REMINDER commands: "set a timer for 5 minutes",
   "set a reminder at 3 o'clock to drink water".
 * CLIPBOARD HISTORY: the app tracks the last few things you copied and
   can replay them ("what did I copy", "show my clipboard").
 * TTS PROFILES: pick the Windows SAPI5 voice and the reading speed from
   the GUI (Speed slider + Voice dropdown).
 * CLICK BEEPS: hand clicks/drags play a short confirmation tone,
   toggleable with the Beep checkbox.
 * AUTO-START: "Run on login" checkbox registers the app to start with
   Windows (HKCU Run key).
 * ACTIVITY LOG: every log line is also persisted to voc_log.txt next to
   the app (capped at ~2000 lines).
 * CHAINED COMMANDS (v5.1): say "open chrome then search the weather" and
   the app runs each step in order (split on " then " / " and then ").
 * WAKE WORD (v5.1): after "stop listening" / pause, say "hey vo" or
   "wake up" to resume listening hands-free.
 * CLIPBOARD CARD (v5.1): the GUI now lists your recent clipboard entries
   - click any row to paste it into the focused window.
 * TIMER TOASTS (v5.1): when a timer/reminder finishes, the app pops a
   small always-on-top notification (click to dismiss) in addition to
   speaking the reminder.
 * CALIBRATION FEEDBACK (v5.1): while CALIBRATE REACH runs, a live
   "size X.XX (REST/REACH)" readout shows the current hand size so you
   can see which pose the app is measuring.
 * GESTURE TRAINER (v5.2): press TRAIN GESTURES (or say "train gestures")
   for a live pass/fail panel of every pose. While it is open the mouse
   is paused and each gesture lights up green once your hand holds it.
  * TOUCHPAD MODE (v5.4): press TOUCHPAD (or say "touchpad mode") to turn
   the hand into a RELATIVE mouse / laptop trackpad - the pointer follows
   hand movement, not absolute position, so you get fine mouse-like
   control. Same gestures (pinch=click, peace=scroll, 3 fingers=right
   click, fist=drag). Say "stop touchpad" to go back to the reach-gated
   cursor.
  * DRAW MACROS (v5.3): press DRAW MACROS (or say "draw macros") and trace
   a shape in the air with your index finger - the stroke is recognised
   when the finger stops for ~1 s or leaves the frame, then the action
   fires. Shapes (remappable via the macro_actions config): circle=lock
   screen, v=new tab, check=copy, l=minimize, s=open settings, z=close
   tab, w=play or pause, line=mute, slash=maximize.
 * EXTRA UTILITIES - time/date, quick notes, calculator, sites, power:
 *    "what time is it" / "what date is it today"
 *    "take a note: <text>" -> appends to voc_notes.txt
 *    "calculate 15 percent of 240" / "what is 12 times 8" (safe math)
 *    "open youtube / gmail / whatsapp / drive / maps / github..."
 *    "shut down the computer" / "restart the computer"
 *    "put the computer to sleep" / "lock screen"
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

VERSION   : 5.4.0
================================================================================
"""

import array
import ast
import base64
import ctypes
import io
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
import winsound
import winreg
from collections import deque
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
    from hand_cursor import (HAND_DEPS_OK, HandCursorEngine, bgr_frame_to_pil,
                             available_cameras, FINGER_NAMES,
                             MACRO_DEFAULT_ACTIONS)
except ImportError:
    HAND_DEPS_OK = False
    HandCursorEngine = bgr_frame_to_pil = available_cameras = None
    FINGER_NAMES = ("T", "I", "M", "R", "P")
    MACRO_DEFAULT_ACTIONS = {}

try:
    from PIL import ImageTk
    from PIL import ImageGrab
    from PIL import Image as PILImage
    from PIL import ImageDraw
    PIL_OK = True
except ImportError:
    ImageTk = None
    ImageGrab = None
    PILImage = None
    ImageDraw = None
    PIL_OK = False

# System tray icon + global hotkey are optional conveniences. If the
# pystray/keyboard packages are missing the app simply quits on the X
# button as before.
try:
    import pystray
    TRAY_OK = True
except ImportError:
    pystray = None
    TRAY_OK = False

try:
    import keyboard
    KEYBOARD_OK = True
except ImportError:
    keyboard = None
    KEYBOARD_OK = False


# ----------------------------------------------------------------------
# Configuration constants (theme colours, paths, hotkeys)
# ----------------------------------------------------------------------
BG_COLOR          = "#0d1117"
FG_COLOR          = "#33ff33"
ACCENT            = "#58a6ff"
MUTED             = "#8b949e"
SUBTLE_BG         = "#161b22"
LOG_BG_COLOR      = "#010409"
LOG_FG_COLOR      = "#00ff66"
BTN_BG_COLOR      = "#1f6f43"
BTN_STOP_BG_COLOR = "#6e2020"
BTN_TEST_BG_COLOR = "#1f5380"
BTN_FG_COLOR      = "#ffffff"
HLIGHT_COLOR      = "#238636"
RED               = "#f85149"
AMBER             = "#d29922"
GREEN             = "#238636"
GREY              = "#3a3f45"

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

# Quick voice shortcuts for popular web sites ("open youtube").
SITE_SHORTCUTS = {
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "google": "https://www.google.com",
    "google drive": "https://drive.google.com",
    "drive": "https://drive.google.com",
    "whatsapp": "https://web.whatsapp.com",
    "github": "https://github.com",
    "stack overflow": "https://stackoverflow.com",
    "maps": "https://www.google.com/maps",
    "wordle": "https://www.nytimes.com/games/wordle",
    "netflix": "https://www.netflix.com",
    "spotify": "https://open.spotify.com",
}

# Quick voice notes are appended to this file next to the app.
NOTES_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "voc_notes.txt"
)

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
    "hand_arm_size": None,      # None = default reach gate (auto-calibratable)
    "hand_disarm_size": None,   # None = default reach gate (auto-calibratable)
    "click_beep": True,         # audible click/drag confirmation beep
    "autostart": False,         # auto-start on Windows login
    "voice_id": "",             # "" = default SAPI5 voice; set by dropdown
    "minimize_to_tray": True,   # X button hides to tray instead of quitting
    "tray_hotkey": "ctrl+alt+v",  # global hotkey to restore the window
    "macro_mode": False,        # True = hand engine starts in DRAW-MACRO mode
    "macro_actions": {},        # optional per-shape action overrides
    "touchpad_mode": False,     # True = hand engine starts in TOUCHPAD mode
    "touchpad_gain": 2.5,       # relative-motion speed multiplier
}

# Windows user32 functions used for window control and virtual keys.
user32 = ctypes.windll.user32

LOG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "voc_log.txt"
)

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
        "open a Chrome search as well. I can look at your screen and "
        "describe it: say screen analysis. I can describe what is near your "
        "mouse: say what is under my cursor. I can set timers: say set a "
        "timer for 5 minutes, or set a reminder at 3 o'clock. I remember "
        "your clipboard: say what did I copy, or click an entry in the "
        "clipboard card to paste it. Chain commands with the word then - "
        "for example: open chrome, then search the weather. I also tell "
        "the time and date, take notes after the word note, do math like "
        "calculate 15 percent of 240, open sites like youtube, gmail and "
        "maps, shut down the computer, restart it, put it to sleep, and "
        "lock the screen. Say clear my memory to reset my conversation "
        "context. Use AUTO-BEST in the app to pick the best microphone. "
        "Say stop listening to pause, start listening, hey vo, or wake up "
        "to resume, and help for this message. I also control the mouse "
        "cursor with your hand: raise your index finger or open hand to "
        "move, pinch to click, three fingers for a right click, make a "
        "peace sign to scroll, and make a fist to drag. Say start hand "
        "cursor or stop hand cursor to switch it on or off, or train "
        "gestures to open the live pass-and-fail practice window. Say "
        "touchpad mode to turn your hand into a relative mouse like a "
        "laptop trackpad, and stop touchpad to go back to the reach-gated "
        "cursor. You can "
        "also draw shapes in the air as macros: say draw macros, then "
        "trace a shape with your finger; a circle locks the screen, a "
        "letter v opens a new tab, a check mark copies, the letter l "
        "minimizes, an s opens settings, a z closes the tab, a w plays or "
        "pauses, a straight line mutes, and a slash maximizes. "
        "Confirmations are audible when click beep sounds are enabled."
    )

    GREETINGS = {"hello", "hi", "hey", "good morning", "good afternoon",
                 "good evening", "wake up", "ok google", "hey google"}

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Voice Control - Speech Application")
        self.root.geometry("630x890")
        self.root.configure(bg=BG_COLOR)
        self._closing = False  # set when the real shutdown starts
        self.root.protocol("WM_DELETE_WINDOW", self._on_close_request)

        # System tray icon + hotkey state (created once the GUI is up).
        self._tray_icon = None
        self._tray_hotkey = None
        self._tray_hint_shown = False

        # Hand-cursor engine state (engine itself is created lazily).
        self._hand_engine = None
        self._hand_preview_photo = None
        self._preview_warning_done = False

        # Hand-cursor calibration state (two-phase reach-sample flow).
        self._calibrating = False
        self._calib_phase = 0          # 0=idle, 1=rest, 2=reach
        self._calib_samples = []       # collected normalised hand sizes
        self._calib_rest = []          # phase-1 (resting) samples kept aside
        self._calib_target = 0         # samples still to collect

        self.config = load_config()
        self._ready = False  # blocks control callbacks until the GUI is built
        self._meter_threshold = 500  # live VAD threshold for the meter/red zone

        # Live voice-activity history for the visualiser waveform.
        self._rms_history = deque(maxlen=60)
        self._waveform_handle = None
        self._voice_state = "idle"   # idle | listening | speaking | paused

        # Timer / reminder state (worker thread guarded list).
        self._timers = []
        self._timers_lock = threading.Lock()
        self._timers_event = threading.Event()
        self._timers_thread = threading.Thread(
            target=self._timer_runner, name="timers", daemon=True)
        self._timers_thread.start()

        # Clipboard history state (populated by a GUI-thread poller).
        self._clip_history = []
        self._clip_lock = threading.Lock()
        self._last_clip_value = None
        self._clip_watch_started = False
        self._last_rendered_clips = None

        # Gesture trainer window state (none until TRAIN GESTURES is pressed).
        self._train_window = None
        self._train_rows = {}

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
        self._voice_id_by_name = {}

        # Allow a couple of Google recognition calls to run in parallel with
        # live capture (barge-in) without flooding the API.
        self._recognize_sem = threading.BoundedSemaphore(2)

        # Reused HTTP session keeps the Gemini connection alive between calls.
        self._http = requests.Session()
        # Gemini session memory: keeps the last few turns so follow-up
        # questions ("and this one?" / "expand that") have context.
        self._gemini_history = []   # list of (role, text) pairs
        self._gemini_lock = threading.Lock()

        self._build_gui()

        # Auto-start the webcam hand control when the app opens.
        if HAND_DEPS_OK and self.config.get("hand_mode", "on") == "on":
            self.root.after(300, self.start_hand_cursor)

    # ------------------------------------------------------------------
    # GUI construction
    # ------------------------------------------------------------------
    def _build_gui(self):
        """Lay out the redesigned card-based UI with a live voice waveform."""
        self.root.geometry("700x940")
        self.root.title("Voice Control - Speech Application")
        self.root.minsize(620, 640)

        # --- Header -----------------------------------------------------
        header = tk.Frame(self.root, bg=SUBTLE_BG)
        header.pack(fill=tk.X)
        tk.Label(header, text="\u25C9  VOICE CONTROL", font=TITLE_FONT,
                 bg=SUBTLE_BG, fg=FG_COLOR).pack(side=tk.LEFT,
                                                 padx=(16, 6), pady=12)
        self.status_badge = tk.Label(header, text="IDLE", font=BTN_FONT,
                                     bg=GREY, fg="#ffffff", padx=10, pady=2)
        self.status_badge.pack(side=tk.RIGHT, padx=16)

        body = tk.Frame(self.root, bg=BG_COLOR)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=(8, 0))

        # --- VOICE CARD -------------------------------------------------
        voice = tk.Frame(body, bg=SUBTLE_BG, bd=1,
                         highlightbackground="#30363d",
                         highlightthickness=1)
        voice.pack(fill=tk.X, pady=(0, 8))

        tk.Label(voice, text="MICROPHONE & VOICE", font=(FONT_NAME, 9, "bold"),
                 bg=SUBTLE_BG, fg=ACCENT).pack(anchor="w", padx=12, pady=(8, 2))

        settings = tk.Frame(voice, bg=SUBTLE_BG)
        settings.pack(fill=tk.X, padx=12, pady=(2, 6))

        tk.Label(settings, text="Mic:", font=SMALL_FONT,
                 bg=SUBTLE_BG, fg=MUTED).pack(side=tk.LEFT)
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
            relief=tk.FLAT, cursor="hand2", padx=8, pady=2,
            command=self._auto_select,
        )
        self.auto_btn.pack(side=tk.LEFT, padx=(0, 14))

        tk.Label(settings, text="Lang:", font=SMALL_FONT,
                 bg=SUBTLE_BG, fg=MUTED).pack(side=tk.LEFT)
        self.lang_var = tk.StringVar(value=self.config["language"])
        self.lang_menu = tk.OptionMenu(
            settings, self.lang_var, *LANGUAGES,
            command=self._on_language_change,
        )
        self.lang_menu.config(bg=BG_COLOR, fg=FG_COLOR,
                              highlightthickness=1, highlightbackground="#30363d")
        self.lang_menu.pack(side=tk.LEFT, padx=(4, 0))

        self._apply_device_selection(device_choices)
        settings.columnconfigure(0, weight=1)

        # --- TTS voice + reading speed ----------------------------------
        tts_row = tk.Frame(voice, bg=SUBTLE_BG)
        tts_row.pack(fill=tk.X, padx=12, pady=(0, 4))

        tk.Label(tts_row, text="Voice:", font=SMALL_FONT,
                 bg=SUBTLE_BG, fg=MUTED).pack(side=tk.LEFT)
        self.voice_var = tk.StringVar(value="Default")
        self.voice_menu = tk.OptionMenu(
            tts_row, self.voice_var, "Default",
            command=self._on_voice_change,
        )
        self.voice_menu.config(bg=BG_COLOR, fg=FG_COLOR,
                               highlightthickness=1,
                               highlightbackground="#30363d")
        self.voice_menu.pack(side=tk.LEFT, padx=(4, 14))

        tk.Label(tts_row, text="Speed:", font=SMALL_FONT,
                 bg=SUBTLE_BG, fg=MUTED).pack(side=tk.LEFT)
        self.tts_rate_var = tk.IntVar(
            value=int(self.config.get("voice_rate", 180)))
        tk.Scale(tts_row, from_=80, to=320, resolution=5,
                 orient=tk.HORIZONTAL, variable=self.tts_rate_var,
                 command=self._on_rate_change, length=150, showvalue=True,
                 bg=SUBTLE_BG, fg=MUTED, troughcolor="#21262d",
                 highlightthickness=0, bd=0,
                 font=(FONT_NAME, 7)).pack(side=tk.LEFT)

        self.autostart_var = tk.BooleanVar(
            value=bool(self.config.get("autostart", False)))
        tk.Checkbutton(
            tts_row, text="Run on login", variable=self.autostart_var,
            font=(FONT_NAME, 7), bg=SUBTLE_BG, fg=MUTED,
            selectcolor=BG_COLOR, activebackground=SUBTLE_BG,
            activeforeground=MUTED, highlightthickness=0, bd=0,
            command=self._on_autostart_toggle,
        ).pack(side=tk.LEFT, padx=(10, 0))

        # --- Live voice-activity meter ----------------------------------
        meter_frame = tk.Frame(voice, bg=SUBTLE_BG)
        meter_frame.pack(fill=tk.X, padx=12, pady=(0, 2))

        self.level_var = tk.StringVar(value="LEVEL: --  (not listening)")
        tk.Label(meter_frame, textvariable=self.level_var, font=(FONT_NAME, 8),
                 bg=SUBTLE_BG, fg=FG_COLOR).pack(side=tk.LEFT)

        self.threshold_var = tk.StringVar(value="speech threshold: --")
        tk.Label(meter_frame, textvariable=self.threshold_var,
                 font=(FONT_NAME, 8), bg=SUBTLE_BG, fg=AMBER).pack(side=tk.RIGHT)

        # The waveform canvas doubles as the speech-level visualiser.
        self.meter_canvas = tk.Canvas(
            voice, height=64, bg=LOG_BG_COLOR, highlightthickness=0,
        )
        self.meter_canvas.pack(fill=tk.X, padx=12, pady=(2, 4))

        # --- Controls row ----------------------------------------------
        btn_frame = tk.Frame(voice, bg=SUBTLE_BG)
        btn_frame.pack(fill=tk.X, padx=12, pady=(0, 4))

        self.start_btn = tk.Button(
            btn_frame, text="\u25B6  START LISTENING", font=BTN_FONT,
            bg=BTN_BG_COLOR, fg=BTN_FG_COLOR,
            activebackground=HLIGHT_COLOR, activeforeground=BTN_FG_COLOR,
            relief=tk.FLAT, cursor="hand2", padx=14, pady=5,
            command=self.start_listening,
        )
        self.start_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.stop_btn = tk.Button(
            btn_frame, text="\u25A0  STOP", font=BTN_FONT,
            bg=BTN_STOP_BG_COLOR, fg=BTN_FG_COLOR,
            activebackground="#8b2c2c", activeforeground=BTN_FG_COLOR,
            relief=tk.FLAT, cursor="hand2", padx=14, pady=5,
            command=self.stop_listening, state="disabled",
        )
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.test_btn = tk.Button(
            btn_frame, text="TEST MIC", font=BTN_FONT,
            bg=BTN_TEST_BG_COLOR, fg=BTN_FG_COLOR,
            activebackground="#2c6496", activeforeground=BTN_FG_COLOR,
            relief=tk.FLAT, cursor="hand2", padx=14, pady=5,
            command=self.test_mic,
        )
        self.test_btn.pack(side=tk.LEFT)

        # --- Hand cursor card ------------------------------------------
        hand_card = tk.Frame(body, bg=SUBTLE_BG, bd=1,
                             highlightbackground="#30363d",
                             highlightthickness=1)
        hand_card.pack(fill=tk.X, pady=(0, 8))

        hand_head = tk.Frame(hand_card, bg=SUBTLE_BG)
        hand_head.pack(fill=tk.X, padx=12, pady=(8, 2))
        tk.Label(hand_head, text="HAND CURSOR (AIR MOUSE)",
                 font=(FONT_NAME, 9, "bold"), bg=SUBTLE_BG,
                 fg=ACCENT).pack(side=tk.LEFT)

        self.hand_state_var = tk.StringVar(value="hand: off")
        self.hand_led = tk.Label(hand_head, text="   ", font=(FONT_NAME, 8),
                                 bg=GREY)
        self.hand_led.pack(side=tk.LEFT, padx=(10, 4))
        tk.Label(hand_head, textvariable=self.hand_state_var,
                 font=(FONT_NAME, 8), bg=SUBTLE_BG, fg=LOG_FG_COLOR,
                 anchor="w").pack(side=tk.LEFT)

        hand_row = tk.Frame(hand_card, bg=SUBTLE_BG)
        hand_row.pack(fill=tk.X, padx=12, pady=(0, 6))

        self.hand_stop_btn = tk.Button(
            hand_row, text="STOP", font=(FONT_NAME, 8, "bold"),
            bg=BTN_STOP_BG_COLOR, fg=BTN_FG_COLOR,
            activebackground="#8b2c2c", activeforeground=BTN_FG_COLOR,
            relief=tk.FLAT, cursor="hand2", padx=10, pady=2,
            command=self.stop_hand_cursor, state="disabled",
        )
        self.hand_stop_btn.pack(side=tk.LEFT)

        self.hand_btn = tk.Button(
            hand_row, text="START", font=(FONT_NAME, 8, "bold"),
            bg=BTN_TEST_BG_COLOR, fg=BTN_FG_COLOR,
            activebackground="#2c6496", activeforeground=BTN_FG_COLOR,
            relief=tk.FLAT, cursor="hand2", padx=10, pady=2,
            command=self._hand_start_btn,
        )
        self.hand_btn.pack(side=tk.LEFT, padx=(0, 4))

        tk.Label(hand_row, text="Cam:", font=(FONT_NAME, 8),
                 bg=SUBTLE_BG, fg=MUTED).pack(side=tk.LEFT, padx=(10, 2))
        self.camera_var = tk.StringVar(
            value=str(self.config.get("camera_index", 0)))
        cam_cams = [str(i) for i in range(6)]
        try:
            if HAND_DEPS_OK and available_cameras is not None:
                cam_cams = [str(i) for i in available_cameras(limit=6)]
                if not cam_cams:
                    cam_cams = ["0"]
        except Exception:
            pass
        cam_menu = tk.OptionMenu(
            hand_row, self.camera_var, *cam_cams,
            command=self._on_camera_change,
        )
        cam_menu.config(bg=BG_COLOR, fg=FG_COLOR, highlightthickness=1,
                        highlightbackground="#30363d")
        cam_menu.pack(side=tk.LEFT)

        self.preview_var = tk.BooleanVar(
            value=bool(self.config.get("show_preview", True)))
        tk.Checkbutton(
            hand_row, text="Preview", variable=self.preview_var,
            font=(FONT_NAME, 8), bg=SUBTLE_BG, fg=MUTED,
            selectcolor=BG_COLOR, activebackground=SUBTLE_BG,
            activeforeground=MUTED, highlightthickness=0, bd=0,
            command=self._on_preview_toggle,
        ).pack(side=tk.LEFT, padx=(10, 0))

        # ---- Sensitivity + scroll sliders + CALIBRATE ----------------------
        hand_sliders = tk.Frame(hand_card, bg=SUBTLE_BG)
        hand_sliders.pack(fill=tk.X, padx=12, pady=(0, 4))

        self.sensitivity_var = tk.DoubleVar(
            value=float(self.config.get("hand_sensitivity", 1.0)))
        tk.Label(hand_sliders, text="Sens:", font=(FONT_NAME, 8),
                 bg=SUBTLE_BG, fg=MUTED).pack(side=tk.LEFT)
        tk.Scale(hand_sliders, from_=0.2, to=2.5, resolution=0.1,
                 orient=tk.HORIZONTAL, variable=self.sensitivity_var,
                 command=self._on_sensitivity_change, length=90,
                 bg=SUBTLE_BG, fg=MUTED, troughcolor="#21262d",
                 highlightthickness=0, bd=0, showvalue=False,
                 font=(FONT_NAME, 7)).pack(side=tk.LEFT, padx=(0, 8))

        self.scroll_speed_var = tk.DoubleVar(
            value=float(self.config.get("hand_scroll_speed", 1.0)))
        tk.Label(hand_sliders, text="Scroll:", font=(FONT_NAME, 8),
                 bg=SUBTLE_BG, fg=MUTED).pack(side=tk.LEFT)
        tk.Scale(hand_sliders, from_=0.2, to=3.0, resolution=0.1,
                 orient=tk.HORIZONTAL, variable=self.scroll_speed_var,
                 command=self._on_scroll_speed_change, length=90,
                 bg=SUBTLE_BG, fg=MUTED, troughcolor="#21262d",
                 highlightthickness=0, bd=0, showvalue=False,
                 font=(FONT_NAME, 7)).pack(side=tk.LEFT, padx=(0, 12))

        self.calib_btn = tk.Button(
            hand_sliders, text="CALIBRATE REACH",
            font=(FONT_NAME, 7, "bold"), bg="#30363d", fg=MUTED,
            activebackground="#484f58", activeforeground=FG_COLOR,
            relief=tk.FLAT, padx=8, pady=1, cursor="hand2",
            command=self._calibrate_reach,
        )
        self.calib_btn.pack(side=tk.LEFT)

        # ---- Second row: TRAIN + Beep + live size readout ----------------
        hand_actions = tk.Frame(hand_card, bg=SUBTLE_BG)
        hand_actions.pack(fill=tk.X, padx=12, pady=(0, 4))

        self.train_btn = tk.Button(
            hand_actions, text="TRAIN GESTURES",
            font=(FONT_NAME, 7, "bold"), bg="#30363d", fg=MUTED,
            activebackground="#484f58", activeforeground=FG_COLOR,
            relief=tk.FLAT, padx=8, pady=1, cursor="hand2",
            command=self._open_trainer,
        )
        self.train_btn.pack(side=tk.LEFT)

        self._macro_mode_var = tk.BooleanVar(
            value=bool(self.config.get("macro_mode", False)))
        self.macro_btn = tk.Button(
            hand_actions, text="DRAW MACROS",
            font=(FONT_NAME, 7, "bold"), bg="#30363d", fg=MUTED,
            activebackground="#484f58", activeforeground=FG_COLOR,
            relief=tk.FLAT, padx=8, pady=1, cursor="hand2",
            command=self._toggle_macro_mode,
        )
        self.macro_btn.pack(side=tk.LEFT, padx=(8, 0))
        self._paint_macro_btn()

        self._touchpad_var = tk.BooleanVar(
            value=bool(self.config.get("touchpad_mode", False)))
        self.touchpad_btn = tk.Button(
            hand_actions, text="TOUCHPAD",
            font=(FONT_NAME, 7, "bold"), bg="#30363d", fg=MUTED,
            activebackground="#484f58", activeforeground=FG_COLOR,
            relief=tk.FLAT, padx=8, pady=1, cursor="hand2",
            command=self._toggle_touchpad,
        )
        self.touchpad_btn.pack(side=tk.LEFT, padx=(8, 0))
        self._paint_touchpad_btn()

        self.click_beep_var = tk.BooleanVar(
            value=bool(self.config.get("click_beep", True)))
        tk.Checkbutton(
            hand_actions, text="Beep", variable=self.click_beep_var,
            font=(FONT_NAME, 7), bg=SUBTLE_BG, fg=MUTED,
            selectcolor=BG_COLOR, activebackground=SUBTLE_BG,
            activeforeground=MUTED, highlightthickness=0, bd=0,
            command=self._on_beep_toggle,
        ).pack(side=tk.LEFT, padx=(10, 0))

        self.hand_size_var = tk.StringVar(value="")
        tk.Label(hand_actions, textvariable=self.hand_size_var,
                 font=(FONT_NAME, 7), bg=SUBTLE_BG, fg=ACCENT).pack(
            side=tk.LEFT, padx=(12, 0))

        self.preview_canvas = tk.Label(
            hand_card, bg=LOG_BG_COLOR, text="(preview turned off)",
            font=(FONT_NAME, 8), fg=MUTED,
            highlightthickness=1, highlightbackground="#30363d",
        )
        self.preview_canvas.pack(fill=tk.X, padx=12, pady=(0, 8), ipadx=6,
                                 ipady=6)

        # --- Live "what I heard" banner --------------------------------
        self.heard_var = tk.StringVar(value="Heard: (nothing yet)")
        heard_label = tk.Label(
            body, textvariable=self.heard_var, font=(FONT_NAME, 10, "bold"),
            bg=SUBTLE_BG, fg="#e6edf3", anchor="w", justify=tk.LEFT,
            wraplength=640,
        )
        heard_label.pack(fill=tk.X, pady=(0, 8), ipady=4)
        self.heard_label = heard_label

        # --- Clipboard manager card (click an entry to paste it) ---------
        clip_card = tk.Frame(body, bg=SUBTLE_BG, bd=1,
                             highlightbackground="#30363d",
                             highlightthickness=1)
        clip_card.pack(fill=tk.X, pady=(0, 8))

        clip_head = tk.Frame(clip_card, bg=SUBTLE_BG)
        clip_head.pack(fill=tk.X, padx=12, pady=(6, 2))
        tk.Label(clip_head, text="CLIPBOARD  (click to paste)",
                 font=(FONT_NAME, 9, "bold"), bg=SUBTLE_BG,
                 fg=ACCENT).pack(side=tk.LEFT)
        tk.Label(clip_head, text="Say: what did I copy",
                 font=(FONT_NAME, 7), bg=SUBTLE_BG, fg=MUTED).pack(
            side=tk.RIGHT)

        self.clip_box = tk.Frame(clip_card, bg=SUBTLE_BG)
        self.clip_box.pack(fill=tk.X, padx=12, pady=(0, 8))
        tk.Label(self.clip_box, text="(nothing copied yet)",
                 font=(FONT_NAME, 8), bg=SUBTLE_BG, fg=MUTED,
                 anchor="w").pack(fill=tk.X)

        # --- Command / activity log -------------------------------------
        log_card = tk.Frame(body, bg=SUBTLE_BG, bd=1,
                            highlightbackground="#30363d",
                            highlightthickness=1)
        log_card.pack(fill=tk.BOTH, expand=True)

        tk.Label(log_card, text="ACTIVITY LOG",
                 font=(FONT_NAME, 9, "bold"), bg=SUBTLE_BG,
                 fg=ACCENT).pack(anchor="w", padx=12, pady=(8, 2))

        self.log_text = scrolledtext.ScrolledText(
            log_card, bg=LOG_BG_COLOR, fg=LOG_FG_COLOR, font=LOG_FONT,
            insertbackground=LOG_FG_COLOR, relief=tk.FLAT,
            highlightthickness=0, state="disabled", wrap=tk.WORD,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))

        # --- Status bar + hint -------------------------------------------
        self.status_var = tk.StringVar(value="Idle. Press START LISTENING.")
        tk.Label(self.root, textvariable=self.status_var, font=SMALL_FONT,
                 bg=BG_COLOR, fg=MUTED).pack(side=tk.BOTTOM, pady=(0, 2))

        tk.Label(
            self.root,
text="Say: Open <app> | Type <text> | Search <query> | "
             "Ask <question> | Analyze screen | What's under my cursor | "
             "Set timer | What did I copy | Open Chrome then search... | "
             "New/Close/Next Tab | Minimize | Lock Screen | "
             "Stop Listening | Help | Start/Stop Hand Cursor",
            font=(FONT_NAME, 8), bg=BG_COLOR, fg=ACCENT,
            wraplength=640, justify=tk.LEFT,
        ).pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=4)

        self._ready = True

        # Draw an idle waveform placeholder, then start the queue poller and
        # the clipboard history watcher (all on the GUI thread).
        self._redraw_waveform()
        self.root.after(30, self._poll_queue)
        self._clip_watch_started = False
        self.root.after(800, self._watch_clipboard)
        # Optional: tray icon + global hotkey for minimize-to-tray.
        self.root.after(1200, self._start_tray)

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
        """Update the live voice-level metre + waveform from a queue message."""
        self.level_var.set(
            f"LEVEL: {rms_value}  ({describe_noise(rms_value)})")

        threshold = self._meter_threshold or 500
        state = "speaking" if rms_value >= threshold else "listening"
        if state != self._voice_state:
            self._voice_state = state
            self._set_badge(state)

        self._rms_history.append(rms_value)
        self._redraw_waveform()

    def _redraw_waveform(self):
        """Draw the animated voice-activity bars on the metre canvas."""
        try:
            width = self.meter_canvas.winfo_width() or 400
            height = self.meter_canvas.winfo_height() or 64
            self.meter_canvas.delete("all")

            n = len(self._rms_history)
            if n == 0:
                grey = "#21262d"
                self.meter_canvas.create_text(
                    width // 2, height // 2,
                    text="waiting for voice...", fill=grey,
                    font=(FONT_NAME, 8))
                return

            bar_w = max(3, width // 40)
            gap = 2
            x = 0.0
            threshold = self._meter_threshold or 500
            for rms in self._rms_history:
                frac = min(rms / 4000.0, 1.0)
                bar_h = max(2, int(height * frac))
                if rms >= threshold:
                    color = RED
                elif rms >= threshold * 0.6:
                    color = AMBER
                else:
                    color = GREEN
                self.meter_canvas.create_rectangle(
                    x, height - bar_h, x + bar_w, height,
                    fill=color, outline="")
                x += bar_w + gap

            # Draw the speech threshold guide line.
            ty = height - max(2, int(height * min(threshold / 4000.0, 1.0)))
            self.meter_canvas.create_line(
                0, ty, width, ty, fill="#58a6ff", dash=(2, 3))
            self.meter_canvas.create_text(
                width - 4, max(6, ty - 2), text="threshold", fill=ACCENT,
                font=(FONT_NAME, 7), anchor="ne")
        except tk.TclError:
            pass  # window closing

    def _set_badge(self, state):
        """Colour the header status badge by the app's voice state."""
        mapping = {
            "idle":      (GREY, "IDLE"),
            "listening": (GREEN, "LISTENING"),
            "speaking":  (RED, "SPEAKING"),
            "paused":    (AMBER, "PAUSED"),
            "stopped":   (GREY, "IDLE"),
        }
        try:
            bg, text = mapping.get(state, (GREY, "IDLE"))
            self.status_badge.config(bg=bg, text=text)
        except tk.TclError:
            pass

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
              "hand_state" | "hand_preview" | "hand_size" | "started" |
              "stopped" | "error" | "beep" | "tts_voices" |
              "quit"
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
                    self._set_badge("listening" if data else "idle")
                elif kind == "level":
                    self._on_level(int(data))
                elif kind == "heard":
                    self._on_heard(data)
                elif kind == "threshold":
                    self._meter_threshold = data
                    self.threshold_var.set(f"speech threshold: {data:.0f}")
                elif kind == "hand_state":
                    self.hand_state_var.set(data)
                    self._set_hand_led(str(data))
                elif kind == "hand_preview":
                    self._show_preview(data)
                elif kind == "hand_size":
                    self._on_hand_size(float(data))
                elif kind == "started":
                    self._set_hand_ui(True, "hand: ON")
                    self._append_log(data)
                elif kind == "stopped":
                    self._set_hand_ui(False, "hand: off")
                    if self._train_window is not None:
                        self._close_trainer()
                elif kind == "error":
                    self._set_hand_ui(False, "hand: error")
                    self._append_log(data)
                elif kind == "autoselect":
                    self._apply_autoselect(data)
                elif kind == "testdone" or kind == "autodone":
                    self.test_btn.config(state="normal")
                    self.auto_btn.config(state="normal")
                elif kind == "timer":
                    self._append_log(data)
                    self._show_toast("Timer", data)
                elif kind == "clipcard":
                    self._append_log(data)
                elif kind == "beep":
                    self._play_beep(data)
                elif kind == "train":
                    self._on_train_update(data)
                elif kind == "macro":
                    self._on_macro(data)
                elif kind == "tts_voices":
                    self._populate_voice_menu(data)
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

    # ------------------------------------------------------------------
    # Clipboard history watcher (GUI thread)
    # ------------------------------------------------------------------
    def _watch_clipboard(self):
        """Poll the OS clipboard and remember new text entries (max 8)."""
        if not getattr(self, "_ready", False):
            try:
                self.root.after(800, self._watch_clipboard)
                return
            except tk.TclError:
                return
        changed = False
        try:
            value = self.root.clipboard_get()
            if (value and isinstance(value, str) and
                    value != self._last_clip_value):
                with self._clip_lock:
                    self._clip_history.append(value)
                    if len(self._clip_history) > 8:
                        self._clip_history = self._clip_history[-8:]
                self._last_clip_value = value
                changed = True
        except tk.TclError:
            pass
        except Exception:
            pass
        with self._clip_lock:
            current = list(self._clip_history)
        if changed or current != self._last_rendered_clips:
            self._render_clip_card(current)
        try:
            self.root.after(1200, self._watch_clipboard)
        except tk.TclError:
            pass

    def _clip_text(self):
        """Return a compact summary of the remembered clipboard entries."""
        with self._clip_lock:
            entries = list(self._clip_history)
        if not entries:
            return ("The clipboard history is empty. I store things "
                    "after you say copy or cut.")
        lines = ["Clipboard history (newest first):"]
        for item in reversed(entries[-5:]):
            short = item.replace("\n", " ").strip()
            lines.append(f"  - {short[:90]}")
        return "\n".join(lines)

    def _render_clip_card(self, entries):
        """Rebuild the clipboard card rows (newest first, click to paste)."""
        try:
            for widget in self.clip_box.winfo_children():
                widget.destroy()
        except (tk.TclError, AttributeError):
            return
        self._last_rendered_clips = list(entries)
        if not entries:
            try:
                tk.Label(self.clip_box, text="(nothing copied yet)",
                         font=(FONT_NAME, 8), bg=SUBTLE_BG, fg=MUTED,
                         anchor="w").pack(fill=tk.X)
            except (tk.TclError, AttributeError):
                pass
            return
        for idx, item in enumerate(reversed(entries[-4:])):
            short = item.replace("\n", " ").strip()
            row = tk.Frame(self.clip_box, bg=SUBTLE_BG)
            row.pack(fill=tk.X, pady=1)
            label = tk.Label(
                row, text=f"{idx + 1}. {short[:70]}",
                font=(FONT_NAME, 8), bg=SUBTLE_BG, fg="#e6edf3",
                anchor="w", cursor="hand2",
                activebackground="#1f242c",
                activeforeground=ACCENT, wraplength=560,
            )
            label.pack(fill=tk.X)
            label.bind("<Button-1>",
                       lambda _e, text=item: self._paste_clip(text))

    def _paste_clip(self, text):
        """Copy a remembered clipboard entry and paste it (GUI thread)."""
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self._last_clip_value = text
        except Exception as exc:
            self._append_log(f"[!] Clipboard paste error: {exc}")
            return
        self._append_log(f"[i] Pasted clipboard entry: "
                         f"{text.replace(chr(10), ' ').strip()[:60]}")
        try:
            import pyautogui as _pg
            _pg.hotkey("ctrl", "v")
        except Exception as exc:
            self._append_log(f"[!] Paste keystroke error: {exc}")

    # ------------------------------------------------------------------
    # Timer / reminder runner (worker thread)
    # ------------------------------------------------------------------
    def _timer_runner(self):
        """Every second check for timers/reminders that have fired."""
        while True:
            try:
                due = []
                with self._timers_lock:
                    now = time.time()
                    keep = []
                    for t in self._timers:
                        if now >= t["due"]:
                            due.append(t)
                        else:
                            keep.append(t)
                    self._timers = keep
                for t in due:
                    self._post("timer",
                               f"[TIMER] {t['label']} - time is up "
                               f"({time.strftime('%H:%M')})")
                    self._speak(f"Timer finished. {t['label']}")
            except Exception as exc:
                self._post("log", f"[!] Timer error: {exc}")
            time.sleep(1.0)

    def _set_timer(self, seconds, label):
        """Queue a new countdown timer in the background."""
        with self._timers_lock:
            self._timers.append({"due": time.time() + seconds, "label": label})
        self._post("log", f"[i] Timer set: {label} in {seconds:.0f} s.")
        self._speak(f"Timer set. {label}.")

    # ------------------------------------------------------------------
    # Screen vision (capture + Gemini) helpers (worker thread)
    # ------------------------------------------------------------------
    def _grab_screen_png(self):
        """Capture the full screen and return PNG bytes (Pillow on Windows)."""
        if not PIL_OK or ImageGrab is None:
            return None
        img = ImageGrab.grab(all_screens=False)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def _ask_gemini_vision(self, question, image_bytes):
        """Send a screenshot + question to Gemini and return (answer, err)."""
        api_key = (self.config.get("gemini_api_key") or
                   os.environ.get("GEMINI_API_KEY", ""))
        if not api_key:
            return None, ("Gemini is not configured. Add your API key to "
                          "voc_config.json or the GEMINI_API_KEY variable.")
        model = self.config.get("gemini_model", "gemini-2.0-flash")
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={api_key}")
        b64 = base64.b64encode(image_bytes).decode("ascii")
        parts = [{"text": question},
                 {"inlineData": {"mimeType": "image/png", "data": b64}}]
        try:
            resp = self._http.post(
                url,
                json={"contents": [{"parts": parts}]},
                headers={"Content-Type": "application/json"},
                timeout=45,
            )
            data = resp.json()
            if resp.status_code != 200:
                message = data.get("error", {}).get("message", resp.reason)
                return None, f"Gemini error ({resp.status_code}): {message}"
            cand = data.get("candidates", [{}])[0].get("content", {}).get(
                "parts", [])
            texts = [p.get("text", "") for p in cand if p.get("text")]
            if not texts:
                return None, "Gemini returned an empty answer."
            return texts[0].strip(), None
        except requests.exceptions.RequestException as exc:
            return None, f"Network error contacting Gemini: {exc}"
        except (ValueError, KeyError, IndexError) as exc:
            return None, f"Unexpected Gemini response: {exc}"

    def _analyze_screen(self, question):
        """Worker-thread: screenshot the screen and ask Gemini to look at it."""
        self._post("status", "Capturing screen...")
        self._post("log", "[i] Taking a screenshot for analysis...")
        image = self._grab_screen_png()
        if not image:
            self._post("log", "[!] Could not capture the screen.")
            self._speak("I could not capture the screen.")
            return
        if not question:
            question = ("Describe what is currently visible on this "
                        "computer screen in 2-3 sentences.")
        self._post("status", "Asking Gemini to look at the screen...")
        answer, error = self._ask_gemini_vision(question, image)
        if error:
            self._post("log", f"[!] {error}")
            self._speak("I could not analyze the screen.")
        else:
            self._post("log", f"\n[SCREEN ANALYSIS]\n{answer}")
            self._speak(self._shorten(answer, 240))
        self._post("status", "Listening...")

    # ------------------------------------------------------------------
    # Cursor-region vision (capture a region around the mouse + Gemini)
    # ------------------------------------------------------------------
    def _grab_cursor_region_png(self, radius=180):
        """Grab a region around the mouse cursor and return PNG bytes."""
        if not PIL_OK or ImageGrab is None:
            return None
        try:
            import pyautogui as _pg
            x, y = _pg.position()
        except Exception:
            return None
        box = (x - radius, y - radius, x + radius, y + radius)
        img = ImageGrab.grab(bbox=box)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def _analyze_cursor_region(self, question):
        """Worker-thread: capture a region around the mouse cursor."""
        self._post("status", "Capturing cursor region...")
        image = self._grab_cursor_region_png()
        if not image:
            self._post("log", "[!] Could not capture near the cursor.")
            self._speak("I could not capture near your cursor.")
            return
        if not question:
            question = ("Describe what is visible in this image. "
                        "What is currently under the cursor?")
        self._post("status", "Asking Gemini to describe cursor region...")
        answer, error = self._ask_gemini_vision(question, image)
        if error:
            self._post("log", f"[!] {error}")
            self._speak("I could not analyze that region.")
        else:
            self._post("log", f"\n[CURSOR REGION]\n{answer}")
            self._speak(self._shorten(answer, 240))
        self._post("status", "Listening...")

    def _append_log(self, line):
        """Insert a timestamped line (or multi-line block) into the log."""
        timestamp = time.strftime("%H:%M:%S")
        body = line if isinstance(line, str) else str(line)

        # Persist every log line to voc_log.txt (keep the file capped at
        # ~2000 lines so it never grows without bound).
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as fh:
                for part in body.split("\n"):
                    fh.write(f"[{timestamp}] {part}\n")
        except OSError:
            pass
        try:
            if os.path.getsize(LOG_FILE) > 256 * 1024:
                lines = []
                with open(LOG_FILE, "r", encoding="utf-8") as fh:
                    lines = fh.readlines()
                with open(LOG_FILE, "w", encoding="utf-8") as fh:
                    fh.writelines(lines[-2000:])
        except OSError:
            pass

        self.log_text.configure(state="normal")
        for part in body.split("\n"):
            self.log_text.insert(tk.END, f"[{timestamp}] {part}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def _play_beep(self, kind):
        """Play a short, distinct confirmation sound for hand clicks/drags.

        kind: "left" (short click) | "right" | "drag" | "right_drag"
        A higher-pitched tone is used for drag start so the user can tell
        that the button is being held rather than clicked.
        """
        if not self.config.get("click_beep", True):
            return
        try:
            tone = {"left": 1200, "right": 1500, "drag": 2200,
                    "right_drag": 2200}.get(kind, 1200)
            winsound.Beep(tone, 60)
        except (OSError, RuntimeError):
            pass  # beep unavailable - never crash the GUI poller

    def _show_toast(self, title, message, duration_ms=6000):
        """Show a small, always-on-top toast popup that auto-dismisses.

        GUI thread only (called from the queue poller / GUI callbacks).
        """
        try:
            toast = tk.Toplevel(self.root)
            toast.title(title)
            toast.overrideredirect(True)   # frameless mini-popup
            toast.attributes("-topmost", True)
            toast.configure(bg=SUBTLE_BG)
            frame = tk.Frame(toast, bg="#1f2329", bd=1,
                             highlightbackground="#58a6ff",
                             highlightthickness=1)
            frame.pack(fill="both", expand=True)
            tk.Label(frame, text=title.upper(), font=(FONT_NAME, 9, "bold"),
                     bg="#1f2329", fg="#58a6ff").pack(
                anchor="w", padx=10, pady=(6, 0))
            tk.Label(frame, text=message, font=(FONT_NAME, 9),
                     bg="#1f2329", fg="#e6edf3", justify="left",
                     wraplength=320).pack(anchor="w", padx=10,
                                          pady=(2, 8))
            # Position near the bottom-right corner of the screen.
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            toast.update_idletasks()
            w = toast.winfo_reqwidth()
            h = toast.winfo_reqheight()
            toast.geometry(f"+{screen_w - w - 24}+{screen_h - h - 80}")
            toast.bind("<Button-1>", lambda _e: toast.destroy())
            toast.after(duration_ms, toast.destroy)
            # Keep a reference so the Toplevel is not garbage-collected.
            if not hasattr(self, "_toast_refs"):
                self._toast_refs = []
            self._toast_refs.append(toast)
            self._toast_refs = self._toast_refs[-5:]
        except (tk.TclError, AttributeError, OSError):
            pass  # toast is cosmetic - never crash on failure

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
        self._voice_state = "listening" if running else "idle"
        self._set_badge(self._voice_state)

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
    @staticmethod
    def _parse_chain(lowered):
        """Return list of sequential command parts, or None if not a chain.

        Strings containing ' then ' or ' and then ' are split but only
        when every resulting segment looks like a real voice command
        (≥2 words, or a recognised single-word action verb).
        """
        if not (re.search(r"\bthen\b", lowered) or
                re.search(r"\band then\b", lowered)):
            return None
        parts = re.split(r"\s+(?:and\s+)?then\s+", lowered, maxsplit=5)
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) < 2:
            return None

        def _chainable(part):
            words = part.split()
            if len(words) >= 2:
                return True
            return part in ("start", "stop", "help", "refresh",
                            "minimize", "maximize", "mute", "enter",
                            "undo", "paste", "copy")
        return parts if all(_chainable(p) for p in parts) else None

    def handle_command(self, phrase):
        """
        Match a spoken phrase against known commands.

        Phrases containing " then " or " and then " are split and executed
        sequentially so multi-step chains work naturally.

        Unknown (but sufficiently long) phrases are forwarded to Gemini
        and the answer is shown in the log and spoken aloud.
        """
        lowered = phrase.lower().strip()
        original = phrase.strip()
        if not lowered:
            return

        # ============ Multi-step command chaining ===========================
        chain = self._parse_chain(lowered)
        if chain is not None:
            self._post("log", f"[i] Chained command ({len(chain)} steps): "
                              " + ".join(chain))
            for i, part in enumerate(chain):
                if i > 0:
                    time.sleep(0.6)
                self.handle_command(part)
            return

        # ============ Paused / suspended state ==========================
        if self._suspended:
            if re.search(r"(?:^|\b)(?:please\s+|now\s+|okay\s+)?start "
                         r"listening\b", lowered) or \
               re.search(r"\b(hey|hi)\s+(vo|voice|vak)\b", lowered) or \
               lowered in (
                "resume", "resume listening", "wake up", "continue",
                "hey vo", "hey voice", "hey vak",
                "vo", "wake up vo", "ok vo"):
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

        # ---- Gesture trainer ----------------------------------------------
        if re.search(r"\b(train|practice|learn|teach|drill)\b",
                     lowered) and \
           re.search(r"\b(gesture|gestures|hand)\b", lowered):
            if re.search(r"\b(stop|close|quit|exit|end)\b", lowered):
                self._close_trainer()
                self._speak("Closing the gesture trainer.")
            else:
                self._open_trainer()
                self._post("log", "[i] Opening gesture trainer.")
                self._speak("Opening the gesture trainer.")
            return

        # ---- Draw macros (trace a shape in the air) ------------------------
        mac_on = re.search(r"\b(draw macros?|draw mode|drawing mode|"
                           r"macros? mode|start draw(ing)?|enable macros?|"
                           r"turn (macros?|macro mode|drawing) on)\b",
                           lowered)
        mac_off = re.search(r"\b(stop macros?|macros? off|disable macros?|"
                            r"stop drawing|exit macros?|close macros?|"
                            r"turn (macros?|macro mode|drawing) off)\b",
                            lowered)
        if mac_on or mac_off:
            if mac_on and not mac_off:
                self._toggle_macro_mode(True)
                self._speak("Macro drawing mode turned on.")
            else:
                self._toggle_macro_mode(False)
                self._speak("Macro drawing mode turned off.")
            return

        # ---- Touchpad mode (relative mouse / trackpad) ----------------------
        tp_on = re.search(r"\b(touchpad|trackpad)", lowered) and \
            re.search(r"\b(on|start|begin|enable|mode)\b", lowered)
        tp_off = re.search(r"\b(touchpad|trackpad)", lowered) and \
            re.search(r"\b(off|stop|disable|exit|end)\b", lowered)
        if tp_on or tp_off:
            if tp_on and not tp_off:
                self._toggle_touchpad(True)
                self._speak("Touchpad mode turned on. Move your hand like "
                            "a mouse.")
            else:
                self._toggle_touchpad(False)
                self._speak("Touchpad mode turned off.")
            return

        # ---- Screen analysis via Gemini Vision ----------------------------
        if re.search(r"\b(analy[sz]e|scan|describe|read|what (is|are|do you "
                     r"see)|look at|show me)\b.*\bscreen\b", lowered) or \
           lowered in ("screen analysis", "analyze screen", "scan screen",
                       "what is on my screen", "what do you see",
                       "what is on the screen"):
            m = re.search(r"\b(what|describe|explain|tell|read)\b.*",
                          lowered, re.I)
            question = m.group(0) if m else ""
            threading.Thread(target=self._analyze_screen,
                             args=(question,), daemon=True).start()
            return

        # ---- Cursor-region vision (what is under my cursor) ---------------
        if re.search(r"\b(what (is|are)|describe|look at|scan|read)\b.*\b"
                     r"(cursor|mouse|under my cursor|under the mouse)\b",
                     lowered) or \
           lowered in ("under my cursor", "under the mouse",
                       "what is under my cursor", "what's under my cursor",
                       "cursor region"):
            m = re.search(r"\b(what|describe|explain|tell|read)\b.*",
                          lowered, re.I)
            question = m.group(0) if m else ""
            threading.Thread(target=self._analyze_cursor_region,
                             args=(question,), daemon=True).start()
            return

        # ---- Timer / reminder --------------------------------------------
        m = re.match(
            r"(?:^|\b)(?:(?:set\s+(?:an?\s+)?(?:timer|alarm|remind(?:er)?))|"
            r"(?:remind(?:er)?\s+(?:me\s+|us\s+)?)|(?:timer\s+)|"
            r"(?:alarm\s+))\s*(?:for\s+|in\s+|at\s+)?(.+)", lowered
        )
        if m:
            raw = m.group(1).strip()
            seconds = self._parse_duration(raw) or self._parse_absolute(raw)
            if seconds and seconds > 0:
                self._set_timer(seconds, raw)
            else:
                self._speak(
                    "I did not understand the time. "
                    "Say for example: set a timer for 5 minutes.")
            return

        # ---- Clipboard history -------------------------------------------
        if re.search(r"\b(clipboard|copy history|what did i (copy|cut)"
                     r"|show (me )?my clipboard|paste history)\b", lowered):
            text = self._clip_text()
            self._post("log", f"[i] {text}")
            self._speak(text[:220])
            return

        # ---- Time / date ------------------------------------------------
        if any(w in lowered for w in ("what time", "time is it",
                                      "current time", "time now",
                                      "what is the time")):
            now = time.strftime("%I:%M %p")
            self._post("log", f"[i] The time is {now}.")
            self._speak(f"The time is {now}.")
            return
        if any(w in lowered for w in ("what date", "what day", "today's date",
                                      "what is the date", "what day is it",
                                      "current date")):
            today = time.strftime("%A, %B %d, %Y")
            self._post("log", f"[i] Today is {today}.")
            self._speak(f"Today is {today}.")
            return

        # ---- Take a note --------------------------------------------------
        m = re.search(r"(?:^|\b)(?:take|write|make|save)\s+(?:a\s+)?note[\s:,]+"
                      r"(.*)", original, re.I)
        if m:
            text = TRAILING_FILLER.sub("", m.group(1)).strip()
            if text:
                self._save_note(text)
            else:
                self._speak("What should I write down?")
            return

        # ---- Calculator (safe arithmetic) --------------------------------
        m = re.match(r"(?:^|\b)(?:calculate|compute|work out|what is|what's|"
                     r"how much is)\s+(.+)", lowered)
        if m:
            expr = TRAILING_FILLER.sub("", m.group(1)).strip()
            expr = expr.replace("x", "*").replace("×", "*").replace(
                "÷", "/").replace("equal to", "").replace("equals", "=")
            result = self._eval_math(expr)
            if result is not None:
                self._post("log", f"[i] {expr} = {result}")
                self._speak(f"That is {result}.")
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
            if re.search(r"\b(minimize|minimise)\b.*\b(tray|to tray)\b",
                         lowered):
                self.root.withdraw()
                self._post("log", "[i] Minimized to system tray.")
                self._speak("Minimized to the system tray.")
                return
            self._window_action("minimize")
            self._speak("Minimized the window.")
            return

        # Restore the window (e.g. from tray) - also covers "show window".
        if re.search(r"\b(show|restore|bring (back|up))\b.*\b(window|app|"
                     r"tray)\b", lowered):
            self._restore_window()
            self._speak("Here I am.")
            return

        if "maximize" in lowered or "maximise" in lowered:
            self._window_action("maximize")
            self._speak("Maximized the window.")
            return

        if "lock" in lowered:  # "lock screen / lock the computer"
            self._safe_keys(lambda: pyautogui.hotkey("win", "l"))
            self._speak("Locking the screen.")
            return

        # ---- Power (dangerous - only trigger with explicit language) ------
        if re.search(r"\b(shut\s*down|power off|turn off)\b.*"
                     r"\b(computer|pc|laptop|machine)\b", lowered):
            self._speak("Shutting down in 30 seconds. "
                        "Say open command prompt to cancel.")
            subprocess.Popen(["shutdown", "/s", "/t", "30", "/c",
                              "Shutting down per voice command."])
            self._post("log", "[i] Shutdown scheduled in 30 seconds.")
            return
        if re.search(r"\brestart\b.*"
                     r"\b(computer|pc|laptop|machine)\b", lowered):
            self._speak("Restarting in 30 seconds.")
            subprocess.Popen(["shutdown", "/r", "/t", "30", "/c",
                              "Restarting per voice command."])
            self._post("log", "[i] Restart scheduled in 30 seconds.")
            return
        if re.search(r"\b(sleep|hibernate)\b.*"
                     r"\b(computer|pc|laptop|machine)\b", lowered):
            self._speak("Putting the computer to sleep.")
            subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState",
                              "0,1,0"])
            self._post("log", "[i] Computer going to sleep.")
            return

        # ---- Auto-start (run on login) ----------------------------------
        if re.search(r"\b(start|launch|open|run)\b.*\b(on|with|at)\b.*"
                     r"\b(login|startup|boot|windows)\b", lowered) or \
           re.search(r"\b(auto ?start|run on login|start on boot)\b",
                     lowered):
            if re.search(r"\b(off|disable|don't|do not|no)\b", lowered):
                self._set_autostart(False)
                self.autostart_var.set(False)
                self._speak("Auto start is now off.")
            else:
                self._set_autostart(True)
                self.autostart_var.set(True)
                self._speak("This app will start automatically when you "
                            "sign in.")
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
            elif norm in SITE_SHORTCUTS:
                self._open_browser(SITE_SHORTCUTS[norm])
                self._post("log", f"[i] Opening {target}...")
                self._speak(f"Opening {target}.")
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

        # ============ "Clear memory" / forget the conversation ============
        if re.search(r"\b(clear|forget|erase|reset)\b.*\b(memory|my "
                     r"memory|conversation|chat)\b", lowered, re.I) or \
           lowered in ("clear memory", "forget memory", "clear the chat",
                       "forget everything", "clear the conversation"):
            self.clear_gemini_memory()
            self._speak("I cleared my memory.")
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

        Uses the in-session conversation history (see _gemini_history) so
        follow-up questions have context. Returns (answer_text, None) on
        success or (None, error_message).
        """
        api_key = (self.config.get("gemini_api_key") or
                   os.environ.get("GEMINI_API_KEY", ""))
        if not api_key:
            return None, ("Gemini is not configured. Add your API key to "
                          "voc_config.json or the GEMINI_API_KEY variable.")
        model = self.config.get("gemini_model", "gemini-2.0-flash")
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={api_key}")
        with self._gemini_lock:
            history = list(self._gemini_history)[-16:]
        contents = [{"parts": [{"text": text}]} for role, text in history]
        contents.append({"parts": [{"text": question}]})
        try:
            resp = self._http.post(
                url,
                json={"contents": contents},
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
            answer = texts[0].strip()
            with self._gemini_lock:
                self._gemini_history.append(("user", question))
                self._gemini_history.append(("model", answer))
                self._gemini_history = self._gemini_history[-16:]
            return answer, None
        except requests.exceptions.RequestException as exc:
            return None, f"Network error contacting Gemini: {exc}"
        except (ValueError, KeyError, IndexError) as exc:
            return None, f"Unexpected Gemini response: {exc}"

    def clear_gemini_memory(self):
        """Forget the current Gemini conversation context."""
        with self._gemini_lock:
            self._gemini_history = []
        self._append_log("[i] Gemini conversation memory cleared.")

    def _memory_size(self):
        with self._gemini_lock:
            return len(self._gemini_history)

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

    @staticmethod
    def _parse_duration(raw):
        """Convert a human duration like '5 minutes 30 seconds' to seconds."""
        total = 0
        for match in re.finditer(r"(\d+)\s*(h(?:ou)?r|min(?:ute)?|sec(?:ond)?)",
                                 raw, re.I):
            val = int(match.group(1))
            unit = match.group(2).lower()
            if unit.startswith("h"):
                total += val * 3600
            elif unit.startswith("m"):
                total += val * 60
            else:
                total += val
        return total if total else None

    @staticmethod
    def _parse_absolute(raw):
        """Parse an absolute clock time like 'at 3 o'clock' or 'at 3:30 pm'."""
        m = re.search(
            r"(\d{1,2})(?::(\d{2}))?\s*(?:o\s*clock|hour)?\s*"
            r"(a\.?m\.?|p\.?m\.?|am|pm)?", raw, re.I)
        if not m:
            return None
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        if hour > 24 or minute > 59:
            return None
        meridiem = (m.group(3) or "").lower().replace(".", "")
        if meridiem.startswith("p") and hour < 12:
            hour += 12
        elif meridiem.startswith("a") and hour == 12:
            hour = 0
        if hour > 23:
            return None
        now = time.localtime()
        due = time.mktime((now.tm_year, now.tm_mon, now.tm_mday,
                          hour, minute, 0, 0, 0, -1))
        seconds = due - time.time()
        if seconds <= 0:
            seconds += 86400  # already passed today -> tomorrow
        return int(seconds)

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

    def _eval_math(self, expression):
        """Safely evaluate a simple arithmetic expression from speech.

        Accepts numbers, + - * / % ( ) and a handful of math functions.
        Returns the result as a readable string, or None on failure.
        """
        expr = expression.lower()
        expr = re.sub(r"\b(percent|percentage)\b of", "/100*", expr)
        expr = expr.replace(" and ", " + ").replace("plus", "+").replace(
            "minus", "-").replace("times", "*").replace("multiplied by", "*").\
            replace("divided by", "/").replace("over", "/").replace(
            "to the power of", "**").replace("squared", "**2").replace(
            "cubed", "**3")
        expr = re.sub(r"[^0-9+\-*/().%\s^]", "", expr)
        expr = expr.replace("^", "**")
        expr = re.sub(r"\s+", "", expr)
        if not expr or not re.search(r"\d", expr):
            return None
        expr = expr.rstrip("=+")
        try:
            tree = ast.parse(expr, mode="eval")
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    return None
                if isinstance(node, ast.Call) and not isinstance(
                        node.func, ast.Name):
                    return None
                if isinstance(node, ast.Name) and node.id not in (
                        "abs", "sqrt", "round", "int", "float"):
                    return None
            result = eval(
                compile(tree, "<voice>", "eval"),
                {"__builtins__": {}},
                {"abs": abs, "round": round, "int": int, "float": float,
                 "sqrt": math.sqrt},
            )
        except Exception:
            return None
        if isinstance(result, float) and result.is_integer():
            return str(int(result))
        if isinstance(result, (int, float)):
            return str(round(result, 6))
        return None

    def _save_note(self, text):
        """Append a quick voice note to voc_notes.txt."""
        try:
            stamp = time.strftime("%Y-%m-%d %H:%M")
            with open(NOTES_FILE, "a", encoding="utf-8") as fh:
                fh.write(f"[{stamp}] {text}\n")
            self._post("log", f"[i] Note saved: {text}")
            self._speak("Noted.")
        except OSError as exc:
            self._post("log", f"[!] Could not save note: {exc}")
            self._speak("I could not save the note.")

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
        from every spoken reply. The rate and voice are read from the config
        on every item, so the Speed slider / Voice dropdown apply live.
        """
        engine = None
        engine_ready = False
        while True:
            text = self._tts_queue.get()
            if text is None:
                break
            try:
                if engine is None:
                    engine = pyttsx3.init()
                    engine.setProperty("volume", 1.0)
                    engine_ready = False
                if not engine_ready:
                    engine.setProperty("rate",
                                       int(self.config.get("voice_rate", 180)))
                    voice_id = self.config.get("voice_id", "")
                    if voice_id:
                        try:
                            engine.setProperty("voice", voice_id)
                        except Exception:
                            pass
                    engine_ready = True
                    self._post_tts_voices(engine)
                else:
                    want_rate = int(self.config.get("voice_rate", 180))
                    if int(engine.getProperty("rate")) != want_rate:
                        engine.setProperty("rate", want_rate)
                    want_voice = self.config.get("voice_id", "")
                    if want_voice:
                        try:
                            if str(engine.getProperty("voice")) != want_voice:
                                engine.setProperty("voice", want_voice)
                        except Exception:
                            pass
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
                engine_ready = False

    def _post_tts_voices(self, engine):
        """Gather SAPI5 voice names + ids and hand them to the GUI dropdown."""
        try:
            voices = list(engine.getProperty("voices"))
            pairs = [(str(v.name).strip(), str(v.id))
                     for v in voices if v and v.name]
        except Exception:
            pairs = []
        if pairs:
            self._post("tts_voices", pairs)

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
    # Auto-start on Windows login
    # ------------------------------------------------------------------
    REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    REG_NAME = "VoiceControlApp"

    def _set_autostart(self, enable):
        """Add or remove a HKCU Run key so the app starts on login."""
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.REG_KEY,
                                 0, winreg.KEY_SET_VALUE)
            if enable:
                python_exe = sys.executable
                script = os.path.abspath(__file__)
                val = f'"{python_exe}" "{script}"'
                winreg.SetValueEx(key, self.REG_NAME, 0, winreg.REG_SZ, val)
            else:
                try:
                    winreg.DeleteValue(key, self.REG_NAME)
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
            self.config["autostart"] = bool(enable)
        except OSError as exc:
            self._post("log", f"[!] Could not change auto-start: {exc}")

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

    def _on_sensitivity_change(self, _val):
        self.config["hand_sensitivity"] = float(self.sensitivity_var.get())
        if self._hand_engine is not None:
            self._hand_engine.set_sensitivity(float(self.sensitivity_var.get()))

    def _on_scroll_speed_change(self, _val):
        self.config["hand_scroll_speed"] = float(self.scroll_speed_var.get())
        if self._hand_engine is not None:
            self._hand_engine.set_scroll_speed(float(self.scroll_speed_var.get()))

    def _on_beep_toggle(self):
        self.config["click_beep"] = bool(self.click_beep_var.get())
        if self.click_beep_var.get():
            self._append_log("[i] Hand click sounds: ON")

    def _on_autostart_toggle(self):
        enable = bool(self.autostart_var.get())
        self._set_autostart(enable)
        state = "ON" if enable else "OFF"
        self._append_log(f"[i] Auto-start on login: {state}")

    def _on_rate_change(self, _val):
        self.config["voice_rate"] = int(self.tts_rate_var.get())

    def _on_voice_change(self, choice):
        """Save the chosen TTS voice id; the TTS thread applies it live."""
        if choice in ("Default", "", None):
            self.config["voice_id"] = ""
        else:
            voice_id = self._voice_id_by_name.get(choice, "")
            self.config["voice_id"] = voice_id
        self._append_log(f"[i] TTS voice: {choice}")

    def _populate_voice_menu(self, voices):
        """Populate the TTS voice dropdown from the pyttsx3 engine.

        voices: list of (name, voice_id) pairs reported by the TTS thread.
        """
        if not voices:
            return
        self._voice_id_by_name = {}
        menu = self.voice_menu["menu"]
        menu.delete(0, "end")
        menu.add_command(label="Default",
                         command=lambda v="Default": self.voice_var.set(v) or
                         self._on_voice_change(v))
        for name, voice_id in voices:
            self._voice_id_by_name[name] = voice_id
            menu.add_command(
                label=name,
                command=lambda v=name: self.voice_var.set(v) or
                self._on_voice_change(v))
        saved = self.config.get("voice_id", "")
        if saved and saved in self._voice_id_by_name:
            self.voice_var.set(saved)

    def _on_hand_size(self, hand_size):
        """Collect live hand-size samples while calibration is active.

        Also shows the live normalised hand size during calibration so the
        user gets instant visual feedback on the "rest vs reach" poses.
        """
        try:
            if self._calibrating:
                phase_name = "REST" if self._calib_phase == 1 else "REACH"
                self.hand_size_var.set(
                    f"size {hand_size:.2f} ({phase_name})")
            elif hand_size > 0:
                self.hand_size_var.set(f"size {hand_size:.2f}")
        except (tk.TclError, AttributeError):
            pass
        if self._calibrating:
            self._calib_samples.append(hand_size)
            self._calib_target -= 1
            if self._calib_target <= 0:
                if self._calib_phase == 1:
                    self._calib_rest = list(self._calib_samples)
                    self._calib_phase = 2
                    self._calib_samples = []
                    self._calib_target = 12
                    self.root.after(1600, self._calibrate_finish)
                    self._append_log("[CALIB] Now REACH toward your screen "
                                     "and HOLD for 2 seconds...")
                elif self._calib_phase == 2:
                    self._calibrate_finish()

    def _calibrate_reach(self):
        """Two-phase calibration: rest-size, then reach-size."""
        if self._calibrating:
            return
        self._calibrating = True
        self._calib_phase = 1
        self._calib_samples = []
        self._calib_target = 12
        self.calib_btn.config(text="CALIBRATING...", state="disabled")
        self._append_log("[CALIB] Phase 1: put your hand in a RESTING "
                         "position (e.g. on your lap). Collecting...")
        self.root.after(2500, self._calibrate_check)

    def _calibrate_check(self):
        if not self._calibrating or self._calib_phase != 1:
            return
        if self._calib_samples:
            self._calib_rest = list(self._calib_samples)
            self._calib_phase = 2
            self._calib_samples = []
            self._calib_target = 12
            self._append_log("[CALIB] Phase 2: REACH toward the screen "
                             "and HOLD for 2 seconds...")
            self.root.after(2500, self._calibrate_finish)
        else:
            self._append_log("[CALIB] No hand detected. Start the hand "
                             "cursor first, then try again.")
            self._calibrate_done()

    def _calibrate_finish(self):
        if not self._calibrating:
            return
        rest_samples = [s for s in self._calib_rest if s < 0.35]
        reach_samples = [s for s in self._calib_samples if s >= 0.25]
        if len(rest_samples) >= 3 and len(reach_samples) >= 3:
            rest_median = sorted(rest_samples)[len(rest_samples)//2]
            reach_max = max(reach_samples)
            arm = max(0.22, min(0.80, reach_max * 0.72))
            disarm = max(0.10, min(arm - 0.06, rest_median * 1.25))
            self.config["hand_arm_size"] = arm
            self.config["hand_disarm_size"] = disarm
            if self._hand_engine is not None:
                self._hand_engine.set_reach_thresholds(arm, disarm)
            self._append_log(
                f"[CALIB] Done. ARM={arm:.2f}  DISARM={disarm:.2f}. "
                "Tuning persisted for next launch.")
        else:
            self._append_log("[CALIB] Could not detect enough variation. "
                             "Repeat and hold each pose more steadily.")
        self._calibrate_done()

    def _calibrate_done(self):
        self._calibrating = False
        try:
            self.hand_size_var.set("")  # stop showing the live size
        except (tk.TclError, AttributeError):
            pass
        self._calib_phase = 0
        self._calib_samples = []
        self._calib_rest = []
        self._calib_target = 0
        self.calib_btn.config(text="CALIBRATE REACH", state="normal")

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
        arm = self.config.get("hand_arm_size")
        disarm = self.config.get("hand_disarm_size")
        try:
            engine = HandCursorEngine(
                camera_index=int(self.config.get("camera_index", 0)),
                sensitivity=float(self.config.get("hand_sensitivity", 1.0)),
                scroll_speed=float(self.config.get("hand_scroll_speed", 1.0)),
                emit_preview=bool(self.preview_var.get() and PIL_OK),
                on_event=self._hand_event,
                on_preview=self._hand_preview,
                arm_size=arm if arm else None,
                disarm_size=disarm if disarm else None,
            )
        except Exception as exc:
            self._post("log", f"[!] Hand cursor failed to start: {exc}")
            return
        self._hand_engine = engine
        engine.start()
        if bool(self.config.get("touchpad_mode", False)):
            engine.set_mode("touchpad")
        elif bool(self.config.get("macro_mode", False)):
            engine.set_mode("macros")

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
            self.hand_led.config(bg="#3a3f45" if not running else "#238636")
            self.hand_btn.config(
                state="disabled" if running else "normal")
            self.hand_stop_btn.config(
                state="normal" if running else "disabled")
        except tk.TclError:
            pass  # window closing

    def _set_hand_led(self, text):
        """Colour the status LED by the recognised gesture (GUI thread)."""
        upper = (text or "").upper()
        try:
            if "CLICK" in upper or "DRAG" in upper:
                color = "#f85149"          # red = mouse button active
            elif "SCROLL" in upper:
                color = "#d29922"          # amber = scrolling
            elif "MOVE" in upper:
                color = "#238636"          # green = moving pointer
            else:
                color = "#3a3f45"          # grey = no gesture
            self.hand_led.config(bg=color)
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
    # Gesture trainer window
    # ------------------------------------------------------------------
    # DRAW-MACRO mode (Phase D): trace a shape in the air to act.
    # ------------------------------------------------------------------
    def _paint_macro_btn(self):
        on = self._macro_mode_var.get()
        self.macro_btn.config(
            text="DRAW MACROS: ON" if on else "DRAW MACROS",
            fg=("#54aeff" if on else MUTED))

    def _toggle_macro_mode(self, enable=None):
        """Turn the hand engine's driver mode on/off (cursor vs macros)."""
        if enable is not None:
            on = bool(enable)
        else:
            on = not bool(self._macro_mode_var.get())
        if on:
            # Macros and touchpad are mutually exclusive; macros wins.
            if self._touchpad_var.get():
                self._touchpad_var.set(False)
                self.config["touchpad_mode"] = False
                self._paint_touchpad_btn()
        if on == bool(self._macro_mode_var.get()):
            return
        self._macro_mode_var.set(on)
        self.config["macro_mode"] = on
        save_config(self.config)
        self._paint_macro_btn()
        engine = getattr(self, "_hand_engine", None)
        if engine is not None and engine.is_running():
            if on:
                engine.set_training(False)
                if self._train_window is not None:
                    self._close_trainer()
            engine.set_mode("macros" if on else "cursor")
            self._append_log("Draw macros: mode ON - trace a shape in the "
                             "air; the stroke is recognised when the finger "
                             "stops for ~1 s or leaves the frame."
                             if on else "Draw macros: mode OFF - back to "
                             "cursor control.")
        elif on:
            self._append_log("Draw macros: turn ON the Hand Cursor first "
                             "(Start Hand Cursor, or say 'start hand cursor').")

    def _on_macro(self, shape):
        """Apply a recognised draw-macro: log it and run its action."""
        if not shape:
            self._append_log("Draw macro: shape not recognised - try again. "
                             "Circle, V, check, L, S, Z, W, line, slash.")
            return
        actions = dict(MACRO_DEFAULT_ACTIONS)
        actions.update(self.config.get("macro_actions", {}) or {})
        action = actions.get(shape)
        if action:
            self._append_log(f"Draw macro: {shape} -> {action}")
            threading.Thread(target=self.handle_command,
                             args=(action,), daemon=True).start()
        else:
            self._append_log(f"Draw macro: {shape} (no action assigned)")

    # ------------------------------------------------------------------
    # TOUCHPAD mode (Phase D): hand = relative mouse / trackpad.
    # ------------------------------------------------------------------
    def _paint_touchpad_btn(self):
        on = self._touchpad_var.get()
        self.touchpad_btn.config(
            text="TOUCHPAD: ON" if on else "TOUCHPAD",
            fg=("#54aeff" if on else MUTED))

    def _toggle_touchpad(self, enable=None):
        """Turn touchpad/relative-mouse mode on or off (mutually exclusive
        with draw-macros mode)."""
        if enable is not None:
            on = bool(enable)
        else:
            on = not bool(self._touchpad_var.get())
        if on:
            # Touchpad and macros are mutually exclusive; touchpad wins.
            if self._macro_mode_var.get():
                self._macro_mode_var.set(False)
                self.config["macro_mode"] = False
                self._paint_macro_btn()
        if on == bool(self._touchpad_var.get()):
            return
        on = bool(on)
        self._touchpad_var.set(on)
        self.config["touchpad_mode"] = on
        save_config(self.config)
        self._paint_touchpad_btn()
        engine = getattr(self, "_hand_engine", None)
        if engine is not None and engine.is_running():
            if on:
                engine.set_training(False)
                if self._train_window is not None:
                    self._close_trainer()
            engine.set_mode("touchpad" if on else "cursor")
            self._append_log(
                "Touchpad: mode ON - move your hand like a mouse; pinch "
                "click, peace scroll, 3 fingers right-click, fist drag."
                if on else "Touchpad: mode OFF - back to absolute cursor.")
        elif on:
            self._append_log("Touchpad: turn ON the Hand Cursor first "
                             "(Start Hand Cursor, or say 'start hand cursor').")

    # ------------------------------------------------------------------
    # Gesture trainer window
    # ------------------------------------------------------------------
    _TRAIN_GESTURES = [
        ("MOVE",   ("index", "open")),
        ("CLICK",  ("pinch",)),
        ("SCROLL", ("peace",)),
        ("RIGHT CLICK", ("three",)),
        ("DRAG",   ("fist",)),
    ]

    def _open_trainer(self):
        """Open or refocus the gesture-trainer Toplevel."""
        if self._train_window is not None:
            try:
                self._train_window.deiconify()
                self._train_window.lift()
                return
            except tk.TclError:
                self._train_window = None
        if (getattr(self, "_hand_engine", None) is None or
                not self._hand_engine.is_running()):
            self.start_hand_cursor()
        self._train_window = win = tk.Toplevel(self.root)
        win.title("Gesture Trainer")
        win.geometry("350x320")
        win.configure(bg=BG_COLOR)
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", self._close_trainer)

        try:
            win.attributes("-topmost", True)
        except Exception:
            pass

        tk.Label(win, text="HOLD EACH POSE FOR ~0.2 s",
                 font=(FONT_NAME, 9, "bold"), bg=BG_COLOR, fg=MUTED
                 ).pack(padx=12, pady=(8, 4))

        # Finger panel row.
        finger_frame = tk.Frame(win, bg=BG_COLOR)
        finger_frame.pack(padx=12, anchor="w")
        tk.Label(finger_frame, text="Fingers:", font=(FONT_NAME, 8),
                 bg=BG_COLOR, fg=MUTED).pack(side=tk.LEFT)
        self._train_fingers = []
        for name in FINGER_NAMES:
            lbl = tk.Label(finger_frame, text=f" {name} ", font=(FONT_NAME, 9),
                           bg=BG_COLOR, fg="#666", width=3)
            lbl.pack(side=tk.LEFT, padx=1)
            self._train_fingers.append(lbl)

        self._train_armed_var = tk.StringVar(value="Cursor armed: --")
        tk.Label(win, textvariable=self._train_armed_var,
                 font=(FONT_NAME, 8), bg=BG_COLOR, fg=MUTED,
                 anchor="w").pack(fill=tk.X, padx=12, pady=(6, 2))

        # Gesture rows.
        rows_frame = tk.Frame(win, bg=BG_COLOR)
        rows_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(4, 0))

        self._train_rows = {}
        for name, _ in self._TRAIN_GESTURES:
            row = tk.Frame(rows_frame, bg=SUBTLE_BG)
            row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=f"  {name}", font=(FONT_NAME, 9, "bold"),
                     bg=SUBTLE_BG, fg="#e6edf3", width=12, anchor="w"
                     ).pack(side=tk.LEFT)
            dot = tk.Label(row, text="  WAIT  ", font=(FONT_NAME, 8),
                           bg="#21262d", fg=MUTED)
            dot.pack(side=tk.LEFT, padx=(6, 0))
            self._train_rows[name] = dot

        # Quit button.
        tk.Button(win, text="QUIT TRAINER", font=(FONT_NAME, 8, "bold"),
                  bg="#30363d", fg=MUTED, activebackground="#484f58",
                  activeforeground=FG_COLOR, relief=tk.FLAT,
                  cursor="hand2", command=self._close_trainer,
                  ).pack(pady=(8, 8))

        # Enable training mode in the engine.
        engine = getattr(self, "_hand_engine", None)
        if engine is not None and engine.is_running():
            engine.set_training(True)
            self._append_log("[i] Gesture trainer open - mouse paused.")
        else:
            self._append_log("[!] Start the hand cursor first, then "
                             "re-open the trainer.")

    def _close_trainer(self):
        """Close trainer window and re-enable normal mouse control."""
        engine = getattr(self, "_hand_engine", None)
        if engine is not None:
            try:
                engine.set_training(False)
            except Exception:
                pass
        if self._train_window is not None:
            try:
                self._train_window.destroy()
            except tk.TclError:
                pass
            self._train_window = None
            self._train_rows.clear()
        self._append_log("[i] Gesture trainer closed.")

    def _on_train_update(self, data):
        """Handle a `train` event from the engine (GUI thread)."""
        if self._train_window is None:
            return
        raw = data.get("raw", "")
        stable = data.get("stable", False)
        fingers = data.get("fingers", [])
        size = data.get("size", 0)
        armed = data.get("armed", False)

        # Update gesture rows.
        for name, poses in self._TRAIN_GESTURES:
            row = self._train_rows.get(name)
            if row is None:
                continue
            match = raw in poses
            if match:
                row.config(text=" PASS " if stable else " PROVING ",
                           bg="#238636" if stable else "#1f6feb")
            else:
                row.config(text="  WAIT  ", bg="#21262d")

        # Update finger panel.
        for i, lbl in enumerate(self._train_fingers):
            ext = fingers[i] if i < len(fingers) else False
            lbl.config(bg="#238636" if ext else "#21262d",
                       fg="#e6edf3" if ext else "#666")

        # Update armed + size.
        arm_txt = "YES" if armed else "NO"
        self._train_armed_var.set(
            f"Cursor armed: {arm_txt}   |   size: {size:.3f}")

    # ------------------------------------------------------------------
    # System tray icon + minimize-to-tray + global hotkey
    # ------------------------------------------------------------------
    def _start_tray(self):
        """Create the tray icon and register the restore hotkey (GUI thread).

        Optional: if pystray/keyboard are missing, or a configuration flag
        is off, the app simply keeps its old close-to-quit behaviour.
        """
        if not TRAY_OK or not self.config.get("minimize_to_tray", True):
            return
        try:
            icon_img = self._tray_image()
            menu = pystray.Menu(
                pystray.MenuItem("Show / Hide window", self._tray_toggle),
                pystray.MenuItem("Quit Voice Control", self._tray_quit),
            )
            self._tray_icon = pystray.Icon(
                "voice-control", icon_img, "Voice Control", menu)
            self._tray_icon.run_detached()
        except Exception as exc:
            self._append_log(f"[i] Tray icon unavailable: {exc}")
            self._tray_icon = None
            return

        hotkey = self.config.get("tray_hotkey", "").strip()
        if hotkey and KEYBOARD_OK:
            try:
                self._tray_hotkey = keyboard.add_hotkey(
                    hotkey, self._hotkey_restore)
                self._append_log(f"[i] Minimized to tray. {hotkey} restores "
                                 "the window; tray menu Quit really exits.")
            except Exception as exc:
                self._append_log(f"[i] Hotkey '{hotkey}' unavailable: {exc}")

    def _tray_image(self):
        """Draw a small microphone-style icon for the tray.

        Uses pure PIL so no image assets are needed.
        """
        if not PIL_OK or PILImage is None or ImageDraw is None:
            return None
        img = PILImage.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle(
            (8, 8, 56, 56), radius=14, fill=(13, 17, 23, 255),
            outline=(88, 166, 255, 255), width=4)
        draw.rectangle((24, 20, 40, 38), fill=(88, 166, 255, 255))
        draw.rounded_rectangle((14, 28, 50, 40), radius=5,
                               fill=(88, 166, 255, 255))
        draw.rectangle((27, 42, 37, 48), fill=(88, 166, 255, 255))
        draw.rounded_rectangle((20, 48, 44, 56), radius=4,
                               fill=(88, 166, 255, 255))
        return img

    def _hotkey_restore(self):
        """Global-hotkey callback (keyboard thread) - hop to the GUI."""
        try:
            self.root.after(0, self._restore_window)
        except tk.TclError:
            pass

    def _restore_window(self):
        """Show and focus the main window (called from tray/hotkey)."""
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.state("normal")
            self.root.attributes("-topmost", True)
            self.root.after(120, lambda: self.root.attributes("-topmost",
                                                              False))
        except tk.TclError:
            pass

    def _tray_toggle(self, icon=None, item=None):
        """Tray menu: show/hide the main window."""
        try:
            if self.root.state() == "withdrawn":
                self._restore_window()
            else:
                self.root.withdraw()
        except tk.TclError:
            pass

    def _tray_quit(self, icon=None, item=None):
        """Tray menu: really quit the app."""
        self._post("quit")

    def _on_close_request(self):
        """The X button: hide to tray (once) instead of quitting outright."""
        if self._closing:
            return
        if (TRAY_OK and self.config.get("minimize_to_tray", True) and
                self._tray_icon is not None):
            try:
                self.root.withdraw()
                if not self._tray_hint_shown:
                    self._tray_hint_shown = True
                    self._append_log(
                        "[i] Minimized to tray - click the tray icon or "
                        f"press {self.config.get('tray_hotkey', '')} to "
                        "restore. Tray menu Quit really exits.")
                return
            except tk.TclError:
                pass
        self.on_close()

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    def on_close(self):
        """Clean up threads and close the window (real shutdown)."""
        if self._closing:
            return
        self._closing = True
        self._listening_event.clear()
        with self._timers_lock:
            self._timers = []
        save_config(self.config)
        # Close trainer and disable training before stopping the engine.
        if self._train_window is not None:
            engine = getattr(self, "_hand_engine", None)
            if engine is not None:
                try:
                    engine.set_training(False)
                except Exception:
                    pass
            try:
                self._train_window.destroy()
            except tk.TclError:
                pass
            self._train_window = None
        if getattr(self, "_hand_engine", None) is not None:
            try:
                self._hand_engine.stop()
            except Exception:
                pass
        # Stop the tray icon and unregister the global hotkey.
        if self._tray_hotkey is not None and KEYBOARD_OK:
            try:
                keyboard.remove_hotkey(self._tray_hotkey)
            except Exception:
                pass
            self._tray_hotkey = None
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
            self._tray_icon = None
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