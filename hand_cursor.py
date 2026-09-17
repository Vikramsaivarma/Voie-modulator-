"""
===============================================================================
 hand_cursor.py
 Hand-gesture "air cursor / touch" engine for the Voice Control app.

 The webcam feed is read in a background daemon thread and MediaPipe Hands
 finds one hand. The user controls the mouse pointer with gestures:

     Index finger up (or open hand) ... move the cursor
     Pinch (thumb+index) ....... left click; hold it = drag
     Peace sign (idx+middle) ... scroll (move the hand up / down)
     3 fingers (idx+mid+ring) .. right click / right-drag
     Fist ...................... hold the left button (drag / select)

     A 5-finger panel on the preview overlay shows whether each finger
     (thumb, index, middle, ring, pinky) is currently extended.

 DRAW-MACRO mode (v1.6)
 ----------------------
 When the driver mode is set to "macros", no mouse actions are performed.
 Instead the index fingertip is tracked and a small $1-style recognizer
 classifies the drawn stroke (circle, V, check, L, S, Z, W, line, slash)
 and emits a ``('macro', name)`` event for the app to act on.

 TOUCHPAD mode (v1.7)
 --------------------
 Driver mode "touchpad" turns the hand into a relative mouse / laptop
 trackpad: the pointer follows accumulated hand-center deltas (not the
 absolute hand position), gestures stay identical (pinch = click, peace =
 scroll, 3 fingers = right click, fist = drag), and the reach gate is
 skipped because a trackpad must respond whenever the hand is in frame.

 CAMERA LIFECYCLE FIXES (v1.7.1)
 --------------------------------
 * stop() now force-releases the capture object, so the webcam LED turns
   off even if a read is blocked, and the engine can be restarted instead
   of leaving a ghost thread holding the device.
 * start() reaps any still-alive engine thread before opening the camera
   again, so two threads never lock the same webcam.
 * _run() releases the capture when the camera fails to open, tolerates a
   few transient bad reads before bailing out, and stops promptly.
 * The camera is requested at 640x480 with a 1-frame buffer for lower
   latency; inference still runs on a 320-wide downscale.

 GESTURE-SAFETY (v1.8)
 ---------------------
 * The reach-gate disarm branch and the hand-loss branch now reset the
   gesture state (`_mode`, `_candidate`, `_pinch_engaged`), so a reappearing
   hand is re-verified for `STABLE_FRAMES` before it can click again - no
   phantom click on the first un-debounced frame after re-arming.
 * `_press_button()` refuses to press while the loop is stopped or the
   gesture trainer is active, so a mid-frame stop/training switch can never
   leave a mouse button held.
 * Macro mode no longer echoes a bogus ``('macro', None)`` every second:
   after a stroke is committed a short cooldown window opens and the last
   fingertip position is kept, so a resting hand cannot re-trigger the
   dwell finalizer, and interrupted strokes are discarded on disarm.
 * `start()`/`stop()` are serialised on a lifecycle lock, so a concurrent
   stop cannot race the thread creation, and a zombie thread that survives
   a stop keeps its reference so the next `start()` always reaps it.
* Every `_run()` exit path (camera open failure, model load failure, read
    failure, stop) now reaches a single `finally` that releases the capture
    and emits ``'stopped'`` - the app can never be stuck in "starting".

 TWO-HAND MODE (v1.9)
 --------------------
 With `two_hand=True` MediaPipe is asked for up to two hands. The hand
 NEAREST the camera (largest normalised size) is the pointer hand and works
 exactly as before; the second hand becomes a MODIFIER and only ever holds
 a keyboard key, it never moves the cursor and never triggers mouse buttons:

     Open hand (4+ fingers) .... hold CTRL
     Fist ...................... hold SHIFT
     Peace sign ............... hold ALT
     3 fingers ................ hold WIN key

 The pose->key mapping can be remapped via the ``mod_keys`` constructor
 argument (or ``set_mod_keys``) - a dict like ``{"fist": "shift"}`` is
 merged over the defaults, and ``.two_hand_keys`` in the app config lets
 users override it without code.

 The modifier pose must be stable for STABLE_FRAMES before the key goes
 down (same anti-accident debounce as gestures), a pose change re-keypresses
 cleanly (old key released first), and the key is always released when the
 second hand leaves the frame, the pointer hand disarms, training starts,
 or the engine stops - a stuck modifier key is impossible. With a single
 hand in view the behaviour is byte-for-byte identical to v1.8.

 ANTI-ACCIDENT DESIGN (v1.2)
 ---------------------------
 A pose is only trusted after it has been observed for several consecutive
 frames (`STABLE_FRAMES`), so a momentary wobble or a resting hand cannot
 trigger a click. The pinch uses hysteresis (tight ENGAGE / RELEASE ratio),
 and any ambiguous pose (e.g. a single middle or ring finger, a thumbs-up,
 several fingers up but not a recognised sign) falls back to ordinary MOVE -
 it can never grab the mouse button by mistake. Every mode change is
 debounced, so the engine never "does things on its own".

 The engine NEVER touches the tkinter GUI directly. All GUI-bound events are
 forwarded through the `on_event(kind, data)` callback (normally the app
 pushes them into its thread-safe queue). Preview frames are forwarded, when
 enabled, through `on_preview(frame)` so the app can embed the camera view.

 USAGE
 -----
     engine = HandCursorEngine(on_event=handle, on_preview=handle_frame)
     engine.start()
     engine.stop()

 VERSION   : 1.9.1
==============================================================================
"""

import math
import threading
import time

# Third-party imports are guarded so the host app can show a friendly message
# instead of crashing when the gesture packages are not installed yet.
try:
    import cv2
    import mediapipe as mp
    import pyautogui
    pyautogui.FAILSAFE = False
    mp_drawing = mp.solutions.drawing_utils
    mp_hands = mp.solutions.hands
    HAND_DEPS_OK = True
except ImportError:
    HAND_DEPS_OK = False
    cv2 = mp = pyautogui = mp_drawing = mp_hands = None

try:
    from PIL import Image as PILImage
    PIL_EMBED_OK = True
except ImportError:
    PIL_EMBED_OK = False
    PILImage = None

