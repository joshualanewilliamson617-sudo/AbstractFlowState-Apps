#!/usr/bin/env python3
"""
AirDraw - draw in MS Paint with your hand.

    F8                      arm / disarm (works from any window, even Paint).
                            Starts DISARMED: the mouse is yours until you press F8.
    OPEN HAND               moves the cursor (it follows the big knuckle in
                            the middle of your hand).
    Closed FIST             clutch: the cursor freezes, so you can move your
                            hand back to a comfy spot. OPEN hand (all four
                            fingers up) carries on from where the cursor stopped.
    Hold SPACE              in DRAW mode: mouse button DOWN at the AirDraw
                            cursor, for as long as you hold it - move your
                            hand to draw. Let go = mouse button UP. While it's
                            held the cursor moves in short strokes (Stroke px
                            per small hand move), and with Axis lock on the
                            line snaps to flat or upright once it has moved
                            "Lock threshold px". Turn clearly onto the
                            other axis and the lock switches there, so
                            corners and plus signs work in one stroke.
                            In CLICK mode: one normal click.
                            Works in any window while armed (Paint doesn't
                            also get the space).
    Draw / click mode       over Paint it follows Paint's tool by itself:
                            Pencil or any Brush = DRAW, everything else
                            (shapes, select, text, fill...) = CLICK. Anywhere
                            else, or if Paint's tool can't be read, the
                            "Draw mode" setting decides - D toggles it.
    D                       Draw mode on / off (the fallback when Paint's tool
                            can't be read)
    P                       pause / carry on
    ESC or Q                quit (closing the window also quits)

    H      flip left/right (if your camera mixes them up)
    [ ]    steadier / snappier
    - =    smaller / larger hand area

One window: the camera picture on the left, the settings on the right
(a drop-down picks the page: Basics, Movement, Zone). They set armed, which
hand (left or right), flip left/right, which camera (switches live; at
startup it lists the cameras that open and, the first time, picks Camo or the
first camera that isn't the laptop's own), the clutch and how straight a
finger must be to count as up, smoothing, precision mode, stroke size, and the
zone's size and position (slide the zone to where your arm is comfortable -
it may go past the camera picture's edge). Your settings are saved to
airdraw_settings.json next to this file and loaded next time.

Only the chosen hand moves the cursor; the other hand is ignored.
The cursor never goes onto the taskbar. Only one AirDraw can run at a time.
Depth steady: moving your hand toward or away from the camera doesn't move
the cursor (normally things drift outward as they come closer to a camera).
Precision mode: the cursor follows how far your hand moves, scaled down, so
small hand moves give small, exact cursor moves.

REQUIRES:  py -m pip install opencv-python mediapipe pynput pillow pywinauto
RUN:       python airdraw.py
"""

import atexit
import ctypes
import json
import math
import os
import queue
import sys
import threading
import time
import urllib.request
from collections import deque
from ctypes import wintypes


# DPI awareness comes first, before anything else can make a window. Without
# it, Windows scaling (125%, 150%...) makes Windows lie about the screen size
# and squash the coordinates, so clicks land beside the icon you aimed at.
def _make_dpi_aware():
    u = ctypes.windll.user32
    try:
        u.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        if u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):   # per-monitor v2
            return
    except Exception:
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:    # per-monitor
            return
    except Exception:
        pass
    try:
        u.SetProcessDPIAware()                                     # older Windows
    except Exception:
        pass


def dpi_report():
    """Plain words for the console, e.g. 'DPI-aware (per monitor), Windows scaling 125%'."""
    u = ctypes.windll.user32
    try:
        u.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        u.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        aw = u.GetAwarenessFromDpiAwarenessContext(u.GetThreadDpiAwarenessContext())
        kind = {0: "NOT DPI-aware - clicks may miss!", 1: "DPI-aware (system)",
                2: "DPI-aware (per monitor)"}.get(aw, "DPI mode unknown")
    except Exception:
        kind = "DPI mode unknown"
    try:
        kind += f", Windows scaling {round(u.GetDpiForSystem() / 96 * 100)}%"
    except Exception:
        pass
    return kind


_make_dpi_aware()

try:
    import cv2
    import numpy as np
    import mediapipe as mp
    import tkinter as tk
    from tkinter import ttk
    from PIL import Image, ImageTk
except ImportError as e:
    sys.exit(f"Missing package: {e.name}\n"
             f"Run:  py -m pip install opencv-python mediapipe pynput pillow")

try:
    from pynput import keyboard as _kb      # for the F8 hotkey that works in any window
except ImportError:
    _kb = None

# ------------------------------------------------------------------ setup ---

CAM_INDEX = 0
CAM_W, CAM_H = 640, 480      # smaller picture = much less work for the laptop
MAX_FPS = 30                 # never process more than this many frames a second

# Some laptop cameras report left and right hands the wrong way round.
# True swaps them back. H flips it while running, and the choice is saved.
INVERT_HANDEDNESS = True

BETA = 0.015           # responsiveness on fast moves
DRAW_STEADY = 0.55     # extra smoothing while the pen is down
FOLD_GAP = 0.15        # a finger counts as folded this far below "Finger straightness"
SMALL_MOVE = 0.10      # a "small hand move" = 10% of your hand size, about 1 cm
MIN_DOWN_S = 0.04      # mouse button stays down at least this long, so Paint sees the click
CLUTCH_S = 0.12        # a fist (or an open hand) must last this long to switch the clutch
DEPTH_REF_S = 20.0     # depth steady: your usual hand size is averaged over this long
ACCEL_FROM = 0.25      # accel: hand speeds below this (screen widths a second) stay precise
ACCEL_K = 3.0          # accel: how fast the speed-up builds at "Accel" 100
TURN_S = 0.35          # axis switch: "recent movement" = the last ~0.35 s
PAINT_POLL_S = 0.2     # how often Paint's selected tool is read

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/1/hand_landmarker.task")
MODEL_NAME = "hand_landmarker.task"
WINDOW = "AirDraw  -  ESC to quit"
HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "airdraw_settings.json")

CONNECTIONS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
               (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14),
               (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20),
               (0, 17)]

# ------------------------------------------------------------- settings -----
DEFAULTS = {                 # your own tuned settings (2026-11-26) are the defaults
    "hand": "Right",         # which hand moves the cursor: "Left" or "Right"
    "invert": INVERT_HANDEDNESS,
    "cutoff": 1.86,          # smoothing (lower = steadier)
    "margin": 0.24,          # unused border around the camera view
    "shift_x": 0.05,        # move the zone right (+) or left (-), fraction of picture width
    "shift_y": 0.0,          # move the zone up (+) or down (-), fraction of picture height
    "up_ratio": 1.15,        # how straight a finger must be to count as up (open hand / fist)
    "precision": True,       # True = slow, exact cursor
    "prec_pct": 44,          # precision speed, % of normal
    "accel": 50,             # precision: faster hand = faster cursor. 0 = off
    "max_speed": 200,        # ...up to this, % of normal (a fast sweep crosses the screen)
    "stroke_px": 8,          # while SPACE is held: px per small hand move. 0 = off
    "clutch": True,          # closed fist freezes the cursor, open hand resumes
    "draw_mode": True,       # SPACE holds the button (draw) - used when Paint's tool can't be read
    "axis_lock": True,       # while drawing, snap the line to flat or upright
    "lock_px": 8,            # ...once it has moved this many px
    "depth_steady": True,    # hand toward / away from the camera doesn't move the cursor
    "camera": -1,           # camera number 0-9. -1 = not chosen yet: pick Camo automatically
}

S = dict(DEFAULTS)
S["armed"] = False           # never saved: every run starts disarmed
_dirty_at = None


def load_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        for k in DEFAULTS:
            if k in saved and type(saved[k]) is type(DEFAULTS[k]):
                S[k] = saved[k]
            elif k in saved and isinstance(DEFAULTS[k], float) and isinstance(saved[k], int):
                S[k] = float(saved[k])
        print(f"Settings loaded from {SETTINGS_FILE}")
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Could not read settings ({e}); using defaults.")
    fix_settings()


def fix_settings():
    S["hand"] = "Left" if S["hand"] == "Left" else "Right"
    S["cutoff"] = clamp(float(S["cutoff"]), 0.15, 3.0)
    S["margin"] = clamp(float(S["margin"]), 0.0, 0.3)
    S["shift_x"] = clamp(float(S["shift_x"]), -0.5, 0.5)
    S["shift_y"] = clamp(float(S["shift_y"]), -0.5, 0.5)
    S["up_ratio"] = clamp(float(S["up_ratio"]), 1.0, 1.5)
    S["prec_pct"] = int(clamp(int(S["prec_pct"]), 5, 100))
    S["accel"] = int(clamp(int(S["accel"]), 0, 100))
    S["max_speed"] = int(clamp(int(S["max_speed"]), 100, 400))
    S["stroke_px"] = int(clamp(int(S["stroke_px"]), 0, 40))
    S["lock_px"] = int(clamp(int(S["lock_px"]), 2, 40))
    S["camera"] = int(clamp(int(S["camera"]), -1, 9))


