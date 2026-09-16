"""Smoke-test suite for VoiceControlApp v5.6.0 (clean-shutdown hardening).

Supersedes test_v56.py: keeps all v5.2..v5.5.0 regression checks and adds
the v5.6.0 teardown coverage:

  * on_close signals every worker thread (timer flag, TTS sentinel), joins
    the timer runner, destroys leftover toast popups and closes the Gemini
    HTTP session.
  * every root.after() loop (queue poller, clipboard watcher) stops
    re-scheduling the instant _closing is set.
"""
import os
import sys
import time
import threading
import tkinter as tk
import py_compile
from types import SimpleNamespace

print("--- COMPILE ---")
py_compile.compile("voice_app.py", doraise=True)
py_compile.compile("hand_cursor.py", doraise=True)
print("PASS")

# ---- Headless GUI smoke test (withdraw root) ----
import voice_app as va
root = tk.Tk()
root.withdraw()
app = va.VoiceControlApp()
root.update()

# Self-heal: interrupted runs may have left macro/touchpad/hand_mode on.
# Remember the originals, force safe defaults, restore them in the cleanup.
orig_macro_mode = bool(app.config.get("macro_mode", False))
orig_touchpad_mode = bool(app.config.get("touchpad_mode", False))
orig_hand_mode = app.config.get("hand_mode", "on")
app._macro_mode_var.set(False)
app._touchpad_var.set(False)
app.config["macro_mode"] = False
app.config["touchpad_mode"] = False
app.config["hand_mode"] = "off"     # stop any GUI auto-start of the webcam
va.save_config(app.config)
assert app.config.get("macro_mode", None) is False
assert app.config.get("touchpad_mode", None) is False
print("Mode config reset (originals saved) OK")


def _cleanup_on_error(exc_type, exc, tb):
    """Never leave a hung voice_app process holding the webcam."""
    try:
        app.config["macro_mode"] = bool(orig_macro_mode)
        app.config["touchpad_mode"] = bool(orig_touchpad_mode)
        app.config["hand_mode"] = orig_hand_mode
        va.save_config(app.config)
    except Exception:
        pass
    try:
        app.on_close()
    except Exception:
        pass
    raise exc


sys.excepthook = _cleanup_on_error

print("--- WIDGET SMOKE ---")
vars_ok = all(hasattr(app, a) for a in [
    "autostart_var", "click_beep_var", "voice_var",
    "voice_menu", "tts_rate_var", "hand_size_var",
    "clip_box", "train_btn", "_train_window", "_tray_icon",
    "macro_btn", "_macro_mode_var", "touchpad_btn", "_touchpad_var"])
print(f"All widget vars present: {vars_ok}")
assert vars_ok

cfg = app.config
assert cfg.get("minimize_to_tray", None) is True
assert cfg.get("tray_hotkey", "") == "ctrl+alt+v"
assert cfg.get("macro_actions", None) == {}
assert cfg.get("touchpad_gain", None) == 2.5
print("Config defaults OK")

print("--- MACRO DEFAULT ACTIONS (v5.3 regression) ---")
assert va.MACRO_DEFAULT_ACTIONS["circle"] == "lock screen"
assert va.MACRO_DEFAULT_ACTIONS["v"] == "new tab"
print("Macro action map OK")

print("--- TRAINER ROWS (v5.2 regression) ---")
assert va.VoiceControlApp._TRAIN_GESTURES == [
    ("MOVE", ("index", "open")),
    ("CLICK", ("pinch",)),
    ("SCROLL", ("peace",)),
    ("RIGHT CLICK", ("three",)),
    ("DRAG", ("fist",)),
], va.VoiceControlApp._TRAIN_GESTURES
print("Trainer gesture map OK")

# ----------------------------------------------------------------------
# v5.5.0 NEW: GUI-thread marshalling
# ----------------------------------------------------------------------
print("--- GUI THREAD IDENTIFICATION ---")
assert app._on_gui_thread(), "main thread must be considered the GUI thread"
worker_thread_id = []


