"""
make_wands.py - builds the 4 wand cursors (.cur files) from your blueprint.

How it works, in plain words:
  * wand_pixels.json holds the exact pixels of each wand, copied from your
    Paint drawing. Each pixel is stored as "how far from the tip" (dx, dy),
    so the tip is always (0, 0).
  * At length scale 1.0 those exact pixels are used.
  * At any other scale the wand is redrawn as a clean straight line with the
    same angle, from the tip, at the new length.
  * Every wand pixel is an "inverting" pixel: Windows flips the colour of the
    screen under it, so it shows white over dark and black over light.
  * The hotspot (where clicks and paint land) is set exactly on the tip.

Usage:
  python make_wands.py                      build cursors at scale 1.0
  python make_wands.py 1.2                  build cursors at scale 1.2
  python make_wands.py --from-blueprint "C:\\path\\drawing.png"
                                            re-copy the pixels from a drawing
"""
import json
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PIXELS_FILE = os.path.join(HERE, "wand_pixels.json")
CURSOR_DIR = os.path.join(HERE, "cursors")

MAX_SIDE = 256          # biggest cursor Windows supports
MIN_SCALE = 0.5
MAX_SCALE = 1.49        # wand 2 is 171 px tall; 171 * 1.49 still fits in 256


# ---------------------------------------------------------------- blueprint
def extract_from_blueprint(path):
    """Find the 4 wand lines in the drawing and save their pixels."""
    from PIL import Image  # only needed for this step

    im = Image.open(path).convert("L")
    px = im.load()
    # The wands all sit in this area of the drawing (arrows and text are
    # filtered out below because they are not thin lines).
    dark = {(x, y) for y in range(80, 360) for x in range(150, 760)
            if px[x, y] < 128}
    seen, lines = set(), []
    for p in dark:
        if p in seen:
            continue
        stack, comp = [p], []
        seen.add(p)
        while stack:
            x, y = stack.pop()
            comp.append((x, y))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    q = (x + dx, y + dy)
                    if q in dark and q not in seen:
                        seen.add(q)
                        stack.append(q)
        xs = [c[0] for c in comp]
        ys = [c[1] for c in comp]
        span = max(max(xs) - min(xs), max(ys) - min(ys))
        # a 1-pixel line has about one pixel per step of its longest side
        if len(comp) >= 50 and len(comp) <= span * 1.6 + 5:
            lines.append(comp)
    if len(lines) != 4:
        raise SystemExit(f"Expected 4 wand lines, found {len(lines)}")
    lines.sort(key=lambda c: min(p[0] for p in c))  # left to right = 1..4

    out = {}
    for n, comp in enumerate(lines, 1):
        top = min(p[1] for p in comp)
        bottom = max(p[1] for p in comp)
        # Tip = the topmost pixel (wand 1: upper-left, 4: upper-right, 2/3: top)
        tip = min((p for p in comp if p[1] == top), key=lambda p: p[0]) \
            if n in (1, 2) else max((p for p in comp if p[1] == top),
                                    key=lambda p: p[0])
        far = max((p for p in comp if p[1] == bottom),
                  key=lambda p: math.dist(p, tip))
        out[str(n)] = {
            "pixels": sorted([p[0] - tip[0], p[1] - tip[1]] for p in comp),
            "far_end": [far[0] - tip[0], far[1] - tip[1]],
        }
        fx, fy = out[str(n)]["far_end"]
        print(f"wand {n}: {len(comp)} px, length {math.hypot(fx, fy):.1f}, "
              f"angle {math.degrees(math.atan2(abs(fy), abs(fx))):.1f} deg")
    with open(PIXELS_FILE, "w") as f:
        json.dump(out, f)
    print("saved", PIXELS_FILE)


# ---------------------------------------------------------------- drawing
def line_pixels(x1, y1):
    """Straight 1-pixel line from (0,0) to (x1,y1) (Bresenham's method)."""
    pts, x, y = [], 0, 0
    dx, dy = abs(x1), -abs(y1)
    sx, sy = (1 if x1 > 0 else -1), (1 if y1 > 0 else -1)
    err = dx + dy
    while True:
        pts.append((x, y))
        if x == x1 and y == y1:
            return pts
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy


def wand_pixels(n, scale):
    """Pixels of wand n (offsets from the tip) at the given length scale."""
    with open(PIXELS_FILE) as f:
        data = json.load(f)[str(n)]
    if abs(scale - 1.0) < 1e-6:
        return [tuple(p) for p in data["pixels"]]   # your exact drawing
    fx, fy = data["far_end"]
    return line_pixels(round(fx * scale), round(fy * scale))