def save_settings():
    global _dirty_at
    _dirty_at = None
    try:
        data = {k: S[k] for k in DEFAULTS}
        with open(SETTINGS_FILE + ".part", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(SETTINGS_FILE + ".part", SETTINGS_FILE)
    except Exception as e:
        print(f"Could not save settings: {e}")


def changed():
    global _dirty_at
    fix_settings()
    _dirty_at = time.time()      # saved a moment later, so dragging a slider doesn't write every frame


# --------------------------------------------------------- windows mouse ----
# SendInput, not SetCursorPos. SetCursorPos teleports the pointer without
# generating input events, so Paint never sees the drag and draws a straight
# line from press to release.

# Every move, press and release is a real Windows input event (SendInput),
# the same kind a physical mouse makes. (DPI awareness is set at the top of
# the file, so these numbers are real screen pixels.)

user32 = ctypes.WinDLL("user32", use_last_error=True)

MOVE, LDOWN, LUP = 0x0001, 0x0002, 0x0004
ABSOLUTE, VIRTUALDESK = 0x8000, 0x4000

VX, VY = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
VW = max(user32.GetSystemMetrics(78), 1)
VH = max(user32.GetSystemMetrics(79), 1)
SCREEN_W, SCREEN_H = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)

_held = False
_down_at = 0.0
_blocked_warned = False


class MI(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _U(ctypes.Union):
    _fields_ = [("mi", MI)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _send(flags, x=0, y=0):
    global _blocked_warned
    i = INPUT(type=0, mi=MI(x, y, 0, flags, 0, None))
    if user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT)) == 0 \
            and not _blocked_warned:
        _blocked_warned = True
        print("Windows blocked AirDraw's mouse input. If the app you're "
              "drawing in runs as administrator, run AirDraw as administrator too.")


# ----------------------------------------------- keep out of the taskbar ---
# WORK = the screen minus the taskbar (left, top, right, bottom). The hand
# zone maps onto this, and every mouse event is kept inside it, so the cursor
# never lands on the taskbar. If the taskbar auto-hides, a few pixels are kept
# off its edge so it doesn't pop up. Re-read every 2 s in case it moves.

class APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uCallbackMessage", wintypes.UINT), ("uEdge", wintypes.UINT),
                ("rc", wintypes.RECT), ("lParam", wintypes.LPARAM)]


AUTOHIDE_GAP = 4
WORK = [0, 0, SCREEN_W, SCREEN_H]


def refresh_work_area():
    l, t, r, b = 0, 0, SCREEN_W, SCREEN_H
    rc = wintypes.RECT()
    if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rc), 0):   # SPI_GETWORKAREA
        l, t, r, b = rc.left, rc.top, rc.right, rc.bottom
    try:
        sh = ctypes.windll.shell32
        sh.SHAppBarMessage.restype = ctypes.c_size_t
        abd = APPBARDATA(cbSize=ctypes.sizeof(APPBARDATA))
        if sh.SHAppBarMessage(4, ctypes.byref(abd)) & 1:                # auto-hide on
            if sh.SHAppBarMessage(5, ctypes.byref(abd)):                # where is it?
                edge = abd.uEdge                                        # 0 L, 1 T, 2 R, 3 B
                if edge == 0:
                    l += AUTOHIDE_GAP
                elif edge == 1:
                    t += AUTOHIDE_GAP
                elif edge == 2:
                    r -= AUTOHIDE_GAP
                else:
                    b -= AUTOHIDE_GAP
    except Exception:
        pass
    if r - l > 100 and b - t > 100:
        WORK[:] = [l, t, r, b]


def keep_in(x, y):
    """(x, y) moved inside the work area, i.e. off the taskbar."""
    return clamp(x, WORK[0], WORK[2] - 1), clamp(y, WORK[1], WORK[3] - 1)


def _abs(x, y):
    """Screen pixels -> the 0..65535 numbers SendInput wants. Always inside
    the work area, so no move or click can reach the taskbar."""
    x, y = keep_in(x, y)
    return (int((x - VX) * 65535 / max(VW - 1, 1)),
            int((y - VY) * 65535 / max(VH - 1, 1)))


def move(x, y):
    _send(MOVE | ABSOLUTE | VIRTUALDESK, *_abs(x, y))


def cursor_pos():
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return float(pt.x), float(pt.y)


def press():
    global _held, _down_at
    _send(LDOWN)
    _held = True
    _down_at = time.time()


def release():
    """Mouse button up - but never sooner than MIN_DOWN_S after the press, or
    Paint can miss the click."""
    global _held
    wait = MIN_DOWN_S - (time.time() - _down_at)
    if wait > 0:
        time.sleep(wait)
    _send(LUP)
    _held = False


def cleanup():
    global _held
    if _held:
        _send(LUP)
        _held = False


atexit.register(cleanup)

# ------------------------------------------------------ one AirDraw only ---
# A named lock that Windows frees by itself when AirDraw ends (even if it
# crashes). A second copy finds it taken and stops before touching the camera
# or the mouse - two copies would both move the cursor.

_instance_lock = None


def only_one_airdraw():
    global _instance_lock
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = wintypes.HANDLE
    _instance_lock = k32.CreateMutexW(None, False, "Local\\AirDraw-one-copy-only")
    if ctypes.get_last_error() == 183:                  # ERROR_ALREADY_EXISTS
        sys.exit("AirDraw is already running - this second copy won't start.\n"
                 "(Use the window that's already open, or close it first.)")


# -------------------------------------------------------------- hotkey ------


def toggle_armed():
    S["armed"] = not S["armed"]
    print("ARMED - your hand controls the mouse" if S["armed"]
          else "DISARMED - the mouse is yours")


_hotkey = None


def start_hotkey():
    global _hotkey
    if _kb is None:
        print("pynput is not installed, so F8 only works while the camera "
              "window is in front.\n  Run:  py -m pip install pynput")
        return
    try:
        _hotkey = _kb.GlobalHotKeys({"<f8>": toggle_armed})
        _hotkey.daemon = True
        _hotkey.start()
    except Exception as e:
        _hotkey = None
        print(f"Could not start the F8 hotkey ({e}); "
              f"F8 works only while the camera window is in front.")


# ------------------------------------------------------- space bar click ---
# Hold SPACE = mouse button down where the AirDraw cursor is; let go = up.
# A keyboard hook sees SPACE in any window (Paint too), but only uses it
# while AirDraw is armed and not paused - then it swallows the key, so Paint
# doesn't also get a space. Otherwise SPACE works as normal.
# The hook only notes the key; a small helper thread sends the mouse button,
# so the hook stays fast (Windows drops hooks that dawdle).

SPACE = {"down": False,      # AirDraw is holding the button for SPACE
         "click": False,     # ...this press was a CLICK-mode single click
         "paused": False,    # mirrors P (pause), set by the frame loop
         "d_down": False}    # D is held (so its key-repeat doesn't keep toggling)
_space_q = queue.Queue()


def _space_worker():
    while True:
        want = _space_q.get()
        try:
            if want == "down" and not _held:
                press()
            elif want == "up" and _held:
                release()
            elif want == "click" and not _held:
                press()
                release()            # waits MIN_DOWN_S, so Paint sees the click
        except Exception as e:
            print(f"(space click: {e})")


def space_event(down):
    """SPACE went down (True) or up (False). True = AirDraw used it."""
    if down:
        if SPACE["down"]:
            return True            # the key repeating while held: still ours
        if not S["armed"] or SPACE["paused"]:
            return False
        SPACE["down"] = True
        SPACE["click"] = not draw_mode_now()
        _space_q.put("click" if SPACE["click"] else "down")
        return True
    if not SPACE["down"]:
        return False
    SPACE["down"] = False          # let go: always lets the button up, even if disarmed meanwhile
    if not SPACE["click"]:
        _space_q.put("up")
    return True


def d_event(down):
    """D went down / up. While armed it toggles Draw mode (and Paint doesn't
    get the D). True = AirDraw used it."""
    if not down:
        was, SPACE["d_down"] = SPACE["d_down"], False
        return was
    if SPACE["d_down"]:
        return True                # key repeat
    if not S["armed"] or SPACE["paused"]:
        return False
    if user32.GetAsyncKeyState(0x11) & 0x8000 or user32.GetAsyncKeyState(0x12) & 0x8000:
        return False               # Ctrl+D / Alt+D belong to the app in front
    SPACE["d_down"] = True
    toggle_draw_mode()
    return True


