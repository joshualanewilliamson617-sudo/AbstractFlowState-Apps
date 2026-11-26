"""
wand.py - swaps your mouse pointer for one of the 4 wands.

Runs hidden in the background with a wand icon in the system tray (by the
clock). Right-click the icon for a menu.

Hotkeys (they work in every window, including Paint and while AirDraw runs):
  Ctrl+Alt+1 .. 4      pick wand 1-4
  Ctrl+Alt+0           your normal cursor
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
def restore_normal_cursor():
    """Reload your own cursor scheme from Windows settings."""
    user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, 0)


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


# ---------------------------------------------------------------- settings
def load_settings():
    s = {"wand": 1, "scale": 1.0}
    try:
        with open(SETTINGS_FILE) as f:
            s.update(json.load(f))
    except (OSError, ValueError):
        pass
    s["wand"] = s["wand"] if s["wand"] in (0, 1, 2, 3, 4) else 1
    s["scale"] = make_wands.clamp_scale(float(s["scale"]))
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
}
# tray-menu command ids reuse the hotkey ids so both do the same thing


class WandApp:
    def __init__(self):
        self.settings = load_settings()
        self.hwnd = None
        self.icon = None
        self.taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        self._wndproc = WNDPROC(self.wndproc)   # keep a reference alive

    # ---- actions
    def do(self, cmd):
        s = self.settings
        if cmd in (1, 2, 3, 4):
            s["wand"] = cmd
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
        self.update_tray()

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
        user32.AppendMenuW(m, MF_SEPARATOR, 0, None)
        item(11, "Longer\tCtrl+Alt+Up", grayed=s["scale"] >= make_wands.MAX_SCALE)
        item(12, "Shorter\tCtrl+Alt+Down", grayed=s["scale"] <= make_wands.MIN_SCALE)
        item(13, "Exact drawing length (1.0x)\tCtrl+Alt+Backspace",
             s["scale"] == 1.0)
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
