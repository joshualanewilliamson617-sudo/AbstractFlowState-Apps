# 3D Anatomy: working notes

Notes for whoever works on this app next (mostly Claude). Read this first, then update it at the end of every session.

## Standing rules from the owner
- One file: `index.html`. Body models live in `assets/` (male/female manifests plus packed chunks per system).
- Keep every existing feature. Every change must work with touch (phone) and with mouse and keyboard (desktop).
- Smooth running on the owner's phone comes first. If a change could lag, find a lighter way or ask.
- Work in rounds. After each round: test only what changed, commit with a clear name, push, and give a short phone checklist. If a round breaks something, roll back to the last working commit.
- Plain-words explanations. Ask one question at a time.

## How it's built (quick map)
- three.js r128, OrbitControls. Every body part is its own mesh inside `bodyRoot` (a group that the height and weight sliders stretch).
- `tissue()` adds the custom shader (fibres, flow, rim glow). `poolMat()` shares one material between parts that look the same, which keeps the draw cost down.
- The female body is a MakeHuman skin. Her organs and the bones and muscles borrowed from the male are carried into it by a warp field. Hands get an extra fit (`handFrames`, `handFix`).
- `apply()` decides what is visible. `refresh()` loads what the current systems need.
- Tours: `LESSONS` (parts and stops, `S_()` steps). Add-on packs: `AnatomyPacks.register()`. Evolution and Gut microbiome are built-in packs, stored as inert `<script type="text/x-anatomy-pack">` blocks that run only when their tab opens.
- Test harness (scratchpad, recreated each session if needed): local PowerShell server on port 8765, an Edge `--app` window, a test hook that POSTs snapshots and logs.

## History
- 2026-11-22: phone lag fix; top-to-bottom layout; info card under the body.
- 2026-11-22: round 1 (one skin layer, peel shows muscles, female hands, cut undo, shared materials). Round 2 (mix systems, tap-to-turn mode, tours in parts plus 5 new tours). Round 3 (height and weight, Evolution and Gut tabs, add-on packs).
- 2026-11-23: new 3-round request started (eyes, skin, desktop controls, menu layout; spoken system tours; moving tours). See below.

## Unfinished / ideas
- The body files have no inner ear; since Round 3 of 2026-11-23 the hearing lesson uses a built ear model (`buildEar`).
- An earlier request (2026-11-22, second list) was replaced by the 2026-11-23 one: layered-view lag, bigger tap areas, starting builds, Evolution reshaping per era with side-by-side models, a gut tab with coronal cut. Not done; ask the owner before picking these up.

## 2026-11-23 Round 1 (done, commit "3D-Anatomy: eyes, solid skin peel, mouse controls, menu layout")
- Layout: sticky top bar with Section (Body/Evolution/Gut + Male/Female), Systems (Single/Mixed/All, faint-behind = old Layered, cycler, colour chips, structure groups, search), Learn (Tours/Guided lessons/Quizzes), three-dot (Profile scores, Settings: theme, labels, flow, body state, time, add-on packs, about and credits). Sliders band under the body; only info cards below.
- Themes: `html[data-theme]` light/dim/dark, saved as `ca-theme`; the stage keeps dark overlay tokens.
- Eyes: `buildEyes()` modelled eyeballs (one sphere each, iris/pupil shader). Her MakeHuman eye surfaces are no longer built.
- Skin: outline off by default in other systems (`st.skin=0`). Skin system peel = solid skin cut from the top down (`uPeel`, `peelY()`), and muscle below the cut is not drawn (`uPeelOn==2` for muscle materials) so it can't poke through.
- Mouse: drag turns at once, wheel scrolls the page, + and − buttons/keys zoom (`zoomBy`). Touch keeps tap-to-turn and pinch.
- Fixed: shader positions are body-local (`uRootInv`) and motion centres use `boxL()`, so heartbeat/breathing and face details stay right after height/weight changes.
- Test harness note: if the Edge test window stalls, delete the `edgewin-run` profile folder in the scratchpad.