def toggle_draw_mode():
    S["draw_mode"] = not S["draw_mode"]
    changed()
    print(f"Draw mode {'ON' if S['draw_mode'] else 'off'}")


# ---------------------------------------------------- Paint's selected tool ---
# A helper thread asks Windows UI Automation which tool Paint has selected,
# 5 times a second, but only while the cursor is over Paint. Paint names its
# canvas after the tool: "Using Pencil tool on Canvas", "Using Brush tool on
# Canvas", "Using Text tool on Canvas"... Pencil or any brush = DRAW mode;
# any other tool = CLICK mode. Not over Paint, or the name can't be read =
# the "Draw mode" setting decides.

DRAW_WORDS = ("pencil", "brush", "crayon", "marker")   # brush covers airbrush, oil, watercolor...
PAINT = {"over": False,      # the cursor is over a Paint window
         "tool": None,       # e.g. "Pencil", or None if it couldn't be read
         "draw": None}       # True / False from the tool, None = unknown


def draw_mode_now():
    """DRAW (True) or CLICK (False) for a SPACE press right now."""
    if PAINT["over"] and PAINT["draw"] is not None:
        return PAINT["draw"]
    return S["draw_mode"]


def mode_words():
    if PAINT["over"] and PAINT["draw"] is not None:
        return (f"{'DRAW' if PAINT['draw'] else 'CLICK'} - Paint's tool is {PAINT['tool']}")
    why = "Paint's tool can't be read" if PAINT["over"] else "not over Paint"
    return f"{'DRAW' if S['draw_mode'] else 'CLICK'} - Draw mode {'on' if S['draw_mode'] else 'off'} (D), {why}"


def _paint_watch():
    try:
        # imported here, in this thread: it sets up Windows' COM for this
        # thread only, away from the camera and the window
        from pywinauto.uia_defines import IUIA
    except Exception as e:
        print(f"(Can't read Paint's tool - {e}. Draw mode (D) decides.)")
        return
    ia = IUIA()
    cond = ia.iuia.CreatePropertyCondition(ia.UIA_dll.UIA_AutomationIdPropertyId, "image")
    names = {}                                   # process id -> exe name
    canvas = {}                                  # Paint window -> its canvas element
    k32 = ctypes.windll.kernel32
    while True:
        time.sleep(PAINT_POLL_S)
        try:
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            root = user32.GetAncestor(user32.WindowFromPoint(pt), 2)     # GA_ROOT
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(root, ctypes.byref(pid))
            if pid.value not in names:
                name = ""
                h = k32.OpenProcess(0x1000, False, pid.value)          # query limited
                if h:
                    buf = ctypes.create_unicode_buffer(512)
                    size = wintypes.DWORD(512)
                    if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                        name = os.path.basename(buf.value).lower()
                    k32.CloseHandle(h)
                names[pid.value] = name
            if names[pid.value] != "mspaint.exe":
                PAINT.update(over=False, tool=None, draw=None)
                continue
            el = canvas.get(root)
            text = None
            if el is not None:
                try:
                    text = el.CurrentName
                except Exception:
                    el = None
            if el is None:                       # first time, or Paint rebuilt it
                el = ia.iuia.ElementFromHandle(root).FindFirst(ia.tree_scope["descendants"], cond)
                canvas[root] = el
                text = el.CurrentName if el else None
            tool = None
            if text and text.startswith("Using ") and " tool" in text:
                tool = text[len("Using "):text.index(" tool")]
            PAINT.update(over=True, tool=tool,
                         draw=None if tool is None else any(w in tool.lower() for w in DRAW_WORDS))
        except Exception:
            PAINT.update(tool=None, draw=None)   # unreadable this time: Draw mode decides


def start_space_click():
    """Start the any-window SPACE hook. None if pynput isn't there (then
    SPACE only works while the AirDraw window is in front)."""
    threading.Thread(target=_space_worker, name="space-click", daemon=True).start()
    threading.Thread(target=_paint_watch, name="paint-tool", daemon=True).start()
    if _kb is None:
        return None
    holder = {}

    def filt(msg, data):
        if data.vkCode not in (0x20, 0x44) or data.flags & 0x10:   # not SPACE / D, or a made-up key
            return True
        down = msg in (0x0100, 0x0104)                   # WM_KEYDOWN, WM_SYSKEYDOWN
        used = space_event(down) if data.vkCode == 0x20 else d_event(down)
        if used:
            holder["l"].suppress_event()                 # Paint doesn't get it
        return True

    try:
        listener = _kb.Listener(win32_event_filter=filt)
        holder["l"] = listener
        listener.daemon = True
        listener.start()
        return listener
    except Exception as e:
        print(f"Could not start the SPACE hook ({e}); "
              f"SPACE only clicks while the AirDraw window is in front.")
        return None


# -------------------------------------------------------------- helpers -----


def d(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def d3(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


class OneEuro:
    """Smooths hard when your hand is nearly still, gets out of the way when
    you move fast. The standard fix for pointer jitter."""

    def __init__(self, cutoff, beta):
        self.cutoff, self.beta = cutoff, beta
        self.x = None
        self.dx = 0.0
        self.t = None

    def reset(self, x=None):
        self.x = x
        self.dx = 0.0
        self.t = None

    def __call__(self, x, t, scale=1.0):
        if self.x is None or self.t is None:
            self.x, self.t = x, t
            return x
        dt = max(t - self.t, 1e-3)
        self.t = t
        a_d = 1 / (1 + (1 / (2 * math.pi)) / dt)
        self.dx = a_d * ((x - self.x) / dt) + (1 - a_d) * self.dx
        c = max(self.cutoff * scale + self.beta * abs(self.dx), 0.05)
        a = 1 / (1 + (1 / (2 * math.pi * c)) / dt)
        self.x = a * x + (1 - a) * self.x
        return self.x


# -------------------------------------------------------------- cameras -----
# OpenCV numbers cameras 0, 1, 2... but can't say which is which. Windows'
# DirectShow lists the cameras in that same order WITH their names, so we ask
# it for the names and use them to find Camo (or any camera that isn't the
# laptop's own).

CAM_SCAN = range(10)           # indexes tried at startup, 0-9
CAM = {"live": 0,              # the camera in use right now
       "want": None,           # slider moved: switch to this one
       "names": [],            # DirectShow names, same order as the indexes
       "opened": [],           # indexes that opened at startup
       "note": ""}             # plain-words status for the settings panel
BUILT_IN_WORDS = ("integrated", "built-in", "builtin", "internal", "truevision", "true vision",
                  "wide vision", "hp hd camera", "facetime", "front", "rear", "ir camera")


class GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort),
                ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]

    def __init__(self, text):
        super().__init__()
        ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(self))


class VARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort),
                ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                ("val", ctypes.c_void_p), ("pad", ctypes.c_void_p)]


_VP, _UL = ctypes.c_void_p, ctypes.c_ulong


def _com(obj, index, argtypes=(), *args):
    """Call method number `index` of a COM object; returns the HRESULT."""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(_VP))).contents
    fn = ctypes.WINFUNCTYPE(ctypes.c_long, _VP, *argtypes)(vtbl[index])
    return fn(obj, *args)


def camera_names():
    """The cameras' names, in OpenCV's DirectShow order. [] if Windows won't say."""
    names = []
    try:
        ctypes.windll.ole32.CoInitialize(None)       # already on = fine
        dev_enum = ctypes.c_void_p()
        hr = ctypes.windll.ole32.CoCreateInstance(
            ctypes.byref(GUID("{62BE5D10-60EB-11d0-BD3B-00A0C911CE86}")), None, 1,
            ctypes.byref(GUID("{29840822-5B84-11D0-BD3B-00A0C911CE86}")), ctypes.byref(dev_enum))
        if hr != 0:
            return names
        monikers = ctypes.c_void_p()
        # ICreateDevEnum::CreateClassEnumerator(video input category)
        hr = _com(dev_enum, 3, (_VP, _VP, _UL),
                  ctypes.byref(GUID("{860BB310-5D01-11d0-BD3B-00A0C911CE86}")),
                  ctypes.byref(monikers), 0)
        if hr == 0 and monikers:
            iid_bag = GUID("{55272A00-42CB-11CE-8135-00AA004BB851}")
            while True:
                mon = ctypes.c_void_p()
                got = _UL()
                # IEnumMoniker::Next
                if _com(monikers, 3, (_UL, _VP, _VP), 1, ctypes.byref(mon),
                        ctypes.byref(got)) != 0 or not got.value:
                    break
                bag = ctypes.c_void_p()
                name = ""
                # IMoniker::BindToStorage -> IPropertyBag
                if _com(mon, 9, (_VP, _VP, _VP, _VP), None, None, ctypes.byref(iid_bag),
                        ctypes.byref(bag)) == 0 and bag:
                    var = VARIANT()
                    # IPropertyBag::Read("FriendlyName")
                    if _com(bag, 3, (ctypes.c_wchar_p, _VP, _VP), "FriendlyName",
                            ctypes.byref(var), None) == 0 and var.vt == 8 and var.val:   # VT_BSTR
                        name = ctypes.wstring_at(var.val)
                    ctypes.windll.oleaut32.VariantClear(ctypes.byref(var))
                    _com(bag, 2)                 # Release
                _com(mon, 2)
                names.append(name)
            _com(monikers, 2)
        _com(dev_enum, 2)
    except Exception as e:
        print(f"(Could not read camera names: {e})")
    return names


