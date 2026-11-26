"""
wand.py - swaps your mouse pointer for one of the 4 wands.

Runs hidden in the background with a wand icon in the system tray (by the
clock). Right-click the icon for a menu.

Hotkeys (they work in every window, including Paint and while AirDraw runs):
  Ctrl+Alt+1 .. 4      pick wand 1-4
  Ctrl+Alt+0           your normal cursor
  Ctrl+Alt+W           wand on / off (back to the last wand you used)
  Ctrl+Alt+Up / Down   longer / shorter wand (0.1x steps, 0.5x - 1.49x)
  Ctrl+Alt+Backspace   back to your exact drawing (1.0x)
  Ctrl+Alt+Q           quit (normal cursor comes back)

Your normal cursor always comes back:
  * when you quit (hotkey or tray menu), log off or shut down,
  * if the script crashes (the error is written to wand_error.log),
  * if it is killed in Task Manager - a tiny "watchdog" helper waits for the
    main script to stop and then restores the cursor,
  * and by hand any time:  python wand.py --restore   (or restore-cursor.bat)
  A restart of Windows also always brings the normal cursor back.

Why AirDraw still works: AirDraw moves the real Windows mouse, and this
script only changes what the pointer LOOKS like, so the wand follows it.

Wand on top in Paint (overlay): Windows lets this script swap only the
standard Windows pointers. Over its canvas Paint shows its OWN pointer
(brush outline, pencil, eraser square...), which no program can swap. So
over Paint:
  * a see-through, click-through, always-on-top window draws the wand, with
    its tip exactly on the mouse's click point;
  * it moves on every single mouse movement (a mouse hook), not on a timer,
    so it never trails behind;
  * Windows' own pointer - and with it Paint's brush pointer - is hidden
    (the same switch screen magnifiers use), so only ONE pointer shows.
Leave Paint and the overlay goes away and the normal (wand) pointer is back.
It decides by "is the pointer over Paint?" only, so it doesn't blink on and
off at the canvas edge. Clicks go straight through it to Paint.
Turn it on/off in the tray menu ("Wand on top in Paint").
"""
import atexit
import ctypes
import ctypes.wintypes as wt
import json
import math
import os
import subprocess
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import make_wands  # noqa: E402  (builds the .cur files)

SETTINGS_FILE = os.path.join(HERE, "wand_settings.json")
ERROR_LOG = os.path.join(HERE, "wand_error.log")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

# ---------------------------------------------------------------- Windows API
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT),
                ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT),
                ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
                ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
                ("szInfo", wt.WCHAR * 256), ("uVersion", wt.UINT),
                ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
                ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wt.HICON)]


user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wt.HWND, wt.HMENU,
                                   wt.HINSTANCE, wt.LPVOID]
user32.CreateWindowExW.restype = wt.HWND
user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT,
                              ctypes.c_int, ctypes.c_int, wt.UINT]
user32.LoadImageW.restype = wt.HANDLE
user32.SetSystemCursor.argtypes = [wt.HANDLE, wt.DWORD]
user32.SystemParametersInfoW.argtypes = [wt.UINT, wt.UINT, wt.LPVOID, wt.UINT]
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
user32.CreatePopupMenu.restype = wt.HMENU
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wt.HWND,
                                  wt.LPVOID]
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT,
                               wt.UINT]
user32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD,
                                      ctypes.POINTER(NOTIFYICONDATAW)]
kernel32.CreateMutexW.argtypes = [wt.LPVOID, wt.BOOL, wt.LPCWSTR]
kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE
user32.DestroyWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.argtypes = [wt.HWND]
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]

# --- for the overlay (handles are 64-bit, so every type is spelled out)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD),
                ("hCursor", wt.HANDLE), ("ptScreenPos", wt.POINT)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG),
                ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte),
                ("AlphaFormat", ctypes.c_ubyte)]


user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.WindowFromPoint.argtypes = [wt.POINT]
user32.WindowFromPoint.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetAncestor.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.LoadCursorW.argtypes = [wt.HINSTANCE, wt.LPVOID]
user32.LoadCursorW.restype = wt.HANDLE
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.SetTimer.argtypes = [wt.HWND, ctypes.c_size_t, wt.UINT, wt.LPVOID]
user32.SetTimer.restype = ctypes.c_size_t
user32.KillTimer.argtypes = [wt.HWND, ctypes.c_size_t]
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.UpdateLayeredWindow.argtypes = [
    wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE),
    wt.HDC, ctypes.POINTER(wt.POINT), wt.DWORD,
    ctypes.POINTER(BLENDFUNCTION), wt.DWORD]
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BITMAPINFOHEADER),
                                   wt.UINT, ctypes.POINTER(ctypes.c_void_p),
                                   wt.HANDLE, wt.DWORD]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD,
                                                wt.LPWSTR,
                                                ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