## 2026-11-23 Round 2 (done, commit "3D-Anatomy: spoken tours for every system, Evolution and Gut")
- Learn › Tours lists 13 spoken tours (11 systems, Evolution, Gut microbiome), each Short and Long with its length worked out first (`renderTourList`, estimate ≈ 2.6 words/s, ×2 for both bodies).
- Engine (`TR`, `startSysTour`, `showTourStep`, `endPhase`, `finishTour`): male body first, then female; stops at the end unless Loop. Next/Back are ignored while the body switches (`TR.busy`).
- Short = intro + each structure group. Long = also every named part (left/right merged by `partKey`, ordered head to toe).
- Voice: `speechSynthesis`, one sentence per utterance with a watchdog timer; muted/no voice = timed. Mute, speed (0.75–2×) and Loop are saved in `ca-voice`.
- During a tour: Single view, skin outline off, peel reset, muscle layers reset, cut off, menus/tabs closed. Changing system or body by hand ends the tour.
- Quiz after each tour: systems use the find-it quiz (groups after Short, named parts after Long); Evolution and Gut use 5 multiple-choice questions saved as `ca-best-tour-<id>`. Profile lists both kinds.
- Pack data (ERAS, WHERE, JOBS, …) now sits outside `build()` so the packs can also register `tour` and `quiz`. `ensurePack(id)` runs a pack's code without opening its tab.

## 2026-11-23 Round 3 (done, commit "3D-Anatomy: moving lessons, spoken lessons, fact check")
- Moving lessons (`FX`, `fxStart`/`fxStep`/`fxTick`/`fxEnd`): a lesson with `fx:'…'` builds its motion when it opens and removes all of it when it closes. Kinds: `knee`, `run`, `nerve`, `heart`, `meal`, `breath`, `blood`, `ear`, `heal`.
- Limb bending is in the tissue vertex shader (`POSE_GLSL`, `tqPose`): hip/knee/shoulder/elbow turn in the front-to-back plane, with weights that fade in over a few cm. Joint centres per body in `POSE` (measured from each body's bones). `U.uPoseOn=0` skips it. `U.uRoot` = body matrix (kept in `applyBuild`); `setBob()` moves the whole body up and down while running.
- Paths (blood, nerve, airway, ear, brain points) are hand-placed per body in `PATHS`, from each body's own vessel, nerve and airway centres. `mkPath()` samples them by distance.
- `st.fxOnly(p)`: lets a lesson show only some parts of the systems on screen (heart only for One heartbeat; skeleton + heart + lungs + adrenals + liver + eyeballs for Fight or flight, which mixes those systems).
- Meal and Breath use a front-to-back cut through the middle of each stop's organs (`fxCut`); removed at the end.
- Ear model (`buildEar`/`earTick`): enlarged pinna, canal, eardrum, 3 ossicles, cochlea spiral + semicircular canals, nerve tube into the real brainstem, signal dots to both hearing cortices.
- Healing femur (`buildCallus`/`healTick`): overlays at the middle of the right femur (centre and radius taken from the bone's own surface): crack, clot, soft callus, hard callus, remodelling.
- Caption on the stage (`#fxCap`) says what is happening now; stops with `cap:` keep their own caption (running bone count in Tour of the skeleton, times in How bones heal).
- Guided lessons are now spoken too (`lessonSay`), sharing mute/speed with the tours; Auto-play waits for the voice to finish.
- Fixed: the male brain's ventricles (stored with the cardiovascular files) were in the Heart group, so they pulsed with the heartbeat and pulled the heart centre up into the head. The Heart group now leaves them out.
- How a muscle moves now centres on the knee; the old elbow, shoulder and blood-supply stops were kept as a last part, "Another pair: the elbow".

## 2026-11-23 Fact check (all 3 rounds)
- Checked every number in the new and rewritten text: bone counts (22 skull + 6 ear + 1 hyoid; 26 spine; 25 rib cage, adding up to 80 axial; 126 appendicular, total 206), cochlea 2.5 turns / ~3.5 cm, ~3,500 inner + ~12,000 outer hair cells, ~30,000 cochlear nerve fibres, eardrum ~9 mm, 17:1 area ratio / ~20× pressure gain, ear canal ~2.5 cm, nerve speed ~60 m/s, reaction ~0.2 s, fracture timeline, femur union 3–6 months, stomach pH 1.5–3.5, 480 million alveoli, 50–75 m², stroke volume ~70 mL, pulse wave ~5 m/s, circulation lap ~1 min.
- Corrected while checking: motor nerve speed (was 80 m/s, now ~60 m/s like the sensory nerve); femur healing time (now 3–6 months).

## Ideas for later
- Ask the owner whether moving lessons should also keep the body state (Fight or flight leaves the vitals on Exercise, as it did before).
- Breathing arrows could split into smaller bronchi; the blood drop could take a second lap through the arm or brain.
- The ear model is built from simple shapes; a more detailed pinna would look better from close up.
- Labels don't follow bent limbs while a moving lesson plays (they use the resting pose).