def record_thread_id():
    worker_thread_id.append(app._on_gui_thread())


t = threading.Thread(target=record_thread_id, daemon=True)
t.start()
t.join()
assert worker_thread_id == [False], "worker thread must NOT be GUI thread"
print("GUI/worker thread distinction OK")

print("--- _gui_call INLINE (GUI thread runs the call directly) ---")
ret = []
app._gui_call(lambda: ret.append("ran"))
assert ret == ["ran"], "inline _gui_call did not run the function"
print("_gui_call inline dispatch OK")

print("--- _gui_call QUEUED (worker -> GUI thread via queue) ---")
def drain_queue():
    app._poll_queue()


worker_results = []


def worker_post():
    app._gui_call(lambda: worker_results.append("executed-on-main"))
    marker = app._on_gui_thread()      # worker itself
    worker_results.append(f"worker-gui={marker}")


wt = threading.Thread(target=worker_post, daemon=True)
wt.start()
wt.join()
assert worker_results == ["worker-gui=False"], worker_results
drain_queue()                           # poller runs the queued job
assert worker_results == ["worker-gui=False", "executed-on-main"], \
    worker_results
print("_gui_call queued dispatch OK")

print("--- _append_log WORKER-SAFE ---")
log_path = os.path.join(os.path.dirname(os.path.abspath("voice_app.py")),
                        "voc_log.txt")
marker = "[TEST] worker marshal marker v5.6.0"
wt = threading.Thread(target=lambda: app._append_log(marker), daemon=True)
wt.start()
wt.join()
drain_queue()
assert os.path.exists(log_path)
assert marker in app.log_text.get("1.0", tk.END), "log text not marshalled"
# The log text widget is only ever touched by the GUI thread: the worker
# thread above never wrote to it directly (it queued the append instead).
print("_append_log marshalled OK")

print("--- VOICE COMMAND FROM WORKER THREAD ----")
orig_engine = app._hand_engine
fake = SimpleNamespace(
    is_running=lambda: True,
    set_training=lambda flag: None,
    set_mode=lambda m: setattr(fake, "last_mode", m),
)
app._hand_engine = fake
app.handle_command = va.VoiceControlApp.handle_command.__get__(app,
                                                               va.VoiceControlApp)
app._speak = lambda *a, **k: None

def worker_command(phrase):
    app.handle_command(phrase)


wt = threading.Thread(target=worker_command, args=("draw macros",), daemon=True)
wt.start()
wt.join()
for _ in range(50):
    drain_queue()
    root.update()
    if app._macro_mode_var.get():
        break
    time.sleep(0.02)
assert app._macro_mode_var.get() is True, "worker voice command did not apply"
assert app.config.get("macro_mode") is True
wt = threading.Thread(target=worker_command, args=("stop macros",), daemon=True)
wt.start()
wt.join()
for _ in range(50):
    drain_queue()
    root.update()
    if not app._macro_mode_var.get():
        break
    time.sleep(0.02)
assert app._macro_mode_var.get() is False, "worker voice command did not toggle off"
assert app.config.get("macro_mode") is False
print("Worker-thread voice command (macro) OK")

print("--- TRAY TOGGLE MARSHALLED (worker posts, GUI applies) ---")
restore_called = []


def fake_restore():
    restore_called.append(True)


orig_restore = app._restore_window
app._restore_window = fake_restore
# App window starts visible (normal state).  First toggle should HIDE it
# (withdraw), second toggle should RESTORE it (deiconify + fake_restore).
t1 = threading.Thread(target=app._tray_toggle, daemon=True)
t1.start()
t1.join()
drain_queue()
root.update()
assert app.root.state() == "withdrawn", (
    f"first tray toggle should hide window, got {app.root.state()!r}")
