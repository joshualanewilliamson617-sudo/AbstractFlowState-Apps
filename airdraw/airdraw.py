#!/usr/bin/env python3
"""
AirDraw - draw in MS Paint with your hand.

    F8                      arm / disarm (works from any window, even Paint).
                            Starts DISARMED: the mouse is yours until you press F8.
    Pinch thumb + INDEX     pen down, draws while held
    Pinch thumb + MIDDLE    left click
    ESC or Q                quit (closing the camera window also quits)

    SPACE  pause            H  flip left/right (if your camera mixes them up)
    [ ]    steadier / snappier
    - =    smaller / larger hand area

A second window, "AirDraw settings", has live sliders for all of this:
armed, which hand (left or right), smoothing, zone size, pinch press and
release distance, hold time, auto pinch, and precision mode. Your settings are
saved to airdraw_settings.json next to this file and loaded next time.

Only the chosen hand moves the cursor; the other hand is ignored.
A pinch counts only when the fingertips actually touch AND stay touching for
the hold time, so a near-pinch never fires by accident.
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
CAM_W, CAM_H = 960, 540

# Some laptop cameras report left and right hands the wrong way round.
# True swaps them back. H flips it while running, and the choice is saved.
INVERT_HANDEDNESS = True

BETA = 0.015           # responsiveness on fast moves
DRAW_STEADY = 0.55     # extra smoothing while the pen is down
RELEASE_GRACE = 0.06   # seconds the fingers must stay apart before a line ends

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

DEFAULTS = {
    "hand": "Right",         # which hand moves the cursor: "Left" or "Right"
    "invert": INVERT_HANDEDNESS,
    "cutoff": 0.70,          # smoothing (lower = steadier)
    "margin": 0.08,          # unused border around the camera view
    "press": 0.16,           # pinch counts below this distance (fingertips touching)
    "release": 0.28,         # pinch lets go above this distance
    "hold_ms": 100,          # how long a pinch must last before it counts
    "auto_pinch": False,     # True = tune press/release to your hand automatically
    "precision": False,      # True = slow, exact cursor
    "prec_pct": 25,          # precision speed, % of normal
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
    S["press"] = clamp(float(S["press"]), 0.05, 0.40)
    S["release"] = clamp(float(S["release"]), 0.10, 0.60)
    if S["release"] < S["press"] + 0.04:
        S["release"] = round(S["press"] + 0.04, 2)
    S["hold_ms"] = int(clamp(int(S["hold_ms"]), 0, 400))
    S["prec_pct"] = int(clamp(int(S["prec_pct"]), 5, 100))


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

user32 = ctypes.WinDLL("user32", use_last_error=True)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

MOVE, LDOWN, LUP = 0x0001, 0x0002, 0x0004
ABSOLUTE, VIRTUALDESK = 0x8000, 0x4000

VX, VY = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
VW = max(user32.GetSystemMetrics(78), 1)
VH = max(user32.GetSystemMetrics(79), 1)
SCREEN_W, SCREEN_H = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)

_held = False


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
    i = INPUT(type=0, mi=MI(x, y, 0, flags, 0, None))
    user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))


def move(x, y):
    _send(MOVE | ABSOLUTE | VIRTUALDESK,
          int((x - VX) * 65535 / max(VW - 1, 1)),
          int((y - VY) * 65535 / max(VH - 1, 1)))


def cursor_pos():
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return float(pt.x), float(pt.y)


def press():
    global _held
    _send(LDOWN)
    _held = True


def release():
    global _held
    _send(LUP)
    _held = False


def click_at(x, y):
    move(x, y)
    time.sleep(0.02)
    move(x, y)
    time.sleep(0.01)
    _send(LDOWN)
    time.sleep(0.02)
    _send(LUP)


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
        """True once the fingertips have stayed apart past the release distance."""
        if r > t_off:
            if self.apart is None:
                self.apart = now
            return now - self.apart >= RELEASE_GRACE
        self.apart = None
        return False


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
    ("Press x100",        5,  40, lambda: int(round(S["press"] * 100)),       lambda v: _set("press", v / 100)),
    ("Release x100",     10,  60, lambda: int(round(S["release"] * 100)),     lambda v: _set("release", v / 100)),
    ("Hold ms",           0, 400, lambda: int(S["hold_ms"]),                  lambda v: _set("hold_ms", int(v))),
    ("Auto pinch",        0,   1, lambda: int(S["auto_pinch"]),               lambda v: _set("auto_pinch", bool(v))),
    ("Precision",         0,   1, lambda: int(S["precision"]),                lambda v: _set("precision", bool(v))),
    ("Precision speed %", 5, 100, lambda: int(S["prec_pct"]),                 lambda v: _set("prec_pct", int(v))),
]
_shown = {}
_panel_at = 0.0


def build_settings_window():
    _shown.clear()
    cv2.namedWindow(SETWIN, cv2.WINDOW_AUTOSIZE)
    cv2.imshow(SETWIN, settings_panel())
    for name, lo, hi, get, _put in SLIDERS:
        cv2.createTrackbar(name, SETWIN, clamp(get(), 0, hi), hi, lambda v: None)
        if lo > 0:
            cv2.setTrackbarMin(name, SETWIN, lo)
        v = clamp(get(), lo, hi)
        cv2.setTrackbarPos(name, SETWIN, v)
        _shown[name] = v


def sync_settings_window(now):
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
        build_settings_window()                  # closed by accident: bring it back


def settings_panel():
    img = np.full((178, 520, 3), 24, np.uint8)
    t_on, t_off = (("auto", "auto") if S["auto_pinch"]
                   else (f"{S['press']:.2f}", f"{S['release']:.2f}"))
    lines = [
        ("ARMED - your hand moves the mouse" if S["armed"]
         else "DISARMED - press F8 to take control", (90, 220, 90) if S["armed"] else (150, 150, 150)),
        (f"Hand: {S['hand']}  (other hand ignored)    Flip L/R: {'on' if S['invert'] else 'off'}", (230, 230, 230)),
        (f"Pinch press {t_on} / release {t_off}    hold {S['hold_ms']} ms", (230, 230, 230)),
        ("  press 0.16 = fingertips touching. Lower = harder to trigger.", (160, 160, 160)),
        (f"Precision: {'ON' if S['precision'] else 'off'} at {S['prec_pct']}% speed    "
         f"Zone {1 - 2 * S['margin']:.0%}", (230, 230, 230)),
        ("Saved automatically to airdraw_settings.json", (160, 160, 160)),
    ]
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

    cap = None
    for backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY):
        c = cv2.VideoCapture(CAM_INDEX, backend)
        if c.isOpened():
            cap = c
            break
        c.release()
    if cap is None:
        sys.exit("Could not open the camera.\n"
                 "  - End any leftover python.exe in Task Manager\n"
                 "  - Settings > Privacy & security > Camera > allow desktop apps\n"
                 "  - Close Zoom / Teams / the Camera app")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAM_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAM_H)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

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
    t0 = time.time()
    last_ms = -1
    fps = 0.0
    last = time.time()

    cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
    build_settings_window()
    start_hotkey()

    print(f"AirDraw running. Screen {SCREEN_W}x{SCREEN_H}. Hand: {S['hand']}.")
    print("DISARMED - press F8 to take control.")
    print("Thumb+index = draw.  Thumb+middle = click.  ESC = quit.\n")

    try:
        while True:
            try:
                if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1 \
                        and fps > 0:
                    break
            except cv2.error:
                pass

            ok, frame = cap.read()
            if not ok:
                continue
            frame = cv2.flip(frame, 1)
            h, w = frame.shape[:2]
            now = time.time()

            sync_settings_window(now)
            if _dirty_at is not None and now - _dirty_at > 0.8:
                save_settings()
            fx.cutoff = fy.cutoff = S["cutoff"]
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

            x0, x1 = margin * w, (1 - margin) * w
            y0, y1 = margin * h, (1 - margin) * h

            status, color = f"no {S['hand'].lower()} hand", (150, 150, 150)
            hand = pick_hand(res)

            if hand is not None:
                p = [(lm.x * w, lm.y * h, lm.z * w) for lm in hand]
                scale = max(d(p[0], p[9]), 1e-3)

                # 3D fingertip distances, so tips that only overlap on camera
                # (one in front of the other) don't count as touching
                r_index = d3(p[4], p[8]) / scale
                r_middle = d3(p[4], p[12]) / scale

                if S["auto_pinch"]:
                    seen.append(r_index)
                    if len(seen) > 45:
                        open_ref = clamp(float(np.percentile(seen, 90)), 0.9, 2.2)
                    t_on, t_off = open_ref * 0.26, open_ref * 0.44
                    t_arm = open_ref * 0.62
                else:
                    t_on, t_off = S["press"], S["release"]
                    t_arm = max(t_off * 1.35, t_on + 0.12)

                # While drawing, track the nib - midway between thumb and index
                # tips. That's where the "pen" really is, and it moves far less
                # than the fingertip alone does.
                if pen:
                    aim = ((p[4][0] + p[8][0]) / 2, (p[4][1] + p[8][1]) / 2)
                else:
                    aim = (0.8 * p[8][0] + 0.2 * p[5][0],
                           0.8 * p[8][1] + 0.2 * p[5][1])

                mx = clamp((aim[0] - x0) / max(x1 - x0, 1), 0, 1) * SCREEN_W
                my = clamp((aim[1] - y0) / max(y1 - y0, 1), 0, 1) * SCREEN_H
                mx = fx(mx, now, DRAW_STEADY if pen else 1.0)
                my = fy(my, now, DRAW_STEADY if pen else 1.0)

                # Precision mode: move by the hand's movement, scaled down,
                # starting from wherever the cursor already is.
                if S["precision"]:
                    if prec_cur is None:
                        prec_cur = list(cursor_pos())
                    if prec_prev is not None:
                        k = S["prec_pct"] / 100.0
                        prec_cur[0] = clamp(prec_cur[0] + (mx - prec_prev[0]) * k, 0, SCREEN_W - 1)
                        prec_cur[1] = clamp(prec_cur[1] + (my - prec_prev[1]) * k, 0, SCREEN_H - 1)
                    prec_prev = (mx, my)
                    mx, my = prec_cur
                else:
                    prec_prev = prec_cur = None

                closing = r_index < t_arm or r_middle < t_arm

                if active:
                    # Freeze the cursor the moment fingers start closing, so
                    # the stroke begins where you were actually aiming.
                    if closing and locked is None and not pen:
                        locked = (mx + offset[0], my + offset[1])
                    if not closing:
                        locked = None

                    # --- pen ---
                    if not pen and pen_pinch.ready(r_index, t_on, now):
                        start = locked or (mx + offset[0], my + offset[1])
                        offset[0], offset[1] = start[0] - mx, start[1] - my
                        move(int(start[0]), int(start[1]))
                        time.sleep(0.012)
                        press()
                        pen = True
                        locked = None
                        spline.clear()
                        spline.append(start)
                        pen_pinch.apart = None
                    elif pen and pen_pinch.let_go(r_index, t_off, now):
                        release()
                        pen = False
                        pen_pinch.reset()
                        spline.clear()

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
                    tx, ty = mx + offset[0], my + offset[1]
                    if not pen:
                        offset[0] *= 0.85
                        offset[1] *= 0.85

                    if locked is not None:
                        move(int(locked[0]), int(locked[1]))
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
                else:
                    if pen:
                        release()
                        pen = False
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

                c_idx = tip_col(r_index, pen_pinch, pen)
                c_mid = tip_col(r_middle, click_pinch, click_closed)
                c_thb = c_idx if (pen or r_index < t_on) else c_mid
                for i, col in ((4, c_thb), (8, c_idx), (12, c_mid)):
                    cv2.circle(frame, (int(p[i][0]), int(p[i][1])), 7, col, -1, cv2.LINE_AA)
                    cv2.circle(frame, (int(p[i][0]), int(p[i][1])), 7, (30, 30, 30), 1, cv2.LINE_AA)

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
                elif pen:
                    status, color = "DRAWING", (0, 0, 255)
                elif locked is not None:
                    status, color = "locked", (255, 200, 0)
                else:
                    status, color = "tracking", (0, 255, 0)
            else:
                fx.reset()
                fy.reset()
                spline.clear()
                locked = None
                offset[0] = offset[1] = 0.0
                prec_prev = None
                pen_pinch.reset()
                click_pinch.reset()
                click_closed = False
                if pen:
                    release()
                    pen = False
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
            # bottom strip: current settings, then the keys
            cv2.rectangle(frame, (0, h - 46), (w, h), (25, 25, 25), -1)
            cv2.putText(frame, f"{S['hand']} hand   steady {fx.cutoff:.2f}   area "
                               f"{1 - 2 * margin:.2f}"
                               f"{'   PRECISION ' + str(S['prec_pct']) + '%' if S['precision'] else ''}",
                        (10, h - 28), cv2.FONT_HERSHEY_SIMPLEX, 0.48,
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
        if _hotkey is not None:
            try:
                _hotkey.stop()
            except Exception:
                pass
        cap.release()
        cv2.destroyAllWindows()
        try:
            landmarker.close()
        except Exception:
            pass
        print("Stopped. Camera released. Settings saved.")


if __name__ == "__main__":
    main()