def cam_name(i):
    n = CAM["names"][i] if i < len(CAM["names"]) else ""
    return n or "unknown name"


def pick_auto_camera(opened):
    """Camo if it's there, else the first camera that isn't the laptop's own,
    else 0 (or whatever opened)."""
    names = CAM["names"]
    for i in opened:
        if i < len(names) and "camo" in names[i].lower():
            return i
    for i in opened:
        if i < len(names) and names[i] and \
                not any(w in names[i].lower() for w in BUILT_IN_WORDS):
            return i
    if not names:                       # no names at all: 0 is usually the laptop's
        for i in opened:
            if i != 0:
                return i
    return 0 if 0 in opened or not opened else opened[0]


def scan_cameras():
    CAM["names"] = camera_names()
    opened = []
    print("Looking for cameras 0-9...")
    for i in CAM_SCAN:
        c = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if c.isOpened():
            opened.append(i)
            print(f"  camera {i}: opens  ({cam_name(i)})")
        c.release()
    if not opened:
        print("  none opened")
    CAM["opened"] = opened
    return opened


def open_camera(i):
    """Open camera i with DirectShow at 640x480, 30 fps. None if it won't open."""
    for backend in (cv2.CAP_DSHOW,):
        c = cv2.VideoCapture(i, backend)
        if c.isOpened():
            c.set(cv2.CAP_PROP_FRAME_WIDTH, CAM_W)
            c.set(cv2.CAP_PROP_FRAME_HEIGHT, CAM_H)
            c.set(cv2.CAP_PROP_FPS, MAX_FPS)
            c.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            print(f"Camera {i} ({cam_name(i)}): {int(c.get(cv2.CAP_PROP_FRAME_WIDTH))}x"
                  f"{int(c.get(cv2.CAP_PROP_FRAME_HEIGHT))}, capped at {MAX_FPS} fps")
            return c
        c.release()
    return None


def switch_camera(cap, i):
    """Swap to camera i. Returns the capture to use (the old one if i fails)."""
    new = open_camera(i)
    if new is None and cap is not None:
        cap.release()                   # some laptops can't run two cameras at once
        new = open_camera(i)
        if new is None:
            print(f"Camera {i} didn't open - going back to camera {CAM['live']}.")
            CAM["note"] = f"camera {i} didn't open"
            return open_camera(CAM["live"])
    elif new is None:
        print(f"Camera {i} didn't open.")
        CAM["note"] = f"camera {i} didn't open"
        return cap
    elif cap is not None:
        cap.release()
    CAM["live"] = i
    CAM["note"] = ""
    return new


def get_model():
    path = os.path.join(HERE, MODEL_NAME)
    if os.path.exists(path) and os.path.getsize(path) > 100_000:
        return path
    print("Downloading hand model (~7 MB), one time only...")
    urllib.request.urlretrieve(MODEL_URL, path + ".part")
    os.replace(path + ".part", path)
    print("done.\n")
    return path


def catmull(a, b, c, dd, t):
    t2, t3 = t * t, t * t * t
    x = 0.5 * ((2 * b[0]) + (-a[0] + c[0]) * t +
               (2 * a[0] - 5 * b[0] + 4 * c[0] - dd[0]) * t2 +
               (-a[0] + 3 * b[0] - 3 * c[0] + dd[0]) * t3)
    y = 0.5 * ((2 * b[1]) + (-a[1] + c[1]) * t +
               (2 * a[1] - 5 * b[1] + 4 * c[1] - dd[1]) * t2 +
               (-a[1] + 3 * b[1] - 3 * c[1] + dd[1]) * t3)
    return x, y


# ------------------------------------------------------ settings window -----
# OpenCV sliders only take whole numbers, so each one converts to and from the
# real setting. Every frame the sliders and the settings are synced both ways,
# so F8 and the keyboard keys move the matching slider too.


def _set(k, v):
    S[k] = v
    if k != "armed":
        changed()


SLIDERS = [
    # label,            low, high, setting -> slider,                          slider -> setting
    ("Armed (F8)",        0,   1, lambda: int(S["armed"]),                    lambda v: _set("armed", bool(v))),
    ("Hand: 0 L  1 R",    0,   1, lambda: int(S["hand"] == "Right"),          lambda v: _set("hand", "Right" if v else "Left")),
    ("Flip L/R (H)",      0,   1, lambda: int(S["invert"]),                   lambda v: _set("invert", bool(v))),
    ("Smoothing",         0, 100, lambda: int(round((3.0 - S["cutoff"]) / 2.85 * 100)),
                                  lambda v: _set("cutoff", round(3.0 - v / 100 * 2.85, 3))),
    ("Zone size %",      40, 100, lambda: int(round((1 - 2 * S["margin"]) * 100)),
                                  lambda v: _set("margin", round((1 - v / 100) / 2, 3))),
    # The zone may move partly past the camera picture's edge on purpose, so
    # you can put it where your arm is comfortable.
    ("Zone left/right",  -50,  50, lambda: int(round(S["shift_x"] * 100)),     lambda v: _set("shift_x", v / 100)),
    ("Zone up/down",     -50,  50, lambda: int(round(S["shift_y"] * 100)),     lambda v: _set("shift_y", v / 100)),
    ("Fingers up x100", 100, 150, lambda: int(round(S["up_ratio"] * 100)),    lambda v: _set("up_ratio", v / 100)),
    ("Precision",         0,   1, lambda: int(S["precision"]),                lambda v: _set("precision", bool(v))),
    ("Precision speed %", 5, 100, lambda: int(S["prec_pct"]),                 lambda v: _set("prec_pct", int(v))),
    ("Accel (0 off)",     0, 100, lambda: int(S["accel"]),                    lambda v: _set("accel", int(v))),
    ("Max speed %",     100, 400, lambda: int(S["max_speed"]),                lambda v: _set("max_speed", int(v))),
    ("Stroke px (0 off)", 0,  40, lambda: int(S["stroke_px"]),                lambda v: _set("stroke_px", int(v))),
    ("Clutch (fist)",     0,   1, lambda: int(S["clutch"]),                   lambda v: _set("clutch", bool(v))),
    ("Draw mode (D)",     0,   1, lambda: int(S["draw_mode"]),                lambda v: _set("draw_mode", bool(v))),
    ("Axis lock",         0,   1, lambda: int(S["axis_lock"]),                lambda v: _set("axis_lock", bool(v))),
    ("Lock threshold px", 2,  40, lambda: int(S["lock_px"]),                  lambda v: _set("lock_px", int(v))),
    ("Depth steady",      0,   1, lambda: int(S["depth_steady"]),             lambda v: _set("depth_steady", bool(v))),
    # Camera: shows the one in use; picking one switches live and saves the choice.
    ("Camera",            0,   9, lambda: CAM["want"] if CAM["want"] is not None else CAM["live"],
                                  lambda v: pick_camera(int(v))),
]


def pick_camera(i):
    """Slider moved: remember the choice and ask the main loop to switch."""
    CAM["want"] = i
    _set("camera", i)


# ------------------------------------------------------------ the window -----
# One window: the camera picture on the left, the settings on the right. A
# drop-down at the top of the settings picks a page, and only that page's
# settings show, so nothing ends up off the bottom of the screen. Hand, Tap
# spot and Camera are drop-downs; on/off settings are tick boxes; numbers are
# sliders. It reads and writes the same settings (S) as everything else, so
# F8 and the keys show up here too.
# The frame loop (frame_loop below) runs inside this window: Tkinter's after()
# asks it for the next frame, so there is only one loop and one window.

SECTIONS = [
    ("Basics", ["Armed (F8)", "Hand: 0 L  1 R", "Flip L/R (H)", "Camera",
                "Clutch (fist)", "Fingers up x100", "Draw mode (D)"]),
    ("Movement", ["Smoothing", "Precision", "Precision speed %", "Accel (0 off)",
                  "Max speed %", "Stroke px (0 off)",
                  "Axis lock", "Lock threshold px", "Depth steady"]),
    ("Zone", ["Zone size %", "Zone left/right", "Zone up/down"]),
]
TK_NAMES = {"Hand: 0 L  1 R": "Hand", "Flip L/R (H)": "Flip left/right (H)",
            "Fingers up x100": "Finger straightness (x100)",
            "Stroke px (0 off)": "Stroke px (0 = off)"}