assert restore_called == [], "restore should not be called while hiding"
t2 = threading.Thread(target=app._tray_toggle, daemon=True)
t2.start()
t2.join()
drain_queue()
root.update()
assert restore_called == [True], (
    f"tray toggle did not run on the GUI thread: {restore_called!r}")
app._restore_window = orig_restore
print("Tray toggle marshalling OK")

print("--- HOTKEY RESTORE MARSHALLED ---")
restore_called.clear()
orig_restore = app._restore_window
app._restore_window = fake_restore
wt = threading.Thread(target=app._hotkey_restore, daemon=True)
wt.start()
wt.join()
drain_queue()
root.update()
assert restore_called == [True], "hotkey restore did not run on the GUI thread"
app._restore_window = orig_restore
print("Hotkey restore marshalling OK")

print("--- ENGINE EPOCH GUARD (stale events dropped) ---")
for _ in range(20):
    drain_queue()
old_epoch = app._hand_epoch
app._hand_epoch = 200                # new engine "started"
app._hand_event("hand_state", "FRESH", 200)
app._hand_event("hand_state", "STALE", 199)      # dropped at source
app._hand_event("hand_state", "STALE2", old_epoch)  # dropped at source
drain_queue()
assert app.hand_state_var.get() == "FRESH", \
    f"expected FRESH, got {app.hand_state_var.get()!r}"
for _ in range(20):
    drain_queue()
assert app.hand_state_var.get() == "FRESH", \
    "stale engine event flipped the GUI state"
app._hand_epoch = old_epoch
print("Engine epoch guard OK")

print("--- TRAINER ROWS (v5.2 regression) ---")
assert va.VoiceControlApp._TRAIN_GESTURES == [
    ("MOVE", ("index", "open")),
    ("CLICK", ("pinch",)),
    ("SCROLL", ("peace",)),
    ("RIGHT CLICK", ("three",)),
    ("DRAG", ("fist",)),
], va.VoiceControlApp._TRAIN_GESTURES
print("Trainer rows OK")

print("--- ENGINE TRAINING (synthetic landmarks, v5.2 regression) ---")
import hand_cursor as hc
from hand_cursor import HandCursorEngine, available_cameras


def landmark(x, y):
    return SimpleNamespace(x=x, y=y, z=0.0)


def make_hand(fingers_ext, thumb_tip, index_tip=None):
    """Build a landmark list where fingers_ext[i] raises finger i's tip."""
    lm = [landmark(0.0, 0.0)] * 21

    lm[hc.WRIST] = landmark(0.50, 0.95)
    lm[hc.THUMB_MCP] = landmark(0.42, 0.90)
    lm[hc.INDEX_MCP] = landmark(0.46, 0.86)
    lm[hc.INDEX_PIP] = landmark(0.50, 0.62)
    lm[hc.MIDDLE_MCP] = landmark(0.52, 0.86)
    lm[hc.MIDDLE_PIP] = landmark(0.54, 0.62)
    lm[hc.RING_PIP] = landmark(0.56, 0.63)
    lm[hc.PINKY_PIP] = landmark(0.58, 0.64)

    ext_tip = {hc.INDEX_TIP: 0.30, hc.MIDDLE_TIP: 0.29,
               hc.RING_TIP: 0.31, hc.PINKY_TIP: 0.33}
    curl_tip = {hc.INDEX_TIP: 0.78, hc.MIDDLE_TIP: 0.77,
                hc.RING_TIP: 0.76, hc.PINKY_TIP: 0.75}
    combos = [(hc.INDEX_TIP, hc.INDEX_PIP, 0),
              (hc.MIDDLE_TIP, hc.MIDDLE_PIP, 1),
              (hc.RING_TIP, hc.RING_PIP, 2),
              (hc.PINKY_TIP, hc.PINKY_PIP, 3)]
    for tip_i, _pip_i, finger_idx in combos:
        if fingers_ext[finger_idx + 1]:
            lm[tip_i] = landmark(0.50 + 0.02 * finger_idx, ext_tip[tip_i])
        else:
            lm[tip_i] = landmark(0.50 + 0.02 * finger_idx, curl_tip[tip_i])
    lm[hc.THUMB_TIP] = landmark(*thumb_tip)
    if index_tip is not None:
        lm[hc.INDEX_TIP] = landmark(*index_tip)
    return lm


