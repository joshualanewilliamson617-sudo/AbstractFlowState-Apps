#!/usr/bin/env python3
"""
AirDraw - draw in MS Paint with your hand.

    F8                      arm / disarm (works from any window, even Paint).
                            Starts DISARMED: the mouse is yours until you press F8.
    THUMB TAP: touch your   mouse button DOWN the moment the thumb touches
    thumb tip to an index   (the dot turns red), held for as long as it stays
    finger knuckle          touching, so you can click-and-drag to draw. UP
                            when you pull the thumb away. The cursor holds
                            still for "Tap freeze ms" at the start of each
                            press, so the first dot lands where you aimed,
                            then follows your hand in short strokes (about
                            8 px per small hand move).
                            "Tap spot" picks which index knuckle the thumb
                            taps: base (default), middle or upper.
    Pinch thumb + MIDDLE    quick left click
    Closed FIST             clutch: the cursor freezes, so you can move your
                            hand back to a comfy spot. OPEN hand (all four
                            fingers up) carries on from where the cursor stopped.
    ESC or Q                quit (closing the camera window also quits)

    SPACE  pause            H  flip left/right (if your camera mixes them up)
    [ ]    steadier / snappier
    - =    smaller / larger hand area

A second window, "AirDraw settings", has live sliders for all of this:
armed, which hand (left or right), smoothing, zone size, zone left/right and
up/down (slide the zone to where your arm is comfortable - it may go past the
camera picture's edge), pinch press and release distance, hold time, release
time, the "3 fingers up" rule and how straight they must be, auto pinch,
precision mode, stroke size, the clutch, and which camera (0-9, switches
live; at startup it lists the cameras that open and, the first time, picks
Camo or the first camera that isn't the laptop's own). Your settings are
saved to airdraw_settings.json next to this file and loaded next time.

Only the chosen hand moves the cursor; the other hand is ignored.
The cursor follows the middle finger's base knuckle (the big knuckle in the
middle of your hand), which stays still while your fingers pinch or curl.
While a tap or pinch is forming the cursor freezes, and the click lands on
that frozen spot. After a tap it stays frozen until the thumb is clearly
away. The cursor never goes onto the taskbar.
A tap or pinch counts only when it actually touches AND stays touching for
the hold time, so a near-touch never fires by accident.
Precision mode: the cursor follows how far your hand moves, scaled down, so
small hand moves give small, exact cursor moves.

REQUIRES:  py -m pip install opencv-python mediapipe pynput
RUN:       python airdraw.py
"""

import atexit
import ctypes
import json
import math
import os
import sys
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
except ImportError as e:
    sys.exit(f"Missing package: {e.name}\n"
             f"Run:  py -m pip install opencv-python mediapipe pynput")

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
FOLD_GAP = 0.15        # a raised finger counts as folded this far below "Fingers up"
SMALL_MOVE = 0.10      # a "small hand move" = 10% of your hand size, about 1 cm
MIN_DOWN_S = 0.04      # mouse button stays down at least this long, so Paint sees the click
CLUTCH_S = 0.12        # a fist (or an open hand) must last this long to switch the clutch
LOCK_BACK_S = 0.08     # the pinch freeze uses where the cursor was this long ago
UNLOCK_GAP = 0.08      # fingers must open this much past the freeze point to unfreeze
TAP_ARM = 1.2          # the tap freeze starts at this many times the release distance
TAP_FREEZE_MAX = 500   # longest "Tap freeze ms": the cursor holds still this long at most

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/1/hand_landmarker.task")
MODEL_NAME = "hand_landmarker.task"
WINDOW = "AirDraw  -  ESC to quit"
SETWIN = "AirDraw settings"
HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "airdraw_settings.json")

CONNECTIONS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
               (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14),
               (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20),
               (0, 17)]

# ------------------------------------------------------------- settings -----
# Distances are measured against the size of your hand (wrist to the middle
# knuckle), so they work at any distance from the camera. 0.16 is about the
# point where the two fingertips touch.