def clamp_scale(scale):
    return round(min(MAX_SCALE, max(MIN_SCALE, scale)), 2)


# ---------------------------------------------------------------- .cur file
def build_cur(pixels):
    """Make a monochrome .cur: returns (bytes, side, hotspot_x, hotspot_y).

    Monochrome cursors have two masks:
      AND mask 1 + XOR mask 0 -> transparent (screen shows through)
      AND mask 1 + XOR mask 1 -> inverted screen colour  (our wand)
    """
    min_x = min(p[0] for p in pixels)
    min_y = min(p[1] for p in pixels)
    w = max(p[0] for p in pixels) - min_x + 1
    h = max(p[1] for p in pixels) - min_y + 1
    side = max(32, w, h)
    side = (side + 31) // 32 * 32          # round up to a multiple of 32
    if side > MAX_SIDE:
        raise ValueError(f"wand needs {side}px, more than {MAX_SIDE}")
    hot_x, hot_y = -min_x, -min_y          # where the tip (0,0) lands

    row_bytes = side // 8                  # side is a multiple of 32 -> padded
    xor = bytearray(row_bytes * side)
    and_ = bytearray(b"\xff" * (row_bytes * side))
    for dx, dy in pixels:
        x, y = dx + hot_x, dy + hot_y
        row = side - 1 - y                 # bitmaps are stored bottom-up
        xor[row * row_bytes + x // 8] |= 0x80 >> (x % 8)

    header = struct.pack("<IiiHHIIiiII", 40, side, side * 2, 1, 1, 0,
                         len(xor) + len(and_), 0, 0, 2, 0)
    palette = b"\x00\x00\x00\x00" + b"\xff\xff\xff\x00"
    image = header + palette + bytes(xor) + bytes(and_)
    size_byte = 0 if side == 256 else side
    icondir = struct.pack("<HHH", 0, 2, 1)
    entry = struct.pack("<BBBBHHII", size_byte, size_byte, 0, 0,
                        hot_x, hot_y, len(image), 6 + 16)
    return icondir + entry + image, side, hot_x, hot_y


def cursor_path(n, scale):
    return os.path.join(CURSOR_DIR, f"wand{n}_x{scale:.2f}.cur")


def make_cursor(n, scale):
    """Write wand n at this scale (if not already made); return its path."""
    scale = clamp_scale(scale)
    path = cursor_path(n, scale)
    if not os.path.exists(path):
        os.makedirs(CURSOR_DIR, exist_ok=True)
        data, *_ = build_cur(wand_pixels(n, scale))
        with open(path, "wb") as f:
            f.write(data)
    return path


# ---------------------------------------------------------------- tray icon
def make_tray_icon():
    """A small 32x32 colour icon (white wand on a dark disc) for the tray."""
    path = os.path.join(HERE, "wand.ico")
    side = 32
    rows = []
    for y in range(side - 1, -1, -1):      # bottom-up
        row = bytearray()
        for x in range(side):
            inside = (x - 15.5) ** 2 + (y - 15.5) ** 2 <= 15.5 ** 2
            on_wand = abs((x - 6) - (y - 6) * 0.72) < 1.0 and 6 <= y <= 27
            if on_wand:
                row += bytes((255, 255, 255, 255))
            elif inside:
                row += bytes((60, 30, 30, 255))   # BGRA: dark blue disc
            else:
                row += bytes((0, 0, 0, 0))
        rows.append(bytes(row))
    pix = b"".join(rows)
    and_mask = bytes(4 * side)             # all 0: alpha channel decides
    header = struct.pack("<IiiHHIIiiII", 40, side, side * 2, 1, 32, 0,
                         len(pix) + len(and_mask), 0, 0, 0, 0)
    image = header + pix + and_mask
    data = struct.pack("<HHH", 0, 1, 1) + struct.pack(
        "<BBBBHHII", side, side, 0, 0, 1, 32, len(image), 22) + image
    with open(path, "wb") as f:
        f.write(data)
    return path


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--from-blueprint":
        extract_from_blueprint(args[1])
        args = args[2:]
    s = clamp_scale(float(args[0])) if args else 1.0
    for n in (1, 2, 3, 4):
        p = make_cursor(n, s)
        _, side, hx, hy = build_cur(wand_pixels(n, s))
        print(f"wand {n} @ {s:.2f}x -> {os.path.basename(p)} "
              f"({side}x{side}, tip at {hx},{hy})")
    print("tray icon ->", make_tray_icon())
