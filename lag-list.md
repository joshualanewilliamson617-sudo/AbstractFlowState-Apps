# Phone lag list, worst to least

Made on 2026-11-22 by reading the code of all 54 apps in this folder. Not included: the home page (`index.html`) and the old `root-field-colony` backup copies.
This order comes from reading the code. The apps weren't tested on a phone, so treat the middle of the list as a rough order.
Nothing has been changed yet.

## Heaviest: likely to lag on most phones

1. **3D-Anatomy**: it has 55 MB of body-model files, about 9–11 MB for each body system, and the phone has to download and unpack each one before it can show anything.
2. **Earth-Scope**: the page is 9 MB because four large photos of Earth are built into it, and on phones it redraws a globe made of about 200,000 points every frame, even when nothing moves.
3. **interference-lab**: it works out a 162,000-point wave on the phone's main processor twice every frame, then repaints the whole picture.
4. **root-field-colony**: it runs the whole ant-colony economy every frame (and catches up in big chunks after you leave it) on top of drawing a 3D scene.
5. **Dead-Block-AR**: on phones it still draws 3D with soft shadows, several lights and 900 raindrops at once.
6. **World-Frontline-Live-Theater**: its 3D mode always draws soft shadows, which are one of the most expensive effects for a phone's graphics chip.
7. **star-atlas**: "live" mode is on from the start, so it redraws all 10,108 stars, the constellation lines and the Milky Way 60 times a second, even though the sky barely moves.
8. **slipstream**: it runs an air-flow simulation several times every frame, and that's heavy maths for a phone.

## Medium: may stutter on older or cheaper phones

9. **Longbow-Sortie**: 3D with shadows and glow effects every frame. It does lower its quality on touch screens, which helps.
10. **Spectre-Gunship**: it draws the scene, then copies the whole screen image into the graphics chip every frame for its effects. It slows itself down automatically when the phone struggles.
11. **Soul-System**: a 3D solar system with detailed planets, and it moves all the on-screen name labels every frame.
12. **Radar-Green**: it unpacks almost 1 MB of map data at start-up, then redraws the radar sweep and every flight with glowing edges every frame.
13. **Lattice-Cipher-Field** and **Lattice-Cipher-Field-and-Bit-Tape**: pages of 0.7–1 MB with a 3D library and a 50-digit maths library to load before the field appears.
14. **Draft-Line-Racer**: it runs the car physics up to 8 times per frame, then redraws the whole track.
15. **element-lab**: it resets its drawing area every single frame, which makes the phone throw it away and rebuild it, while a 3D view also runs.
16. **numberfield-volume**: every particle gets moved through the field every frame, nonstop.
17. **star-lens-seek**: it redraws the sky with glow effects every frame and keeps slowly panning on its own.
18. **Burn-Out** and **burn-out-pwa**: glow effects on the canvas every frame, and glow is slow on phones.
19. **infinity-explorer**: rebuilding its shapes while animating is heavy, but it has a built-in brake so the screen stays responsive.
20. **Photo-bench**: it has 429 KB of pictures built into the page, so it opens slowly. It only works hard while a slideshow plays.
21. **cymatics-plate**: it reads back every pixel of the plate to make its streak effect, and that's slow on phones.
22. **CP3**: a big 3D editor (389 KB) that's slow to open, but it only redraws when something changes.
23. **Core-83**: a 1 MB page that rebuilds lots of on-screen text as you use it, so it's slow to open and switch views. Its background animations only run while its activity panel is open.
24. **fighter-jet-cockpit**: 3D plus several 2D gauges redrawn every frame, though it keeps the screen sharpness capped.
25. **Geometry-Battle**: it redraws the battle every frame, but it watches its own speed and adjusts.

## Light: should run smoothly on most phones

26. **dna-rna-explorer**: a small 3D model that keeps redrawing even when it isn't spinning.
27. **entangle3d**: a small simulation and drawing every frame.
28. **chamber**: listens to the microphone and redraws the bars every frame while it's on.
29. **math-roadmap**: redraws the current picture every frame even when paused.
30. **Sound-Levels-1-and-2**: redraws the sound wave every frame, all the time.
31. **Stone-Yard-Dock-Master**: a simple 2D game that redraws every frame.
32. **Apex-Prism** and **Byte-Pyramid**: small puzzles that keep redrawing even when you aren't touching them.
33. **teleport_projection**: a small shape that slowly spins and redraws every frame.
34. **oil-wheel** and **oil-wheel-pwa**: a small fluid effect plus rotating the canvas; light work.
35. **Shell-Lock**: a small 3D object, so light work.
36. **Water-Line**: redraws its water wave nonstop, but the drawing is simple.
37. **Lattice-Cipher-Field-Lite**: the flat 2D version of the field and much lighter than the 3D ones.
38. **Analyzer**: it only draws the rows you can see, so it stays fast unless you load a huge file.
39. **paint-lapse**: no animation, but screen recording uses a lot of power on its own, so recording is the heavy part.
40. **number-field** and **register**: they only animate while you play a tour or a sound.
41. **sos-beacon**: it only flips the background colour for the strobe, which is almost no work.
42. **Gift-Box-Unblocker** and **Tumble-House**: they only animate during a spin or a dice roll.
43. **Vextrix-Nexus**, **Exact-Ledger**, **Local-File-Organizer**, **Idea-Verdict**, **Flower-Field** and **bit-tape**: no constant animation, so they should never lag.

## Common causes in this folder

- **Redrawing every frame even when nothing changes.** Fix: only redraw when something moves or changes.
- **Heavy 3D effects, especially soft shadows.** Fix: turn shadows off or lower them on phones.
- **Big files to load** (built-in photos, large model or map data). Fix: shrink them, or load them only when needed.

## Next step

Start fixing from #1 (3D-Anatomy), one app at a time, and ask before each change.
Follow the standing app rules: keep each app a single HTML file, never remove features, and make sure it works with both phone and desktop controls.