DEFAULTS = {                 # your own tuned settings (2026-11-26) are the defaults
    "hand": "Right",         # which hand moves the cursor: "Left" or "Right"
    "invert": INVERT_HANDEDNESS,
    "cutoff": 1.86,          # smoothing (lower = steadier)
    "margin": 0.24,          # unused border around the camera view
    "shift_x": 0.05,        # move the zone right (+) or left (-), fraction of picture width
    "shift_y": 0.0,          # move the zone up (+) or down (-), fraction of picture height
    "press": 0.23,           # tap / pinch counts below this distance (touching)
    "release": 0.34,         # tap / pinch lets go above this distance
    "hold_ms": 100,          # how long a pinch must last before mouse DOWN
    "release_ms": 60,        # how long it must stay open before mouse UP (stops flicker)
    "fingers_up": True,      # True = middle, ring and pinky must be raised for mouse down
    "up_ratio": 1.15,        # how straight "raised" is (fingertip vs middle joint, from the wrist)
    "auto_pinch": False,     # True = tune press/release to your hand automatically
    "precision": True,       # True = slow, exact cursor
    "prec_pct": 44,          # precision speed, % of normal
    "stroke_px": 8,          # while the button is held: px per small hand move. 0 = off
    "clutch": True,          # closed fist freezes the cursor, open hand resumes
    "tap_freeze_ms": 150,    # cursor holds still this long at the start of a press, then draws
    "tap_spot": 5,           # index knuckle the thumb taps: 5 base, 6 middle, 7 upper
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
    S["press"] = clamp(float(S["press"]), 0.05, 0.40)
    S["release"] = clamp(float(S["release"]), 0.10, 0.60)
    if S["release"] < S["press"] + 0.04:
        S["release"] = round(S["press"] + 0.04, 2)
    S["hold_ms"] = int(clamp(int(S["hold_ms"]), 0, 400))
    S["release_ms"] = int(clamp(int(S["release_ms"]), 0, 400))
    S["up_ratio"] = clamp(float(S["up_ratio"]), 1.0, 1.5)
    S["prec_pct"] = int(clamp(int(S["prec_pct"]), 5, 100))
    S["stroke_px"] = int(clamp(int(S["stroke_px"]), 0, 40))
    S["tap_freeze_ms"] = int(clamp(int(S["tap_freeze_ms"]), 0, TAP_FREEZE_MAX))
    S["tap_spot"] = int(clamp(int(S["tap_spot"]), 5, 7))
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


def press_at(x, y):
    """Mouse button down at (x, y). Moves there first, twice with a pause, so
    Paint's buttons see the pointer arrive (hover) before the press - Paint
    ignores a press that arrives with no hover."""
    global _held, _down_at
    move(x, y)
    time.sleep(0.015)
    move(x, y)
    time.sleep(0.015)
    _send(LDOWN | MOVE | ABSOLUTE | VIRTUALDESK, *_abs(x, y))
    _held = True
    _down_at = time.time()


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


def click_at(x, y):
    press_at(x, y)
    release()


def cleanup():
    global _held
    if _held:
        _send(LUP)
        _held = False


atexit.register(cleanup)

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


# -------------------------------------------------------------- helpers -----


def d(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def d3(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def to_segment(pt, a, b):
    """3D distance from pt to the line segment a-b, and the nearest point on it."""
    ab = [b[i] - a[i] for i in range(3)]
    ll = sum(v * v for v in ab)
    t = 0.0 if ll < 1e-9 else clamp(sum((pt[i] - a[i]) * ab[i] for i in range(3)) / ll, 0.0, 1.0)
    q = tuple(a[i] + t * ab[i] for i in range(3))
    return d3(pt, q), q


TAP_SPOTS = {5: "base", 6: "middle", 7: "upper"}   # index knuckles the thumb can tap


def thumb_to_index_side(p):
    """How close the thumb tip (4) is to the index knuckle picked with the
    "Tap spot" setting: 5 = base, 6 = middle, 7 = upper. The fingertip (8)
    is never used, so the old tip-to-tip OK sign doesn't count.
    Returns (distance in camera pixels, the knuckle's position)."""
    k = p[S["tap_spot"]]
    return d3(p[4], k), k


def tap_freeze_on(pen, now):
    """True for the first "Tap freeze ms" of a press: the cursor holds still
    so the first dot lands where you aimed. After that it draws."""
    return pen and (now - _down_at) * 1000.0 < S["tap_freeze_ms"]


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


class Pinch:
    """A pinch that must stay touching for the hold time before it counts,
    and must stay open a moment before it lets go."""

    def __init__(self):
        self.since = None      # when the fingertips started touching
        self.apart = None      # when they started coming apart

    def reset(self):
        self.since = self.apart = None

    def progress(self, now):
        if self.since is None:
            return 0.0
        hold = S["hold_ms"] / 1000.0
        return 1.0 if hold <= 0 else clamp((now - self.since) / hold, 0.0, 1.0)

    def ready(self, r, t_on, now):
        """True once the fingertips have touched for the full hold time."""
        if r < t_on:
            if self.since is None:
                self.since = now
            return self.progress(now) >= 1.0
        self.since = None
        return False

    def let_go(self, r, t_off, now):
        """True once the fingertips have stayed apart past the release distance
        for the release time."""
        if r > t_off:
            if self.apart is None:
                self.apart = now
            return now - self.apart >= S["release_ms"] / 1000.0
        self.apart = None
        return False


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


def lock_spot(recent, now, fallback):
    """Where the cursor was LOCK_BACK_S ago - before the pinch started to form."""
    for t, x, y in recent:
        if t >= now - LOCK_BACK_S:
            return (x, y)
    return fallback


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
    ("Press x100",        5,  40, lambda: int(round(S["press"] * 100)),       lambda v: _set("press", v / 100)),
    ("Release x100",     10,  60, lambda: int(round(S["release"] * 100)),     lambda v: _set("release", v / 100)),
    ("Hold ms",           0, 400, lambda: int(S["hold_ms"]),                  lambda v: _set("hold_ms", int(v))),
    ("Release ms",        0, 400, lambda: int(S["release_ms"]),               lambda v: _set("release_ms", int(v))),
    ("3 fingers up",      0,   1, lambda: int(S["fingers_up"]),               lambda v: _set("fingers_up", bool(v))),
    ("Fingers up x100", 100, 150, lambda: int(round(S["up_ratio"] * 100)),    lambda v: _set("up_ratio", v / 100)),
    ("Auto pinch",        0,   1, lambda: int(S["auto_pinch"]),               lambda v: _set("auto_pinch", bool(v))),
    ("Precision",         0,   1, lambda: int(S["precision"]),                lambda v: _set("precision", bool(v))),
    ("Precision speed %", 5, 100, lambda: int(S["prec_pct"]),                 lambda v: _set("prec_pct", int(v))),
    ("Stroke px (0 off)", 0,  40, lambda: int(S["stroke_px"]),                lambda v: _set("stroke_px", int(v))),
    ("Clutch (fist)",     0,   1, lambda: int(S["clutch"]),                   lambda v: _set("clutch", bool(v))),
    ("Tap freeze ms",     0, TAP_FREEZE_MAX, lambda: int(S["tap_freeze_ms"]), lambda v: _set("tap_freeze_ms", int(v))),
    # 5 = base knuckle, 6 = middle, 7 = upper (the slider's own minimum is 5)
    ("Tap spot 5/6/7",    5,   7, lambda: int(S["tap_spot"]),                 lambda v: _set("tap_spot", int(v))),
    # Camera: shows the one in use; moving it switches live and saves the choice.
    ("Camera",            0,   9, lambda: CAM["want"] if CAM["want"] is not None else CAM["live"],
                                  lambda v: pick_camera(int(v))),
]


def pick_camera(i):
    """Slider moved: remember the choice and ask the main loop to switch."""
    CAM["want"] = i
    _set("camera", i)
_shown = {}
_panel_at = 0.0


def build_settings_window():
    """The drop-down settings window (Tkinter) if it starts, otherwise the
    old OpenCV slider window."""
    if not _tk_on and _tk_failed is None and start_tk_settings():
        return
    build_cv_settings_window()


def sync_settings_window(now):
    if _tk_on:
        return                  # the drop-down window keeps itself in sync
    sync_cv_settings_window(now)


# -------------------------------------------- drop-down settings window -----
# A normal Windows settings window. A drop-down at the top picks a page, and
# only that page's settings show, so nothing ends up off the bottom of the
# screen. Hand, Tap spot and Camera are drop-downs; on/off settings are tick
# boxes; numbers are sliders. It runs in its own thread and reads and writes
# the same settings (S) as everything else, so F8 and the keys show up here
# too. Closing it only minimises it.

SECTIONS = [
    ("Basics", ["Armed (F8)", "Hand: 0 L  1 R", "Flip L/R (H)", "Camera", "Clutch (fist)"]),
    ("Tap & click", ["Tap spot 5/6/7", "Press x100", "Release x100", "Hold ms",
                     "Release ms", "Tap freeze ms", "3 fingers up", "Fingers up x100",
                     "Auto pinch"]),
    ("Movement", ["Smoothing", "Precision", "Precision speed %", "Stroke px (0 off)"]),
    ("Zone", ["Zone size %", "Zone left/right", "Zone up/down"]),
]
TK_NAMES = {"Hand: 0 L  1 R": "Hand", "Tap spot 5/6/7": "Tap spot",
            "Flip L/R (H)": "Flip left/right (H)", "Press x100": "Press (x100)",
            "Release x100": "Release (x100)", "Fingers up x100": "Fingers up (x100)",
            "Stroke px (0 off)": "Stroke px (0 = off)"}


def _camera_choices():
    idx = sorted(set(CAM["opened"]) | set(range(len(CAM["names"]))) | {CAM["live"]})
    return [(f"{i}: {cam_name(i)}" + ("" if i in CAM["opened"] else "  (didn't open)"), i)
            for i in idx if 0 <= i <= 9]


TK_CHOICES = {
    "Hand: 0 L  1 R": lambda: [("Left hand", 0), ("Right hand", 1)],
    "Tap spot 5/6/7": lambda: [("Base knuckle", 5), ("Middle knuckle", 6), ("Upper knuckle", 7)],
    "Camera": _camera_choices,
}

_tk_on = False          # the drop-down window is running
_tk_failed = None       # why it couldn't start (then the slider window is used)
_tk_quit = False        # main loop finished: close the window


class TkSettings:
    def __init__(self):
        import tkinter as tk
        from tkinter import ttk
        self.tk = tk
        self.root = root = tk.Tk()
        root.title(SETWIN)
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", root.iconify)   # closing just minimises
        font = ("Segoe UI", 10)
        root.option_add("*Font", font)

        self.status = tk.StringVar()
        tk.Label(root, textvariable=self.status, justify="left", anchor="w",
                  font=("Segoe UI", 9), fg="#444").pack(fill="x", padx=10, pady=(8, 4))

        top = tk.Frame(root)
        top.pack(fill="x", padx=10, pady=4)
        tk.Label(top, text="Settings page:", font=("Segoe UI", 10, "bold")).pack(side="left")
        self.page = ttk.Combobox(top, state="readonly", width=18,
                                 values=[name for name, _ in SECTIONS])
        self.page.pack(side="left", padx=8)
        self.page.bind("<<ComboboxSelected>>", lambda e: self.show(self.page.get()))

        by_name = {s[0]: s for s in SLIDERS}
        self.rows = []      # (kind, slider entry, widget var, combobox / None)
        self.frames = {}
        for section, labels in SECTIONS:
            fr = tk.Frame(root)
            self.frames[section] = fr
            for r, label in enumerate(labels):
                entry = by_name[label]
                name, lo, hi, get, put = entry
                tk.Label(fr, text=TK_NAMES.get(name, name), anchor="w", width=20).grid(
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
                    tk.Checkbutton(fr, variable=var,
                                   command=lambda v=var, p=put, g=get: (
                                       p(v.get()) if v.get() != g() else None)
                                   ).grid(row=r, column=1, sticky="w")
                    self.rows.append(("tick", entry, var, None))
                else:
                    var = tk.IntVar(value=clamp(get(), lo, hi))
                    tk.Scale(fr, from_=lo, to=hi, orient="horizontal", length=260,
                             variable=var, showvalue=True,
                             command=lambda s, p=put, g=get: (
                                 p(int(float(s))) if int(float(s)) != g() else None)
                             ).grid(row=r, column=1, sticky="w")
                    self.rows.append(("number", entry, var, None))

        tk.Label(root, text="Saved automatically to airdraw_settings.json",
                 font=("Segoe UI", 8), fg="#777").pack(side="bottom", anchor="w", padx=10, pady=(4, 8))
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
        if _tk_quit:
            self.root.destroy()
            return
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


def start_tk_settings():
    """Start the drop-down window in its own thread. False if it can't."""
    global _tk_on, _tk_failed
    import threading
    ready = threading.Event()

    def run():
        global _tk_on, _tk_failed
        try:
            app = TkSettings()
        except Exception as e:
            _tk_failed = str(e) or "unknown error"
            ready.set()
            return
        _tk_on = True
        ready.set()
        try:
            app.root.mainloop()
        finally:
            _tk_on = False

    global _tk_thread
    _tk_thread = threading.Thread(target=run, name="settings-window", daemon=True)
    _tk_thread.start()
    ready.wait(5.0)
    if not _tk_on:
        _tk_failed = _tk_failed or "didn't start in time"
        print(f"Drop-down settings window didn't start ({_tk_failed}); "
              f"using the slider window instead.")
        return False
    return True


def stop_tk_settings():
    global _tk_quit
    _tk_quit = True             # the window closes itself on its next check
    if _tk_thread is not None:
        _tk_thread.join(1.0)    # let it close before Python shuts down


_tk_thread = None


# --------------------------------------------- old slider settings window ---
# Used only if the drop-down window can't start.

def build_cv_settings_window():
    _shown.clear()
    cv2.namedWindow(SETWIN, cv2.WINDOW_AUTOSIZE)
    cv2.imshow(SETWIN, settings_panel())
    for name, lo, hi, get, _put in SLIDERS:
        cv2.createTrackbar(name, SETWIN, clamp(get(), 0, hi), hi, lambda v: None)
        if lo != 0:                              # the zone sliders go negative
            cv2.setTrackbarMin(name, SETWIN, lo)
        v = clamp(get(), lo, hi)
        cv2.setTrackbarPos(name, SETWIN, v)
        _shown[name] = v


def sync_cv_settings_window(now):
    global _panel_at
    try:
        for name, lo, hi, get, put in SLIDERS:
            pos = cv2.getTrackbarPos(name, SETWIN)
            if pos != _shown.get(name):          # you moved the slider
                put(pos)
            want = clamp(get(), lo, hi)
            if want != pos:                      # a key or F8 changed the setting
                cv2.setTrackbarPos(name, SETWIN, want)
            _shown[name] = want
        if cv2.getWindowProperty(SETWIN, cv2.WND_PROP_VISIBLE) < 1:
            raise cv2.error("settings window closed")
        if now - _panel_at > 0.25:
            _panel_at = now
            cv2.imshow(SETWIN, settings_panel())
    except cv2.error:
        build_cv_settings_window()               # closed by accident: bring it back


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
    """The settings summary as (text, BGR colour) lines - drawn by the old
    slider window, shown as plain text in the drop-down settings window."""
    t_on, t_off = (("auto", "auto") if S["auto_pinch"]
                   else (f"{S['press']:.2f}", f"{S['release']:.2f}"))
    lines = [
        ("ARMED - your hand moves the mouse" if S["armed"]
         else "DISARMED - press F8 to take control", (90, 220, 90) if S["armed"] else (150, 150, 150)),
        (f"Hand: {S['hand']}  (other hand ignored)    Flip L/R: {'on' if S['invert'] else 'off'}", (230, 230, 230)),
        (f"Tap/pinch press {t_on} / release {t_off}    release {S['release_ms']} ms    "
         f"quick-click hold {S['hold_ms']} ms (the tap presses at once)", (230, 230, 230)),
        ("  press = thumb touching the index finger's side. Lower = harder.", (160, 160, 160)),
        ((f"Mouse down: thumb tip on index side, middle/ring/pinky not curled ({S['up_ratio']:.2f})"
          if S["fingers_up"] else "Mouse down: thumb tip on index side (other fingers any way)"),
         (230, 230, 230)),
        (f"Tap spot: {TAP_SPOTS[S['tap_spot']]} knuckle of the index finger "
         f"(landmark {S['tap_spot']})", (230, 230, 230)),
        (f"Tap: touch = mouse down (red dot), hold to drag/draw, pull away = up. "
         f"Still for {S['tap_freeze_ms']} ms first", (230, 230, 230)),
        ((f"Drawing: short strokes, {S['stroke_px']} px per small hand move (~1 cm)"
          if S["stroke_px"] else "Drawing: full-speed movement (stroke px is 0)"), (230, 230, 230)),
        (("Clutch: ON - closed fist freezes the cursor, open hand resumes"
          if S["clutch"] else "Clutch: off"), (230, 230, 230)),
        (f"Camera {CAM['live']}: {cam_name(CAM['live'])}"
         + (f"   (saved choice {S['camera']} not found)" if S["camera"] not in (-1, CAM["live"]) else "")
         + (f"   - {CAM['note']}" if CAM["note"] else ""),
         (0, 165, 255) if CAM["note"] or S["camera"] not in (-1, CAM["live"]) else (230, 230, 230)),
        (f"Precision: {'ON' if S['precision'] else 'off'} at {S['prec_pct']}% speed    "
         f"Zone {1 - 2 * S['margin']:.0%}", (230, 230, 230)),
        (f"Zone moved: {shift_words()}", (230, 230, 230)),
        ("Saved automatically to airdraw_settings.json", (160, 160, 160)),
    ]
    return lines


def settings_panel():
    lines = panel_lines()
    img = np.full((14 + len(lines) * 27, 560, 3), 24, np.uint8)
    for i, (txt, col) in enumerate(lines):
        cv2.putText(img, txt, (12, 26 + i * 27), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1, cv2.LINE_AA)
    return img


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


def main():
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
    pen_pinch, click_pinch = Pinch(), Pinch()

    # Auto pinch (a setting, off by default): watches how wide your hand opens
    # and sets the thresholds from that.
    seen = deque(maxlen=240)
    open_ref = 1.30

    pen = False
    click_closed = False
    locked = None
    offset = [0.0, 0.0]
    paused = False
    prec_prev = None           # precision mode: last absolute position, for the hand's movement
    prec_cur = None            # precision mode: where the cursor is
    # Short strokes: while the button is held the cursor moves by the nib's
    # movement, measured in hand sizes so it's the same at any camera distance.
    sfx, sfy = OneEuro(S["cutoff"], BETA), OneEuro(S["cutoff"], BETA)
    pen_spot = None            # where the tap pressed: the cursor stays here while frozen
    stroke_cur = None          # where the cursor is during a short stroke
    stroke_prev = None         # last smoothed nib position (camera pixels)
    hand_size = None           # smoothed wrist-to-knuckle length (camera pixels)
    clutched = False           # clutch: closed fist froze the cursor
    fist_since = open_since = None
    clutch_off = [0.0, 0.0]    # clutch, normal mode: shift between hand and cursor
    recent = deque(maxlen=12)  # last few cursor spots (time, x, y), for the pinch freeze
    unlock_since = None        # when the fingers opened enough to unfreeze
    work_at = 0.0              # when the taskbar area was last checked
    refresh_work_area()
    next_frame = time.time()   # the 30 fps cap
    t0 = time.time()
    last_ms = -1
    fps = 0.0
    last = time.time()

    cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
    build_settings_window()
    start_hotkey()

    print(f"AirDraw running. Screen {SCREEN_W}x{SCREEN_H} ({dpi_report()}). Hand: {S['hand']}.")
    print("DISARMED - press F8 to take control.")
    print("Thumb tip against the side of your index finger (middle/ring/pinky up) = "
          "mouse down; pull it away = mouse up.  Thumb+middle = quick click.  "
          "Fist = clutch.  ESC = quit.\n")

    try:
        while True:
            try:
                if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1 \
                        and fps > 0:
                    break
            except cv2.error:
                pass

            # 30 fps cap: if we're early, rest instead of grabbing another frame
            wait = next_frame - time.time()
            if wait > 0:
                time.sleep(wait)
            next_frame = max(next_frame + 1.0 / MAX_FPS, time.time())

            # Camera slider moved: switch live. Let go of the mouse button and
            # forget the old hand position, since the new picture is different.
            if CAM["want"] is not None:
                want, CAM["want"] = CAM["want"], None
                if want != CAM["live"]:
                    if pen:
                        release()
                        pen = False
                    cap = switch_camera(cap, want)
                    if CAM["live"] != want:          # didn't open: don't remember it
                        S["camera"] = CAM["live"]
                        changed()
                    fx.reset()
                    fy.reset()
                    spline.clear()
                    locked = stroke_cur = prec_prev = hand_size = None
                    recent.clear()
                    pen_pinch.reset()
                    click_pinch.reset()
                    click_closed = False
                    if cap is None:
                        sys.exit("Lost the camera while switching.")

            ok, frame = cap.read()
            if not ok:
                # No picture (camera busy or unplugged). Keep the windows and
                # sliders working so you can pick another camera.
                sync_settings_window(time.time())
                if cv2.waitKey(30) & 0xFF in (27, ord("q")):
                    break
                continue
            if frame.shape[1] != CAM_W or frame.shape[0] != CAM_H:
                frame = cv2.resize(frame, (CAM_W, CAM_H))   # always 640x480
            frame = cv2.flip(frame, 1)
            h, w = frame.shape[:2]
            now = time.time()

            sync_settings_window(now)
            if _dirty_at is not None and now - _dirty_at > 0.8:
                save_settings()
            if now - work_at > 2.0:              # taskbar moved / resolution changed?
                work_at = now
                refresh_work_area()
            fx.cutoff = fy.cutoff = S["cutoff"]
            sfx.cutoff = sfy.cutoff = S["cutoff"] * DRAW_STEADY
            margin = S["margin"]
            active = S["armed"] and not paused

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
            tap_info = None            # the live tap readout line, when a hand is seen
            hand = pick_hand(res)

            if hand is not None:
                p = [(lm.x * w, lm.y * h, lm.z * w) for lm in hand]
                scale = max(d(p[0], p[9]), 1e-3)
                hand_size = scale if hand_size is None else 0.85 * hand_size + 0.15 * scale

                # 3D fingertip distances, so tips that only overlap on camera
                # (one in front of the other) don't count as touching
                r_middle = d3(p[4], p[12]) / scale
                # The thumb tap: thumb tip against the side of the index finger
                side_px, touch = thumb_to_index_side(p)
                r_tap = side_px / scale

                # Middle, ring, pinky: a finger is up when its tip is clearly
                # farther from the wrist than its middle joint is, and folded
                # when it's well below that.
                # The tap only needs them NOT CURLED IN (fewer than 2 folded).
                # Pressing the thumb into the index finger relaxes the other
                # fingers into a half-bend, and the old "all three clearly
                # straight" rule blocked every tap. A fist still folds them,
                # so a fist never taps.
                ups = [d3(p[tip], p[0]) / max(d3(p[pip], p[0]), 1e-3)
                       for pip, tip in ((10, 12), (14, 16), (18, 20))]
                if S["fingers_up"]:
                    folded = sum(u < S["up_ratio"] - FOLD_GAP for u in ups) >= 2
                    fingers_ok = not folded
                else:
                    fingers_ok, folded = True, False
                stroke = pen and S["stroke_px"] > 0

                if S["auto_pinch"]:
                    seen.append(r_tap)
                    if len(seen) > 45:
                        open_ref = clamp(float(np.percentile(seen, 90)), 0.9, 2.2)
                    t_on, t_off = open_ref * 0.26, open_ref * 0.44
                    t_arm = open_ref * 0.62
                else:
                    t_on, t_off = S["press"], S["release"]
                    t_arm = max(t_off * 1.35, t_on + 0.12)
                # The tap's freeze starts closer in than the pinch's: a relaxed
                # thumb rests fairly near the index finger, and shouldn't freeze.
                t_arm_tap = t_off * TAP_ARM

                # The cursor follows the middle finger's base knuckle (landmark
                # 9). It barely moves when you tap, pinch or make a fist, so
                # gestures don't drag the cursor off target.
                aim = (p[9][0], p[9][1])

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
                tap_frozen = tap_freeze_on(pen, now)

                if not S["precision"]:
                    mx, my = keep_in(mx + clutch_off[0], my + clutch_off[1])

                # Precision mode: move by the hand's movement, scaled down,
                # starting from wherever the cursor already is.
                if S["precision"]:
                    if prec_cur is None:
                        prec_cur = list(cursor_pos())
                    # a short stroke moves it instead; the clutch, the pinch
                    # freeze and the tap freeze stop it
                    if prec_prev is not None and not stroke and not hold and locked is None \
                            and not tap_frozen:
                        k = S["prec_pct"] / 100.0
                        prec_cur[0], prec_cur[1] = keep_in(prec_cur[0] + (mx - prec_prev[0]) * k,
                                                           prec_cur[1] + (my - prec_prev[1]) * k)
                    prec_prev = (mx, my)
                    mx, my = prec_cur
                else:
                    prec_prev = prec_cur = None

                closing = r_tap < t_arm_tap or r_middle < t_arm
                # What can stop a touch from pressing (the dot shows ORANGE
                # then, and the strip under the picture says why):
                #  - a thumb+middle quick click in progress, or the thumb
                #    nearer the middle fingertip than the knuckle
                #  - the index finger folded: a fist is forming, so the
                #    clutch gets it instead of a stray mark
                #  - "3 fingers up" on and middle/ring/pinky curled in
                idx_folded = idx_up < S["up_ratio"] - FOLD_GAP
                tap_ok = (fingers_ok and not idx_folded and not click_closed
                          and not (r_middle < t_on and r_middle < r_tap))

                # While clutched nothing moves or clicks: the branch below
                # lets go of the button and leaves the cursor where it is.
                if active and not hold:
                    # Freeze the cursor the moment a tap or pinch starts
                    # forming, at where it was a split second BEFORE (forming
                    # the gesture nudges the hand), then press/click right
                    # there. It unfreezes only once the thumb is clearly
                    # away again for the release time, so it can't flicker.
                    if closing and locked is None and not pen:
                        locked = lock_spot(recent, now, (mx + offset[0], my + offset[1]))
                        unlock_since = None
                        if S["precision"]:
                            prec_cur = list(locked)
                    elif locked is not None and not pen:
                        if r_tap > t_arm_tap + UNLOCK_GAP and r_middle > t_arm + UNLOCK_GAP:
                            unlock_since = now if unlock_since is None else unlock_since
                            if now - unlock_since >= S["release_ms"] / 1000.0:
                                if not S["precision"]:      # glide, don't jump, back to your hand
                                    offset[0], offset[1] = locked[0] - mx, locked[1] - my
                                locked = None
                                unlock_since = None
                        else:
                            unlock_since = None

                    # --- pen = the mouse button, worked by the thumb tap ---
                    # Down: the moment the thumb touches the knuckle (below
                    # Press) - no hold wait, so green-means-touching is the
                    # press itself and the dot goes straight to red.
                    # Stays down the whole time the thumb keeps touching, so
                    # you can drag and draw.
                    # Up: thumb pulled away past Release for the release time
                    # (a tiny wait so one shaky frame doesn't break a line).
                    # A fist lets go too (the clutch branch below).
                    if not pen and tap_ok and r_tap < t_on:
                        pen_pinch.reset()
                        start = locked or (mx + offset[0], my + offset[1])
                        offset[0], offset[1] = start[0] - mx, start[1] - my
                        press_at(int(start[0]), int(start[1]))
                        pen = True
                        pen_spot = (start[0], start[1])
                        locked = None
                        spline.clear()
                        spline.append(start)
                        pen_pinch.apart = None
                        stroke_cur = [start[0], start[1]]
                        stroke_prev = None
                        sfx.reset()
                        sfy.reset()
                    elif pen and pen_pinch.let_go(r_tap, t_off, now):
                        release()
                        pen = False
                        pen_pinch.reset()
                        spline.clear()
                        if stroke and stroke_cur is not None:
                            # carry on from where the short stroke ended
                            if S["precision"]:
                                prec_cur = list(stroke_cur)
                                mx, my = stroke_cur
                                offset[0] = offset[1] = 0.0
                            else:      # glides back to where your hand points
                                offset[0], offset[1] = stroke_cur[0] - mx, stroke_cur[1] - my
                        stroke_cur = None
                        pen_spot = None
                        # Stay frozen where the button went up while the thumb
                        # pulls away (that nudges the hand too). The freeze
                        # above lets go once the thumb is clearly away.
                        locked = keep_in(*cursor_pos())
                        unlock_since = None
                        if S["precision"]:
                            prec_cur = list(locked)

                    # --- click ---
                    if not pen:
                        if not click_closed and click_pinch.ready(r_middle, t_on, now):
                            click_closed = True
                            q = locked or (mx + offset[0], my + offset[1])
                            click_at(int(q[0]), int(q[1]))
                        elif click_closed and click_pinch.let_go(r_middle, t_off, now):
                            click_closed = False
                            click_pinch.reset()

                    # --- movement ---
                    tap_frozen = tap_freeze_on(pen, now)
                    if tap_frozen and pen_spot is not None:
                        # keep the offset matched to the frozen spot, so when
                        # a timed freeze ends the cursor doesn't jump
                        offset[0], offset[1] = pen_spot[0] - mx, pen_spot[1] - my
                    tx, ty = mx + offset[0], my + offset[1]
                    if not pen:
                        offset[0] *= 0.85
                        offset[1] *= 0.85
                    elif S["stroke_px"] > 0 and stroke_cur is not None:
                        # Short strokes: follow the knuckle's movement, scaled so
                        # a small hand move (~1 cm) moves the cursor stroke_px.
                        # Not during the tap freeze.
                        nx, ny = sfx(aim[0], now), sfy(aim[1], now)
                        if stroke_prev is not None and not tap_frozen:
                            k = S["stroke_px"] / (SMALL_MOVE * hand_size)
                            stroke_cur[0], stroke_cur[1] = keep_in(stroke_cur[0] + (nx - stroke_prev[0]) * k,
                                                                   stroke_cur[1] + (ny - stroke_prev[1]) * k)
                        stroke_prev = (nx, ny)
                        tx, ty = stroke_cur

                    if locked is not None:
                        move(int(locked[0]), int(locked[1]))
                    elif tap_frozen and pen_spot is not None:
                        # tap held: the cursor stays exactly where it pressed
                        move(int(pen_spot[0]), int(pen_spot[1]))
                        spline.clear()
                        spline.append(pen_spot)
                    elif pen:
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
                        recent.append((now,) + keep_in(tx, ty))   # for lock_spot
                else:
                    if pen:
                        release()
                        pen = False
                    stroke_cur = None
                    locked = None
                    click_closed = False
                    pen_pinch.reset()
                    click_pinch.reset()
                    if S["precision"]:      # disarmed or paused: the cursor is yours again
                        prec_cur = None

                # --- overlay: faint hand, clear fingertip, pinch colours ---
                for a, b in CONNECTIONS:
                    cv2.line(frame, (int(p[a][0]), int(p[a][1])),
                             (int(p[b][0]), int(p[b][1])), (150, 150, 150), 1, cv2.LINE_AA)

                def tip_col(r, pinch, pressed):
                    if pressed:
                        return (0, 0, 255)          # red: pressed
                    if r < t_on:
                        return (0, 220, 0)          # green: touching, hold filling
                    return (230, 230, 230)          # white: apart

                # The tap dot: red = mouse down. Touching but NOT pressed
                # (blocked - the strip below says why) = orange, so green
                # never shows without a press. White = apart.
                c_tap = ((0, 0, 255) if pen else
                         (0, 165, 255) if r_tap < t_on else (230, 230, 230))
                c_mid = tip_col(r_middle, click_pinch, click_closed)
                c_thb = c_tap if (pen or r_tap < t_on) else c_mid
                for i, col in ((4, c_thb), (12, c_mid)):
                    cv2.circle(frame, (int(p[i][0]), int(p[i][1])), 7, col, -1, cv2.LINE_AA)
                    cv2.circle(frame, (int(p[i][0]), int(p[i][1])), 7, (30, 30, 30), 1, cv2.LINE_AA)
                # the spot on the index finger's side the thumb taps against
                cv2.circle(frame, (int(touch[0]), int(touch[1])), 6, c_tap, -1, cv2.LINE_AA)
                cv2.circle(frame, (int(touch[0]), int(touch[1])), 6, (30, 30, 30), 1, cv2.LINE_AA)
                if S["fingers_up"]:          # ring and pinky: green = fine, grey = curled in
                    for i, u in ((16, ups[1]), (20, ups[2])):
                        col = (0, 220, 0) if u >= S["up_ratio"] - FOLD_GAP else (120, 120, 120)
                        cv2.circle(frame, (int(p[i][0]), int(p[i][1])), 5, col, -1, cv2.LINE_AA)

                # Live thumb-to-dot distance, next to the thumb tip
                dtxt = f"{r_tap:.2f}"
                tx0, ty0 = int(p[4][0]) + 12, int(p[4][1]) - 10
                cv2.putText(frame, dtxt, (tx0, ty0), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                            (20, 20, 20), 4, cv2.LINE_AA)
                cv2.putText(frame, dtxt, (tx0, ty0), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                            c_tap, 2, cv2.LINE_AA)

                # ...and in a strip, with what (if anything) is stopping the press
                if pen:
                    why, wcol = "PRESSED - mouse down, drag to draw", (0, 0, 255)
                elif r_tap >= t_on:
                    why, wcol = f"not touching - needs below {t_on:.2f}", (230, 230, 230)
                elif not S["armed"]:
                    why, wcol = "blocked: not armed - press F8", (0, 165, 255)
                elif paused:
                    why, wcol = "blocked: paused - press SPACE", (0, 165, 255)
                elif hold:
                    why, wcol = "blocked: fist = clutch", (255, 0, 200)
                elif click_closed or (r_middle < t_on and r_middle < r_tap):
                    why, wcol = "blocked: thumb is on the middle fingertip (quick click)", (0, 165, 255)
                elif idx_folded:
                    why, wcol = "blocked: index finger folded (looks like a fist) - straighten it", (0, 165, 255)
                elif not fingers_ok:
                    why, wcol = "blocked: middle/ring/pinky curled in - relax them open", (0, 165, 255)
                else:
                    why, wcol = "touching - pressing", (0, 220, 0)
                tap_info = (f"thumb to {TAP_SPOTS[S['tap_spot']]} knuckle {r_tap:.2f}   press {t_on:.2f}   release {t_off:.2f}   "
                            f"middle tip {r_middle:.2f}   {why}", wcol)

                ring = (0, 0, 255) if pen else (
                    (255, 200, 0) if locked is not None else (0, 255, 255))
                ax, ay = int(aim[0]), int(aim[1])
                cv2.circle(frame, (ax, ay), 14, (20, 20, 20), 4, cv2.LINE_AA)
                cv2.circle(frame, (ax, ay), 14, ring, 2, cv2.LINE_AA)
                cv2.circle(frame, (ax, ay), 4, ring, -1, cv2.LINE_AA)
                prog = max(pen_pinch.progress(now) if not pen else 0,
                           click_pinch.progress(now) if not click_closed else 0)
                if 0 < prog < 1:                   # the hold filling up around the ring
                    cv2.ellipse(frame, (ax, ay), (20, 20), -90, 0, 360 * prog,
                                (0, 220, 0), 3, cv2.LINE_AA)

                if not S["armed"]:
                    status, color = "DISARMED - F8 to take control", (170, 170, 170)
                elif paused:
                    status, color = "PAUSED", (0, 165, 255)
                elif clutched:
                    status, color = "CLUTCH - cursor frozen, open hand to resume", (255, 0, 200)
                elif fist_raw:
                    status, color = "fist... (clutch)", (255, 0, 200)
                elif pen and tap_frozen:
                    status, color = "TAP - button down, cursor frozen", (0, 0, 255)
                elif pen:
                    status, color = ("DRAWING - short strokes" if S["stroke_px"] else "DRAWING"), (0, 0, 255)
                elif r_tap < t_on and idx_folded:
                    status, color = "straighten your index finger", (0, 165, 255)
                elif r_tap < t_on and not fingers_ok:
                    status, color = "relax middle, ring, pinky open", (0, 165, 255)
                elif locked is not None:
                    status, color = "frozen - tap / pinch", (255, 200, 0)
                else:
                    status, color = "tracking", (0, 255, 0)
            else:
                fx.reset()
                fy.reset()
                spline.clear()
                locked = None
                offset[0] = offset[1] = 0.0
                prec_prev = None
                hand_size = None
                fist_since = open_since = None    # a clutch stays on until an open hand comes back
                recent.clear()
                unlock_since = None
                pen_pinch.reset()
                click_pinch.reset()
                click_closed = False
                if pen:
                    release()
                    pen = False
                    if stroke_cur is not None:
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
            # tap readout, just above the bottom strip
            if tap_info is not None:
                txt, tcol = tap_info
                cv2.rectangle(frame, (0, h - 70), (w, h - 46), (25, 25, 25), -1)
                (iw, _), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
                cv2.putText(frame, txt, (10, h - 53), cv2.FONT_HERSHEY_SIMPLEX,
                            min(0.45, 0.45 * (w - 20) / max(iw, 1)),   # shrink to fit
                            tcol, 1, cv2.LINE_AA)
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
            cv2.putText(frame, "F8 arm   H flip L/R   SPACE pause   ESC quit",
                        (10, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (230, 230, 230), 1, cv2.LINE_AA)
            cv2.imshow(WINDOW, frame)

            k = cv2.waitKeyEx(1)
            if k == 0x770000 and _hotkey is None:     # F8 in the camera window, only if the any-window hotkey isn't running
                toggle_armed()
            k &= 0xFF
            if k in (27, ord("q")):
                break
            elif k == ord(" "):
                paused = not paused
                if paused and pen:
                    release()
                    pen = False
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
        stop_tk_settings()
        if _hotkey is not None:
            try:
                _hotkey.stop()
            except Exception:
                pass
        if cap is not None:
            cap.release()
        cv2.destroyAllWindows()
        try:
            landmarker.close()
        except Exception:
            pass
        print("Stopped. Camera released. Settings saved.")


if __name__ == "__main__":
    main()