# MediaPipe single-hand landmark indices used by the gesture logic.
WRIST        = 0
THUMB_MCP    = 2
THUMB_TIP    = 4
INDEX_MCP    = 5
INDEX_PIP    = 6
INDEX_TIP    = 8
MIDDLE_MCP   = 9
MIDDLE_PIP   = 10
MIDDLE_TIP   = 12
RING_PIP     = 14
RING_TIP     = 16
PINKY_PIP    = 18
PINKY_TIP    = 20

# Finger labels (display order: thumb, index, middle, ring, pinky).
FINGER_NAMES = ("T", "I", "M", "R", "P")

# Horizontal multiplier applied to hand movement when scrolling.
SCROLL_GAIN  = 0.05
# Pinch recognition with HYSTERESIS: it must get closer than PINCH_ENGAGE before
# a click/drag engages, and may stay engaged until it opens wider than RELEASE.
# This prevents a resting thumb+index pair from flickering into a click.
PINCH_ENGAGE = 0.28
PINCH_RELEASE = 0.55
# Minimum thumb-to-index distance (fraction of hand size) counted as "extended"
# when we decide if the thumb is raised.
THUMB_EXT_RATIO = 0.40
# A pose must be seen for this many consecutive frames before it takes effect.
# With ~20-30 fps that is only ~0.15-0.2 s, but it kills one-frame flickers.
STABLE_FRAMES = 5
# "Reach toward the screen" arming. The cursor is only controlled while your
# hand is CLOSE to the camera (= towards you = towards the screen). Hand size
# is measured in normalised image units (0..1), so a resting hand in front of
# you/holding the laptop stays small and is IGNORED. It must grow bigger than
# ARM_SIZE to engage, and shrinks below DISARM_SIZE to disengage.
ARM_SIZE       = 0.30
DISARM_SIZE    = 0.20
# Cursor smoothing factor (0..1). Lower = steadier but lazier.
SMOOTHING    = 0.45
# Frames per second cap for the inference loop.
MAX_FPS      = 30
# Preview frames per second shipped to the GUI.
PREVIEW_FPS  = 15
# State-line updates per second shown in the GUI status label.
STATE_FPS    = 8
# Consecutive camera read failures before the engine gives up and reports
# an error (a single glitch should not kill the session, but a dead/busy
# webcam must be detected quickly and the device released).
BAD_READ_LIMIT = 30

# ----------------------------------------------------------------------
# DRAW-MACRO mode ("draw a shape in the air to trigger an action").
# A small $1-style single-stroke recognizer normalises the fingertip path
# (resample -> offset to origin -> scale into a 0..1 box) and matches it
# against the templates with a cosine-distance threshold.
# ----------------------------------------------------------------------
MACRO_TEMPLATES = {  # name -> list of (x, y) in a 0..1 box (normalised)
    "circle": [(0.5 + 0.45 * math.cos(-math.pi / 2 + 2 * math.pi * k / 16),
                0.5 + 0.45 * math.sin(-math.pi / 2 + 2 * math.pi * k / 16))
               for k in range(16)],
    "v":      [(0.2, 0.10), (0.3, 0.35), (0.5, 0.90),
               (0.65, 0.45), (0.80, 0.10)],
    "check":  [(0.20, 0.75), (0.35, 0.45), (0.50, 0.25), (0.80, 0.10)],
    "l":      [(0.25, 0.10), (0.25, 0.90), (0.80, 0.90)],
    "s":      [(0.80, 0.15), (0.25, 0.35), (0.80, 0.55), (0.25, 0.85)],
    "z":      [(0.20, 0.15), (0.80, 0.15), (0.20, 0.85), (0.80, 0.85)],
    "w":      [(0.10, 0.15), (0.30, 0.85), (0.50, 0.30),
               (0.70, 0.85), (0.90, 0.15)],
    "line":   [(0.10, 0.50), (0.50, 0.50), (0.90, 0.50)],
    "slash":  [(0.15, 0.85), (0.50, 0.50), (0.85, 0.15)],
}

# Default actions for each recognised shape (sent to the app, which runs
# them like a voice command). Users can override via macro_actions config.
MACRO_DEFAULT_ACTIONS = {
    "circle": "lock screen",
    "v":      "new tab",
    "check":  "copy",
    "l":      "minimize",
    "s":      "open settings",
    "z":      "close tab",
    "w":      "play or pause",
    "line":   "mute",
    "slash":  "maximize",
}

# Drawing pipeline numbers: minimum points to consider a stroke, minimum
# travel between stored samples (normalised units), and how long the finger
# must stay still before the stroke is "committed" (seconds).
MACRO_MIN_POINTS = 12
MACRO_SAMPLE_GAP = 0.025
MACRO_COMMIT_TIME = 1.0
# Match below this average per-point distance wins; otherwise "unknown".
MACRO_MATCH_MAX = 0.22

# ----------------------------------------------------------------------
# TOUCHPAD mode: the hand is used like a laptop trackpad / mouse.
# Cursor = accumulated hand-center DELTA (relative motion), not the
# absolute position, so it feels like a mouse.
# ----------------------------------------------------------------------
TOUCHPAD_GAIN = 2.5         # delta fraction -> screen-fraction multiplier
TOUCHPAD_SMOOTH = 0.35      # EMA weight applied to the per-frame delta
TOUCHPAD_NOISE = 0.0015     # ignore deltas below this (tracking jitter)
TOUCHPAD_MAX_STEP = 0.04    # clamp single-frame jumps (normalised units)


def _norm_dist(a, b):
    return math.hypot(a.x - b.x, a.y - b.y)


# ----------------------------------------------------------------------
# $1-style stroke helpers for DRAW-MACRO mode. Paths are plain (x, y)
# tuples in normalised image space (0..1).
# ----------------------------------------------------------------------
def _macro_path_len(path):
    return sum(math.hypot(path[i][0] - path[i - 1][0],
                          path[i][1] - path[i - 1][1])
               for i in range(1, len(path)))


