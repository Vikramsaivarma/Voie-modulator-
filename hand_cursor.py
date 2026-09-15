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

 The engine NEVER touches the tkinter GUI directly. All GUI-bound events are
 forwarded through the `on_event(kind, data)` callback (normally the app
 pushes them into its thread-safe queue). Preview frames are forwarded, when
 enabled, through `on_preview(frame)` so the app can embed the camera view.

 USAGE
 -----
     engine = HandCursorEngine(on_event=handle, on_preview=handle_frame)
     engine.start()
     engine.stop()

 VERSION   : 1.1.0
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
# How close (as a fraction of hand size) the thumb and index must be before we
# call it a pinch.
PINCH_RATIO  = 0.45
# Minimum thumb-to-index distance (fraction of hand size) counted as "extended"
# when we decide if the thumb is raised.
THUMB_EXT_RATIO = 0.40
# Cursor smoothing factor (0..1). Lower = steadier but lazier.
SMOOTHING    = 0.45
# Frames per second cap for the inference loop.
MAX_FPS      = 30
# Preview frames per second shipped to the GUI.
PREVIEW_FPS  = 15
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
        self._pressed_at = 0.0       # when the left button was pressed
        self._right_down = False     # pyautogui right-button state
        self._right_pressed_at = 0.0
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

    def _release_button(self, right=False):
        """Let go of a mouse button; report a click for short presses."""
        if right:
            if self._right_down:
                self._right_down = False
                try:
                    pyautogui.mouseUp(button="right")
                except Exception:
                    pass
                if time.time() - self._right_pressed_at < 0.22:
                    self._send("log", "Right click.")
            return
        if self._button_down:
            self._button_down = False
            try:
                pyautogui.mouseUp()
            except Exception:
                pass
            if time.time() - self._pressed_at < 0.22:
                self._send("log", "Click.")

    def _press_button(self, right=False):
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

    def _thumb_ext(self, lm):
        """True when the thumb is spread away from the palm."""
        hand_size = max(_norm_dist(lm[WRIST], lm[MIDDLE_MCP]), 1e-4)
        return _norm_dist(lm[THUMB_TIP], lm[INDEX_PIP]) / \
            hand_size > THUMB_EXT_RATIO

    def _handle_landmarks(self, lm):
        """Classify the hand and drive the mouse.

        Returns a tuple `(label, fingers)`:
          * label   - short text shown on the overlay / status bar
          * fingers - list of 5 booleans (thumb, index, middle, ring, pinky)
                      saying whether each finger is currently extended.
        """
        fingers = [
            self._thumb_ext(lm),
            self._finger_ext(lm, INDEX_PIP, INDEX_TIP),
            self._finger_ext(lm, MIDDLE_PIP, MIDDLE_TIP),
            self._finger_ext(lm, RING_PIP, RING_TIP),
            self._finger_ext(lm, PINKY_PIP, PINKY_TIP),
        ]
        t_ext, idx_ext, mid_ext, ring_ext, pinky_ext = fingers
        n_up = sum(fingers)

        hand_size = max(_norm_dist(lm[WRIST], lm[MIDDLE_MCP]), 1e-4)
        pinch = _norm_dist(lm[THUMB_TIP], lm[INDEX_TIP]) / hand_size < PINCH_RATIO

        tip = lm[INDEX_TIP]
        x, y = self._to_screen(tip)

        # ---- pick a mode --------------------------------------------------
        if pinch:
            mode = "pinch"
        elif idx_ext and mid_ext and not ring_ext and not pinky_ext:
            mode = "peace"
        elif idx_ext and mid_ext and ring_ext and not pinky_ext:
            mode = "three"
        elif idx_ext and not mid_ext and not ring_ext and not pinky_ext:
            mode = "index"
        elif n_up == 0:
            mode = "fist"
        else:
            mode = "open"

        if mode != self._mode:
            self._mode = mode
            self._scroll_base_y = None
            self._scroll_acc = 0.0

        label = None
        if mode == "pinch":
            self._release_button(right=True)
            self._move_pointer(x, y)
            self._press_button()
            label = "PINCH = CLICK" if time.time() - self._pressed_at < 0.22 \
                else "PINCH = DRAG"
            self._emit_state(label, (x, y))
        elif mode == "three":
            self._release_button()
            self._move_pointer(x, y)
            self._press_button(right=True)
            label = "3 FINGERS = RIGHT CLICK" if \
                time.time() - self._right_pressed_at < 0.22 \
                else "3 FINGERS = RIGHT DRAG"
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
            self._emit_state(label, (x, y))
        else:  # index / open hand  ->  move the pointer
            self._release_button()
            self._release_button(right=True)
            self._move_pointer(x, y)
            label = "INDEX = MOVE" if n_up == 1 else \
                    ("OPEN HAND = MOVE" if n_up == 5 else "MOVE")
            self._emit_state(label, (x, y))

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

    def _draw_overlay(self, frame, label, lms=None, fingers=None):
        """Draw the hand skeleton, finger states and recognised gesture."""
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
        self._send("log", "Gestures: index/open=move  pinch=click  "
                          "peace=scroll  three=right click  fist=drag.")

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
                # Downscale only for inference -> faster, pointer still smooth.
                small = cv2.resize(rgb, (320, int(rgb.shape[0] *
                                                  (320 / rgb.shape[1]))))
                results = hands.process(small)

                overlay_text = None
                fingers = None
                landmarks = None
                if results.multi_hand_landmarks:
                    self._lost_since = None
                    try:
                        overlay_text, fingers = self._handle_landmarks(
                            results.multi_hand_landmarks[0].landmark)
                        landmarks = results.multi_hand_landmarks[0]
                    except Exception as exc:
                        self._send("log", f"[!] Gesture error: {exc}")

                elif self._lost_since is None:
                    self._lost_since = time.time()
                elif time.time() - self._lost_since > 0.6:
                    if self._button_down or self._right_down:
                        self._release_button()
                        self._release_button(right=True)

                if (self.emit_preview and self.on_preview is not None and
                        time.time() - self._last_preview >= 1.0 / PREVIEW_FPS):
                    self._last_preview = time.time()
                    try:
                        self._draw_overlay(frame, overlay_text,
                                           landmarks, fingers)
                        self.on_preview(frame)
                    except Exception:
                        pass

                elapsed = time.time() - started
                if elapsed < frame_dt:
                    time.sleep(frame_dt - elapsed)
        finally:
            self._release_button()
            self._release_button(right=True)
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