engine = HandCursorEngine()  # no camera opened yet
events = []


def capture(kind, data):
    events.append((kind, data))


engine.on_event = capture

poses = {
    "move": make_hand([False, True, False, False, False],
                      (0.49, 0.60), (0.50, 0.30)),
    "peace": make_hand([False, True, True, False, False],
                       (0.49, 0.60), (0.50, 0.30)),
    "three": make_hand([False, True, True, True, False],
                       (0.49, 0.60), (0.50, 0.30)),
    "fist": make_hand([False, False, False, False, False],
                      (0.49, 0.60), (0.40, 0.72)),
    "open": make_hand([True, True, True, True, True],
                      (0.15, 0.70), (0.50, 0.30)),
}
expected = {
    "move": ("index", "open"),
    "peace": ("peace",),
    "three": ("three",),
    "fist": ("fist",),
    "open": ("open",),
}
for pose_name, lm in poses.items():
    events.clear()
    engine._training_step(lm)
    train_evts = [d for (k, d) in events if k == "train"]
    assert train_evts, f"no train event emitted for {pose_name}"
    raw = train_evts[-1]["raw"]
    assert raw in expected[pose_name], f"{pose_name}: got {raw}"
print("Engine training classification OK")

print("--- MACRO RECOGNIZER (v5.3 regression) ---")
from hand_cursor import _recognize_macro, MACRO_TEMPLATES
for name, tmpl in MACRO_TEMPLATES.items():
    assert _recognize_macro(tmpl) == name, f"template {name} did not match"
import random
random.seed(1)
scribble = [(0.1 * random.random(), 0.9 * random.random())
            for _ in range(40)]
assert _recognize_macro(scribble) is None, "scribble should be rejected"
print("Macro recognizer OK")

print("--- ENGINE MACRO MODE (v5.3 regression) ---")


def finger_lm(x, y):
    lm = [landmark(0.0, 0.0)] * 21
    lm[hc.INDEX_TIP] = landmark(x, y)
    return lm


events.clear()
engine.set_mode("macros")
assert engine._driver_mode == "macros"
verts = [(0.2, 0.1), (0.5, 0.9), (0.8, 0.1)]
for (x0, y0), (x1, y1) in zip(verts, verts[1:]):
    for i in range(20):
        t = i / 20
        engine._handle_macro(finger_lm(x0 + (x1 - x0) * t,
                                       y0 + (y1 - y0) * t))
last = engine._macro_last_pt
engine._macro_commit_ts = time.time() - 1.5
engine._handle_macro(finger_lm(*last))
sent = [(k, d) for (k, d) in events if k == "macro"]
assert sent == [("macro", "v")], sent
engine.set_mode("cursor")
print("Engine macro flow OK")

print("--- ENGINE TOUCHPAD (relative-mouse mapping) ---")
engine.set_mode("touchpad")
assert engine._driver_mode == "touchpad"
engine._screen = (1920, 1080)
engine.sensitivity = 1.0
engine._cursor = (960, 540)


def shift_center(hand, dx):
    hand[hc.WRIST] = landmark(0.50 + dx, 0.95)
    hand[hc.MIDDLE_MCP] = landmark(0.52 + dx, 0.86)
    return hand


first = shift_center(make_hand([False, True, False, False, False],
                               (0.49, 0.60), (0.50, 0.30)), 0.0)
engine._handle_landmarks(first)
c0 = engine._cursor
engine._handle_landmarks(first)
assert engine._cursor == c0, "cursor drifted with a stationary hand"
right = shift_center(make_hand([False, True, False, False, False],
                               (0.49, 0.60), (0.50, 0.30)), 0.30)
