"""
===============================================================================
 hand_cursor.py
 Hand-gesture "air cursor / touch" engine for the Voice Control app.

 The webcam feed is read in a background daemon thread and MediaPipe Hands
 finds one hand. The user controls the mouse pointer with gestures:

     Index finger raised ....... move the cursor
     Pinch (thumb+index) ....... left click; hold it = drag
     Peace sign (idx+middle) ... scroll (move the hand up / down)
     Fist ...................... hold the left button (drag / select)

 The engine NEVER touches the tkinter GUI directly. All GUI-bound events are
 forwarded through the `on_event(kind, data)` callback (normally the app
 pushes them into its thread-safe queue). Preview frames are forwarded, when
 enabled, through `on_preview(frame)` so the app can embed the camera view.

 USAGE
 -----
     engine = HandCursorEngine(on_event=handle, on_preview=handle_frame)
     engine.start()
     engine.stop()

 VERSION   : 1.0.0
===============================================================================
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
    HAND_DEPS_OK = True
except ImportError:
    HAND_DEPS_OK = False
    cv2 = mp = pyautogui = None

try:
    from PIL import Image as PILImage
    PIL_EMBED_OK = True
except ImportError:
    PIL_EMBED_OK = False
    PILImage = None

# MediaPipe single-hand landmark indices used by the gesture logic.
WRIST        = 0
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

# Horizontal multiplier applied to hand movement when scrolling.
SCROLL_GAIN  = 0.6
# How close (as a fraction of hand size) the thumb and index must be before we
# call it a pinch.
PINCH_RATIO  = 0.45
# Cursor smoothing factor (0..1). Lower = steadier but lazier.
SMOOTHING    = 0.45
# Frames per second cap for the inference loop.
MAX_FPS      = 30
# Preview frames per second shipped to the GUI.
PREVIEW_FPS  = 12
# State-line updates per second shown in the GUI status label.
STATE_FPS    = 8


def _norm_dist(a, b):
    return math.hypot(a.x - b.x, a.y - b.y)


def available_cameras(limit=6):
    """Return indexes of webcams that can actually be opened."""
    if not HAND_DEPS_OK:
        return []
    found = []
    for index in range(limit):
        try:
            cap = cv2.VideoCapture(index)
            ok = cap.isOpened()
            cap.release()
            if ok:
                found.append(index)
        except Exception:
            continue
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
                 emit_preview=False, on_event=None, on_preview=None):
        if not HAND_DEPS_OK:
            raise RuntimeError("Hand-tracking packages are not installed.")
        self.camera_index = int(camera_index)
        self.sensitivity = float(sensitivity)
        self.scroll_speed = float(scroll_speed)
        self.emit_preview = bool(emit_preview)
        self.on_event = on_event
        self.on_preview = on_preview

        self._running = threading.Event()
        self._thread = None
        self._cap = None

        # Per-frame gesture state.
        self._cursor = None          # last EMA-smoothed pointer position
        self._button_down = False    # pyautogui left-button state
        self._pressed_at = 0.0       # when the button was pressed
        self._mode = None            # last recognised gesture name
        self._scroll_base_y = None   # scroll gesture reference Y
        self._scroll_acc = 0.0       # fractional wheel clicks
        self._lost_since = None      # when the hand last disappeared
        self._last_preview = 0.0
        self._last_state = 0.0
        self._screen = (1920, 1080)

    # ------------------------------------------------------------------
    # Public control API
    # ------------------------------------------------------------------
    def start(self):
        """Start the capture + inference loop in a daemon thread."""
        if self.is_running():
            return
        self._running.set()
        self._thread = threading.Thread(
            target=self._run, name="hand-cursor", daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the loop, release the camera and the mouse button."""
        self._running.clear()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def set_emit_preview(self, flag):
        self.emit_preview = bool(flag)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _send(self, kind, data):
        if self.on_event is not None:
            try:
                self.on_event(kind, data)
            except Exception:
                pass

    def _release_button(self):
        """Let go of the left button; report a click for short presses."""
        if self._button_down:
            self._button_down = False
            try:
                pyautogui.mouseUp()
            except Exception:
                pass
            if time.time() - self._pressed_at < 0.22:
                self._send("log", "Click.")

    def _press_button(self):
        if not self._button_down:
            self._button_down = True
            self._pressed_at = time.time()
            try:
                pyautogui.mouseDown()
            except Exception:
                pass

    def _to_screen(self, tip):
        """Map a normalised fingertip to real screen pixels, centred."""
        w, h = self._screen
        sx, sy = self.sensitivity, (self.sensitivity * h) / w
        x = int(w / 2 + (tip.x - 0.5) * w * sx)
        y = int(h / 2 + (tip.y - 0.5) * h * sy)
        return max(0, min(x, w - 1)), max(0, min(y, h - 1))

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
    # Gesture handling
    # ------------------------------------------------------------------
    def _finger_ext(self, lm, pip_idx, tip_idx):
        """True when a finger is pointing up (tip above its PIP joint)."""
        return lm[tip_idx].y < lm[pip_idx].y

    def _handle_landmarks(self, lm):
        idx_ext = self._finger_ext(lm, INDEX_PIP, INDEX_TIP)
        mid_ext = self._finger_ext(lm, MIDDLE_PIP, MIDDLE_TIP)
        ring_ext = self._finger_ext(lm, RING_PIP, RING_TIP)
        pinky_ext = self._finger_ext(lm, PINKY_PIP, PINKY_TIP)
        extended = (idx_ext, mid_ext, ring_ext, pinky_ext)

        hand_size = max(_norm_dist(lm[WRIST], lm[MIDDLE_MCP]), 1e-4)
        pinch = _norm_dist(lm[THUMB_TIP], lm[INDEX_TIP]) / hand_size < PINCH_RATIO

        tip = lm[INDEX_TIP]
        x, y = self._to_screen(tip)

        if pinch:
            mode = "pinch"
        elif idx_ext and mid_ext and not ring_ext and not pinky_ext:
            mode = "peace"
        elif idx_ext and not mid_ext and not ring_ext and not pinky_ext:
            mode = "index"
        elif not any(extended):
            mode = "fist"
        else:
            mode = f"{sum(extended)}fingers"

        if mode != self._mode:
            self._mode = mode
            self._scroll_base_y = None
            self._scroll_acc = 0.0

        if mode == "pinch":
            self._move_pointer(x, y)
            self._press_button()
            self._emit_state(
                "PINCH = CLICK" if time.time() - self._pressed_at < 0.22
                else "PINCH = DRAG", None)
        elif mode == "peace":
            self._release_button()
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
                    pyautogui.scroll(clicks)
                except Exception:
                    pass
            self._emit_state("PEACE = SCROLL", None)
        elif mode == "fist":
            self._move_pointer(x, y)
            self._press_button()
            self._emit_state("FIST = DRAG", None)
        else:
            self._release_button()
            if mode == "index":
                self._move_pointer(x, y)
                self._emit_state("INDEX = MOVE", None)
            else:
                self._emit_state(mode.upper(), None)

    # ------------------------------------------------------------------
    # Main loop (engine thread)
    # ------------------------------------------------------------------
    def _run(self):
        cap = cv2.VideoCapture(self.camera_index)
        if not cap.isOpened():
            self._send("error",
                       f"Camera {self.camera_index} could not be opened. "
                       "Check the webcam and Cam selector.")
            return
        self._cap = cap
        try:
            self._screen = tuple(int(v) for v in pyautogui.size())
        except Exception:
            pass

        hands = mp.solutions.hands.Hands(
            static_image_mode=False,
            max_num_hands=1,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self._send("started",
                   f"Hand cursor started on camera {self.camera_index}.")
        self._send("log", "Gestures: index=move  pinch=click  peace=scroll "
                          "fist=drag.")

        frame_dt = 1.0 / MAX_FPS
        try:
            while self._running.is_set():
                started = time.time()

                ok, frame = cap.read()
                if not ok:
                    self._send("error", "The webcam stopped returning frames.")
                    break

                # Mirror the feed so it feels like a mirror (natural).
                frame = cv2.flip(frame, 1)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                results = hands.process(rgb)

                if results.multi_hand_landmarks:
                    self._lost_since = None
                    try:
                        self._handle_landmarks(
                            results.multi_hand_landmarks[0].landmark)
                    except Exception as exc:
                        self._send("log", f"[!] Gesture error: {exc}")

                elif self._lost_since is None:
                    self._lost_since = time.time()
                elif self._button_down and \
                        time.time() - self._lost_since > 0.6:
                    self._release_button()

                if (self.emit_preview and self.on_preview is not None and
                        time.time() - self._last_preview >= 1.0 / PREVIEW_FPS):
                    self._last_preview = time.time()
                    try:
                        self.on_preview(frame)
                    except Exception:
                        pass

                elapsed = time.time() - started
                if elapsed < frame_dt:
                    time.sleep(frame_dt - elapsed)
        finally:
            self._release_button()
            try:
                hands.close()
            except Exception:
                pass
            try:
                cap.release()
            except Exception:
                pass
            self._cap = None
            self._send("stopped", "Hand cursor stopped.")