def _camera_choices():
    idx = sorted(set(CAM["opened"]) | set(range(len(CAM["names"]))) | {CAM["live"]})
    return [(f"{i}: {cam_name(i)}" + ("" if i in CAM["opened"] else "  (didn't open)"), i)
            for i in idx if 0 <= i <= 9]


TK_CHOICES = {
    "Hand: 0 L  1 R": lambda: [("Left hand", 0), ("Right hand", 1)],
    "Camera": _camera_choices,
}

KEYS = deque()          # keys pressed in the window, read by the frame loop
QUIT = [False]          # the window was closed: the frame loop stops


class SettingsPanel:
    def __init__(self, root, parent):
        self.root = root
        self.status = tk.StringVar()
        tk.Label(parent, textvariable=self.status, justify="left", anchor="w",
                 font=("Segoe UI", 9), fg="#444").pack(fill="x", pady=(8, 4))

        top = tk.Frame(parent)
        top.pack(fill="x", pady=4)
        tk.Label(top, text="Settings page:", font=("Segoe UI", 10, "bold")).pack(side="left")
        self.page = ttk.Combobox(top, state="readonly", width=18,
                                 values=[name for name, _ in SECTIONS])
        self.page.pack(side="left", padx=8)
        self.page.bind("<<ComboboxSelected>>", lambda e: self.show(self.page.get()))

        by_name = {s[0]: s for s in SLIDERS}
        self.rows = []      # (kind, slider entry, widget var, combobox / None)
        self.frames = {}
        for section, labels in SECTIONS:
            fr = tk.Frame(parent)
            self.frames[section] = fr
            for r, label in enumerate(labels):
                entry = by_name[label]
                name, lo, hi, get, put = entry
                tk.Label(fr, text=TK_NAMES.get(name, name), anchor="w", width=24).grid(
                    row=r, column=0, sticky="w", padx=(0, 8), pady=3)
                if name in TK_CHOICES:
                    var = tk.StringVar()
                    cb = ttk.Combobox(fr, textvariable=var, state="readonly", width=34)
                    cb.grid(row=r, column=1, sticky="w")
                    cb.bind("<<ComboboxSelected>>",
                            lambda e, n=name, v=var, p=put, g=get: self.choose(n, v, p, g))
                    self.rows.append(("choice", entry, var, cb))
                elif lo == 0 and hi == 1:
                    var = tk.IntVar(value=get())
                    # takefocus off: SPACE stays the click, it doesn't tick boxes
                    tk.Checkbutton(fr, variable=var, takefocus=0,
                                   command=lambda v=var, p=put, g=get: (
                                       p(v.get()) if v.get() != g() else None)
                                   ).grid(row=r, column=1, sticky="w")
                    self.rows.append(("tick", entry, var, None))
                else:
                    var = tk.IntVar(value=clamp(get(), lo, hi))
                    tk.Scale(fr, from_=lo, to=hi, orient="horizontal", length=260,
                             variable=var, showvalue=True, takefocus=0,
                             command=lambda s, p=put, g=get: (
                                 p(int(float(s))) if int(float(s)) != g() else None)
                             ).grid(row=r, column=1, sticky="w")
                    self.rows.append(("number", entry, var, None))

        tk.Label(parent, text="Saved automatically to airdraw_settings.json",
                 font=("Segoe UI", 8), fg="#777").pack(side="bottom", anchor="w", pady=(4, 8))
        self.current = None
        self.page.set(SECTIONS[0][0])
        self.show(SECTIONS[0][0])
        self.poll()

    def show(self, section):
        if self.current is not None:
            self.frames[self.current].pack_forget()
        self.frames[section].pack(fill="x", padx=10, pady=(4, 6))
        self.current = section

    def choose(self, name, var, put, get):
        for text, value in TK_CHOICES[name]():
            if text == var.get():
                if value != get():
                    put(value)
                return

    def poll(self):
        """Every 1/4 s: pick up changes made by F8, the keys or the main loop."""
        try:
            for kind, (name, lo, hi, get, _put), var, cb in self.rows:
                want = get()
                if kind == "choice":
                    opts = TK_CHOICES[name]()
                    texts = [t for t, _ in opts]
                    if list(cb.cget("values")) != texts:
                        cb.configure(values=texts)
                    now_txt = next((t for t, v in opts if v == want), str(want))
                    if var.get() != now_txt:
                        var.set(now_txt)
                else:
                    want = clamp(want, lo, hi)
                    if var.get() != want:
                        var.set(want)
            self.status.set("\n".join(t for t, _ in panel_lines()[:2]
                                      + [l for l in panel_lines() if l[0].startswith("Camera")]))
        except Exception as e:          # never let one bad read kill the window
            print(f"(settings window: {e})")
        self.root.after(250, self.poll)


def tk_key(event):
    """A key pressed in the window -> the same codes cv2.waitKeyEx gave."""
    ks = event.keysym
    if ks == "Escape":
        KEYS.append(27)
    elif ks == "F8":
        KEYS.append(0x770000)
    elif ks == "space":
        pass                       # SPACE is the click (space_event), not a key here
    elif event.char and len(event.char) == 1:
        KEYS.append(ord(event.char))


def shift_words():
    """The zone shift in plain words, e.g. 'right 12%, up 5%' or 'centered'."""
    sx, sy = round(S["shift_x"] * 100), round(S["shift_y"] * 100)
    parts = []
    if sx:
        parts.append(f"{'right' if sx > 0 else 'left'} {abs(sx)}%")
    if sy:
        parts.append(f"{'up' if sy > 0 else 'down'} {abs(sy)}%")
    return ", ".join(parts) or "centered"


def shift_short():
    """Short form for the camera window's bottom strip, e.g. '  zone R12 U5'."""
    sx, sy = round(S["shift_x"] * 100), round(S["shift_y"] * 100)
    if not sx and not sy:
        return ""
    return (f"   zone {'R' if sx >= 0 else 'L'}{abs(sx)}"
            f" {'U' if sy >= 0 else 'D'}{abs(sy)}")


def panel_lines():
    """The settings summary as (text, BGR colour) lines, shown as plain text
    at the top of the settings."""
    lines = [
        ("ARMED - your hand moves the mouse" if S["armed"]
         else "DISARMED - press F8 to take control", (90, 220, 90) if S["armed"] else (150, 150, 150)),
        (f"Hand: {S['hand']}  (other hand ignored)    Flip L/R: {'on' if S['invert'] else 'off'}", (230, 230, 230)),
        (f"SPACE: {mode_words()}", (230, 230, 230)),
        ((f"Axis lock: on - a held line snaps flat or upright after {S['lock_px']} px, turns switch it"
          if S["axis_lock"] else "Axis lock: off"), (230, 230, 230)),
        ((f"Drawing: short strokes, {S['stroke_px']} px per small hand move (~1 cm)"
          if S["stroke_px"] else "Drawing: full-speed movement (stroke px is 0)"), (230, 230, 230)),
        (("Clutch: ON - closed fist freezes the cursor, open hand resumes"
          if S["clutch"] else "Clutch: off"), (230, 230, 230)),
        (f"Camera {CAM['live']}: {cam_name(CAM['live'])}"
         + (f"   (saved choice {S['camera']} not found)" if S["camera"] not in (-1, CAM["live"]) else "")
         + (f"   - {CAM['note']}" if CAM["note"] else ""),
         (0, 165, 255) if CAM["note"] or S["camera"] not in (-1, CAM["live"]) else (230, 230, 230)),
        (f"Precision: {'ON' if S['precision'] else 'off'} at {S['prec_pct']}% speed, accel {S['accel']} to {S['max_speed']}%    "
         f"Zone {1 - 2 * S['margin']:.0%}", (230, 230, 230)),
        (f"Zone moved: {shift_words()}", (230, 230, 230)),
        ("Saved automatically to airdraw_settings.json", (160, 160, 160)),
    ]
    return lines


# ----------------------------------------------------------------- main -----


def pick_hand(res):
    """The landmarks of the chosen hand, or None. The other hand is ignored."""
    for lms, hd in zip(res.hand_landmarks, res.handedness):
        name = hd[0].category_name
        if S["invert"]:
            name = "Left" if name == "Right" else "Right"
        if name == S["hand"]:
            return lms
    return None