WH_MOUSE_LL, WM_MOUSEMOVE = 14, 0x200

WS_POPUP = 0x80000000
WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x8, 0x20, 0x80
WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x80000, 0x08000000
HWND_TOPMOST = wt.HWND(-1)
SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x4, 0x10, 0x40
WM_TIMER = 0x113
# The mouse hook moves the overlay on every mouse movement. This slow timer
# only catches changes without movement (Paint closed, a window popped up).
OVERLAY_TIMER, OVERLAY_MS = 7, 100
GA_ROOT = 2
CURSOR_SHOWING = 1
DEFAULT_OVERLAY_APPS = ["mspaint.exe"]   # Windows 11 Paint (and classic Paint)

IMAGE_CURSOR, IMAGE_ICON, LR_LOADFROMFILE = 2, 1, 0x10
SPI_SETCURSORS = 0x57
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x1, 0x2, 0x4000
WM_DESTROY, WM_COMMAND, WM_HOTKEY = 0x2, 0x111, 0x312
WM_ENDSESSION, WM_CLOSE, WM_NULL = 0x16, 0x10, 0x0
WM_RBUTTONUP, WM_CONTEXTMENU, WM_LBUTTONUP = 0x205, 0x7B, 0x202
WM_TRAY = 0x8000 + 1                      # WM_APP + 1
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 1, 2, 4, 0x10
MF_STRING, MF_CHECKED, MF_SEPARATOR, MF_GRAYED = 0, 8, 0x800, 1
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x2, 0x100

# Which Windows pointers become the wand. Pointers for resizing windows and
# "busy" stay normal so you can still tell what is going on.
REPLACED_CURSORS = {
    32512: "normal arrow",
    32515: "crosshair",
    32649: "link hand",
    32513: "text I-beam",
    32650: "working in background",
}


# ---------------------------------------------------------------- cursors
_mag = None      # the Magnification library: None = not loaded yet, False = can't be used


def system_pointer(show):
    """Show or hide Windows' own pointer everywhere - including the private
    pointers apps like Paint draw - with the switch screen magnifiers use.
    True if it worked."""
    global _mag
    try:
        if _mag is None:
            _mag = ctypes.WinDLL("Magnification")
            if not _mag.MagInitialize():
                _mag = False
        if _mag:
            return bool(_mag.MagShowSystemCursor(bool(show)))
    except Exception:
        _mag = False
    return False


def restore_normal_cursor():
    """Reload your own cursor scheme from Windows settings, and make sure the
    pointer isn't hidden."""
    user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, 0)
    system_pointer(True)


def apply_wand(n, scale):
    path = make_wands.make_cursor(n, scale)
    with open(path, "rb") as f:
        size_byte = f.read(7)[6]
    side = size_byte or 256
    restore_normal_cursor()  # start from your scheme each time
    for cursor_id in REPLACED_CURSORS:
        # A fresh copy per pointer: Windows takes ownership of each one.
        # Loading at the file's own size stops Windows shrinking it to 32px.
        h = user32.LoadImageW(None, path, IMAGE_CURSOR, side, side,
                              LR_LOADFROMFILE)
        if not h or not user32.SetSystemCursor(h, cursor_id):
            raise ctypes.WinError(ctypes.get_last_error())


def make_dpi_aware():
    """Real screen pixels, so the overlay wand sits exactly on the pointer
    even with Windows scaling at 125% / 150%."""
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


