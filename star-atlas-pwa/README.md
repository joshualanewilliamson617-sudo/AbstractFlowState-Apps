# Star Atlas — installable app

A star atlas that opens pointing at whatever patch of sky your phone is aimed at.
One HTML file, no build step, no server. It runs offline once installed.

---

## Put it online (about ten minutes)

The app needs **HTTPS**. Motion sensors, geolocation and service workers all refuse
to run over plain `http://`. That rules out opening `index.html` from your hard
drive — sensors will be dead. Any of these hosts give you HTTPS free:

### Cloudflare Pages — easiest, no account juggling
1. Go to `dash.cloudflare.com` → **Workers & Pages** → **Create** → **Pages** →
   **Upload assets**.
2. Drag this whole folder in.
3. Deploy. You get `your-project.pages.dev`. Done.

### Netlify — same idea
1. `app.netlify.com/drop`
2. Drag the folder onto the page.
3. It deploys immediately and gives you a URL.

### GitHub Pages — if you want version history
1. New repo, upload the folder contents to the root.
2. Settings → Pages → Source: `main`, folder `/root`. Save.
3. Live at `yourname.github.io/reponame` in a minute or two.

## Install it on a phone

- **Android/Chrome** — open the URL, then menu → *Add to Home screen* (or take
  the install prompt Chrome offers on its own). It launches full-screen with no
  browser bars.
- **iPhone/Safari** — open the URL, Share button → *Add to Home Screen*.
  It must be Safari; Chrome on iOS cannot install web apps.

First launch asks for motion and location. Both are needed for sky-pointing.
On iPhone the motion prompt only appears if you tap the button — that is an
Apple rule, not a bug.

---

## The shared gallery

Out of the box, publishing a constellation saves it to that device only.
To make the gallery genuinely shared between users, connect Supabase (free tier):

1. Create a project at `supabase.com`.
2. SQL Editor, run:

```sql
create table constellations (
  id bigint generated always as identity primary key,
  name text not null,
  author text not null,
  segs jsonb not null,
  created_at timestamptz default now()
);

alter table constellations enable row level security;

create policy "anyone can read"  on constellations for select to anon using (true);
create policy "anyone can post"  on constellations for insert to anon with check (true);
```

3. Settings → API. Copy the **Project URL** and the **anon public** key.
4. Open `index.html`, find this near the top of the script (search for `CONFIG`):

```js
const CONFIG = { supabaseUrl:'', supabaseKey:'' };
```

Fill both in. Redeploy. The gallery is now shared.

The anon key is safe to ship in the browser — that is what it is for. But those
policies let anyone post, so add rate limiting or moderation before you promote
the app anywhere busy.

---

## Compass upkeep

The magnetic model bundled here is **WMM2020**, whose nominal validity ran to
2025. It is being extrapolated, which costs perhaps a few tenths of a degree —
far less than a phone compass's own error, so it is not urgent. When you want
to refresh it, download the current `WMM.COF` from NOAA and replace the numbers
in `WMM_C` near the top of the script, along with `WMM_EPOCH`.

The in-app "Align on a bright object" tool overrides the model entirely, so a
stale model never leaves a user stuck.

## Making changes

Everything lives in `index.html`, including the star catalogue. Change the file
and redeploy — that is all.

The page is served **network first**, so an installed phone picks up a redeploy
the next time it is opened online. You no longer have to bump anything in
`sw.js`. (An earlier version of this app served the page cache first, which
meant a phone that had opened it once kept that copy forever and redeploying
appeared to do nothing. That is fixed.)

Only bump `CACHE` in `sw.js` if you change which *icons* ship, since those are
still cache-first.

---

## Turning it into a Play Store app

The PWA above is genuinely enough for most people. If you want a store listing:

```bash
npm install -g @bubblewrap/cli
bubblewrap init --manifest https://YOUR-URL/manifest.webmanifest
bubblewrap build
```

Bubblewrap wraps the live PWA in a thin Android shell and produces a signed
`.aab` for the Play Console ($25, one time). You need Java and the Android SDK
installed; Bubblewrap offers to fetch them on first run.

For iOS you need a Mac, Xcode, and $99/year. Capacitor (`npx cap add ios`) does
the wrapping. Not worth it until the Android version earns its keep.

---

## Where the data comes from — read before you sell this

None of the astronomy is invented, and none of it is mine to license:

| Data | Source | Licence |
|---|---|---|
| 10,109 stars to mag 6.6 | HYG Database v4.1 (Hipparcos, Yale BSC, Gliese) | CC BY-SA 4.0 |
| 1,378 figures, 20 sky cultures | Stellarium sky cultures | varies per culture — GPL-2.0+ or CC BY-SA |
| Milky Way contours, deep sky | d3-celestial | BSD-3-Clause |
| Sun, Moon, planets | Meeus, *Astronomical Algorithms*; JPL elements | algorithms, not copyrightable |
| Meteor radiants | IMO published values | facts, not copyrightable |
| Magnetic declination | World Magnetic Model 2020 (NOAA/BGS) | public domain |

Share-alike is the thing to understand. It generally lets you **sell** the app,
but requires attribution and requires derivative data to stay open. A free app
with a credits screen is comfortably inside that. A locked paid app is a
conversation with a lawyer. Read the individual licence files — the Stellarium
cultures differ from one another. I am not a lawyer and this is not advice.

Add a visible credits screen before you ship, whatever you decide.