def _macro_resample(path, n=32):
    """Evenly re-space a stroke so point count does not affect matching."""
    if len(path) < 2:
        return list(path)
    total = _macro_path_len(path)
    if total <= 1e-9:
        return [path[0]] * n
    step = total / (n - 1)
    out = [path[0]]
    d = 0.0
    i = 1
    while i < len(path) and len(out) < n:
        seg = math.hypot(path[i][0] - path[i - 1][0],
                         path[i][1] - path[i - 1][1])
        if seg <= 1e-9:
            i += 1
            continue
        if d + seg >= step:
            t = (step - d) / seg
            x = path[i - 1][0] + (path[i][0] - path[i - 1][0]) * t
            y = path[i - 1][1] + (path[i][1] - path[i - 1][1]) * t
            out.append((x, y))
            path = path[:i] + [(x, y)] + path[i:]
            d = 0.0
        else:
            d += seg
        i += 1
    while len(out) < n:
        out.append(out[-1])
    return out[:n]


def _macro_normalize(path):
    """Translate to the origin then scale into a 0..1 box (aspect kept)."""
    if not path:
        return []
    xs = [p[0] for p in path]
    ys = [p[1] for p in path]
    minx, miny = min(xs), min(ys)
    span = max(max(xs) - minx, max(ys) - miny)
    if span <= 1e-9:
        return [(0.0, 0.0)] * len(path)
    return [((x - minx) / span, (y - miny) / span) for x, y in path]


def _macro_path_distance(a, b):
    return sum(math.hypot(a[i][0] - b[i][0], a[i][1] - b[i][1])
               for i in range(len(a))) / len(a)


def _recognize_macro(path):
    """Best template name for a stroke, or None if nothing is close enough."""
    if len(path) < 2:
        return None
    norm = _macro_normalize(_macro_resample(path))
    if not norm:
        return None
    best_name, best_d = None, 1e9
    for name, tmpl in MACRO_TEMPLATES.items():
        d = _macro_path_distance(norm, _macro_normalize(_macro_resample(tmpl)))
        if d < best_d:
            best_name, best_d = name, d
    return best_name if best_d <= MACRO_MATCH_MAX else None


def available_cameras(limit=6):
    """Return indexes of webcams that can actually be opened."""
    if not HAND_DEPS_OK:
        return []
    found = []
    for index in range(limit):
        cap = None
        try:
            cap = cv2.VideoCapture(index)
            if cap.isOpened():
                found.append(index)
        except Exception:
            continue
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
    return found


def bgr_frame_to_pil(frame):
    """Convert an OpenCV BGR frame into a PIL image (for the GUI preview)."""
    if not PIL_EMBED_OK or PILImage is None:
        return None
    return PILImage.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


