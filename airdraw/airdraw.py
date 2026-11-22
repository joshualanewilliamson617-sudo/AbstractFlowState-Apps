#!/usr/bin/env python3
"""
AirDraw - draw in MS Paint with your hand. No setup, no calibration.

    Pinch thumb + INDEX     pen down, draws while held
    Pinch thumb + MIDDLE    left click
    ESC                     quit

    [ ]   steadier / snappier
    - =   smaller / larger hand area
    SPACE pause

The whole camera view is your drawing area, so move your hand across the full
frame to reach the edges of the screen. Pinch sensitivity tunes itself to your
hand over the first few seconds - just open and close your hand once at the
start and it sorts itself out.

REQUIRES:  py -m pip install opencv-python mediapipe pyautogui
RUN:       python airdraw.py
"""

import atexit
import ctypes
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
             f"Run:  py -m pip install opencv-python mediapipe pyautogui")

# ------------------------------------------------------------------ setup ---

CAM_INDEX = 0
CAM_W, CAM_H = 960, 540

MARGIN = 0.08          # unused border around the frame; - and = adjust it
SMOOTH = 0.7           # One Euro min cutoff. Lower = steadier.
BETA = 0.015           # responsiveness on fast moves
DRAW_STEADY = 0.55     # extra smoothing while the pen is down

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/1/hand_landmarker.task")
MODEL_NAME = "hand_landmarker.task"
WINDOW = "AirDraw  -  ESC to quit"

CONNECTIONS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
               (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14),
               (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20),
               (0, 17)]

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

# -------------------------------------------------------------- helpers -----


def d(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


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


def get_model():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), MODEL_NAME)
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


# ----------------------------------------------------------------- main -----


def main():
    global MARGIN

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
            num_hands=1,
            min_hand_detection_confidence=0.6,
            min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5))

    fx, fy = OneEuro(SMOOTH, BETA), OneEuro(SMOOTH, BETA)
    spline = deque(maxlen=4)

    # Self-tuning pinch. It watches how wide your hand opens and sets the
    # thresholds from that, so there's no calibration screen and no saved
    # settings file that can go stale.
    seen = deque(maxlen=240)
    open_ref = 1.30

    pen = False
    click_closed = False
    locked = None
    offset = [0.0, 0.0]
    paused = False
    t0 = time.time()
    fps = 0.0
    last = time.time()

    print(f"AirDraw running. Screen {SCREEN_W}x{SCREEN_H}.")
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

            res = landmarker.detect_for_video(
                mp.Image(image_format=mp.ImageFormat.SRGB,
                         data=np.ascontiguousarray(
                             cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))),
                int((now - t0) * 1000))

            x0, x1 = MARGIN * w, (1 - MARGIN) * w
            y0, y1 = MARGIN * h, (1 - MARGIN) * h

            status, color = "no hand", (150, 150, 150)

            if res.hand_landmarks:
                p = [(lm.x * w, lm.y * h) for lm in res.hand_landmarks[0]]
                scale = max(d(p[0], p[9]), 1e-3)

                r_index = d(p[4], p[8]) / scale
                r_middle = d(p[4], p[12]) / scale

                seen.append(r_index)
                if len(seen) > 45:
                    open_ref = clamp(float(np.percentile(seen, 90)), 0.9, 2.2)
                t_arm = open_ref * 0.62
                t_on = open_ref * 0.26
                t_off = open_ref * 0.44

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

                closing = r_index < t_arm or r_middle < t_arm

                if not paused:
                    # Freeze the cursor the moment fingers start closing, so
                    # the stroke begins where you were actually aiming.
                    if closing and locked is None and not pen:
                        locked = (mx + offset[0], my + offset[1])
                    if not closing:
                        locked = None

                    # --- pen ---
                    if not pen and r_index < t_on:
                        start = locked or (mx + offset[0], my + offset[1])
                        offset[0], offset[1] = start[0] - mx, start[1] - my
                        move(int(start[0]), int(start[1]))
                        time.sleep(0.012)
                        press()
                        pen = True
                        locked = None
                        spline.clear()
                        spline.append(start)
                    elif pen and r_index > t_off:
                        release()
                        pen = False
                        spline.clear()

                    # --- click ---
                    if not pen:
                        if not click_closed and r_middle < t_on:
                            click_closed = True
                            q = locked or (mx + offset[0], my + offset[1])
                            click_at(int(q[0]), int(q[1]))
                        elif click_closed and r_middle > t_off:
                            click_closed = False

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
                elif pen:
                    release()
                    pen = False

                for a, b in CONNECTIONS:
                    cv2.line(frame, (int(p[a][0]), int(p[a][1])),
                             (int(p[b][0]), int(p[b][1])), (255, 210, 100), 2)
                col = (0, 0, 255) if pen else (
                    (255, 200, 0) if locked is not None else (0, 255, 255))
                cv2.circle(frame, (int(aim[0]), int(aim[1])), 10, col, -1)
                cv2.line(frame, (int(p[4][0]), int(p[4][1])),
                         (int(p[8][0]), int(p[8][1])), col, 2)

                if paused:
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
                if pen:
                    release()
                    pen = False

            cv2.rectangle(frame, (int(x0), int(y0)), (int(x1), int(y1)),
                          (90, 200, 90), 2)
            fps = 0.9 * fps + 0.1 / max(now - last, 1e-6)
            last = now
            cv2.rectangle(frame, (0, 0), (w, 30), (25, 25, 25), -1)
            cv2.putText(frame, status, (10, 21), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, color, 2)
            cv2.putText(frame, f"steady {fx.cutoff:.2f}   area "
                               f"{1 - 2 * MARGIN:.2f}   {fps:4.1f}fps",
                        (200, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.48,
                        (200, 200, 200), 1)
            cv2.imshow(WINDOW, frame)

            k = cv2.waitKey(1) & 0xFF
            if k in (27, ord("q")):
                break
            elif k == ord(" "):
                paused = not paused
                if paused and pen:
                    release()
                    pen = False
            elif k == ord("["):
                fx.cutoff = fy.cutoff = clamp(fx.cutoff - 0.05, 0.15, 3)
            elif k == ord("]"):
                fx.cutoff = fy.cutoff = clamp(fx.cutoff + 0.05, 0.15, 3)
            elif k == ord("-"):
                MARGIN = clamp(MARGIN + 0.02, 0.0, 0.3)
            elif k in (ord("="), ord("+")):
                MARGIN = clamp(MARGIN - 0.02, 0.0, 0.3)

    finally:
        cleanup()
        cap.release()
        cv2.destroyAllWindows()
        try:
            landmarker.close()
        except Exception:
            pass
        print("Stopped. Camera released.")


if __name__ == "__main__":
    main()