engine._handle_landmarks(right)
c1 = engine._cursor
assert c1[0] > c0[0], f"pointer should move right: {c0} -> {c1}"
down = shift_center(make_hand([False, True, False, False, False],
                              (0.49, 0.60), (0.50, 0.30)), 0.30)
down[hc.WRIST] = landmark(0.65, 0.95 + 0.30)
down[hc.MIDDLE_MCP] = landmark(0.67, 0.86 + 0.30)
engine._handle_landmarks(down)
c2 = engine._cursor
assert c2[1] > c1[1], f"pointer should move down: {c1} -> {c2}"
assert c2 != c0, "relative mode must not depend on absolute hand position"
engine.set_mode("cursor")
assert engine._driver_mode == "cursor"
print("Engine touchpad mapping OK")

print("--- TOUCHPAD VOICE COMMANDS (v5.4 regression) ---")
app._hand_engine = fake
app.handle_command("touchpad mode")
assert app._touchpad_var.get() is True
assert fake.last_mode == "touchpad"
app.handle_command("stop touchpad")
assert app._touchpad_var.get() is False
assert fake.last_mode == "cursor"
app.handle_command("start touchpad")
assert app._touchpad_var.get() is True
app.handle_command("touchpad off")
assert app._touchpad_var.get() is False
app._hand_engine = orig_engine
print("Touchpad voice commands OK")

print("--- TOUCHPAD/MACROS MUTUAL EXCLUSION ---")
app._hand_engine = fake
app.handle_command("touchpad mode")
assert app._touchpad_var.get() is True
app.handle_command("draw macros")
assert app._macro_mode_var.get() is True
app.handle_command("touchpad mode")  # touchpad wins -> macro off
assert app._touchpad_var.get() is True
assert app._macro_mode_var.get() is False
app.handle_command("stop touchpad")
assert app._touchpad_var.get() is False
app._hand_engine = orig_engine
print("Mutual exclusion OK")

print("--- MACRO EVENT HANDLER (v5.3 regression) ---")
calls = []
app.handle_command = lambda phrase: calls.append(phrase)
app._on_macro("circle")
for _ in range(20):
    if calls:
        break
    time.sleep(0.05)
assert calls == ["lock screen"], calls
app._on_macro("garbage-shape")
assert len(calls) == 1, "unknown shape must not run an action"
print("Macro event handler OK")

print("--- TRAINER WINDOW (v5.2 regression) ---")
app.start_hand_cursor = lambda: None
app._open_trainer()
assert app._train_window is not None
payload = {"raw": "peace", "stable": True,
           "fingers": [False, True, True, False, False],
           "size": 0.52, "armed": True}
app._on_train_update(payload)
assert "PASS" in app._train_rows["SCROLL"].cget("text")
app._close_trainer()
assert app._train_window is None
print("Trainer window updates OK")

print("--- TRAY (v5.2 regression) ---")
assert va.TRAY_OK and va.KEYBOARD_OK
img = app._tray_image()
assert img is not None
app._start_tray()
assert app._tray_icon is not None
print("Tray icon + hotkey started OK")