class HandCursorEngine:
    """
    Webcam hand tracker that drives the mouse pointer with gestures.

    The instance is meant to be created on any thread; only `on_event` /
    `on_preview` callbacks are invoked (from the engine thread), and those
    should hand their payloads to a thread-safe queue.
    """

    def __init__(self, camera_index=0, sensitivity=1.0, scroll_speed=1.0,
                 emit_preview=False, on_event=None, on_preview=None,
                 arm_size=None, disarm_size=None, two_hand=True,
                 mod_keys=None):
        if not HAND_DEPS_OK:
            raise RuntimeError("Hand-tracking packages are not installed.")
        self.camera_index = int(camera_index)
        self.sensitivity = float(sensitivity)
        self.scroll_speed = float(scroll_speed)
        self.emit_preview = bool(emit_preview)
        self.on_event = on_event
        self.on_preview = on_preview
        self._arm_size = float(arm_size) if arm_size else ARM_SIZE
        self._disarm_size = float(disarm_size) if disarm_size else DISARM_SIZE
        self.two_hand = bool(two_hand)  # 2nd hand = modifier key
        self.set_mod_keys(mod_keys)

        self._running = threading.Event()
        self._thread = None
        self._cap = None
        self._cap_lock = threading.Lock()  # protects `_cap` across threads
        self._lifecycle_lock = threading.Lock()  # serialises start()/stop()
        self._bad_reads = 0               # consecutive failed camera reads

        # Per-frame gesture state.
        self._cursor = None          # last EMA-smoothed pointer position
        self._button_down = False    # pyautogui left-button state
        self._pressed_at = 0.0       # when the left button was pressed
        self._right_down = False     # pyautogui right-button state
        self._right_pressed_at = 0.0
        self._mode = None            # last recognised gesture name
        self._scroll_base_y = None   # scroll gesture reference Y
        self._scroll_acc = 0.0       # fractional wheel clicks
        self._lost_since = None      # when the hand last disappeared
        self._lost_handled = False   # hand-loss cleanup done for this gap
        self._last_preview = 0.0
        self._last_state = 0.0
        self._screen = (1920, 1080)

        # Anti-accident debounce state.
        self._armed = False          # cursor control armed (hand is near screen)
        self._candidate = None       # pose seen in the current run
        self._candidate_frames = 0   # consecutive frames for that pose
        self._pinch_engaged = False  # hysteresis latch for the pinch
        self._last_size_event = 0.0  # throttle for calibration size events
        self._drag_beeped = False    # left-click beeped at drag start
        self._right_drag_beeped = False  # right-click beeped at drag start
        self.training = False          # gesture trainer mode (no mouse driving)
        self._driver_mode = "cursor"   # "cursor" | "macros" | "touchpad"
        self._macro_pts = []           # stroke points collected during draw
        self._macro_last_pt = None     # last stored (x, y)
        self._macro_commit_ts = 0.0    # when the finger stopped moving
        self._macro_armed = False      # a stroke is actively being collected
        self._tp_center = None         # last hand-center (touchpad relative)
        self._tp_sm = (0.0, 0.0)       # EMA-smoothed per-frame delta

        # Modifier-hand (second hand) state.
        self._mod_pose = None          # pose currently being held by hand 2
        self._mod_frames = 0           # consecutive frames for that pose
        self._mod_key = None           # keyboard key currently held down
        self._mod_lost_since = None    # when hand 2 last disappeared

    # ------------------------------------------------------------------
    # Public control API
    # ------------------------------------------------------------------
    def start(self):
        """Start the capture + inference loop in a daemon thread.

        If a previous engine thread is still alive (a stop that never fully
        finished), it is stopped and reaped first so two threads never open
        the same webcam together.
        """
        with self._lifecycle_lock:
            old = self._thread
            if old is not None:
                if old.is_alive():
                    self._stop_locked()
                    if old.is_alive():
                        raise RuntimeError(
                            "The previous hand-cursor thread did not exit; "
                            "the camera may still be busy.")
                self._thread = None
            self._running.set()
            thread = threading.Thread(
                target=self._run, name="hand-cursor", daemon=True)
            self._thread = thread
            thread.start()

    def stop(self):
        """Stop the loop and hand the webcam back to the OS.

        The capture is force-released so the camera LED turns off even when
        one thread is blocked inside a read, and every thread reference is
        cleared so the engine can be restarted cleanly afterwards.
        """
        with self._lifecycle_lock:
            self._stop_locked()

    def _stop_locked(self):
        """Shared stop logic; the caller must hold the lifecycle lock."""
        self._running.clear()
        self._release_camera()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
            if thread.is_alive():
                # Rearmed backgrounds can leave a device open while a read is
                # blocked; give the loop one more chance, then release again
                # so the pending read fails and the thread unwinds.
                try:
                    time.sleep(0.05)
                except Exception:
                    pass
                self._release_camera()
                thread.join(timeout=1.0)
            if thread.is_alive():
                # A truly wedged thread keeps its reference so start() can
                # reap it later instead of spawning a competing thread.
                self._thread = thread
            else:
                self._thread = None
        self._release_button()
        self._release_button(right=True)

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def _reset_gesture_state(self):
        """Drop every recognised-pose latch so a fresh hand must re-verify.

        Called whenever the gesture stream is interrupted (reach-gate
        disarm, hand loss, mode switch, training toggle) so a reappearing
        hand can never re-enter its previous pose action without going
        through STABLE_FRAMES debouncing again.
        """
        self._mode = None
        self._candidate = None
        self._candidate_frames = 0
        self._pinch_engaged = False
        self._scroll_base_y = None
        self._scroll_acc = 0.0

    def set_emit_preview(self, flag):
        self.emit_preview = bool(flag)

    def set_sensitivity(self, value):
        """Live cursor-movement sensitivity (1.0 = full frame maps to screen)."""
        self.sensitivity = max(0.1, float(value))

    def set_scroll_speed(self, value):
        """Live scroll gain while doing the peace sign."""
        self.scroll_speed = max(0.1, float(value))

    def set_training(self, flag):
        """Enable/disable gesture trainer mode.

        While training, the engine performs NO mouse actions - it only
        classifies the hand and emits `train` events so the GUI can show
        live pass/fail feedback for every gesture.
        """
        self.training = bool(flag)
        self._reset_gesture_state()
        if self.training:
            self._release_button()
            self._release_button(right=True)

    def set_mode(self, mode):
        """Switch driver mode: ``'cursor'`` (default), ``'macros'`` or
        ``'touchpad'``."""
        if mode not in ("cursor", "macros", "touchpad"):
            return
        if mode == self._driver_mode:
            return
        self._driver_mode = mode
        self._reset_gesture_state()
        self._macro_pts.clear()
        self._macro_commit_ts = 0.0
        self._macro_last_pt = None
        self._macro_armed = mode == "macros"
        self._tp_center = None
        self._tp_sm = (0.0, 0.0)
        if mode == "cursor":
            self._send("log", "Mode: CURSOR")
        elif mode == "touchpad":
            self._send("log", "Mode: TOUCHPAD - move the hand like a mouse")
        else:
            self._send("log", "Mode: DRAW MACROS - trace a shape in the air")

    def set_reach_thresholds(self, arm_size, disarm_size):
        """Apply a calibrated reach gate without restarting the engine.

        arm_size must be bigger than disarm_size; both are normalised
        hand-size values (0..1). If the values are impossible, they are
        ignored so the engine never locks itself out.
        """
        arm = float(arm_size)
        disarm = float(disarm_size)
        if 0 < disarm < arm:
            self._arm_size = arm
            self._disarm_size = disarm

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _send(self, kind, data):
        if self.on_event is not None:
            try:
                self.on_event(kind, data)
            except Exception:
                pass

    def _release_camera(self):
        """Close the capture object (thread-safe, idempotent).

        Typically called from `stop()` so the webcam is returned to the OS
        even if the engine thread is blocked on a read.
        """
        with self._cap_lock:
            cap = self._cap
            self._cap = None
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass

    def _release_button(self, right=False):
        """Let go of a mouse button; report a click for short presses."""
        if right:
            if self._right_down:
                self._right_down = False
                self._right_drag_beeped = False
                try:
                    pyautogui.mouseUp(button="right")
                except Exception:
                    pass
                if time.time() - self._right_pressed_at < 0.22:
                    self._send("log", "Right click.")
                    self._send("beep", "right")
            return
        if self._button_down:
            self._button_down = False
            self._drag_beeped = False
            try:
                pyautogui.mouseUp()
            except Exception:
                pass
            if time.time() - self._pressed_at < 0.22:
                self._send("log", "Click.")
                self._send("beep", "left")

    def _press_button(self, right=False):
        if not self._running.is_set() or self.training:
            return
        if right:
            if not self._right_down:
                self._right_down = True
                self._right_pressed_at = time.time()
                try:
                    pyautogui.mouseDown(button="right")
                except Exception:
                    pass
            return
        if not self._button_down:
            self._button_down = True
            self._pressed_at = time.time()
            try:
                pyautogui.mouseDown()
            except Exception:
                pass

    def _to_screen(self, tip):
        """Map a normalised fingertip to real screen pixels, centred.

        Sensitivity 1.0 maps the whole camera frame onto the ENTIRE screen
        (both axes), so the cursor can reach every corner.
        """
        w, h = self._screen
        s = self.sensitivity
        x = int(w / 2 + (tip.x - 0.5) * w * s)
        y = int(h / 2 + (tip.y - 0.5) * h * s)
        return max(0, min(x, w - 1)), max(0, min(y, h - 1))

    def _touchpad_next(self, lm):
        """Relative cursor step: hand-center delta is added to the cursor.

        Unlike absolute-mode, the hand does not map one-to-one onto the
        screen - moving the palm across the frame moves the pointer as if
        you were dragging a finger on a trackpad.
        """
        if self._cursor is None:
            self._cursor = (self._screen[0] // 2, self._screen[1] // 2)
        w = lm[WRIST]
        mc = lm[MIDDLE_MCP]
        center = ((w.x + mc.x) / 2.0, (w.y + mc.y) / 2.0)
        p0 = self._tp_center
        if p0 is None:
            self._tp_center = center
            return self._cursor
        dx = center[0] - p0[0]
        dy = center[1] - p0[1]
        self._tp_center = center
        if abs(dx) < TOUCHPAD_NOISE and abs(dy) < TOUCHPAD_NOISE:
            return self._cursor
        dx = max(-TOUCHPAD_MAX_STEP, min(dx, TOUCHPAD_MAX_STEP))
        dy = max(-TOUCHPAD_MAX_STEP, min(dy, TOUCHPAD_MAX_STEP))
        # EMA on the delta so webcam jitter does not make the pointer dance.
        sx = self._tp_sm[0] + TOUCHPAD_SMOOTH * (dx - self._tp_sm[0])
        sy = self._tp_sm[1] + TOUCHPAD_SMOOTH * (dy - self._tp_sm[1])
        self._tp_sm = (sx, sy)
        gain = self.sensitivity * TOUCHPAD_GAIN
        nx = int(self._cursor[0] + sx * self._screen[0] * gain)
        ny = int(self._cursor[1] + sy * self._screen[1] * gain)
        w_b, h_b = self._screen
        return max(0, min(nx, w_b - 1)), max(0, min(ny, h_b - 1))

    def _move_pointer(self, x, y):
        """Move the cursor with EMA smoothing so it does not shake."""
        if self._cursor is None:
            self._cursor = (x, y)
        px, py = self._cursor
        nx = int(px + SMOOTHING * (x - px))
        ny = int(py + SMOOTHING * (y - py))
        self._cursor = (nx, ny)
        try:
            pyautogui.moveTo(nx, ny, duration=0)
        except Exception:
            pass

    def _emit_state(self, text, coords, extra=""):
        if time.time() - self._last_state < 1.0 / STATE_FPS:
            return
        self._last_state = time.time()
        if coords is not None:
            text = f"{text} - ({coords[0]}, {coords[1]})"
        if extra:
            text = f"{text}  {extra}"
        self._send("hand_state", text)

    # ------------------------------------------------------------------
    # Modifier hand (two-hand mode)
    # ------------------------------------------------------------------
    # The second hand only ever holds a keyboard modifier key; it never
    # moves the cursor and never presses a mouse button. Mapping is decided
    # from the raw finger count so it shares no mutable gesture state with
    # the pointer hand (candidate/pinch cannot race between the two hands).
    MOD_OPEN  = "ctrl"
    MOD_FIST  = "shift"
    MOD_PEACE = "alt"
    MOD_THREE = "win"
    # pose name ("open"/"fist"/"peace"/"three") -> keyboard key. Users can
    # remap any pose via mod_keys/set_mod_keys (merged over these defaults).
    DEFAULT_MOD_KEYS = {
        "open": MOD_OPEN, "fist": MOD_FIST,
        "peace": MOD_PEACE, "three": MOD_THREE,
    }

    def set_mod_keys(self, mapping=None):
        """Remap the modifier poses. ``{pose: key}`` entries are merged over
        the defaults; ``None`` restores the defaults."""
        keys = dict(self.DEFAULT_MOD_KEYS)
        if mapping:
            keys.update({k: v for k, v in mapping.items() if v})
        self._mod_keys = keys

    def _modifier_key_for(self, lm):
        """Which modifier key the second hand should hold, or None."""
        n_up = sum(self._fingers_of(lm))
        if n_up >= 4:
            pose = "open"
        elif n_up == 3:
            pose = "three"
        elif n_up == 2:
            pose = "peace"
        elif n_up == 0:
            pose = "fist"
        else:
            return None
        return self._mod_keys.get(pose)

    def _release_modifier(self):
        """Always-safe key release; resets every modifier latch."""
        if self._mod_key is not None:
            try:
                pyautogui.keyUp(self._mod_key)
            except Exception:
                pass
            self._mod_key = None
        self._mod_pose = None
        self._mod_frames = 0
        self._mod_lost_since = None

    def _update_modifier(self, lm):
        """Track the second hand and hold the matching modifier key.

        Called once per frame AFTER the pointer hand was handled, with
        ``lm=None`` when only one (or no) hand is in the frame.
        """
        if not self.two_hand or self.training:
            self._release_modifier()
            return
        # The modifier is meaningless without an active pointer hand.
        if self._driver_mode not in ("touchpad", "macros") and not self._armed:
            self._release_modifier()
            return
        if lm is None:
            if self._mod_lost_since is None:
                self._mod_lost_since = time.time()
            elif time.time() - self._mod_lost_since > 0.6:
                self._release_modifier()
            return
        self._mod_lost_since = None

        want = self._modifier_key_for(lm)
        if want == self._mod_pose:
            self._mod_frames += 1
        else:
            self._mod_pose = want
            self._mod_frames = 1
        # Same anti-accident debounce as the pointer gestures.
        target = want if self._mod_frames >= STABLE_FRAMES else None

        if target == self._mod_key:
            return
        if self._mod_key is not None:
            try:
                pyautogui.keyUp(self._mod_key)
            except Exception:
                pass
            self._mod_key = None
        if target is not None:
            try:
                pyautogui.keyDown(target)
            except Exception:
                pass
            self._mod_key = target
            self._send("hand_state", f"MOD KEY HOLD: {target.upper()}")

    # ------------------------------------------------------------------
    # Gesture handling
    # ------------------------------------------------------------------
    def _finger_ext(self, lm, pip_idx, tip_idx):
        """True when a finger is pointing up (tip above its PIP joint)."""
        return lm[tip_idx].y < lm[pip_idx].y

    def _thumb_ext(self, lm):
        """True when the thumb is spread away from the palm."""
        hand_size = max(_norm_dist(lm[WRIST], lm[MIDDLE_MCP]), 1e-4)
        return _norm_dist(lm[THUMB_TIP], lm[INDEX_PIP]) / \
            hand_size > THUMB_EXT_RATIO

    def _hand_size_norm(self, lm):
        """Normalised hand size (0..1) = biggest wrist-to-fingertip span.

        Measured in the SAME units as the landmark coordinates, so it is a
        direct depth proxy: a hand reaching toward the screen becomes big,
        a resting hand stays small.  Used to arm/disarm the cursor.
        """
        tips = (THUMB_TIP, INDEX_TIP, MIDDLE_TIP, RING_TIP, PINKY_TIP)
        return max(_norm_dist(lm[WRIST], lm[i]) for i in tips)

    def _classify(self, lm, fingers, hand_size):
        """Classify the hand pose into a mode string.

        Ambiguous poses (middle/ring/pinky alone, thumbs up, odd combos)
        deliberately fall through to "open" = plain MOVE, so a resting or
        partially folded hand can never grab a mouse button.
        """
        t, idx, mid, ring, pinky = fingers

        pinch_ratio = _norm_dist(lm[THUMB_TIP], lm[INDEX_TIP]) / \
            max(hand_size, 1e-4)
        if self._pinch_engaged:
            if pinch_ratio > PINCH_RELEASE:
                self._pinch_engaged = False
        elif pinch_ratio < PINCH_ENGAGE:
            self._pinch_engaged = True
        if self._pinch_engaged:
            return "pinch"

        if idx and mid and not ring and not pinky:
            return "peace"
        if idx and mid and ring and not pinky:
            return "three"
        if idx and not mid and not ring and not pinky:
            return "index"
        if not t and not idx and not mid and not ring and not pinky:
            return "fist"
        return "open"

    def _arm_if_needed(self, hand_size):
        """Update the reached-for-screen arming latch with hysteresis."""
        if self._armed:
            if hand_size < self._disarm_size:
                self._armed = False
        elif hand_size >= self._arm_size:
            self._armed = True
        return self._armed

    # ------------------------------------------------------------------
    # Finger extraction helper (shared by _handle_landmarks & training)
    # ------------------------------------------------------------------
    def _fingers_of(self, lm):
        """Return a 5-tuple of booleans: thumb, index, middle, ring, pinky
        extended."""
        return [
            self._thumb_ext(lm),
            self._finger_ext(lm, INDEX_PIP, INDEX_TIP),
            self._finger_ext(lm, MIDDLE_PIP, MIDDLE_TIP),
            self._finger_ext(lm, RING_PIP, RING_TIP),
            self._finger_ext(lm, PINKY_PIP, PINKY_TIP),
        ]

    def _handle_landmarks(self, lm):
        """Classify the hand and drive the mouse.

        Returns a tuple `(label, fingers)`:
          * label   - short text shown on the overlay / status bar
          * fingers - list of 5 booleans (thumb, index, middle, ring, pinky)
                      saying whether each finger is currently extended.
        """
        fingers = self._fingers_of(lm)
        n_up = sum(fingers)
        hand_size = self._hand_size_norm(lm)

        # Emit throttled hand-size samples so the app can calibrate the
        # reach gate without owning a frame counter.
        if time.time() - self._last_size_event >= 1.0 / STATE_FPS:
            self._last_size_event = time.time()
            self._send("hand_size", round(hand_size, 3))

        tip = lm[INDEX_TIP]
        if self._driver_mode == "touchpad":
            x, y = self._touchpad_next(lm)
        else:
            x, y = self._to_screen(tip)

        # ---- 1) Reach gate: only control the cursor near the screen --------
        # (touchpad mode skips the gate - it is relative/ms-like on purpose,
        #  so the pointer follows your hand as long as it stays in frame.)
        if self._driver_mode != "touchpad" and not self._arm_if_needed(
                hand_size):
            self._release_button()
            self._release_button(right=True)
            self._reset_gesture_state()
            # An interrupted draw stroke must not merge into the next one.
            self._macro_pts.clear()
            self._macro_last_pt = None
            self._macro_commit_ts = 0.0
            self._macro_armed = False
            self._emit_state("REACH TOWARD SCREEN", None)
            return "REACH TOWARD SCREEN", fingers

        # ---- 2) classify + debounce (STABLE_FRAMES) -------------------------
        candidate = self._classify(lm, fingers, hand_size)
        if candidate == self._candidate:
            self._candidate_frames += 1
        else:
            self._candidate = candidate
            self._candidate_frames = 1

        if (self._candidate_frames >= STABLE_FRAMES and
                candidate != self._mode):
            self._mode = candidate
            self._scroll_base_y = None
            self._scroll_acc = 0.0

        mode = self._mode
        if candidate != mode or mode is None:
            # Still proving the new pose, or it flickered away: do NO click
            # action yet, just follow the fingertip and release any buttons.
            self._release_button()
            self._release_button(right=True)
            self._move_pointer(x, y)
            label = "READY"
            self._emit_state(label, (x, y))
            return label, fingers

        label = None
        if mode == "pinch":
            self._release_button(right=True)
            self._move_pointer(x, y)
            self._press_button()
            held = time.time() - self._pressed_at
            if held < 0.22:
                label = "PINCH = CLICK"
            else:
                label = "PINCH = DRAG"
                if not self._drag_beeped:
                    self._drag_beeped = True
                    self._send("beep", "drag")
        elif mode == "three":
            self._release_button()
            self._move_pointer(x, y)
            self._press_button(right=True)
            held = time.time() - self._right_pressed_at
            if held < 0.22:
                label = "3 FINGERS = RIGHT CLICK"
            else:
                label = "3 FINGERS = RIGHT DRAG"
                if not self._right_drag_beeped:
                    self._right_drag_beeped = True
                    self._send("beep", "right_drag")
            self._emit_state(label, (x, y))
        elif mode == "peace":
            self._release_button()
            self._release_button(right=True)
            if self._scroll_base_y is None:
                self._scroll_base_y = y
                self._scroll_acc = 0.0
            dy = self._scroll_base_y - y
            self._scroll_base_y = y
            self._scroll_acc += dy * SCROLL_GAIN * self.scroll_speed
            clicks = int(self._scroll_acc)
            if clicks:
                self._scroll_acc -= clicks
                try:
                    pyautogui.scroll(max(-30, min(30, clicks)))
                except Exception:
                    pass
            label = "PEACE = SCROLL"
            self._emit_state(label, None)
        elif mode == "fist":
            self._release_button(right=True)
            self._move_pointer(x, y)
            self._press_button()
            label = "FIST = DRAG"
            if not self._drag_beeped:
                self._drag_beeped = True
                self._send("beep", "drag")
            self._emit_state(label, (x, y))
        else:  # index / open hand  ->  move the pointer
            self._release_button()
            self._release_button(right=True)
            self._move_pointer(x, y)
            label = "INDEX = MOVE" if n_up == 1 else \
                    ("OPEN HAND = MOVE" if n_up >= 4 else "MOVE")
            self._emit_state(label, (x, y))

        return label, fingers

    # ------------------------------------------------------------------
    # DRAW-MACRO mode
    # ------------------------------------------------------------------
    def _handle_macro(self, lm):
        """Collect fingertip path while the hand is present; when the finger
        stops moving (or is lost) the stroke is classified and sent as a
        ``('macro', name)`` or ``('macro', None)`` event.
        """
        now = time.time()
        tip = lm[INDEX_TIP]
        pt = (tip.x, tip.y)
        label = "DRAW"
        fingers = [1, 1, 0, 0, 0]  # placeholder shown on finger panel

        if not self._macro_armed:
            # Idle after a commit: wait until the finger clearly moves away
            # from the last anchor before a fresh stroke may start. This is
            # what stops a resting hand from re-finalising a bogus
            # one-point "stroke" every second.
            if self._macro_last_pt is not None and \
                    math.hypot(pt[0] - self._macro_last_pt[0],
                               pt[1] - self._macro_last_pt[1]) \
                    < MACRO_SAMPLE_GAP:
                self._emit_state(label, fingers)
                return label, fingers
            self._macro_armed = True

        moved = (self._macro_last_pt is None or
                 math.hypot(pt[0] - self._macro_last_pt[0],
                            pt[1] - self._macro_last_pt[1])
                 >= MACRO_SAMPLE_GAP)
        if moved:
            self._macro_pts.append(pt)
            self._macro_last_pt = pt
            self._macro_commit_ts = now
        elif self._macro_pts and \
                (now - self._macro_commit_ts) >= MACRO_COMMIT_TIME:
            # Finger held still - check dwell to commit the collected stroke.
            self._finalize_macro()

        # Also commit when the stroke gets suspiciously long.
        if len(self._macro_pts) >= 500:
            self._finalize_macro()

        self._emit_state(label, fingers)
        return label, fingers

    def _finalize_macro(self):
        """Run the recognizer on the accumulated stroke, emit the event."""
        pts = self._macro_pts
        if len(pts) >= MACRO_MIN_POINTS:
            name = _recognize_macro(pts)
            self._send("macro", name)
            self._send("log", f"Draw macro: "
                              f"{name if name else 'shape not recognised'}")
        else:
            self._send("macro", None)
            self._send("log", "Draw macro: stroke too short")
        last = self._macro_last_pt
        self._macro_pts = []
        self._macro_last_pt = last
        self._macro_commit_ts = 0.0
        self._macro_armed = False

    def _training_step(self, lm):
        """Gesture-trainer mode: classify but never move/click the mouse.

        Emits a `train` event with the live pose so the GUI can show
        pass/fail for every gesture. Returns `(label, fingers)` so the
        preview overlay still renders normally.
        """
        fingers = self._fingers_of(lm)
        hand_size = self._hand_size_norm(lm)

        self._release_button()
        self._release_button(right=True)

        candidate = self._classify(lm, fingers, hand_size)
        if candidate == self._candidate:
            self._candidate_frames += 1
        else:
            self._candidate = candidate
            self._candidate_frames = 1
        stable = self._candidate_frames >= STABLE_FRAMES

        self._send("train", {
            "raw": candidate,
            "pose": candidate if stable else None,
            "stable": stable,
            "fingers": list(fingers),
            "size": round(hand_size, 3),
            "armed": self._armed,
        })
        label = ("HOLD " + candidate.upper()) if not stable else \
                candidate.upper()
        self._emit_state(label, None)
        return label, fingers

    def _color_for(self, label):
        """Pick a colour for the on-screen overlay / status LED."""
        label = (label or "").upper()
        if "CLICK" in label or "DRAG" in label:
            return (0, 80, 255)          # red (BGR)
        if "SCROLL" in label:
            return (0, 180, 220)         # amber (BGR)
        if "MOVE" in label:
            return (60, 200, 60)         # green (BGR)
        return (120, 120, 120)           # grey

    def _draw_overlay(self, frame, label, lms=None, fingers=None,
                     mod_lms=None):
        """Draw the hand skeleton(s), finger states and recognised gesture."""
        h, w = frame.shape[:2]

        if lms is not None:
            try:
                mp_drawing.draw_landmarks(
                    frame, lms, mp_hands.HAND_CONNECTIONS,
                    mp_drawing.DrawingSpec(color=(60, 220, 60), thickness=2,
                                           circle_radius=3),
                    mp_drawing.DrawingSpec(color=(0, 210, 255), thickness=2),
                )
            except Exception:
                pass

        # The modifier hand (two-hand mode) is drawn in amber - it never
        # drives the cursor, it only holds a keyboard modifier.
        if mod_lms is not None:
            try:
                mp_drawing.draw_landmarks(
                    frame, mod_lms, mp_hands.HAND_CONNECTIONS,
                    mp_drawing.DrawingSpec(color=(0, 180, 220), thickness=1,
                                           circle_radius=2),
                    mp_drawing.DrawingSpec(color=(40, 120, 160), thickness=1),
                )
            except Exception:
                pass

        # ---- finger-state panel on the right --------------------------------
        if fingers is not None:
            chip_w = 30
            origin_x = w - 5 * chip_w - 14
            for i, extended in enumerate(fingers):
                cx = origin_x + i * chip_w + chip_w // 2
                cy = 18
                fill = (60, 220, 60) if extended else (88, 88, 88)
                cv2.circle(frame, (cx, cy), 9, fill, -1, cv2.LINE_AA)
                cv2.circle(frame, (cx, cy), 9, (10, 14, 16), 1, cv2.LINE_AA)
                cv2.putText(frame, FINGER_NAMES[i], (cx - 5, cy + 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255),
                            1, cv2.LINE_AA)

        color = self._color_for(label)
        text = label or "NO HAND"
        cv2.rectangle(frame, (0, 0), (w, int(h * 0.09)), (10, 14, 16), -1)
        cv2.putText(frame, text, (8, int(h * 0.065)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)
        cv2.putText(frame, f"camera {self.camera_index}"
                           f"  x,y=({self._cursor[0] if self._cursor else 0},"
                           f"{self._cursor[1] if self._cursor else 0})",
                    (8, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (180, 180, 180), 1, cv2.LINE_AA)

    # ------------------------------------------------------------------
    # Main loop (engine thread)
    # ------------------------------------------------------------------
    def _run(self):
        cap = None
        hands = None
        try:
            cap = cv2.VideoCapture(self.camera_index)
            # Lower the acquisition latency: 640x480 is enough for gesture
            # tracking and the inference stage already downscales to 320 wide.
            for prop, value in ((cv2.CAP_PROP_FRAME_WIDTH, 640),
                                (cv2.CAP_PROP_FRAME_HEIGHT, 480),
                                (cv2.CAP_PROP_BUFFERSIZE, 1)):
                try:
                    cap.set(prop, value)
                except Exception:
                    pass
            if not cap.isOpened():
                self._send("error",
                           f"Camera {self.camera_index} could not be opened. "
                           "Check the webcam and Cam selector.")
                return
            with self._cap_lock:
                self._cap = cap
            try:
                self._screen = tuple(int(v) for v in pyautogui.size())
            except Exception:
                pass

            try:
                hands = mp.solutions.hands.Hands(
                    static_image_mode=False,
                    max_num_hands=2 if self.two_hand else 1,
                    min_detection_confidence=0.5,
                    min_tracking_confidence=0.5,
                )
            except Exception as exc:
                self._send("error",
                           f"Hand model failed to load: {exc}")
                return
            self._send("started",
                       f"Hand cursor started on camera {self.camera_index}.")
            self._send("log", "Bring your hand close to the screen to arm the "
                              "cursor. Gestures: index/open=move  pinch=click  "
                              "peace=scroll  three=right click  fist=drag.")
            if self.two_hand:
                self._send("log", "Two-hand mode: the nearest hand drives the "
                                  "cursor, the second hand holds a modifier - "
                                  "open=ctrl  fist=shift  peace=alt  "
                                  "three=win.")

            frame_dt = 1.0 / MAX_FPS
            while self._running.is_set():
                started = time.time()

                try:
                    ok, frame = cap.read()
                except Exception as exc:
                    self._send("error", f"Camera read error: {exc}")
                    break
                if not ok:
                    if not self._running.is_set():
                        break
                    # A single glitch is tolerated; a dead or busy webcam
                    # shows up as many consecutive failures and we bail out.
                    self._bad_reads += 1
                    if self._bad_reads >= BAD_READ_LIMIT:
                        self._send("error",
                                   "The webcam stopped returning frames.")
                        break
                    continue
                self._bad_reads = 0
                if not self._running.is_set():
                    break

                # Mirror the feed so it feels like a mirror (natural).
                try:
                    frame = cv2.flip(frame, 1)
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    # Downscale only for inference -> faster, pointer smooth.
                    small = cv2.resize(
                        rgb, (320, int(rgb.shape[0] *
                                       (320 / max(rgb.shape[1], 1)))))
                    results = hands.process(small)
                except Exception:
                    # Decode failure is treated like a transient bad read.
                    self._bad_reads += 1
                    if self._bad_reads >= BAD_READ_LIMIT:
                        self._send(
                            "error",
                            "The webcam feed could not be decoded.")
                        break
                    continue

                overlay_text = None
                fingers = None
                landmarks = None
                mod_lms = None
                if results.multi_hand_landmarks:
                    self._lost_since = None
                    self._lost_handled = False
                    try:
                        # Pointer hand = the one NEAREST the camera (largest
                        # normalised size); the other hand, when present, only
                        # ever holds a modifier key (two-hand mode).
                        ordered = sorted(
                            results.multi_hand_landmarks,
                            key=lambda h: self._hand_size_norm(h.landmark),
                            reverse=True)
                        hand0 = ordered[0]
                        landmark = hand0.landmark
                        if self.training:
                            overlay_text, fingers = self._training_step(landmark)
                        elif self._driver_mode == "macros":
                            overlay_text, fingers = self._handle_macro(landmark)
                        else:
                            overlay_text, fingers = self._handle_landmarks(landmark)
                        landmarks = hand0
                        if len(ordered) > 1:
                            mod_lms = ordered[1]
                    except Exception as exc:
                        self._send("log", f"[!] Gesture error: {exc}")

                elif self._lost_since is None:
                    self._lost_since = time.time()
                elif time.time() - self._lost_since > 0.6:
                    if self._button_down or self._right_down:
                        self._release_button()
                        self._release_button(right=True)
                    if self._driver_mode == "macros" and self._macro_pts:
                        self._finalize_macro()
                    if not self._lost_handled:
                        self._lost_handled = True
                        self._reset_gesture_state()
                        self._release_modifier()

                # Second hand -> modifier key (also releases when absent).
                self._update_modifier(
                    mod_lms.landmark if mod_lms is not None else None)

                if (self.emit_preview and self.on_preview is not None and
                        time.time() - self._last_preview >= 1.0 / PREVIEW_FPS):
                    self._last_preview = time.time()
                    try:
                        self._draw_overlay(frame, overlay_text,
                                           landmarks, fingers, mod_lms)
                        self.on_preview(frame)
                    except Exception:
                        pass

                elapsed = time.time() - started
                if elapsed < frame_dt:
                    time.sleep(frame_dt - elapsed)
        finally:
            self._release_button()
            self._release_button(right=True)
            self._release_modifier()
            if hands is not None:
                try:
                    hands.close()
                except Exception:
                    pass
            with self._cap_lock:
                if cap is not None and self._cap is cap:
                    self._cap = None
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            self._bad_reads = 0
            self._macro_pts.clear()
            self._macro_armed = False
            self._reset_gesture_state()
            self._send("stopped", "Hand cursor stopped.")