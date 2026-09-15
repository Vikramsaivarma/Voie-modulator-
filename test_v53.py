"""Smoke-test suite for VoiceControlApp v5.3 features (draw-macro mode).

Supersedes test_v52.py: keeps all v5.2 regression checks and adds macro
recognizer + engine + GUI coverage.
"""
import os
import sys
import time
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
    "clip_box", "train_btn", "_train_window", "_tray_icon",
    "macro_btn", "_macro_mode_var"])
print(f"All widget vars present: {vars_ok}")
assert vars_ok

cfg = app.config
assert cfg.get("minimize_to_tray", None) is True
assert cfg.get("tray_hotkey", "") == "ctrl+alt+v"
assert cfg.get("macro_mode", None) is False
assert cfg.get("macro_actions", None) == {}
print("Config defaults OK")

print("--- MACRO DEFAULT ACTIONS ---")
assert va.MACRO_DEFAULT_ACTIONS["circle"] == "lock screen"
assert va.MACRO_DEFAULT_ACTIONS["v"] == "new tab"
print("Macro action map OK")

print("--- TRAINER ROWS ---")
assert va.VoiceControlApp._TRAIN_GESTURES == [
    ("MOVE", ("index", "open")),
    ("CLICK", ("pinch",)),
    ("SCROLL", ("peace",)),
    ("RIGHT CLICK", ("three",)),
    ("DRAG", ("fist",)),
], va.VoiceControlApp._TRAIN_GESTURES
print("Trainer gesture map OK")

print("--- ENGINE TRAINING (synthetic landmarks, v5.2 regression) ---")
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

print("--- MACRO RECOGNIZER (unit) ---")
from hand_cursor import _recognize_macro, MACRO_TEMPLATES
for name, tmpl in MACRO_TEMPLATES.items():
    assert _recognize_macro(tmpl) == name, f"template {name} did not match"
print("All templates self-match OK")
import math
v = MACRO_TEMPLATES["v"]
v2 = [(x + 0.02 * math.sin(i), y + 0.02 * math.cos(i))
      for i, (x, y) in enumerate(v)]
assert _recognize_macro(v2) == "v", "perturbed v lost its match"
print("Perturbed stroke OK")
import random
random.seed(1)
scribble = [(0.1 * random.random(), 0.9 * random.random())
            for _ in range(40)]
assert _recognize_macro(scribble) is None, "scribble should be rejected"
print("Scribble rejected OK")

print("--- ENGINE MACRO MODE (dwell commit) ---")


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
assert engine._macro_pts == [], "stroke not reset after commit"
print("Draw-v macro event OK")

# Hand-lost commit path: populate then finalize directly.
events.clear()
for (x0, y0), (x1, y1) in zip(verts, verts[1:]):
    for i in range(20):
        t = i / 20
        engine._handle_macro(finger_lm(x0 + (x1 - x0) * t,
                                       y0 + (y1 - y0) * t))
engine._finalize_macro()
sent = [(k, d) for (k, d) in events if k == "macro"]
assert sent == [("macro", "v")], sent
print("Hand-lost finalize OK")

# Too-short stroke -> macro None.
events.clear()
engine._handle_macro(finger_lm(0.5, 0.5))
engine._handle_macro(finger_lm(0.53, 0.53))
engine._finalize_macro()
sent = [(k, d) for (k, d) in events if k == "macro"]
assert sent == [("macro", None)], sent
print("Short stroke rejected OK")

# Back to cursor mode.
engine.set_mode("cursor")
assert engine._driver_mode == "cursor"
print("Mode switch back to cursor OK")

print("--- MACRO VOICE COMMAND ---")
orig_engine = app._hand_engine
fake = SimpleNamespace(
    is_running=lambda: True,
    set_training=lambda flag: None,
    set_mode=lambda m: setattr(fake, "last_mode", m),
)
app._hand_engine = fake
app._speak = lambda *a, **k: None
app._toggle_macro_mode(True)
assert app._macro_mode_var.get() is True
assert fake.last_mode == "macros"
app._toggle_macro_mode(False)
assert app._macro_mode_var.get() is False
assert fake.last_mode == "cursor"
app._hand_engine = orig_engine
print("Macro toggle (engine path) OK")

app.handle_command("draw macros")
assert app._macro_mode_var.get() is True, "voice 'draw macros' did not turn on"
app.handle_command("stop drawing")
assert app._macro_mode_var.get() is False, "'stop drawing' did not turn off"
app.handle_command("macro mode")
assert app._macro_mode_var.get() is True
app.handle_command("start drawing")
assert app._macro_mode_var.get() is True
app.handle_command("stop macros")
assert app._macro_mode_var.get() is False
print("Macro voice commands OK")

print("--- MACRO EVENT HANDLER ---")
calls = []
app.handle_command = lambda phrase: calls.append(phrase)
app._on_macro("circle")
for _ in range(20):
    if calls:
        break
    time.sleep(0.05)
assert calls == ["lock screen"], calls
app._on_macro("v")
for _ in range(20):
    if len(calls) >= 2:
        break
    time.sleep(0.05)
assert calls == ["lock screen", "new tab"], calls
app._on_macro("garbage-shape")
assert len(calls) == 2, "unknown shape must not run an action"
print("Macro event handler OK")

print("--- TRAINER WINDOW (GUI, v5.2 regression) ---")
app.start_hand_cursor = lambda: None
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

print("--- LOG PERSISTENCE (v5.2 regression) ---")
log_path = os.path.join(os.path.dirname(os.path.abspath("voice_app.py")),
                        "voc_log.txt")
app._append_log("[TEST] persistence marker v5.3")
assert os.path.exists(log_path)
with open(log_path, "r", encoding="utf-8") as f:
    lines = f.readlines()
assert any("persistence marker v5.3" in l for l in lines)
print("Log persistence OK")

print("--- CHAIN PARSE (v5.2 regression) ---")
assert va.VoiceControlApp._parse_chain(
    "open chrome then search weather") == ["open chrome", "search weather"]
assert va.VoiceControlApp._parse_chain("banana then kiwifruit") is None
print("Chain parse OK")

print("--- GEMINI MEMORY (v5.2 regression) ---")
app.clear_gemini_memory()
assert len(app._gemini_history) == 0
print("Memory OK")

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

# Restore macro_mode in the local config to whatever it was before.
app.config["macro_mode"] = False
va.save_config(app.config)

print()
print("=== ALL v5.3 TESTS PASSED ===")
app.on_close()  # real shutdown: stops tray, hotkey, threads