def frame_loop():
    """Everything AirDraw does per camera frame. A generator: it pauses
    (yield) where it used to show the picture or sleep, and the window's
    after() timer resumes it. Yields ("ready", None) once set up, then
    ("frame", picture) after each frame and ("wait", seconds) for the 30 fps
    cap."""
    load_settings()
    model = get_model()

    cv2.setNumThreads(1)       # OpenCV's part here is tiny; don't let it grab every core

    # Cameras: scan 0-9, then use the camera you picked with the slider if
    # it's there. Not picked yet: Camo / the first non-built-in camera, else
    # 0 - checked fresh every start, so Camo is found whenever it's connected.
    # If your picked camera is missing today (e.g. the phone isn't connected),
    # another one is used for now but your choice is kept.
    opened = scan_cameras()
    if S["camera"] in opened:
        first = S["camera"]
    else:
        first = pick_auto_camera(opened)
        if S["camera"] == -1:
            print(f"Auto-picked camera {first} ({cam_name(first)}). "
                  f"Move the Camera slider to choose one for good.")
        else:
            print(f"Your saved camera {S['camera']} didn't open - using camera {first} for now.")
    cap = None
    for i in [first] + [j for j in opened if j != first] + ([CAM_INDEX] if not opened else []):
        cap = open_camera(i)
        if cap is not None:
            CAM["live"] = i
            break
    if cap is None:
        sys.exit("Could not open the camera.\n"
                 "  - End any leftover python.exe in Task Manager\n"
                 "  - Settings > Privacy & security > Camera > allow desktop apps\n"
                 "  - Close Zoom / Teams / the Camera app")

    landmarker = mp.tasks.vision.HandLandmarker.create_from_options(
        mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=model),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_hands=2,               # both are found, then only the chosen one is used
            min_hand_detection_confidence=0.6,
            min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5))

    fx, fy = OneEuro(S["cutoff"], BETA), OneEuro(S["cutoff"], BETA)
    spline = deque(maxlen=4)

    pen = False                # the mouse button is down (SPACE held)
    offset = [0.0, 0.0]
    paused = False
    prec_prev = None           # precision mode: last absolute position, for the hand's movement
    prec_cur = None            # precision mode: where the cursor is
    # Short strokes: while the button is held the cursor moves by the hand's
    # movement, measured in hand sizes so it's the same at any camera distance.
    sfx, sfy = OneEuro(S["cutoff"], BETA), OneEuro(S["cutoff"], BETA)
    stroke_cur = None          # where the cursor is during a short stroke
    stroke_prev = None         # last smoothed knuckle position (camera pixels)
    hand_size = None           # smoothed wrist-to-knuckle length (camera pixels)
    clutched = False           # clutch: closed fist froze the cursor
    fist_since = open_since = None
    clutch_off = [0.0, 0.0]    # clutch, normal mode: shift between hand and cursor
    depth_ref = None           # depth steady: your usual hand size (camera pixels)
    lock_start = None          # axis lock: where the held line started
    lock_axis = None           # "h" (flat) or "v" (upright) once decided
    lock_ref = None            # axis lock: the hand's (unlocked) point that lock_start matches
    lock_out = None            # axis lock: where the locked line put the cursor last
    turn_ref = None            # axis lock: a point trailing the hand by ~TURN_S, to spot a turn
    acc_v = None               # accel: smoothed hand speed (screen widths a second)
    work_at = 0.0              # when the taskbar area was last checked
    refresh_work_area()
    next_frame = time.time()   # the 30 fps cap
    t0 = time.time()
    last_ms = -1
    fps = 0.0
    last = time.time()

    start_hotkey()
    space_hook = start_space_click()

    print(f"AirDraw running. Screen {SCREEN_W}x{SCREEN_H} ({dpi_report()}). Hand: {S['hand']}.")
    print("DISARMED - press F8 to take control.")
    print("Open hand = move the cursor.  Closed fist = freeze it.  "
          "SPACE = draw (held) or click, by Paint's tool or D.  P = pause.  ESC = quit.")
    if not space_hook:
        print("  (SPACE only clicks while the AirDraw window is in front - "
              "install pynput for it to work in Paint:  py -m pip install pynput)")
    print()

    try:
        yield ("ready", None)      # set up: the window can be built now
        while True:
            if QUIT[0]:            # the window was closed
                break

            # 30 fps cap: if we're early, rest instead of grabbing another frame
            # (the window waits, so it stays responsive meanwhile)
            wait = next_frame - time.time()
            if wait > 0.002:
                yield ("wait", wait)
            next_frame = max(next_frame + 1.0 / MAX_FPS, time.time())

            # Camera picked in the drop-down: switch live, and forget the old
            # hand position, since the new picture is different.
            if CAM["want"] is not None:
                want, CAM["want"] = CAM["want"], None
                if want != CAM["live"]:
                    cap = switch_camera(cap, want)
                    if CAM["live"] != want:          # didn't open: don't remember it
                        S["camera"] = CAM["live"]
                        changed()
                    fx.reset()
                    fy.reset()
                    spline.clear()
                    stroke_cur = prec_prev = hand_size = depth_ref = None
                    if cap is None:
                        sys.exit("Lost the camera while switching.")

            ok, frame = cap.read()
            if not ok:
                # No picture (camera busy or unplugged). Keep the window and
                # settings working so you can pick another camera.
                if KEYS and KEYS.popleft() & 0xFF in (27, ord("q")):
                    break
                yield ("wait", 0.03)
                continue
            if frame.shape[1] != CAM_W or frame.shape[0] != CAM_H:
                frame = cv2.resize(frame, (CAM_W, CAM_H))   # always 640x480
            frame = cv2.flip(frame, 1)
            h, w = frame.shape[:2]
            now = time.time()

            if _dirty_at is not None and now - _dirty_at > 0.8:
                save_settings()
            if now - work_at > 2.0:              # taskbar moved / resolution changed?
                work_at = now
                refresh_work_area()
            fx.cutoff = fy.cutoff = S["cutoff"]
            sfx.cutoff = sfy.cutoff = S["cutoff"] * DRAW_STEADY
            margin = S["margin"]
            active = S["armed"] and not paused
            SPACE["paused"] = paused

            ms = int((now - t0) * 1000)
            if ms <= last_ms:
                ms = last_ms + 1
            last_ms = ms
            res = landmarker.detect_for_video(
                mp.Image(image_format=mp.ImageFormat.SRGB,
                         data=np.ascontiguousarray(
                             cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))), ms)

            # The zone: shrunk by the margin, then slid by the shift sliders.
            # (Up is +, but picture rows count downward, hence the minus.)
            sx, sy = S["shift_x"] * w, -S["shift_y"] * h
            x0, x1 = margin * w + sx, (1 - margin) * w + sx
            y0, y1 = margin * h + sy, (1 - margin) * h + sy

            status, color = f"no {S['hand'].lower()} hand", (150, 150, 150)
            hand = pick_hand(res)

            # --- the mouse button: SPACE (see space_event) ---
            # The space bar presses and releases the button itself, the moment
            # you press it. Here we only notice it, to start or end a stroke.
            down_now = _held
            if down_now and not pen:
                start = cursor_pos()             # pressed right where the cursor is
                spline.clear()
                spline.append(start)
                stroke_cur = [start[0], start[1]]
                stroke_prev = None
                sfx.reset()
                sfy.reset()
                pen_started = True
                lock_start, lock_axis = (start[0], start[1]), None
                lock_ref, lock_out, turn_ref = lock_start, None, None
            else:
                pen_started = False
            offset_from_stroke = None
            if pen and not down_now and stroke_cur is not None and S["stroke_px"] > 0:
                # carry on from where the stroke ended (normal mode: then
                # glide back to where your hand points). With axis lock, that's
                # where the locked line ended.
                end = list(lock_out) if lock_out is not None else stroke_cur
                if S["precision"]:
                    prec_cur = list(end)
                    prec_prev = None
                offset_from_stroke = end
            if pen and not down_now:
                stroke_cur = None
                spline.clear()
            pen = down_now

            if hand is not None:
                p = [(lm.x * w, lm.y * h, lm.z * w) for lm in hand]
                scale = max(d(p[0], p[9]), 1e-3)
                hand_size = scale if hand_size is None else 0.85 * hand_size + 0.15 * scale

                # Each finger: up when its tip is clearly farther from the
                # wrist than its middle joint is, folded when well below that.
                ups = [d3(p[tip], p[0]) / max(d3(p[pip], p[0]), 1e-3)
                       for pip, tip in ((10, 12), (14, 16), (18, 20))]
                stroke = pen and S["stroke_px"] > 0

                # The cursor follows the middle finger's base knuckle (landmark
                # 9). It barely moves when you make a fist or open your hand.
                aim = (p[9][0], p[9][1])
                # Depth steady: a camera sees everything drift outward from the
                # middle of the picture as it comes closer, so moving your hand
                # toward or away from the camera used to slide the cursor. The
                # hand's size in the picture tells how close it is; scaling the
                # knuckle's offset from the middle by (usual size / size now)
                # cancels that drift. The usual size is a slow average, and the
                # size now is smoothed, so tilting your hand doesn't jolt it.
                depth_ref = hand_size if depth_ref is None else \
                    depth_ref + (hand_size - depth_ref) * min(1.0, (now - last) / DEPTH_REF_S)
                dk = depth_ref / hand_size if S["depth_steady"] else 1.0
                dk = clamp(dk, 0.6, 1.6)
                aim = (w / 2 + (aim[0] - w / 2) * dk, h / 2 + (aim[1] - h / 2) * dk)

                # zone -> work area (the screen minus the taskbar)
                mx = WORK[0] + clamp((aim[0] - x0) / max(x1 - x0, 1), 0, 1) * (WORK[2] - WORK[0] - 1)
                my = WORK[1] + clamp((aim[1] - y0) / max(y1 - y0, 1), 0, 1) * (WORK[3] - WORK[1] - 1)
                mx = fx(mx, now, DRAW_STEADY if pen else 1.0)
                my = fy(my, now, DRAW_STEADY if pen else 1.0)

                # --- clutch: closed fist = cursor stays put while you move
                # your hand; open hand = carry on from where the cursor stopped.
                # Like lifting a real mouse off the desk to re-centre it.
                # Fist = index, middle, ring and pinky all folded. Open = all
                # four raised. Each must last CLUTCH_S, so a twitch does nothing.
                idx_up = d3(p[8], p[0]) / max(d3(p[6], p[0]), 1e-3)
                four = [idx_up] + ups
                fist_raw = S["clutch"] and all(u < S["up_ratio"] - FOLD_GAP for u in four)
                open_raw = all(u > S["up_ratio"] for u in four)
                if not S["clutch"]:
                    clutched = False
                    fist_since = open_since = None
                elif not clutched:
                    open_since = None
                    if fist_raw:
                        fist_since = now if fist_since is None else fist_since
                        if now - fist_since >= CLUTCH_S:
                            clutched = True
                    else:
                        fist_since = None
                else:
                    fist_since = None
                    if open_raw:
                        open_since = now if open_since is None else open_since
                        if now - open_since >= CLUTCH_S:
                            clutched = False
                            open_since = None
                            # resume from wherever the cursor really is now
                            here = cursor_pos()
                            if S["precision"]:
                                prec_cur = list(here)
                                prec_prev = None
                            else:
                                clutch_off[0], clutch_off[1] = here[0] - mx, here[1] - my
                            offset[0] = offset[1] = 0.0
                    else:
                        open_since = None
                hold = clutched or fist_raw      # frozen (also while a fist is forming)

                if not S["precision"]:
                    mx, my = keep_in(mx + clutch_off[0], my + clutch_off[1])

                # Precision mode: move by the hand's movement, scaled down,
                # starting from wherever the cursor already is.
                if S["precision"]:
                    if prec_cur is None:
                        prec_cur = list(cursor_pos())
                    # a short stroke moves it instead; the clutch stops it
                    if prec_prev is not None and not stroke and not hold:
                        k = S["prec_pct"] / 100.0
                        ddx, ddy = mx - prec_prev[0], my - prec_prev[1]
                        # Accel: slow hand = precision speed; the faster the
                        # hand, the closer the speed gets to "Max speed", so
                        # one quick open-palm sweep crosses the whole screen.
                        v = math.hypot(ddx, ddy) / max(WORK[2] - WORK[0], 1) / max(now - last, 1e-3)
                        acc_v = v if acc_v is None else acc_v + (v - acc_v) * 0.5
                        if S["accel"] > 0:
                            top = max(k, S["max_speed"] / 100.0)
                            k += (top - k) * (1 - math.exp(-S["accel"] / 100 * ACCEL_K
                                                           * max(0.0, acc_v - ACCEL_FROM)))
                        prec_cur[0], prec_cur[1] = keep_in(prec_cur[0] + ddx * k,
                                                           prec_cur[1] + ddy * k)
                    prec_prev = (mx, my)
                    mx, my = prec_cur
                else:
                    prec_prev = prec_cur = None

                if pen_started:                  # the stroke starts where the cursor is
                    offset[0], offset[1] = stroke_cur[0] - mx, stroke_cur[1] - my
                if offset_from_stroke is not None:
                    offset[0] = offset_from_stroke[0] - mx
                    offset[1] = offset_from_stroke[1] - my

                # While clutched nothing moves: the cursor stays where it is.
                if active and not hold:
                    # --- movement ---
                    tx, ty = mx + offset[0], my + offset[1]
                    if not pen:
                        offset[0] *= 0.85
                        offset[1] *= 0.85
                    elif S["stroke_px"] > 0:
                        # Short strokes: follow the knuckle's movement, scaled so
                        # a small hand move (~1 cm) moves the cursor stroke_px.
                        if stroke_cur is None:          # e.g. the hand came back mid-stroke
                            stroke_cur = list(cursor_pos())
                            stroke_prev = None
                        nx, ny = sfx(aim[0], now), sfy(aim[1], now)
                        if stroke_prev is not None:
                            # (with depth steady, aim is already in "usual size" pixels)
                            k = S["stroke_px"] / (SMALL_MOVE * (depth_ref if S["depth_steady"] else hand_size))
                            stroke_cur[0], stroke_cur[1] = keep_in(stroke_cur[0] + (nx - stroke_prev[0]) * k,
                                                                   stroke_cur[1] + (ny - stroke_prev[1]) * k)
                        stroke_prev = (nx, ny)
                        tx, ty = stroke_cur

                    # Axis lock (draw mode): hold still until the line has
                    # moved "Lock threshold px", then keep it flat (mostly
                    # left/right) or upright (mostly up/down) until SPACE goes up.
                    # Turn clearly onto the other axis (recent movement past the
                    # threshold, at least twice as much that way) and the lock
                    # switches there, from the corner - for corners and plus
                    # signs in one stroke. Slow wobble doesn't count: the
                    # "recent" point trails the hand, so drift never builds up.
                    # (tx, ty) is the hand's unlocked point; lock_start is the
                    # current line's start, lock_ref the hand's point there.
                    if pen and S["axis_lock"] and not SPACE["click"] and lock_start is not None:
                        if turn_ref is None:
                            turn_ref = [tx, ty]
                        rdx, rdy = tx - turn_ref[0], ty - turn_ref[1]
                        a_ = min(1.0, (now - last) / TURN_S)
                        turn_ref[0] += (tx - turn_ref[0]) * a_
                        turn_ref[1] += (ty - turn_ref[1]) * a_
                        lp = S["lock_px"]
                        if lock_axis is None:
                            dx, dy = tx - lock_ref[0], ty - lock_ref[1]
                            if math.hypot(dx, dy) >= lp:
                                lock_axis = "h" if abs(dx) >= abs(dy) else "v"
                        elif lock_axis == "h" and abs(rdy) >= lp and abs(rdy) >= 2 * abs(rdx):
                            lock_start = (lock_start[0] + tx - lock_ref[0], lock_start[1])
                            lock_ref, lock_axis = (tx, ty - rdy), "v"
                        elif lock_axis == "v" and abs(rdx) >= lp and abs(rdx) >= 2 * abs(rdy):
                            lock_start = (lock_start[0], lock_start[1] + ty - lock_ref[1])
                            lock_ref, lock_axis = (tx - rdx, ty), "h"
                        if lock_axis == "h":
                            tx, ty = lock_start[0] + tx - lock_ref[0], lock_start[1]
                        elif lock_axis == "v":
                            tx, ty = lock_start[0], lock_start[1] + ty - lock_ref[1]
                        else:
                            tx, ty = lock_start
                        lock_out = (tx, ty)

                    if pen:
                        spline.append((tx, ty))
                        if len(spline) == 4:
                            a, b, c2, dd = spline
                            steps = int(clamp(d(b, c2) / 4, 1, 24))
                            for i in range(1, steps + 1):
                                cx, cy = catmull(a, b, c2, dd, i / steps)
                                move(int(cx), int(cy))
                        else:
                            move(int(tx), int(ty))
                    else:
                        move(int(tx), int(ty))
                elif not active and S["precision"]:
                    prec_cur = None          # disarmed or paused: the cursor is yours again

                # --- overlay: faint hand, fingertip dots, cursor ring ---
                for a, b in CONNECTIONS:
                    cv2.line(frame, (int(p[a][0]), int(p[a][1])),
                             (int(p[b][0]), int(p[b][1])), (150, 150, 150), 1, cv2.LINE_AA)
                # fingertips: green = up, grey = folded, white = in between.
                # All four green = open hand (moves / resumes), all grey = fist.
                for i, u in ((8, idx_up), (12, ups[0]), (16, ups[1]), (20, ups[2])):
                    col = ((0, 220, 0) if u > S["up_ratio"] else
                           (120, 120, 120) if u < S["up_ratio"] - FOLD_GAP else (230, 230, 230))
                    cv2.circle(frame, (int(p[i][0]), int(p[i][1])), 6, col, -1, cv2.LINE_AA)
                    cv2.circle(frame, (int(p[i][0]), int(p[i][1])), 6, (30, 30, 30), 1, cv2.LINE_AA)

                # the cursor ring on the knuckle: yellow = moving, red = mouse
                # button down (SPACE), purple = clutch (frozen)
                ring = ((0, 0, 255) if pen else
                        (255, 0, 200) if hold else (0, 255, 255))
                ax, ay = int(p[9][0]), int(p[9][1])     # drawn on the real knuckle
                cv2.circle(frame, (ax, ay), 14, (20, 20, 20), 4, cv2.LINE_AA)
                cv2.circle(frame, (ax, ay), 14, ring, 2, cv2.LINE_AA)
                cv2.circle(frame, (ax, ay), 4, ring, -1, cv2.LINE_AA)

                if not S["armed"]:
                    status, color = "DISARMED - F8 to take control", (170, 170, 170)
                elif paused:
                    status, color = "PAUSED - P to carry on", (0, 165, 255)
                elif clutched:
                    status, color = "CLUTCH - cursor frozen, open hand to resume", (255, 0, 200)
                elif fist_raw:
                    status, color = "fist... (clutch)", (255, 0, 200)
                elif pen and not SPACE["click"]:
                    status, color = ("DRAWING" + (" - locked flat" if lock_axis == "h" else
                                                  " - locked upright" if lock_axis == "v" else "")
                                     + (" - short strokes" if S["stroke_px"] else "")), (0, 0, 255)
                else:
                    status, color = "tracking - hold SPACE to click", (0, 255, 0)
            else:
                fx.reset()
                fy.reset()
                spline.clear()
                offset[0] = offset[1] = 0.0
                prec_prev = None
                hand_size = None
                fist_since = open_since = None    # a clutch stays on until an open hand comes back
                if pen and stroke_cur is not None:
                    prec_cur = None    # precision picks up from where the cursor really is
                stroke_cur = None
                if not S["armed"]:
                    status = f"DISARMED - no {S['hand'].lower()} hand"

            cv2.rectangle(frame, (int(x0), int(y0)), (int(x1), int(y1)),
                          (90, 200, 90) if S["armed"] else (110, 110, 110), 2)
            fps = 0.9 * fps + 0.1 / max(now - last, 1e-6)
            last = now
            cv2.rectangle(frame, (0, 0), (w, 34), (25, 25, 25), -1)
            cv2.putText(frame, status, (10, 23), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, color, 2, cv2.LINE_AA)
            ftxt = f"{fps:4.1f} FPS"
            (tw, _), _ = cv2.getTextSize(ftxt, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)
            cv2.putText(frame, ftxt, (w - tw - 12, 26), cv2.FONT_HERSHEY_SIMPLEX,
                        0.8, (0, 255, 0) if fps >= 20 else (0, 200, 255), 2, cv2.LINE_AA)
            # draw / click mode, just above the bottom strip
            mtxt = "SPACE = " + mode_words()
            cv2.rectangle(frame, (0, h - 70), (w, h - 46), (25, 25, 25), -1)
            (mw, _), _ = cv2.getTextSize(mtxt, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.putText(frame, mtxt, (10, h - 53), cv2.FONT_HERSHEY_SIMPLEX,
                        min(0.5, 0.5 * (w - 20) / max(mw, 1)),
                        (0, 0, 255) if draw_mode_now() else (255, 200, 0), 1, cv2.LINE_AA)
            # bottom strip: current settings, then the keys
            cv2.rectangle(frame, (0, h - 46), (w, h), (25, 25, 25), -1)
            strip = (f"{S['hand']} hand   steady {fx.cutoff:.2f}   area "
                     f"{1 - 2 * margin:.2f}"
                     f"{shift_short()}"
                     f"{'   PRECISION ' + str(S['prec_pct']) + '%' if S['precision'] else ''}"
                     f"{'   STROKE ' + str(S['stroke_px']) + 'px' if S['stroke_px'] else ''}")
            (sw, _), _ = cv2.getTextSize(strip, cv2.FONT_HERSHEY_SIMPLEX, 0.48, 1)
            cv2.putText(frame, strip, (10, h - 28), cv2.FONT_HERSHEY_SIMPLEX,
                        min(0.48, 0.48 * (w - 20) / max(sw, 1)),   # shrink to fit 640 px
                        (200, 200, 200), 1, cv2.LINE_AA)
            cv2.putText(frame, "F8 arm  SPACE click  D draw mode  P pause  H flip  ESC quit",
                        (10, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (230, 230, 230), 1, cv2.LINE_AA)
            yield ("frame", frame)     # the window shows it (cursor already moved)

            k = KEYS.popleft() if KEYS else -1
            if k == 0x770000 and _hotkey is None:     # F8 in the window, only if the any-window hotkey isn't running
                toggle_armed()
            k &= 0xFF
            if k in (27, ord("q")):
                break
            elif k in (ord("d"), ord("D")) and space_hook is None:
                toggle_draw_mode()               # (with the hook, D works in every window)
            elif k in (ord("p"), ord("P")):
                paused = not paused
                print("PAUSED" if paused else "carrying on")
            elif k in (ord("h"), ord("H")):
                S["invert"] = not S["invert"]
                changed()
                print(f"Flip L/R {'on' if S['invert'] else 'off'}")
            elif k == ord("["):
                S["cutoff"] = clamp(S["cutoff"] - 0.05, 0.15, 3)
                changed()
            elif k == ord("]"):
                S["cutoff"] = clamp(S["cutoff"] + 0.05, 0.15, 3)
                changed()
            elif k == ord("-"):
                S["margin"] = clamp(S["margin"] + 0.02, 0.0, 0.3)
                changed()
            elif k in (ord("="), ord("+")):
                S["margin"] = clamp(S["margin"] - 0.02, 0.0, 0.3)
                changed()

    finally:
        cleanup()
        save_settings()
        if _hotkey is not None:
            try:
                _hotkey.stop()
            except Exception:
                pass
        if space_hook is not None:
            try:
                space_hook.stop()
            except Exception:
                pass
        if cap is not None:
            cap.release()
        try:
            landmarker.close()
        except Exception:
            pass
        print("Stopped. Camera released. Settings saved.")


def main():
    """Build the one window and run the frame loop inside it."""
    only_one_airdraw()
    try:
        ctypes.windll.winmm.timeBeginPeriod(1)     # 1 ms timers, so waits don't add lag
    except Exception:
        pass
    loop = frame_loop()
    next(loop)                                     # settings, cameras, hand model

    root = tk.Tk()
    root.title(WINDOW)
    root.resizable(False, False)
    root.option_add("*Font", ("Segoe UI", 10))
    video = tk.Label(root, bd=0, bg="black")       # the camera picture
    video.grid(row=0, column=0, sticky="n")
    side = tk.Frame(root)                          # the settings
    side.grid(row=0, column=1, sticky="nsew", padx=10)
    SettingsPanel(root, side)
    photo = ImageTk.PhotoImage(Image.new("RGB", (CAM_W, CAM_H)))
    video.configure(image=photo)
    root.bind_all("<Key>", tk_key)
    if _kb is None:                                # no any-window hook: SPACE here
        root.bind_all("<KeyPress-space>", lambda e: space_event(True))
        root.bind_all("<KeyRelease-space>", lambda e: space_event(False))
    root.protocol("WM_DELETE_WINDOW", lambda: QUIT.__setitem__(0, True))
    failed = []

    def finish():
        loop.close()                               # runs the loop's clean-up
        root.destroy()

    def step():
        try:
            kind, val = next(loop)
        except StopIteration:                      # ESC, Q or the window closed
            finish()
            return
        except BaseException as e:                 # e.g. lost the camera
            failed.append(e)
            finish()
            return
        if kind == "frame":
            # one reused picture, pasted in place - no new image per frame
            photo.paste(Image.fromarray(cv2.cvtColor(val, cv2.COLOR_BGR2RGB)))
            root.after(1, step)
        else:
            root.after(max(1, int(val * 1000)), step)

    root.after(1, step)
    try:
        root.mainloop()
    finally:
        loop.close()          # camera released, mouse button up, settings saved - even on Ctrl+C
        try:
            ctypes.windll.winmm.timeEndPeriod(1)
        except Exception:
            pass
    if failed:
        raise failed[0]


if __name__ == "__main__":
    main()