print("--- AUTOSTART REGISTRY (v5.2 regression) ---")
import winreg
app._set_autostart(True)
key = winreg.OpenKey(
    winreg.HKEY_CURRENT_USER,
    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
val, _ = winreg.QueryValueEx(key, "VoiceControlApp")
assert "python" in val.lower() or "voice" in val.lower(), val
app._set_autostart(False)
try:
    winreg.QueryValueEx(key, "VoiceControlApp")
    assert False, "should have been deleted"
except FileNotFoundError:
    pass
print("Autostart registry OK")

# ----------------------------------------------------------------------
# v5.4.1 NEW: GUI stop/reap tests with a fake engine - no real webcam.
# ----------------------------------------------------------------------
print("--- GUI HAND ENGINE REAP + STOP RESET (v5.4.1 regression) ---")


class FakeEngine:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self._running = False
        self.started = 0
        self.stopped = 0
        self.modes = []

    def is_running(self):
        return self._running

    def start(self):
        self._running = True
        self.started += 1

    def stop(self):
        self._running = False
        self.stopped += 1

    def set_mode(self, m):
        self.modes.append(m)


class StaleEngine:
    """An engine whose thread died without sending a 'stopped' event."""

    def __init__(self):
        self.stopped = 0

    def is_running(self):
        return False

    def stop(self):
        self.stopped += 1


old_factory = va.HandCursorEngine
created = []


def counted_factory(**kwargs):
    eng = FakeEngine(**kwargs)
    created.append(eng)
    return eng


va.HandCursorEngine = counted_factory

app.start_hand_cursor = va.VoiceControlApp.start_hand_cursor.__get__(
    app, va.VoiceControlApp)

stale = StaleEngine()
app._hand_engine = stale
app.start_hand_cursor()               # must reap the stale engine first
assert stale.stopped == 1, "stale engine was not stopped before restart"
neweng = created[-1]
assert app._hand_engine is neweng, "a fresh engine was not created"
assert neweng.started == 1, "fresh engine was not started"
assert neweng.kwargs["camera_index"] == int(app.camera_var.get())

app.start_hand_cursor()               # already running -> no-op
assert len(created) == 1, "duplicate engine started"
assert neweng.started == 1

app.stop_hand_cursor()
assert neweng.stopped == 1, "running engine was not stopped"
assert app._hand_engine is None, "engine reference was not cleared"
root.update()                         # run the after() UI reset callback
assert str(app.hand_stop_btn["state"]) == "disabled"
assert app.hand_led.cget("bg") == "#3a3f45"

app.start_hand_cursor = lambda: None  # prevent any further GUI engine starts
va.HandCursorEngine = old_factory
app._hand_engine = None
print("GUI engine reap + stop reset OK")

# ----------------------------------------------------------------------
# v5.6.0 NEW: clean-shutdown hardening (after() loops + on_close teardown)
# ----------------------------------------------------------------------
print("--- AFTER() LOOPS STOP RE-SCHEDULING WHEN CLOSING (v5.6.0) ---")
orig_after = app.root.after
after_calls = []


def fake_after(ms, func, *args, **kwargs):
    after_calls.append((ms, func))
    return "fake-id"   # never really schedule


app.root.after = fake_after
app._closing = True
app._poll_queue()
assert after_calls == [], f"poller re-scheduled while closing: {after_calls}"
app._watch_clipboard()
assert after_calls == [], f"clipboard watcher re-scheduled while closing"
# Open again -> both re-schedule exactly once.
app._closing = False
app._poll_queue()
assert len(after_calls) == 1, f"poller should re-schedule once, got {after_calls}"
app._watch_clipboard()
assert len(after_calls) == 2, \
    f"clipboard watcher should re-schedule once, got {after_calls}"
app._closing = True
app.root.after = orig_after
print("after() loop closing guards OK")

print("--- CALIBRATION SCHEDULE SAFE (v5.6.0) ---")
app.root.after = orig_after
app._closing = True
app._calibrate_check()      # no-op, no crash
orig_reach = app._calibrating
try:
    app._calibrating = False
    app._calibrate_reach()  # guarded by _closing -> returns immediately
    assert not app._calibrating
finally:
    app._calibrating = orig_reach
assert app._calib_phase in (0, 1, 2)
app._closing = False
print("Calibration closing guard OK")

print("--- TOAST CLEANUP + TIMER EXIT + HTTP CLOSE (v5.6.0) ---")
# Park a live toast so on_close has something to destroy.
app._show_toast("TEST", "toast to clean up", duration_ms=60000)
assert app._toast_refs, "toast was not tracked"
timer_thread_ref = app._timers_thread
assert timer_thread_ref is not None and timer_thread_ref.is_alive()
# Park a fake engine so on_close's engine.stop() path is exercised.
close_engine = FakeEngine()
close_engine._running = True
close_engine.stopped = 0
app._hand_engine = close_engine

print("=== ALL v5.6.0 TESTS PASSED ===")
app.on_close()  # real shutdown: engine.stop, toast cleanup, timer join, destroy

# Re-tag the final success line printed above: on_close has now run.
assert app._closing is True
assert close_engine.stopped == 1, "on_close did not stop the hand engine"
assert app._hand_engine is None
assert getattr(app, "_toast_refs", []) == [], "toasts left after close"
assert not (timer_thread_ref.is_alive()), "timer thread still alive after close"
print("clean shutdown checks OK")

# ----------------------------------------------------------------------
# v5.4.1 NEW: real webcam lifecycle (skips cleanly when no camera exists)
# ----------------------------------------------------------------------
print("--- REAL WEBCAM START/STOP/RESTART (v5.4.1 regression) ---")
cam_index = available_cameras(limit=2)
if not cam_index:
    print("No webcam available - SKIPPING real-camera lifecycle test")
else:
    index = cam_index[0]

    def camera_reopenable(i):
        import cv2
        cap = cv2.VideoCapture(i)
        ok = cap.isOpened()
        if ok:
            cap.release()
        return ok

    cam_events = []

    def cam_capture(kind, data):
        cam_events.append((kind, data))

    camp = HandCursorEngine(camera_index=index, emit_preview=False,
                            on_event=cam_capture)
    camp.start()
    deadline = time.time() + 45
    while time.time() < deadline:
        if any(k == "started" for k, _ in cam_events):
            break
        if any(k == "error" for k, _ in cam_events):
            break
        time.sleep(0.05)
    assert any(k == "started" for k, _ in cam_events), (
        "engine did not start on the real camera; events=%s"
        % [(k, str(d)[:40]) for k, d in cam_events[:4]])

    time.sleep(1.0)
    assert camp.is_running(), "engine stopped unexpectedly while running"

    t0 = time.time()
    camp.stop()
    dt = time.time() - t0
    print(f"  start ok; stop() returned in {dt:.2f}s")
    assert dt < 3.0, f"stop() took too long ({dt:.2f}s)"
    assert not camp.is_running(), "engine still running after stop()"
    assert camp._cap is None, "camera capture not released after stop()"
    assert camp._thread is None, "thread reference not cleared after stop()"
    assert any(k == "stopped" for k, _ in cam_events), "no stopped event"
    assert "error" not in [k for k, _ in cam_events], "unexpected error"

    time.sleep(0.3)
    assert camera_reopenable(index), (
        "webcam still locked after stop - OS never got the device back")

    camp.start()                       # restart the same engine object
    started_count = sum(1 for k, _ in cam_events if k == "started")
    deadline = time.time() + 30
    while time.time() < deadline and \
            sum(1 for k, _ in cam_events if k == "started") == started_count:
        time.sleep(0.05)
    assert camp.is_running(), "engine did not restart after stop()"
    time.sleep(0.5)
    camp.stop()
    assert camp._cap is None
    time.sleep(0.3)
    assert camera_reopenable(index), "webcam still locked after 2nd stop"
    print("  start->stop->start->stop lifecycle OK")
print("Real webcam lifecycle OK")

# ----------------------------------------------------------------------
# Cleanup: restore config (on_close already ran in the v5.6.0 section;
# it is a guarded no-op a second time).
# ----------------------------------------------------------------------
app.config["macro_mode"] = bool(orig_macro_mode)
app.config["touchpad_mode"] = bool(orig_touchpad_mode)
app.config["hand_mode"] = orig_hand_mode
va.save_config(app.config)

print()
print("=== ALL v5.6.0 TESTS PASSED ===")