# ---------------------------------------------------------------- overlay
class WandOverlay:
    """A see-through, click-through, always-on-top window showing the wand,
    used only where an app (Paint) shows its own pointer over the wand.
    While it shows, Windows' pointer is hidden, so there is only one."""

    def __init__(self):
        self.hwnd = None
        self.size = (0, 0)
        self.hot = (0, 0)          # where the tip is inside the overlay picture
        self.shown = False
        self.pos = None
        self.ticks = 0
        self.names = {}            # process id -> exe name
        self.my_pid = os.getpid()
        self.apps = set(DEFAULT_OVERLAY_APPS)
        self.wand_handles = set()
        self.hid_pointer = False   # we hid Windows' pointer
        self._proc = WNDPROC(lambda h, m, w, l:
                             user32.DefWindowProcW(h, m, w, l))

    def create(self, hinst):
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.lpszClassName = "WandCursorOverlay"
        user32.RegisterClassW(ctypes.byref(wc))
        ex = (WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST |
              WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
        self.hwnd = user32.CreateWindowExW(ex, wc.lpszClassName,
                                           "Wand overlay", WS_POPUP,
                                           0, 0, 1, 1, None, None, hinst,
                                           None)
        # The swapped Windows pointers keep their handles, so "is Windows
        # showing the wand right now?" = "is the current pointer one of these?"
        self.wand_handles = {user32.LoadCursorW(None, ctypes.c_void_p(cid))
                             for cid in REPLACED_CURSORS}

    def set_wand(self, n, scale):
        """Draw wand n: a 1-pixel black line with a soft white edge, so it
        shows on any colour (an overlay can't invert like the real cursor)."""
        core = set(make_wands.wand_pixels(n, scale))
        edge = {(x + dx, y + dy) for x, y in core
                for dx in (-1, 0, 1) for dy in (-1, 0, 1)} - core
        min_x = min(x for x, _ in edge)
        min_y = min(y for _, y in edge)
        w = max(x for x, _ in edge) - min_x + 1
        h = max(y for _, y in edge) - min_y + 1
        buf = bytearray(w * h * 4)           # BGRA, premultiplied, top row first
        for x, y in edge:
            i = ((y - min_y) * w + (x - min_x)) * 4
            buf[i:i + 4] = b"\xc8\xc8\xc8\xc8"   # white at 78%
        for x, y in core:
            i = ((y - min_y) * w + (x - min_x)) * 4
            buf[i:i + 4] = b"\x00\x00\x00\xff"   # solid black
        self.size, self.hot = (w, h), (-min_x, -min_y)

        bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32,
                               0, 0, 0, 0, 0, 0)
        screen = user32.GetDC(None)
        mem = gdi32.CreateCompatibleDC(screen)
        bits = ctypes.c_void_p()
        bmp = gdi32.CreateDIBSection(screen, ctypes.byref(bmi), 0,
                                     ctypes.byref(bits), None, 0)
        ctypes.memmove(bits, bytes(buf), len(buf))
        old = gdi32.SelectObject(mem, bmp)
        pt = wt.POINT(*(self.pos or (-10000, -10000)))
        size = wt.SIZE(w, h)
        src = wt.POINT(0, 0)
        blend = BLENDFUNCTION(0, 0, 255, 1)  # use the picture's own alpha
        user32.UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(pt),
                                   ctypes.byref(size), mem, ctypes.byref(src),
                                   0, ctypes.byref(blend), 2)  # ULW_ALPHA
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)

    def proc_name(self, pid):
        if pid not in self.names:
            if len(self.names) > 200:
                self.names.clear()
            name = ""
            h = kernel32.OpenProcess(0x1000, False, pid)  # limited query
            if h:
                buf = ctypes.create_unicode_buffer(520)
                n = wt.DWORD(520)
                if kernel32.QueryFullProcessImageNameW(h, 0, buf,
                                                       ctypes.byref(n)):
                    name = os.path.basename(buf.value).lower()
                kernel32.CloseHandle(h)
            self.names[pid] = name
        return self.names[pid]

    def wanted(self, pt):
        """Is the pointer over one of the overlay apps (Paint)? Only that -
        not which pointer Windows shows - so it can't blink at the canvas
        edge where Paint switches between its pointer and the normal one."""
        root = user32.GetAncestor(user32.WindowFromPoint(pt), GA_ROOT)
        if not root:
            return False
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(root, ctypes.byref(pid))
        if pid.value == self.my_pid:
            return self.shown                # pointing at ourselves: no change
        return self.proc_name(pid.value) in self.apps

    def follow(self, x, y):
        """The pointer is at (x, y): put the wand's tip exactly there, or
        hide the overlay if the pointer isn't over Paint. Called by the mouse
        hook on every movement, so it keeps up with the real pointer."""
        pt = wt.POINT(x, y)
        if not self.wanted(pt):
            self.hide()
            return
        self.ticks += 1
        # the tip pixel sits at self.hot inside the picture
        pos = (x - self.hot[0], y - self.hot[1])
        if not self.shown or self.ticks % 200 == 0:
            # show it / re-assert "always on top" now and then
            user32.SetWindowPos(self.hwnd, HWND_TOPMOST, pos[0], pos[1], 0, 0,
                                SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        elif pos != self.pos:
            user32.SetWindowPos(self.hwnd, None, pos[0], pos[1], 0, 0,
                                SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
        self.pos, self.shown = pos, True
        if not self.hid_pointer:
            self.hid_pointer = system_pointer(False)   # only the wand shows now

    def tick(self):
        pt = wt.POINT()
        if user32.GetCursorPos(ctypes.byref(pt)):
            self.follow(pt.x, pt.y)

    def hide(self):
        if self.hid_pointer:
            system_pointer(True)         # Windows' pointer is back
            self.hid_pointer = False
        if self.shown:
            user32.ShowWindow(self.hwnd, 0)  # SW_HIDE
            self.shown = False


# ---------------------------------------------------------------- settings
def load_settings():
    s = {"wand": 1, "scale": 1.0, "overlay": True, "last_wand": 1,
         "overlay_apps": list(DEFAULT_OVERLAY_APPS)}
    try:
        with open(SETTINGS_FILE) as f:
            s.update(json.load(f))
    except (OSError, ValueError):
        pass
    s["wand"] = s["wand"] if s["wand"] in (0, 1, 2, 3, 4) else 1
    s["last_wand"] = s["last_wand"] if s["last_wand"] in (1, 2, 3, 4) else 1
    s["scale"] = make_wands.clamp_scale(float(s["scale"]))
    s["overlay"] = bool(s["overlay"])
    if not isinstance(s["overlay_apps"], list):
        s["overlay_apps"] = list(DEFAULT_OVERLAY_APPS)
    s["overlay_apps"] = [str(a).lower() for a in s["overlay_apps"]]
    return s


def save_settings(s):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(s, f, indent=2)


def log_error(text):
    with open(ERROR_LOG, "a") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")


# ---------------------------------------------------------------- watchdog
def watchdog(pid):
    """Wait for the main script to stop, then put the normal cursor back."""
    SYNCHRONIZE = 0x00100000
    h = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if h:
        kernel32.WaitForSingleObject(h, 0xFFFFFFFF)
    restore_normal_cursor()


def start_watchdog():
    exe = sys.executable
    if exe.lower().endswith("python.exe"):   # use the no-window version
        exe = exe[:-10] + "pythonw.exe"
    subprocess.Popen([exe, os.path.abspath(__file__), "--watchdog",
                      str(os.getpid())], creationflags=0x08000000)


# ---------------------------------------------------------------- the app
HOTKEYS = {  # id: (virtual key, label)
    1: (0x31, "Ctrl+Alt+1"), 2: (0x32, "Ctrl+Alt+2"),
    3: (0x33, "Ctrl+Alt+3"), 4: (0x34, "Ctrl+Alt+4"),
    10: (0x30, "Ctrl+Alt+0"),
    11: (0x26, "Ctrl+Alt+Up"), 12: (0x28, "Ctrl+Alt+Down"),
    13: (0x08, "Ctrl+Alt+Backspace"),
    14: (0x51, "Ctrl+Alt+Q"),
    16: (0x57, "Ctrl+Alt+W"),
}
# tray-menu command ids reuse the hotkey ids so both do the same thing


class WandApp:
    def __init__(self):
        self.settings = load_settings()
        self.hwnd = None
        self.icon = None
        self.taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        self._wndproc = WNDPROC(self.wndproc)   # keep a reference alive
        self.overlay = WandOverlay()
        self.timer_on = False
        self.hook = None
        self._hookproc = HOOKPROC(self.mouse_hook)   # keep a reference alive
        self.hook_errors = 0

    # ---- actions
    def do(self, cmd):
        s = self.settings
        if cmd in (1, 2, 3, 4):
            s["wand"] = cmd
            s["last_wand"] = cmd
        elif cmd == 16:   # wand on / off
            if s["wand"]:
                s["last_wand"] = s["wand"]   # remembered for switching back on
                s["wand"] = 0
            else:
                s["wand"] = s["last_wand"]
        elif cmd == 10:
            s["wand"] = 0
        elif cmd == 11:   # next 0.1 step up (1.4 -> 1.49 at the top)
            s["scale"] = make_wands.clamp_scale(
                math.floor(s["scale"] * 10 + 1e-6) / 10 + 0.1)
        elif cmd == 12:   # next 0.1 step down (1.49 -> 1.4)
            s["scale"] = make_wands.clamp_scale(
                math.ceil(s["scale"] * 10 - 1e-6) / 10 - 0.1)
        elif cmd == 13:
            s["scale"] = 1.0
        elif cmd == 14:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
            return
        elif cmd == 15:
            s["overlay"] = not s["overlay"]
        else:
            return
        save_settings(s)
        self.apply()

    def apply(self):
        s = self.settings
        if s["wand"] == 0:
            restore_normal_cursor()
        else:
            apply_wand(s["wand"], s["scale"])
        self.apply_overlay()
        self.update_tray()

    def apply_overlay(self):
        """Overlay on = redraw it for the current wand and follow the
        pointer. Off (or normal cursor) = hide it and stop checking."""
        s = self.settings
        ov = self.overlay
        if ov.hwnd is None:
            return
        on = s["overlay"] and s["wand"] != 0
        if on:
            ov.apps = set(s["overlay_apps"])
            ov.set_wand(s["wand"], s["scale"])
            if not self.timer_on:
                user32.SetTimer(self.hwnd, OVERLAY_TIMER, OVERLAY_MS, None)
                self.timer_on = True
            if not self.hook:
                self.hook = user32.SetWindowsHookExW(
                    WH_MOUSE_LL, self._hookproc, kernel32.GetModuleHandleW(None), 0)
                if not self.hook:
                    log_error("Mouse hook could not start; the overlay follows the timer only.")
            ov.tick()
        else:
            self.stop_overlay()

    def stop_overlay(self):
        if self.timer_on:
            user32.KillTimer(self.hwnd, OVERLAY_TIMER)
            self.timer_on = False
        if self.hook:
            user32.UnhookWindowsHookEx(self.hook)
            self.hook = None
        self.overlay.hide()                  # also brings Windows' pointer back

    def mouse_hook(self, code, wparam, lparam):
        """Windows calls this on every mouse event, before the pointer
        moves: move the wand to where the pointer is going, so it never
        trails behind. Must be quick - it never blocks the mouse."""
        if code == 0 and wparam == WM_MOUSEMOVE:
            try:
                info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                self.overlay.follow(info.pt.x, info.pt.y)
            except Exception:
                self.hook_errors += 1
                if self.hook_errors == 1:
                    log_error("Overlay mouse hook:\n" + traceback.format_exc())
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def status_text(self):
        s = self.settings
        if s["wand"] == 0:
            return f"Wand cursor: normal cursor (length {s['scale']:.2f}x)"
        return f"Wand cursor: wand {s['wand']}, length {s['scale']:.2f}x"

    # ---- tray icon
    def tray_data(self, flags):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(nid)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = flags
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self.icon
        nid.szTip = self.status_text()[:127]
        return nid

    def add_tray(self):
        nid = self.tray_data(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

    def update_tray(self):
        nid = self.tray_data(NIF_TIP)
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def balloon(self, title, text):
        nid = self.tray_data(NIF_INFO)
        nid.szInfoTitle = title[:63]
        nid.szInfo = text[:255]
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def remove_tray(self):
        nid = self.tray_data(0)
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))

    def show_menu(self):
        s = self.settings
        m = user32.CreatePopupMenu()

        def item(cmd, text, checked=False, grayed=False):
            flags = MF_STRING | (MF_CHECKED if checked else 0) | \
                (MF_GRAYED if grayed else 0)
            user32.AppendMenuW(m, flags, cmd, text)

        item(0, self.status_text().replace("Wand cursor: ", "Now: "),
             grayed=True)
        user32.AppendMenuW(m, MF_SEPARATOR, 0, None)
        names = {1: "Wand 1  \\ long", 2: "Wand 2  \\ steep",
                 3: "Wand 3  / short", 4: "Wand 4  / long"}
        for n in (1, 2, 3, 4):
            item(n, f"{names[n]}\tCtrl+Alt+{n}", s["wand"] == n)
        item(10, "Normal cursor\tCtrl+Alt+0", s["wand"] == 0)
        item(16, "Wand on / off\tCtrl+Alt+W")
        user32.AppendMenuW(m, MF_SEPARATOR, 0, None)
        item(11, "Longer\tCtrl+Alt+Up", grayed=s["scale"] >= make_wands.MAX_SCALE)
        item(12, "Shorter\tCtrl+Alt+Down", grayed=s["scale"] <= make_wands.MIN_SCALE)
        item(13, "Exact drawing length (1.0x)\tCtrl+Alt+Backspace",
             s["scale"] == 1.0)
        user32.AppendMenuW(m, MF_SEPARATOR, 0, None)
        item(15, "Wand on top in Paint (overlay)", s["overlay"])
        user32.AppendMenuW(m, MF_SEPARATOR, 0, None)
        item(14, "Quit (normal cursor back)\tCtrl+Alt+Q")

        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.hwnd)   # so the menu closes properly
        cmd = user32.TrackPopupMenu(m, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                                    pt.x, pt.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(m)
        if cmd:
            self.do(cmd)

    # ---- window messages
    def wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TIMER and wparam == OVERLAY_TIMER:
                try:
                    self.overlay.tick()
                except Exception:            # overlay trouble: switch it off, keep the wand
                    log_error("Overlay stopped:\n" + traceback.format_exc())
                    self.stop_overlay()
                return 0
            if msg == WM_HOTKEY:
                self.do(int(wparam))
                return 0
            if msg == WM_TRAY:
                if lparam in (WM_RBUTTONUP, WM_CONTEXTMENU, WM_LBUTTONUP):
                    self.show_menu()
                return 0
            if msg == self.taskbar_created:      # Explorer restarted
                self.add_tray()
                return 0
            if msg == WM_ENDSESSION and wparam:  # logging off / shutting down
                restore_normal_cursor()
                return 0
            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            log_error(traceback.format_exc())
            restore_normal_cursor()
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ---- run
    def run(self):
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.lpszClassName = "WandCursorHiddenWindow"
        user32.RegisterClassW(ctypes.byref(wc))
        # A hidden window: it never shows, it only receives hotkeys and
        # tray-icon clicks.
        self.hwnd = user32.CreateWindowExW(0, wc.lpszClassName, "Wand cursor",
                                           0, 0, 0, 0, 0, None, None, hinst,
                                           None)
        ico_path = make_wands.make_tray_icon()
        self.icon = user32.LoadImageW(None, ico_path, IMAGE_ICON, 0, 0,
                                      LR_LOADFROMFILE | 0x40)  # LR_DEFAULTSIZE
        self.add_tray()
        try:
            self.overlay.create(hinst)
        except Exception:                    # the wand cursor still works without it
            self.overlay.hwnd = None
            log_error("Overlay could not start:\n" + traceback.format_exc())

        failed = []
        for hk_id, (vk, label) in HOTKEYS.items():
            if not user32.RegisterHotKey(self.hwnd, hk_id,
                                         MOD_CONTROL | MOD_ALT | MOD_NOREPEAT,
                                         vk):
                failed.append(label)
        if failed:
            msg = ("These hotkeys are taken by another program, use the tray "
                   "menu instead: " + ", ".join(failed))
            log_error(msg)
            self.balloon("Wand cursor", msg)

        self.apply()

        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        self.stop_overlay()                  # unhook, Windows' pointer back
        for hk_id in HOTKEYS:
            user32.UnregisterHotKey(self.hwnd, hk_id)
        self.remove_tray()


def main():
    args = sys.argv[1:]
    if args[:1] == ["--restore"]:
        restore_normal_cursor()
        print("Normal cursor restored.")
        return
    if args[:1] == ["--watchdog"]:
        watchdog(int(args[1]))
        return

    # Only one copy at a time.
    kernel32.CreateMutexW(None, False, "Local\\WandCursorSingleInstance")
    if ctypes.get_last_error() == 183:   # ERROR_ALREADY_EXISTS
        user32.MessageBoxW(None, "Wand cursor is already running - look for "
                           "the wand icon by the clock.", "Wand cursor", 0x40)
        return

    make_dpi_aware()
    atexit.register(restore_normal_cursor)
    start_watchdog()
    try:
        WandApp().run()
    except Exception:
        log_error(traceback.format_exc())
    finally:
        restore_normal_cursor()


if __name__ == "__main__":
    main()
