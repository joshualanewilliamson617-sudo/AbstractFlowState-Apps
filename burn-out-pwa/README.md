# Burn-Out — installable app

The game itself is still one file: `index.html`. Everything else in this folder
is there so Chrome and Edge can install it like a real app.

| File | What it's for |
|---|---|
| `index.html` | The whole game (all features from `Burn-Out.html`, plus an **INSTALL AS APP** button on the start screen). |
| `manifest.webmanifest` | The app's "ID card": its name, colours and which icons to use. |
| `sw.js` | A background helper that saves a copy of the game so it opens with no internet. |
| `icon-*.png`, `apple-touch-icon.png`, `favicon-32.png` | The app icon at different sizes (desktop, taskbar, phone home screen, browser tab). |
| `icon-source.svg` | The original drawing of the icon. Edit this if you want to change it. |
| `serve.ps1` | A tiny web server so you can install it from your own computer. |

## Install it on this PC

Browsers only offer "Install" for pages that come from a web address, not a
double-clicked file. So:

1. Right-click `serve.ps1` → **Run with PowerShell**
   (or run `powershell -ExecutionPolicy Bypass -File serve.ps1` in this folder).
2. Open **http://localhost:8765** in Chrome or Edge.
3. Click **INSTALL AS APP** on the start screen, or the install icon at the right
   end of the address bar.

Burn-Out now has its own window, desktop/Start-menu icon and taskbar icon. After
installing once you can close the server; the app keeps working offline.

## Put it online (works on phones too)

Upload this whole folder to any web host that uses https (GitHub Pages, Netlify,
your own site). Open the address on a phone and choose **Add to Home screen** /
**Install app**.
