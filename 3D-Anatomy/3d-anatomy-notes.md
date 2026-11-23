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
- 2026-11-23: second 3-round request (skin layering, real eyes, face muscles, real nerves; tours fixed; Evolution eras change the body). See the last section.

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

## 2026-11-23 Second request, 3 rounds (commits fae8885, e8a8547, f5bb449)
Owner said keep exactly as is: the respiratory system, How a Muscle Moves, the stomach cut, the leg motion in the run, the top menus.

Round 1, the body (fae8885)
- Skin layering: the skin shell and the eyes draw on camera layer 1 after a depth clear (`drawScene`, `skinLayerOn`), so with the skin fully on nothing under it can show through. Skin peel now fades the skin out and the muscles in (`st.skinOwn`); `uPeel` is kept at 99. Depth nudge in `tissue()` (`uBias`): muscle 4 mm, vessels and nerves 2 mm toward the eye, so the stack order is right without changing any sizes.
- Eyes: wet white with a soft shine, coloured iris, dark pupil (`eyeMaterial`).
- Height and weight: sliders start at the average, fit build (male 172 cm / 154 lb, female 167 cm / 137 lb); `resetBuild()` at boot and on back-navigation; range inputs have `autocomplete=off`.
- Face and scalp muscles (`FACE_M`, `headMap`, `faceMuscles`): 18 thin sheets laid on the head skin, from frontalis to the scalp's epicranial aponeurosis. Facts in `MFACTS`, searchable.
- Nerves (`drawnNerves`): a branching tree (brain, cord, plexuses, named nerves) that thins toward fingers and toes, following each body's own bone centres.

Round 2, tours (e8a8547)
- Every tour and guided lesson shows only its own system and puts the view back afterwards (`saveView`/`restoreView`, `VIEW`). Changing system or body by hand drops the saved view.
- Camera flies to each part (`focusOn`): the right-hand one of a pair, from the side it faces.
- The muscular tour stalled because hundreds of muscles glowed one by one and her muscles were built at the switch. Now the glow goes through shared materials (`st.selPools`, at most 60 single parts) and her body is built during the male half.
- Skin tour: cut-out block (`SKIN_BLOCK_STEPS`, `buildSkinBlock`, `skinBlock`) after the Skin stop: epidermis, dermis (hair roots, sweat glands, nerve endings, capillaries), fat layer, muscle with fascia.
- One heartbeat: the Coronary vessels group no longer catches brain, leg and gut arteries with "marginal" or "descending branch" in their names.
- Fight or flight: arm bones turn as solid pieces (`rigBones`, `aRig` attribute: 1 trunk/leg, 2 upper arm, 3 forearm and hand); legs unchanged. Gold markers on the adrenal glands (`runAdr`) and a close-up at that stop. Fixed: since Round 1 the lesson showed only the eyes (skin-full rule now skips moving lessons).
- Nerve signal: starts at the right index fingertip and follows the drawn median nerve (`NPATH`, `nerveUpPts`), with a faint skin outline and a touch ring.
- Hearing: the ear starts at real size in place on the head, then moves out (×3) and its parts pull apart (`o.place`, `o.expl`); back together for the nerve stops.
- How bones heal: the femur snaps (two clipped copies, flash, chips, the lower piece shifts) and is set straight from stop 2.

Round 3, Evolution (f5bb449)
- Before: every era showed the same modern body with one part lit, so nothing changed (proof sheet r3-before). Now `EVFX.era0…era13` + `evoFx(key)` change the body for each stop and undo it when the stop closes: fish fin, jaw bones into the ear, overlapping eye views, opening under the skull, Lucy's height (110 cm), big toe into line, longer thumb and stone tool, Homo erectus grows and runs, colon shrinks, brain grows (600→1,350 cm³), long low erectus skull, voice box drops, ancestral tail, milk digestion.
- The Evolution tab's "See it on the body" plays the same effect (`info.efx`).
- Fixed: the Hand bones group caught little-toe bones. Early primates now shows the eyes; Stone tools shows the hand bones.

Unfinished / ideas
- The body has no separate voice-box cartilages; era 11 draws one on top of the windpipe.
- The skin block's layers are drawn thicker than true scale (said in the narration).
- Evolution effects could get a short spoken line of their own; the fish fin could swing into the arm shape.
- Labels still don't follow bent limbs during moving lessons.

## 2026-11-23 Third request: menus, Gut cards, vitals, tour card, real Evolution shapes (one commit)
Owner was away and said: don't ask, choose, list choices at the end. Phone smoothness first.

What changed
- Menus: the option you're on gets a tinted outline and a ✓ (seg buttons, tabs, species list, chips, running tour, current lesson). The top buttons show the current pick: `sectionBtn()` ("Evolution: Chimpanzee", "Section: Body · Male", tab title) and `learnBtn()` ("Tour: …", "Lesson: …", "Quiz: …"). `menuTick` refreshes them every 400 ms.
- Section menu has a new "Evolution · body shape" picker (`#spList`, `SP_MENU`, `setSpecies`): Modern human, Chimpanzee, Lucy, Homo habilis, Homo erectus, Neanderthal, Early primate. Tapping one reshapes the body and shows a card. Tours, lessons and quizzes reset it to Modern human (`spReset`).
- Body reshaping runs on the graphics chip (`MORPH_GLSL`, `tqMorph`, uniforms `uMorph uMo0..3 uML`), with a matching JS copy (`morphPt`) for eyes, overlays and camera. Per species (`SPECIES`): arm and leg length, braincase height/length/width, face forward, jaw, brow ridge, chin, head tilt, pelvis width/height (bones only, `uBone`), crouch, forward lean, overall size. `moTo/moTick` animate; `moCull` turns off view culling while reshaped.
- Evolution stops 2–12 now use real shapes (`evShape`): early primate, chimp, Lucy (110 cm, both bodies), habilis, erectus (grows from Lucy and runs), brain stop blends early erectus skull → ours, sapiens stop blends ours ↔ erectus, voice stop blends ape face ↔ ours, tail stop goes tailed ancestor → human. Stops 0, 1, 13 (fish fin, ear bones, milk) are unchanged. Proof: before/after sheets made in the session scratchpad (compare1/compare2/species).
- Gut microbiome tab: every row (where they live, jobs, phyla, history) is tappable; a card sticks to the bottom with "What it is" / "What it does" and "See it on the body". The tapped row is highlighted. On the body, tapping stomach, small or large intestine adds a "Gut microbes here" line (pack `partNote`, `packNotes`).
- Vitals: "–" button shrinks it to a beating heart (beat follows heart rate); remembered in localStorage `ca-vitals`. Hidden automatically during spoken tours and the systems tour (`stage.touring`), not during lessons (Fight or flight uses it).
- Tour and lesson cards: ▾ collapse button (`#stMin`, `#lMin`) shrinks to a one-line pill; tap it to open again. The camera is shifted up by the card's height (`camera.setViewOffset`, `VOFF`, `coverNow`, ResizeObserver) so the card never covers the part or the skin block (checked: skin block centre 76 px above the card top at phone size).

Unfinished / ideas
- Tapping the body while reshaped hits the un-reshaped shape; name labels are hidden while reshaped.
- Neanderthal is in the picker only (no Evolution stop of its own).
- Headless Edge won't go below ~490 px wide; phone checks were done in a 390×844 frame.
