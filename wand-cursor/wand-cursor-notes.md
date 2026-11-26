# Wand cursor — notes

Swaps your mouse pointer for one of 4 thin "wands" drawn in your blueprint
(`OneDrive\PAINT\long night\Anthropic Coursor.png`). The click/paint point is
exactly at the tip you marked with an arrow. Separate from AirDraw, but works
with it (AirDraw moves the real mouse; this only changes how it looks).

## Start / stop
- **Start:** double-click `start-wand.bat`. Nothing opens — look for the
  wand icon by the clock (it may be under the `^` arrow).
- **Quit:** Ctrl+Alt+Q, or right-click the tray icon → Quit.
- **Emergency:** double-click `restore-cursor.bat`, end `pythonw.exe` in Task
  Manager, or restart Windows. All of these bring your normal cursor back.

## Hotkeys (work in every window)
| Keys | Does |
|---|---|
| Ctrl+Alt+1 / 2 / 3 / 4 | wand 1 / 2 / 3 / 4 |
| Ctrl+Alt+0 | normal cursor (script keeps running) |
| Ctrl+Alt+W | wand on / off (comes back as the last wand you used) |
| Ctrl+Alt+Up / Down | longer / shorter, 0.1× steps, 0.5×–1.49× |
| Ctrl+Alt+Backspace | back to your exact drawing (1.0×) |
| Ctrl+Alt+Q | quit |

The tray icon's right-click menu does all of the same things and shows which
wand and length are on.

## The 4 wands (measured from the drawing)
| Wand | Lean | Angle | Length | Tip | Cursor size |
|---|---|---|---|---|---|
| 1 | `\` | 35.5° | 170.7 px | upper-left | 160×160 |
| 2 | `\` | 67.3° | 184.2 px | top | 192×192 |
| 3 | `/` | 71.1° | 83.5 px | top | 96×96 |
| 4 | `/` | 32.5° | 169.5 px | upper-right | 160×160 |

At 1.0× the wands are your exact hand-drawn pixels (the two stray dots in the
drawing are left out). At other lengths they are redrawn as clean straight
lines with the same angle. 1.49× is the limit because wand 2 then fills the
biggest cursor Windows allows (256×256).

**Colour:** every wand pixel inverts what's under it — white over dark, black
over light. Over a middle grey the inverted colour is also a middle grey, so
the wand is hard to see there; that's how inverting pixels behave.

**Which pointers change:** normal arrow, crosshair, link hand, text I-beam,
"working in background". Resize arrows and the busy spinner stay normal so
you can still see what Windows is doing.

## Paint check (2026-11-25)
Tested Windows 11 Paint with Brushes, Pencil, Eraser, Select and Text: over
the canvas Paint uses the normal Windows pointer, so the wand shows there.
**Overlay mode was not needed, so it wasn't built.** Paint may still draw its
own brush-size circle on top of the canvas — that's Paint, not the cursor.

## Overlay added (2026-11-26)
In real use Paint's own canvas pointer (brush outline / pencil / eraser)
covered the wand. Those are Paint's private pointers, and Windows only lets
us swap the standard ones, so `SetSystemCursor` can't reach them. Fix: over
`mspaint.exe`, a see-through, click-through, always-on-top window draws the
wand (black 1-px line with a soft white edge — an overlay can't invert
colours) and follows the pointer ~66×/s. It shows only when Windows is *not*
already showing the wand there, so there's never a double wand. Tray menu →
"Wand on top in Paint (overlay)" turns it on/off. `wand_settings.json` keys:
`overlay` (true/false) and `overlay_apps` (exe names, default
`["mspaint.exe"]`; add others that hide the wand the same way). If the
overlay ever fails, it switches itself off, writes `wand_error.log`, and the
normal wand cursor keeps working. wand.py is now DPI-aware too.

## Flicker / double cursor fixed (2026-11-26)
Over Paint's canvas you saw two pointers fighting: Paint's own brush/pencil
pointer sat exactly on the mouse while the overlay wand chased it on a 15 ms
timer, always a step behind. And the overlay switched on only where Windows
*wasn't* showing the wand, so at the canvas edge (where Paint flips between
its pointer and the normal one) it blinked on and off.
Now:
- **No lag:** a mouse hook moves the overlay on every single mouse movement
  (AirDraw's too), to where the pointer is going. Tested: after each move the
  wand tip and the pointer were on the same pixel.
- **One pointer:** while the overlay shows, Windows' own pointer is hidden
  with the switch screen magnifiers use (`MagShowSystemCursor`), and that
  hides Paint's private pointers too. Leaving Paint, turning the overlay or
  wand off, quitting, the watchdog and `restore-cursor.bat` all show it again.
- **No blinking:** the overlay is on exactly when the pointer is over Paint.
- **Tip on the hotspot:** checked for all 4 wands at 1.0x and 1.3x - the tip
  pixel lands exactly on the mouse point.
- **Ctrl+Alt+W** toggles the wand (also in the tray menu).
- Still Paint's: if Paint draws a brush-size circle *on the canvas itself*,
  that's part of the picture, not a pointer, so it can't be hidden.

## Files
- `make_wands.py` — builds the `.cur` files. `python make_wands.py 1.2` builds
  all 4 at 1.2×. `--from-blueprint "path.png"` re-copies the pixels if you
  redraw the blueprint (same layout: 4 lines left-to-right, near the top).
- `wand_pixels.json` — the copied pixels of each wand (offsets from the tip).
- `cursors/` — the built cursor files, e.g. `wand2_x1.00.cur`. Made on demand.
- `wand.py` — the background switcher (hotkeys, tray icon, settings, safety).
- `wand_settings.json` — remembers which wand (0 = normal) and the length.
- `wand.ico` — the tray icon.
- `wand_error.log` — only appears if something goes wrong.

## How "always restore" works
1. Quitting normally, logging off or shutting down restores the cursor.
2. If Python hits an error, it writes it to `wand_error.log` and restores.
3. A tiny second `pythonw.exe` (the "watchdog") waits for the main one to stop
   for any reason — including being killed in Task Manager — then restores.
   Tested: a forced kill brought the normal cursor back within 2 seconds.
4. Windows forgets swapped pointers on restart anyway.
