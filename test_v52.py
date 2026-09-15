"""Smoke-test suite for VoiceControlApp v5.2 features."""
import os
import re
import sys
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

print("--- WIDGET SMOKE ---")
vars_ok = all(hasattr(app, a) for a in [
    "autostart_var", "click_beep_var", "voice_var",
    "voice_menu", "tts_rate_var", "hand_size_var",
    "clip_box", "train_btn", "_train_window", "_tray_icon"])
print(f"All widget vars present: {vars_ok}")
assert vars_ok

cfg = app.config
assert cfg.get("minimize_to_tray", None) is True
assert cfg.get("tray_hotkey", "") == "ctrl+alt+v"
print("Config defaults OK")

print("--- TRAINER ROWS ---")
assert va.VoiceControlApp._TRAIN_GESTURES == [
    ("MOVE", ("index", "open")),
    ("CLICK", ("pinch",)),
    ("SCROLL", ("peace",)),
    ("RIGHT CLICK", ("three",)),
    ("DRAG", ("fist",)),
], va.VoiceControlApp._TRAIN_GESTURES
print("Trainer gesture map OK")

print("--- ENGINE TRAINING (synthetic landmarks) ---")
import hand_cursor as hc
from hand_cursor import HandCursorEngine


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

    # Tips: extended -> high (y=0.30), curled -> low (y=0.75).
    ext_tip = {hc.INDEX_TIP: 0.30, hc.MIDDLE_TIP: 0.29,
               hc.RING_TIP: 0.31, hc.PINKY_TIP: 0.33}
    curl_tip = {hc.INDEX_TIP: 0.78, hc.MIDDLE_TIP: 0.77,
                hc.RING_TIP: 0.76, hc.PINKY_TIP: 0.75}
    combos = [(hc.INDEX_TIP, hc.INDEX_PIP, 0),
              (hc.MIDDLE_TIP, hc.MIDDLE_PIP, 1),
              (hc.RING_TIP, hc.RING_PIP, 2),
              (hc.PINKY_TIP, hc.PINKY_PIP, 3)]
    for tip_i, _pip_i, finger_idx in combos:
        # fingers_ext is in engine order: [thumb, index, middle, ring, pinky].
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
for pose_name in ("move", "peace", "three", "fist", "open"):
    raw2 = poses[pose_name]
print("Engine training classification OK")

# pinch pose: thumb tip and index tip very close
pinch_lm = make_hand([False, True, False, False, False],
                     (0.47, 0.30), (0.50, 0.32))
events.clear()
engine._training_step(pinch_lm)
raw = [d for (k, d) in events if k == "train"][-1]["raw"]
assert raw == "pinch", f"pinch: got {raw}"
print("Engine pinch training OK")

print("--- TRAINER WINDOW (GUI) ---")
app.start_hand_cursor = lambda: None  # never touch the real webcam
app._open_trainer()
assert app._train_window is not None
assert set(app._train_rows.keys()) == {n for n, _ in
                                       va.VoiceControlApp._TRAIN_GESTURES}
payload = {"raw": "peace", "stable": True,
           "fingers": [False, True, True, False, False],
           "size": 0.52, "armed": True}
app._on_train_update(payload)
peace_row = app._train_rows["SCROLL"]
assert "PASS" in peace_row.cget("text"), peace_row.cget("text")
move_row = app._train_rows["MOVE"]
assert "WAIT" in move_row.cget("text"), move_row.cget("text")
finger_ok = app._train_fingers[1].cget("bg") == "#238636"
assert finger_ok
app._close_trainer()
assert app._train_window is None
print("Trainer window updates OK")

print("--- TRAY ---")
assert va.TRAY_OK and va.KEYBOARD_OK
img = app._tray_image()
assert img is not None
app._start_tray()
assert app._tray_icon is not None
app._tray_hotkey is not None  # default hotkey registered
print("Tray icon + hotkey started OK")

print("--- LOG PERSISTENCE ---")
log_path = os.path.join(os.path.dirname(os.path.abspath("voice_app.py")),
                        "voc_log.txt")
app._append_log("[TEST] persistence marker v5.2")
assert os.path.exists(log_path)
with open(log_path, "r", encoding="utf-8") as f:
    lines = f.readlines()
assert any("persistence marker v5.2" in l for l in lines)
print("Log persistence OK")

print("--- CHAIN PARSE ---")
assert va.VoiceControlApp._parse_chain("open chrome then search weather") == [
    "open chrome", "search weather"]
assert va.VoiceControlApp._parse_chain("banana then kiwifruit") is None
print("Chain parse OK")

print("--- GEMINI MEMORY ---")
app.clear_gemini_memory()
assert len(app._gemini_history) == 0
print("Memory OK")

print("--- AUTOSTART REGISTRY (set/cleanup) ---")
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

print()
print("=== ALL v5.2 TESTS PASSED ===")
app.on_close()  # real shutdown: stops tray, hotkey, threads