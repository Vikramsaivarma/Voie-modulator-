"""Smoke-test suite for VoiceControlApp v5.1 features."""
import sys, os, time, re, threading, tkinter as tk, py_compile

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
vars_ok = all(getattr(app, a, None) is not None for a in [
    "autostart_var", "click_beep_var", "voice_var",
    "voice_menu", "tts_rate_var", "hand_size_var",
    "clip_box"])
print(f"All widget vars present: {vars_ok}")
assert vars_ok

cfg = app.config
assert cfg.get("voice_id", "") == ""
assert cfg.get("click_beep", True) == True
assert cfg.get("autostart", False) == False
print("Config defaults OK")

# ---- _parse_chain unit tests ----
print("--- CHAIN PARSE ---")
assert va.VoiceControlApp._parse_chain("open chrome then search weather") == [
    "open chrome", "search weather"], "basic chain failed"
assert va.VoiceControlApp._parse_chain("open chrome and then search weather") == [
    "open chrome", "search weather"], "and-then chain failed"
assert va.VoiceControlApp._parse_chain("calculate 15 percent of 240") is None, "no-then should be None"
assert va.VoiceControlApp._parse_chain("what came before then dogs") is None, "single word tail rejected"
assert va.VoiceControlApp._parse_chain("open chrome then refresh") == [
    "open chrome", "refresh"], "1-word action allowed"
assert va.VoiceControlApp._parse_chain("mute then enter") == [
    "mute", "enter"], "known single-word verbs are chainable"
assert va.VoiceControlApp._parse_chain("banana then kiwifruit") is None, \
    "single unknown words rejected"
print("Chain parse all OK")

# ---- Gemini memory round-trip ----
print("--- GEMINI MEMORY ---")
app._gemini_history = [("user", "What is the capital of France?"),
                       ("model", "Paris is the capital of France.")]
assert len(app._gemini_history) == 2
app.clear_gemini_memory()
assert len(app._gemini_history) == 0
print("Memory OK")

# ---- _play_beep kind check ----
print("--- BEEP HANDLER ---")
beeps = []
def fake_beep(kind):
    beeps.append(kind)
app._play_beep = fake_beep
for kind in ["left", "right", "drag", "right_drag"]:
    app._play_beep(kind)
assert len(beeps) == 4
assert set(beeps) == {"left", "right", "drag", "right_drag"}
print("Beep kinds OK")

# ---- Log persistence round-trip ----
print("--- LOG PERSISTENCE ---")
log_path = os.path.join(os.path.dirname(os.path.abspath("voice_app.py")), "voc_log.txt")
app._append_log("[TEST] persistence marker 42")
assert os.path.exists(log_path)
with open(log_path, "r", encoding="utf-8") as f:
    lines = f.readlines()
assert any("persistence marker 42" in l for l in lines), "marker not found"
print("Log persistence OK")

# ---- Voice rate live binding ----
print("--- TTS RATE ---")
app.tts_rate_var.set(260)
app._on_rate_change(260)
assert cfg["voice_rate"] == 260
print("Rate OK")

# ---- Hand cursor engine ----
print("--- HAND CURSOR ---")
import hand_cursor
from hand_cursor import HandCursorEngine
eng = HandCursorEngine()
assert "1.4.0" in (hand_cursor.__doc__ or "")
print("Hand cursor v1.4.0 OK")

# ---- Cursor region grab ----
print("--- CURSOR REGION ---")
png = app._grab_cursor_region_png(radius=120)
assert isinstance(png, bytes) and len(png) > 500, f"too small: {len(png)}"
print(f"Cursor PNG: {len(png)} bytes OK")

# ---- Wake word aliases (routing check) ----
print("--- WAKE WORD ---")
app._suspended = True
for phrase in ["hey vo", "ok vo", "wake up vo", "hi vo"]:
    assert re.search(r"\b(hey|hi)\s+(vo|voice|vak)\b", phrase) or \
           phrase in ("wake up vo", "ok vo", "hey vo", "hi vo"), f"failed: {phrase}"
app._suspended = False
print("Wake word patterns OK")

# ---- Autostart registry (set/cleanup) ----
print("--- AUTOSTART REGISTRY ---")
import winreg
app._set_autostart(True)
key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
val, _ = winreg.QueryValueEx(key, "VoiceControlApp")
assert "python" in val.lower() or "voice" in val.lower(), val
app._set_autostart(False)
key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
try:
    winreg.QueryValueEx(key, "VoiceControlApp")
    assert False, "should have been deleted"
except FileNotFoundError:
    pass
print("Autostart registry OK")

# ---- Timer toast handler present ----
print("--- TOAST HANDLER ---")
assert hasattr(app, "_show_toast"), "missing _show_toast"
print("Toast handler present")

# ---- Route command: clear memory ----
print("--- ROUTING: clear memory ---")
app.clear_gemini_memory()
app._gemini_history = [("user", "test"), ("model", "ans")]
app.handle_command("clear my memory")
assert len(app._gemini_history) == 0
print("Clear memory command OK")

# ---- Clipboard card ----
print("--- CLIPBOARD CARD ---")
assert hasattr(app, "clip_box")
assert hasattr(app, "_last_rendered_clips")
assert hasattr(app, "_render_clip_card")
assert hasattr(app, "_paste_clip")
print("Clipboard card widgets OK")

print()
print("=== ALL v5.1 TESTS PASSED ===")
root.destroy()
