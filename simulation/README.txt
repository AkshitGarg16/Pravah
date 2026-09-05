================================================================================
 PRAVAH -- AI Traffic Digital Twin (SIH 2026, Problem Statement 1)
 SUMO Simulation Layer -- README
================================================================================

Last updated: 2026-08-28
Author of this pass: simulation engineering (SUMO digital twin only -- the
YOLO/vehicle-detection model and the RL controller are owned by other
teammates; see "WHAT THIS IS NOT" below).


--------------------------------------------------------------------------------
1. WHAT THIS IS
--------------------------------------------------------------------------------

A working, TraCI-controllable SUMO simulation of the real road network described
by the project's submitted TomTom-style traffic dataset
(data/pravah_900to1000_balanced.csv). It is a "digital twin" in the literal
sense: the road geometry, street names, speed limits and road classes in the
simulation are built directly from the real CSV, not a synthetic placeholder
map. This is the layer that a traffic-signal controller (Max-Pressure, then a
GNN-RL agent) will eventually run against, and the layer that live/simulated
vehicle-detection data (YOLO) will eventually feed into.

Current state: network builds cleanly, demand generates cleanly, a full
1-hour TraCI simulation runs start-to-finish with no errors, using SUMO's
default static traffic-light timing (no active control logic yet).


--------------------------------------------------------------------------------
2. WHAT THIS IS NOT (scope boundary)
--------------------------------------------------------------------------------

This pass does NOT include:
  - Any vehicle-detection / computer-vision / YOLO code.
  - Any RL agent, reward function, or trained policy.
  - Any traffic-signal control logic beyond SUMO's own default static timing.
  - The CUSUM/anomaly-detection pipeline sketched in segment_data_mapper.py
    (left untouched at the repo root, deferred to a later pass).
  - A GUI/visual renderer (this SUMO build is headless-only -- see section 6).

See PLAN section at the end of this file (and the accompanying chat message)
for what has to change here before those pieces can be wired in.


--------------------------------------------------------------------------------
3. THE REAL DATA THIS WAS BUILT FROM
--------------------------------------------------------------------------------

  data/pravah_900to1000_balanced.csv
    - 1,495 rows = 299 unique road segments x 5 weekday rows each (Mon-Fri,
      09:00-10:00 window). Each segment's geometry/speed-limit/street-name is
      identical across its 5 rows, so the network builder dedupes by
      segment_id and keeps one static record per segment.
    - Real columns used: segment_id, street_name, frc (TomTom Functional Road
      Class, 1=arterial..4=residential), speed_limit, geometry_wkt (a real
      lon/lat LINESTRING per segment -- East Delhi: Vikas Marg, Geeta Colony
      Road, NH44, Mahatma Gandhi Marg, Indra Prastha Road, etc.).
    - Checked before building anything: all 299 segments share endpoints with
      at least one other segment -- they form a single connected road network
      with 81 real-world junctions. That is why the full 299-segment network
      was built rather than a cut-down subset: it was already one graph.


--------------------------------------------------------------------------------
4. HOW THE NETWORK IS BUILT (CSV -> runnable SUMO network)
--------------------------------------------------------------------------------

  CSV (real geometry)
     |
     v  src/pravah/sumo_twin/network_builder.py
  synthetic OSM XML (sumo/network/pravah.net.osm.xml)
     |
     v  netconvert  (scripts/build_sumo_network.py)
  SUMO network (sumo/network/pravah.net.xml)

  Why go via a synthetic OSM file instead of writing .net.xml directly:
  netconvert's OSM importer already does geographic projection, lane-count
  inference from road-class tags, and one-way handling correctly. Hand-writing
  .net.xml plain-XML edges/nodes would just re-implement that importer, with
  more bugs. So network_builder.py's job is only: CSV -> valid OSM XML.

  Key steps inside network_builder.py:
    - load_segments(): dedupes 1,495 CSV rows down to 299 unique segments.
    - Endpoint coordinates are rounded to 5 decimal places (~1m precision) so
      that two segments meeting at the same real junction get the SAME OSM
      <node>, instead of two separate, disconnected nodes. This is what turns
      299 independent line segments back into one connected road graph.
    - frc -> OSM `highway` tag mapping: 1->primary, 2->secondary, 3->tertiary,
      4->residential. (Documented assumption -- easy to revisit.)
    - Each segment is written as one-way (`oneway=yes`), because TomTom
      segments are directional flow measurements -- a real two-way street
      already appears as two separate opposing segments in the CSV, so this
      is a correct reading of the data, not an import artifact.
    - IMPORTANT ORDERING RULE: all <node> elements must be written to the XML
      file before any <way> element that references them (see BUGS FIXED,
      #1). The code does two clean passes -- collect every node first, then
      emit nodes, then emit ways -- specifically to satisfy this.

  netconvert flags used (scripts/build_sumo_network.py) and why:
    --geometry.remove     merges consecutive same-attribute edges through
                           plain (non-junction) points into one longer edge.
                           This is why the final network has 132 edges, not
                           299 -- expected, standard behavior (same thing
                           osmWebWizard does on real OSM imports), not a bug.
    --roundabouts.guess    detects roundabout junctions from geometry.
    --junctions.join       clusters nearby OSM nodes that represent one real
                           physical intersection into a single SUMO junction.
    --tls.guess            infers traffic-light placement from junction
                           geometry itself. (NOT --tls.guess-signals -- see
                           BUGS FIXED, #2 -- our synthetic OSM has no real
                           traffic_signals tags for that flag to reinterpret.)
    --tls.default-type static   generated lights use fixed-time phase plans.

  Result (verified, current build):
    132 edges, 99 real junctions (76 priority-controlled, 3 traffic-light-
    controlled, 20 dead-ends at the network's outer fringe), plus 9 internal
    junction shapes SUMO adds automatically at intersections.


--------------------------------------------------------------------------------
5. HOW TRAFFIC DEMAND IS GENERATED (placeholder, not real OD data)
--------------------------------------------------------------------------------

  The CSV tells us how fast traffic moved on each segment -- it has no
  origin/destination trip data, so there is no way to derive "real" vehicle
  routes from it directly. For this pass, demand comes from SUMO's bundled
  randomTrips.py tool (src/pravah/sumo_twin/demand.py,
  scripts/generate_demand.py):

    - Random origin/destination edges are sampled and routed with duarouter.
    - --speed-exponent 2 biases selection toward higher-speed/arterial edges,
      so traffic doesn't spread evenly onto tiny residential edges (which
      would look nothing like real urban traffic).
    - --fringe-factor 5 biases toward through-traffic entering/leaving at the
      network's outer boundary, which is more realistic for an arterial
      corridor than lots of very short internal trips.
    - --validate makes randomTrips.py call duarouter internally to reject any
      generated origin/destination pair that isn't actually reachable.
    - --threads 1 (see BUGS FIXED, #3) -- required on this build.
    - Default run: 3,600 simulated seconds (matches the CSV's 09:00-10:00
      window), period=3.0s between departures -> 1,200 vehicles generated.

  This demand generator is explicitly a stand-in. It is the piece that real
  GNSS/detector-derived origin-destination demand (or YOLO-derived live counts
  -- see PLAN section) is meant to replace or calibrate later.


--------------------------------------------------------------------------------
6. HOW THE SIMULATION IS RUN
--------------------------------------------------------------------------------

  scripts/run_digital_twin.py connects over TraCI, steps the simulation to
  completion, and periodically samples traci.edge.getLastStepMeanSpeed() per
  edge -- deliberately the same quantity as the CSV's average_speed column,
  so a later pass can compare simulated vs. real speeds on a like-for-like
  basis. No control logic runs yet -- signals use whatever static timing
  netconvert generated.

  This build was HEADLESS ONLY when first written -- the machine had no FOX
  toolkit / X11 GUI dependencies configured, so there was no sumo-gui.
  NO LONGER TRUE: FOX was later installed (with the user's explicit sudo
  authorization) and SUMO rebuilt with GUI support -- see section 13's
  "SUMO GUI" addendum for the full story. sumo-gui exists and works now.

  Required environment (not persisted anywhere in the repo -- must be set in
  every shell before running any script here):

      export SUMO_HOME=/home/kreacher/sumo-src
      export PATH=$SUMO_HOME/bin:$PATH

  Reproduce the whole pipeline from scratch:

      export SUMO_HOME=/home/kreacher/sumo-src
      export PATH=$SUMO_HOME/bin:$PATH
      cd /home/kreacher/Pravah

      python3 scripts/build_sumo_network.py \
          --csv data/pravah_900to1000_balanced.csv \
          --out sumo/network/pravah.net.xml

      python3 scripts/generate_demand.py \
          --net sumo/network/pravah.net.xml \
          --out sumo/routes/pravah.rou.xml \
          --duration 3600

      python3 scripts/run_digital_twin.py \
          --sumocfg sumo/pravah.sumocfg \
          --duration 3600

      python3 -m pytest tests/ -v

  Last verified run output (2026-08-28):
      Edges with traffic: 132 / 132
      Mean speed across active edges: 15.78 m/s (~56.8 km/h)
      Min: 10.26 m/s, Max: 20.55 m/s
      1,200 vehicles inserted, all completed or teleported by end of run.
      One benign warning: "Teleporting vehicle '213'; waited too long
      (yield)" at t=943s -- SUMO's normal behavior for a single vehicle stuck
      too long at a junction under random demand + static signal timing; not
      a bug, did not require a code change.
      pytest: 4 passed.


--------------------------------------------------------------------------------
7. WHY SUMO WAS BUILT FROM SOURCE (not pip, not apt)
--------------------------------------------------------------------------------

  - `apt install sumo` would need sudo; not available on this machine.
  - `pip install eclipse-sumo` would have worked (user-space, no sudo) and
    was the original plan, but git-clone-from-source was explicitly preferred
    once confirmed feasible (build toolchain -- cmake, g++, xerces-c, proj --
    was already present, no extra sudo-gated dependency installs needed).
  - Source: git clone --branch v1_27_1 --depth 1
    https://github.com/eclipse-sumo/sumo.git into /home/kreacher/sumo-src
    (a sibling directory to this project, not inside it), built with
    `cmake -DCMAKE_BUILD_TYPE=Release -DSUMO_UTILS=OFF` + `cmake --build . -j16`.
  - No FOX toolkit / X11 dev packages were available at the time (would
    have needed sudo too), so this build initially had NO GUI (no
    sumo-gui) and NO parallel duarouter routing -- hence --threads 1
    everywhere duarouter gets invoked. UPDATE: FOX was later installed
    with the user's explicit sudo authorization and SUMO rebuilt with GUI
    support (see section 13's "SUMO GUI" addendum) -- sumo-gui works now.
    --threads 1 stays as-is regardless; nothing needed it changed.
  - Because of this, there is no requirements.txt / pip dependency in this
    repo for SUMO itself. Pravah's own Python code (network_builder.py,
    demand.py, run_digital_twin.py) uses only the standard library. traci,
    sumolib and randomTrips.py are provided by the sumo-src checkout's
    tools/ directory, located via $SUMO_HOME at run time -- not installed
    as a package at all.


--------------------------------------------------------------------------------
8. BUGS HIT AND FIXED DURING THIS BUILD
--------------------------------------------------------------------------------

  1. OSM node/way ordering (network_builder.py)
     Symptom: netconvert failed with "No nodes loaded. Quitting (on error)."
     Cause: the first version of build_osm_xml() wrote a <node> the first
     time it saw a new coordinate, interleaved with <way> elements, in a
     single pass over the segments. netconvert's OSM parser resolves
     <nd ref=...> only against nodes it has already seen earlier in the
     document -- exactly like real OSM XML files are structured -- so a way
     referencing a node written later in the file failed to resolve.
     Fix: two clean passes -- collect every unique node first, emit all
     <node> elements, then emit all <way> elements referencing them.

  2. Wrong traffic-light flag (build_sumo_network.py)
     Symptom: network built successfully but with 0 traffic lights.
     Cause: used --tls.guess-signals, which only converts junctions that
     already carry a real OSM `traffic_signals` tag into SUMO traffic lights.
     Our synthetic OSM data has no such tags (the CSV doesn't carry that
     information), so it found nothing.
     Fix: switched to --tls.guess, which infers likely signal placement from
     junction geometry itself (number of incoming roads, road classes) --
     produced 3 traffic lights. Root-caused via `netconvert --help`.

  3. randomTrips.py / duarouter parallel-routing crash (demand.py)
     Symptom: "Error: Parallel routing is only possible when compiled with
     Fox."
     Cause: randomTrips.py's --validate flag calls duarouter internally and
     defaults its thread count to os.cpu_count() // 2; this SUMO build has
     no Fox toolkit, so anything above 1 thread fails immediately.
     Fix: pinned --threads 1 explicitly in generate_routes().

  Note on something that looked like a bug but wasn't: the edge count drops
  from 299 (CSV segments) to 132 (final network) because of
  --geometry.remove merging consecutive same-attribute segments through
  plain points -- confirmed via `netconvert --help` as intended behavior,
  not data loss. All 299 original segment_ids are still recoverable from the
  merged edges' IDs (see PLAN section, open item 1) -- geometry.remove does
  not delete the original identifiers, it just doesn't expose all of them
  in a lookup table by default.


--------------------------------------------------------------------------------
9. PHASE A: THE TRACI CONTROL WRAPPER (src/pravah/sumo_twin/env.py)
--------------------------------------------------------------------------------

  Added after the initial digital-twin pass, as the interface Max-Pressure
  and later the GNN-RL controller are meant to run against. It replaces
  run_digital_twin.py's read-only speed sampling with three things a real
  controller needs and the original script didn't provide:

  1. OBSERVATION -- per traffic light, per controlled lane: queue length
     (getLastStepHaltingNumber), occupancy, waiting time, mean speed, plus
     the light's current green phase and how many green phases it has.
     Not just network-wide mean speed.

  2. SAFE ACTIONS -- a caller picks a green phase by slot index (0, 1, ...),
     never a raw SUMO phase index. If the requested green differs from the
     current one, the wrapper inserts the junction's real programmed
     yellow transition automatically and holds it for its full duration
     before switching -- jumping straight from one green to another is not
     offered, because it isn't a valid signal plan, not because of a
     simulation limitation. Verified against the real network's 3 traffic
     lights (each: 2 green phases, 5s yellow between them, discovered via
     traci.trafficlight.getAllProgramLogics(), not hardcoded).

  3. FAST EPISODIC RESET -- reset() uses traci.load() after the first
     traci.start(), instead of restarting the SUMO process every episode.
     Measured on this build: first reset (traci.start) ~1.0s, every reset
     after that (traci.load) ~0.02s -- roughly 50x faster, which matters
     once RL training means thousands of resets.

  No reward function lives here -- step() returns raw metrics (arrived
  count, teleports, total waiting time, mean edge speed) in an info dict so
  a controller can define its own reward. That choice belongs to whoever
  builds the controller.

  Also added: PravahEnv.inject_vehicle(edge_id) -- spawns one vehicle on a
  given edge right now, bypassing the pre-generated route file entirely.
  This is the hook a live (or synthetic-camera) detection feed will
  eventually call to make simulated demand track detected counts instead
  of only randomTrips.py's fixed .rou.xml -- no detection model exists yet
  to drive it, so today it's exercised by tests/a stand-in caller only.
  Route is deliberately a single edge (the vehicle travels to that edge's
  end and is removed there) -- extend to a multi-edge route via
  traci.simulation.findRoute() once real usage needs vehicles that
  continue past their injection point; inventing a destination that
  doesn't exist yet would be guessing, not integration work.

  Tests: tests/test_env.py -- 2 pure-logic unit tests (no SUMO needed) plus
  1 integration test that drives the real network end-to-end, including
  inject_vehicle() (auto-skips if $SUMO_HOME isn't set, rather than
  failing). All passing.

  Known bug hit and fixed while building this: the module originally did
  `import traci` unconditionally at the top of env.py. That meant the
  entire module -- including the pure _green_phase_indices() helper, which
  has nothing to do with SUMO -- failed to even import without $SUMO_HOME
  set, so pytest errored at collection instead of skipping just the one
  test that actually needs a live SUMO instance. Fixed by wrapping the
  traci/sumolib imports in try/except and only requiring them inside
  PravahEnv.__init__.


--------------------------------------------------------------------------------
10. SEGMENT-ID MAPPING (src/pravah/sumo_twin/segment_map.py)
--------------------------------------------------------------------------------

  Answers "which original CSV segment_id(s) does this final SUMO edge come
  from" -- needed for anything that has to relate a real-world location
  (a camera, a detection, a per-segment sim-vs-real speed check) to a
  simulation edge, since netconvert's --geometry.remove merges chains of
  original segments into fewer, longer edges (299 -> 132 in this network).

  network_builder.py now also writes a `ways` side file next to the
  synthetic OSM XML (<net-name>.ways.json: segment_id -> ordered node-id
  sequence). build_sumo_network.py automatically builds the mapping from
  it after every network build and writes
  sumo/network/pravah.net.segment_map.json (edge_id -> [segment_id, ...]).

  How it works: for each final edge, seed from the one original segment_id
  its own id is guaranteed to come from (stripping a trailing #0/#1/...,
  which netconvert appends when it has to *split* one original segment
  instead of merging several), then walk outward through neighbouring
  segments for as long as the shared point between them is NOT a real
  junction in netconvert's own output -- stopping there, whatever
  netconvert's reason was.

  Two real bugs hit while building this, both instructive:
    1. First version matched purely by each edge's start/end node
       (via --output.original-names) and took the shortest path between
       them. Wrong: at any point where 3+ segments meet (common near this
       network's roundabouts), several similarly-short paths can exist
       between the same two endpoints, and "shortest" has no reason to
       pick the *correct* one. Root-caused by finding a node with
       out-degree 3 that a "shortest path" search wandered through.
    2. Second version walked through any point with exactly one incoming
       and one outgoing connection (computed from the ways.json side
       file), stopping only at real branches. Also wrong, more subtly:
       netconvert can decline to merge through a plain 1-in/1-out point
       for reasons that connectivity alone can't see -- a placed traffic
       light, or a lane-count/speed mismatch between the two segments.
       That produced two different final edges that both claimed the same
       downstream segment. Fixed by using netconvert's own <junction>
       elements in the output net.xml as the authoritative stop condition
       instead of a self-computed degree -- if a point survived as a real
       junction in netconvert's output, stop there, full stop, regardless
       of what our own reconstruction thinks the degree should be.

  Current measured coverage: 256/299 original segments (85.6%) resolve to
  a final edge; 0 segments are ever claimed by more than one edge (the
  bug-2 failure mode, now fixed and covered by a regression test). The
  other 43 are short connectors fully absorbed into roundabout/complex-
  junction internal geometry -- there's no junction id for the eliminated
  node to recover in that case, on either version of the algorithm; this
  is a structural limit of reconstructing from the final network's own
  output, not a bug. tests/test_segment_map.py enforces a >=80% coverage
  floor as a regression guard, plus a hard "zero duplicates" check.


--------------------------------------------------------------------------------
11. RENDERING VIDEO CLIPS (src/pravah/sumo_twin/render.py) -- RETIRED
--------------------------------------------------------------------------------

  Added because a hackathon demo needs an actual visual, and this SUMO
  build has no GUI at all: confirmed no FOX toolkit anywhere on this
  machine (no fox.pc, no libFOX on disk), which is what sumo-gui needs,
  and installing it would need apt (sudo, unavailable) or a from-source
  FOX build of its own. Rather than chase that, this renders straight to
  an .mp4 via matplotlib + ffmpeg (both already present) -- no display
  server, no X session, no sumo-gui involved.

  RETIRED per explicit direction: the user wants to watch the simulation
  run live (with Gazebo, section 13), not a recorded clip -- "the current
  work of recording the headless .mp4 should be discarded". The module and
  scripts/render_video.py are left in place (still work, still tested) but
  get no further work. build_showcase.py's browser "Pravah Control Room"
  page (originally documented in section 12 below) is retired the same
  way and for the same reason -- a separate GUI already exists and isn't
  this codebase's job to build a competing one for.

  scripts/render_video.py:
      python scripts/render_video.py \
          --sumocfg sumo/pravah.sumocfg \
          --net sumo/network/pravah.net.xml \
          --out sumo/video/pravah_run.mp4 \
          --duration 3600

  record_trajectory() and render_video() are deliberately separate:
  recording needs a live TraCI connection ($SUMO_HOME set), rendering only
  needs the saved trajectory (--trajectory-out to keep it) and the
  .net.xml, so a clip can be re-styled without re-running the simulation.

  The canvas size is computed from the network's own bounding-box aspect
  ratio, not assumed square -- this corridor is much wider than it is
  tall, and an early square-canvas version left dead black bars above and
  below the network; fixed by sizing the figure to match the data instead
  of a fixed default.

  Verified: rendered and visually inspected a 150s test clip (frame
  extracted via ffmpeg) -- road network drawn in gray, vehicles as yellow
  dots, the 3 traffic lights as green/yellow/red dots reflecting live
  signal state, live time/vehicle-count overlay top-left. Full network
  width now fills the frame correctly.


--------------------------------------------------------------------------------
12. MAX-PRESSURE CONTROLLER + COMPARISON SHOWCASE
--------------------------------------------------------------------------------

  src/pravah/sumo_twin/controllers.py -- two controllers, same interface
  (reset(obs), decide_actions(obs) -> {tls_id: green_slot}), run through the
  identical PravahEnv.step() loop so a comparison is apples-to-apples:

    - FixedTimeController: the baseline. Reproduces the network's own
      fixed-time program (each green held for netconvert's programmed
      duration, alternating) -- NOT the same as calling env.step({})
      forever, which would leave every light stuck on its first green for
      the whole run, since PravahEnv only switches a phase when explicitly
      told to. That's a real distinction that mattered while building this.
    - MaxPressureController: at each control step, every TLS independently
      switches to whichever green phase has the highest "pressure" (queue
      on the lanes it would serve, minus queue on the lanes it would send
      that traffic onto) -- stateless, no fixed cycle.

  Building MaxPressureController surfaced a real bug in PravahEnv itself:
  step()'s "hold the current phase" logic set the phase's remaining
  duration to *exactly* how long the loop was about to step, which could
  let SUMO's own program clock auto-advance into yellow right at the last
  simulated second of that loop, before control ever returned. Harmless
  under FixedTimeController (which rarely switches, so this rarely
  triggered) but broke immediately under Max-Pressure, which can request a
  different phase on nearly every control step. Fixed by holding with a
  safety margin (duration set a few seconds longer than what's actually
  stepped) instead of an exact match -- every step re-arms it before the
  margin could matter.

  scripts/compare_control.py runs the same seed/demand through both
  controllers and writes their metrics + full vehicle trajectories to one
  JSON file. Measured result (1200s, seed 1, 10s control interval):

      metric                    fixed-time    max-pressure    change
      mean speed (m/s)              15.86           15.87     +0.1%
      final waiting time (s)        37.00            3.00    -91.9%
      vehicles arrived                319             319     +0.0%

  Throughput and speed barely move -- expected, this demand level isn't
  heavily congested -- but max-pressure cuts total network waiting time at
  signals by ~92% for the same traffic. That's the honest story: it's a
  signal-efficiency win, not a capacity win, which is exactly what
  max-pressure is supposed to do.

  scripts/build_showcase.py assembles that comparison data plus the
  network geometry into a single self-contained HTML page ("Pravah Control
  Room"): two synced, scrubbable canvas replays (fixed-time vs
  max-pressure) side by side, live speed/waiting readouts, summary stat
  cards, and overlaid time-series charts. Built to replace the earlier
  matplotlib .mp4 approach (section 11) for anything meant to *show* the
  controller's effect -- interactive and comparative, not a linear clip.
  The .mp4 pipeline in render.py is still there for a quick single-run
  visual, but the showcase page is the one built for making the case that
  the algorithm helped.


--------------------------------------------------------------------------------
13. GAZEBO SYNTHETIC-CAMERA BRIDGE (started -- foundation + live pose bridge done)
--------------------------------------------------------------------------------

  Why this exists: no real CCTV footage exists for training a vehicle-
  detection model, and the plan adds traffic lights that don't exist yet
  either -- so real camera data is a structural dead end, not just
  unavailable for now. Synthetic camera frames rendered from this twin are
  the only way to get any training data at all. Decided earlier (see
  chat): render via Gazebo (gz sim 8.13, already installed on this
  machine, GPU-proven), not CARLA (fresh ~20-30GB install against 24GB
  free disk, GPU requirements above this machine's 6GB VRAM).

  Before writing any of this, every risky unknown was verified against the
  real gz-sim 8.13 install rather than assumed from general Gazebo
  knowledge (SDF conventions and message/service shapes vary enough across
  Gazebo generations that guessing would likely have produced broken
  code):
    - Headless rendering: `gz sim -s -r --headless-rendering` starts
      cleanly, loads the ogre2 render engine, no GL/EGL errors -- despite
      this machine having no FOX/X11 setup for sumo-gui, gz-sim's own
      render stack is entirely separate and just works.
    - Camera sensors produce real, non-blank pixel data, retrievable from
      Python via gz.transport13 + gz.msgs10 (confirmed: subscribed to a
      camera topic, received actual varied RGB bytes, not zeros).
    - Entities can be moved live from outside the simulation via the
      /world/<name>/set_pose(_vector) service -- called it, the model's
      pose actually updated to the requested position. This is the core
      mechanism the whole bridge depends on.
    - Entities can be spawned/removed live via /world/<name>/create and
      /remove -- used for vehicles entering/leaving, same lifecycle TraCI
      already tracks.
    - gz-sim ships a native boundingbox-camera sensor
      (libgz-sensors8-boundingbox-camera) -- means auto-generated 2D/3D
      bounding-box ground truth can come from a first-party sensor plugin
      instead of hand-rolled projection math. Not wired up yet.

  Built and verified so far:

    - src/pravah/gazebo_bridge/world_builder.py: SUMO .net.xml -> a
      complete SDF world. Every road-network edge polyline segment becomes
      one flat box <visual> under a single static model (no <collision> at
      all -- Gazebo never needs to physically reason about roads here, it
      only renders them), plus a ground plane and a marker post at each
      real traffic-light position. withInternal=True on the net.xml read,
      same fix as render.py, so junctions render continuous rather than
      gapped. scripts/build_gazebo_world.py is the CLI.
      Current build: 1,522 road-segment visuals from the real network,
      loads into gz-sim with zero errors.

    - src/pravah/gazebo_bridge/bridge.py: GazeboBridge.sync(vehicles) --
      spawns a vehicle model the first step its id appears, pushes one
      batched /set_pose_vector call per sync for every currently-active
      vehicle, removes a vehicle's model the step after TraCI stops
      reporting it. Gazebo does no physics on these -- every model is
      static and moved purely by overwriting its pose, so Gazebo is
      strictly a renderer here, never a second source of vehicle motion.
      SUMO's compass-bearing vehicle angle is converted to standard
      math-convention yaw for orientation (documented assumption, not yet
      visually cross-checked -- see the function's docstring).

    - scripts/run_gazebo_twin.py: launches gz-sim itself, waits for it to
      come up, then steps SUMO exactly like run_digital_twin.py but calls
      bridge.sync() every step instead of only summarizing speeds.

  End-to-end verification (see chat transcript): ran the bridge against
  the real network and real demand. Vehicle "0"'s tracked position moved
  step over step (1927.9,992.0) -> (1802.3,949.3) -> (1634.5,913.3),
  matching its real SUMO trajectory. Gazebo's own live model list matched
  SUMO's vehicle count exactly at every check (7 vehicle models when SUMO
  reported 7 active vehicles). Spawning/despawning tracked correctly as
  vehicles entered and left.

  ITO-focused, live, watchable scene (added in the pivot -- see section 14
  for the full ITO demo story; this is the Gazebo-specific half of it):

    - world_builder.py gained center=(x,y)/radius scoping: build_world()
      only includes road segments and TLS markers within that radius, so
      the world can be "just ITO and its immediate approaches" (305 road-
      segment visuals) instead of the full network (1,522). Each TLS
      marker is now its own model (tls_<id>, one link, one visual named
      "visual") instead of several visuals sharing one "tls_markers"
      model -- needed for live per-marker recoloring (see below).
    - scripts/build_gazebo_world.py: --center-junction (e.g. cluster_2_72,
      the real ITO junction) + --radius. TLS marker positions are read
      fresh from --net every time (every traffic_light-type junction's own
      coordinate) rather than from a separately generated JSON file -- a
      real bug hit and fixed: an old network_geometry.json from before ITO
      had a forced signal (section 14) silently had no entry for it,
      so ITO's own marker was missing from a scoped world built against
      that stale file.
    - bridge.py: vehicles are now visually distinct by type. VEHICLE_SDF
      takes length/width/height/color per SUMO vType (car/twowheeler/bus/
      truck), matching demand.py's VTYPE_MIX dimensions -- the Gazebo box
      is the same size SUMO itself is modeling, not a separate guess.
    - bridge.py gained set_tls_colors(tls_states): publishes a live
      /world/<name>/material_color update per TLS marker every call.
      Confirmed live (see chat) that MaterialColor only takes effect
      against entity.type=VISUAL with the exact "model::link::visual"
      scoped name -- targeting the model itself is silently ignored, no
      error, no warning.
    - Two more real bugs hit and fixed while verifying set_tls_colors
      end-to-end against a real running TraCI/Gazebo pair:
        1. The first collapsed-color rule ("any green link anywhere in
           the state string") meant both of a junction's alternating real
           green phases collapsed to the identical color -- the marker
           would visibly change almost only during the brief 4s yellow
           transitions. Fixed by tracking one specific link's own
           character (state[0]) instead, giving a real
           red -> yellow -> green -> yellow -> red cycle.
        2. gz-transport pub/sub has no delivery acknowledgment, and this
           publisher's very first message can race the SceneBroadcaster
           subscriber's own discovery and get silently dropped. An
           earlier version skipped republishing a color that "hadn't
           changed" per its own local bookkeeping -- but that bookkeeping
           trusted a publish that never actually arrived, so the marker
           stayed on its initial color the entire run despite the real
           signal changing correctly the whole time. Fixed by always
           republishing every call (cheap, self-healing) instead of
           deduping client-side with no way to confirm delivery.
    - scripts/run_gazebo_twin.py: drops --headless-rendering by default
      (gz sim -r, GUI window visible -- --headless opts back into the old
      behavior) and gained --speed (default 1.0 = real-time) with an
      actual wall-clock pacing loop. Without it, TraCI steps as fast as
      the CPU allows -- confirmed live that this races far ahead of
      gz-sim's own rendering and message processing, so a diagnostic
      sampling colors over an unpaced run saw the marker "stuck" for
      dozens of simulated seconds at a time purely because the render
      pipeline hadn't caught up yet, not because sync/set_tls_colors were
      wrong. Re-ran the same check with per-step pacing added and the
      marker cycled red/yellow/green correctly in step with the real
      signal.
    - Also fixed: --tls.set (build_sumo_network.py, section 14) can force
      a merged-cluster junction the same way as any other, and
      generate_calibrated_routes's departSpeed (section 14) needed to be
      "max", not a literal computed number, to avoid a fatal SUMO error --
      both hit and fixed during this same end-to-end verification pass.

  Still not done: the native boundingbox-camera sensor for auto-generated
  detection labels, and any actual dataset export -- unchanged from
  before, still deliberately deferred pending real design decisions
  (camera placement/FOV, sampling interval, label format), and orthogonal
  to "watch it run live," which is what this pass was scoped to.

  VISUAL REALISM PASS (added after watching the first live run -- "just
  boxes travelling", not realistic):

    - Confirmed this machine has real internet access (checked directly --
      hadn't been assumed either way before). That unlocked Gazebo Fuel,
      the online library of ready-made, correctly-scaled 3D models.
    - bridge.py's vehicles are now real meshes, not boxes: car -> Fuel
      OpenRobotics/Hatchback, truck -> OpenRobotics/TruckDelivery, bus ->
      OpenRobotics/Bus, twowheeler -> athackst/bicycle (the closest
      available stand-in -- no real motorcycle/scooter model exists in
      Fuel's catalog, checked; a bicycle is not what actually dominates
      Indian two-wheeler traffic, worth knowing).
    - Orientation was checked empirically, not assumed: spawned each model
      at yaw=0 under a top-down camera with red/green markers at world
      +X/+Y as a ruler, then read the image. Hatchback and TruckDelivery
      already face +X at yaw=0, matching this codebase's convention
      throughout (sumo_angle_to_yaw) with zero correction. Bus's long axis
      sits along Y instead, needing +90 degrees -- now baked into
      VEHICLE_MODELS. This is why the check was done at all: guessing
      would have had a good chance of shipping cars driving sideways.
    - Two of the four downloaded models had genuine upstream packaging
      bugs, not something this codebase did wrong: the bicycle's .mtl
      looked for its texture (bike.png) in the same folder as itself,
      but Fuel had actually packaged it one directory away
      (materials/textures/); the bus's wheel material hard-referenced a
      *different* model's texture file (model://suv/materials/textures/
      wheels_01.png) that isn't included with the bus download at all.
      Fixed by copying the misplaced texture into the expected folder
      (bicycle) and downloading the SUV model purely to supply the
      missing wheel texture (bus) -- both are local Fuel-cache fixes; a
      fresh `gz fuel download` on a different machine will hit the same
      two bugs and need the same fix.
    - world_builder.py: attempted a real photographic asphalt texture
      (Fuel: OpenRobotics/Asphalt Plane) for the road surface and
      reverted it -- both the legacy Ogre material-script form and Ogre2's
      own native PBR form rendered the road as solid black in this
      gz-sim build, checked with scene ambient light and a sky added, and
      with the texture referenced by its Fuel URI and by a verified-
      correct local file path -- four combinations, all solid black, no
      error logged anywhere. The texture file itself was opened and
      inspected directly and is fine (an ordinary gray asphalt photo).
      This is a real, unresolved rendering-pipeline limitation in this
      build/config -- not investigated further past a reasonable amount
      of time, not silently worked around. Reverted to ROAD_COLOR, a
      deliberately dark, desaturated solid gray using the same plain
      ambient/diffuse mechanism that works everywhere else in this file
      (it's specifically applying an external script/PBR material to a
      *box primitive* that failed -- the same downloaded vehicle *meshes*
      render their own textures completely fine, confirmed visually).
      The ground plane's color was also changed, from neutral gray to a
      warm earthy tone, so it reads as ground next to the road rather
      than the same flat material.
    - Net effect, confirmed visually end-to-end against a real paced run:
      real, correctly-shaped, correctly-oriented bus and car models
      visible together at the actual ITO junction, on dark asphalt-toned
      roads against warm earthy ground, with the live-colored signal
      marker in frame -- a genuinely different picture from "boxes
      travelling," even though the road surface is a solid color rather
      than a working photographic texture.

  SUMO GUI (this SUMO build now has one -- it didn't for the rest of this
  document):

    The user gave explicit sudo authorization (with the password) to
    install libfox-1.6-dev, the FOX toolkit sumo-gui needs -- every
    earlier statement in this file that this build is headless-only /
    has no GUI describes a limitation that no longer holds. With FOX
    installed, the existing sumo-src/build/cmake-build tree was
    reconfigured (clearing the stale cached FOX_INCLUDE_DIR-NOTFOUND
    entries first -- CMake doesn't auto-retry a cached NOTFOUND, a plain
    re-run silently kept using it) and rebuilt; "GUI" now appears in
    CMake's own "Enabled features" line, and sumo-gui exists at
    $SUMO_HOME/bin/sumo-gui and was launched and verified running
    directly (not just that the binary exists).

    run_gazebo_twin.py gained --sumo-gui: not a second, separately-seeded
    sumo-gui process running its own independent copy of the simulation
    alongside Gazebo (which would show different vehicles, unsynced
    signals -- confusing, not a real "watch it run" experience), but the
    exact same TraCI connection driving both. Adds --start (so it begins
    the moment TraCI steps arrive instead of sitting on an unclicked play
    button) and --quit-on-end (closes itself when TraCI disconnects) to
    the sumo-gui invocation.

    --threads 1 (duarouter's parallel routing, demand.py) is unrelated to
    sumo-gui specifically -- still needs the Fox toolkit's own threading
    support wired through duarouter's build, not exercised or re-checked
    here, left as-is.


--------------------------------------------------------------------------------
14. THE ITO JUNCTION DEMO (tomtom_feed.py, calibrated demand, the 10s signal lock, graph_export.py)
--------------------------------------------------------------------------------

  Everything in this section exists to answer one request: build a demo of
  the real ITO junction (Delhi -- Mahatma Gandhi Marg x Indraprastha Marg,
  by the Secretariat) that runs on (linearly interpolated, per the user's
  current-round scope) TomTom-style data, lets that data drive vehicle
  speed/type/density, makes the traffic light simply controllable so an
  RL model can plug into the same interface later, and gives out every
  signal change in a defined format.

  Finding ITO in the existing network: `Mahatma Gandhi Marg` and
  `Indra Prasta Road` are real street names already in the CSV, and their
  segments converge on exactly one shared SUMO junction -- `cluster_2_72`,
  a 3-in/3-out junction, by far the highest-degree shared point between
  the two streets (checked directly, not assumed). No new OSM data was
  needed; the real ITO junction was already part of the network built
  earlier this session.

  src/pravah/sumo_twin/tomtom_feed.py: the CSV only has 5 discrete numbers
  per segment (one hourly average per weekday) -- there's no true sub-hour
  discreteness in this dataset to interpolate between. interpolate_
  segment_series() spreads those 5 weekday samples evenly across the
  simulated hour and linearly interpolates between them, so demand varies
  smoothly over the run instead of being one flat number for all 3600s --
  the same *shape* of fix (discrete anchors -> continuous stream) the
  team's real GNN work is meant to do on genuinely sub-minute updates, not
  a claim this reproduces real intra-hour dynamics. Documented as a
  stand-in explicitly, in the module's own docstring.

  demand.py's new generate_calibrated_routes(): replaces randomTrips.py's
  uniform random sampling with a per-edge insertion rate driven by that
  interpolated data (piecewise-constant over 300s chunks -- SUMO's <flow>
  only supports a constant rate over its own window, so this is the
  standard way to approximate a continuously-varying one). Covers the
  whole network, not just ITO's approaches (confirmed scope decision).
  Also introduces VTYPE_MIX -- car/twowheeler/bus/truck with an
  approximate Indian-urban-arterial mix ratio (45/40/5/10), real SUMO
  vClasses so each type actually behaves differently, not just looks
  different.

  Two real bugs hit and fixed while building this:
    1. First version picked each edge's demand destination uniformly at
       random from the network's fringe edges. Over half the generated
       vehicles got rejected by duarouter ("No connection between edge X
       and edge Y found") -- this network is mostly one-way, so most
       origin/fringe pairs simply aren't reachable. Fixed by parsing the
       net.xml's own <connection> elements (the exact edge-to-edge moves
       duarouter itself honors) into a graph and BFS-searching for a
       destination actually reachable from each origin -- vehicle count
       for the same target went from 507 to 1,708 (undershoot to a
       healthy, if not exact, overshoot -- see the module for why an
       exact match isn't the goal).
    2. departSpeed set to the literal interpolated speed caused a FATAL
       SUMO error ("too high for the departure edge") the first time a
       real run hit an edge where interpolation overshot what that edge
       actually allows -- unlike duarouter's routing failures, SUMO itself
       has no --ignore-errors leniency for this by default. Fixed by using
       departSpeed="max" (SUMO's own keyword, always valid, arguably more
       realistic anyway) -- the interpolated speed still does real work,
       it's what shapes vehsPerHour, just not the literal one-time
       departure speed.

  build_sumo_network.py gained --force-tls (default cluster_2_72): passes
  --tls.set to netconvert so ITO is guaranteed a controllable signal
  regardless of what --tls.guess's geometry heuristic would have picked --
  TomTom carries no signal data at all, so the simulation is the thing
  that originates one, per the user's own framing of the problem.

  env.py's end-of-phase lock (the confirmed reading of "the last 10
  seconds are non-variable"): a requested phase change arriving with
  lock_window (default 10) seconds or less left in the current green is
  deferred and only applied once that phase completes its full programmed
  run -- never cut short in its final stretch. Enforced in PravahEnv
  itself, the same way the mandatory yellow transition already was, so a
  future controller (RL or otherwise) can't bypass it by requesting an
  unsafe change; it can only ask, not force. step()'s info dict gained
  "phase_changes" ({tls_id: {from_slot, to_slot}}) reporting exactly what
  PravahEnv actually did this step, including a deferred change firing on
  its own with no fresh action given that step -- tested against the real
  network with duration/interval numbers chosen so the lock boundary lands
  exactly on a step (no off-by-one guessing).

  graph_export.py -- the two concrete, decided pieces of "give the
  arbitrary light changes out in some format":
    - segment_node_state(): rolls TraCI's live edge speed back up to
      original CSV segment_id granularity (via segment_map.py), in
      PRAVAH's own graph vocabulary (segments as nodes) rather than
      SUMO's (edges). congestion_ratio = 1 - live_speed/speed_limit,
      clamped to [0,1]. pressure is always None -- an explicit future
      hook, not a fabricated number.
    - SignalEventLogger: one JSON line per real phase change (reads
      step()'s own phase_changes, never independently guesses what
      changed), with t/tls_id/from_slot/to_slot/triggering_controller.

  What still isn't decided (per the user, explicitly deferred, not
  guessed at): the exact interface a separately-built GUI would consume
  from this simulation. segment_node_state() and SignalEventLogger are
  the natural data sources whenever that's known -- this pass stops short
  of inventing a live feed/API for an unknown consumer.


--------------------------------------------------------------------------------
15. STREET LIGHTS + THE "BEFORE/AFTER PRAVAH" COMPARISON DEMO
--------------------------------------------------------------------------------

  Two follow-up asks after watching the ITO demo run live: make the TLS
  markers look like real street lights instead of floating boxes, and
  actually demonstrate PRAVAH's value with two directly comparable runs --
  one showing the problem (dense ITO traffic, a plain traffic light,
  visible chaos) and one showing the fix (identical density, PRAVAH's
  signal logic instead). Both build entirely on already-existing pieces
  (PravahEnv, MaxPressureController, generate_calibrated_routes,
  GazeboBridge's live recoloring) -- no new subsystem.

  STREET LIGHTS (src/pravah/gazebo_bridge/world_builder.py):

    Every TLS marker model now also includes a real pole mesh
    (_street_light_pole()) alongside the existing small colored box
    (unchanged tls_marker_visual naming/mechanism -- still the thing that
    actually gets recolored live, still targeted the exact same way).

    Two Fuel candidates were checked empirically (spawn under a camera,
    read the actual rendered frame -- same discipline as the vehicle-
    orientation calibration) before picking one:
      - OpenRobotics/Stop light post -- thematically ideal (an actual
        signal-head fixture on a pole), tried first, REJECTED: its SDF
        nests two <include>s of OpenRobotics/Stop light via a bare
        `model://stop_light` URI that this gz-sim build's resource
        resolver can't find ("Unable to find uri[model://stop_light]"),
        so the whole model never spawns. Its sub-model was then checked
        completely standalone too: even after finding and fixing a real
        packaging bug (its .mtl's `map_Kd stop_light.png` pointing at a
        file one directory away from where it actually ships -- same
        class of bug already hit and fixed for the bicycle/bus vehicle
        models, see section 13), it still rendered nothing at all.
        Confirmed via a known-good control (the Hatchback vehicle model
        rendered correctly in the identical test setup, ruling out a
        camera/framing bug) that this is a genuine, deeper problem
        specific to that mesh -- not chased further (bounded effort, same
        as the asphalt-texture investigation in section 13).
      - OpenRobotics/Lamp Post -- single self-contained mesh, no nested
        includes, the same <include> mechanism already proven for all
        four vehicle models. Empirically confirmed rendering correctly: a
        real ~8m pole with a curved arm and lamp head. This is the one
        actually used (STREET_LIGHT_POLE_URI).

    The marker box itself shrank from a floating 0.3x0.3x3.0m box to a
    small 0.4m cube (SIGNAL_MARKER_SIZE) at roughly mast-arm signal height
    (SIGNAL_MARKER_Z=3.0, well below the pole's own decorative lamp head),
    offset SIGNAL_MARKER_OFFSET=0.4m off the pole's own center so it
    doesn't clip through the pole mesh. Verified live end-to-end against
    the real pravah_ito.sdf world: the pole renders correctly at the real
    junction, and publishing a material_color update to
    tls_cluster_2_72::link::visual still correctly recolors the marker
    (red -> green checked directly by re-capturing a camera frame).

  DENSE, ITO-FOCUSED DEMAND (src/pravah/sumo_twin/demand.py,
  scripts/generate_calibrated_demand.py):

    generate_calibrated_routes() gained hotspot_edges/hotspot_multiplier.
    Edges in hotspot_edges get their interpolated sample_size scaled by
    hotspot_multiplier before THEIR OWN rate is computed, but the shared
    weight_total every edge's proportional share comes from is computed
    BEFORE that scaling -- so every non-hotspot edge gets exactly the rate
    it would've gotten without this option at all; only the hotspot edges
    get artificially busier, stacked on top of (not redistributed away
    from) the rest of the network's honest calibrated levels. Verified
    directly: a plain run and a hotspot(multiplier=5) run with the same
    seed produce identical rates on every edge except the boosted one,
    which comes out at exactly 5x (tests/test_demand_calibrated.py).

    scripts/generate_calibrated_demand.py is the CLI wrapper this
    function never had -- previously only reachable via an inline
    `python3 -c` call (see the file layout note in section 14's original
    version of this file). Exposes --hotspot-edges/--hotspot-multiplier
    alongside the existing parameters.

    sumo/routes/pravah_calibrated_dense.rou.xml (sumo/pravah_ito_dense.
    sumocfg points at it) was generated with cluster_2_72's 6 approach/
    exit edges as the hotspot set and multiplier=6. Tuning note, done
    empirically not guessed: ITO's approaches are short (22-51m) but
    mostly single-lane, and their baseline TomTom-derived share of the
    network's total demand weight is uneven (one approach got ~500 vph at
    this multiplier, another only ~150, the 2-lane one barely anything) --
    checked directly with a headless TraCI run sampling
    getLastStepHaltingNumber()/getWaitingTime() every 20-30s before
    settling on this multiplier: queues build to 8-10 halted vehicles on
    the busiest approaches each cycle (exceeding those short edges' own
    length -- a real spillback look) with waiting-time peaks over 300s,
    which is genuinely visible congestion without every approach being
    saturated identically (real junctions aren't either). This is an
    explicit, deliberate synthetic stress scenario for the demo, not a
    claim about real ITO traffic volumes.

  CONTROLLER-DRIVEN SIGNAL MODE (scripts/run_gazebo_twin.py):

    New --controller {none,maxpressure} flag, default none (today's
    unmodified raw-TraCI passthrough -- SUMO's native fixed-time program
    drives the light, unaffected). This is the "before PRAVAH" case.

    --controller maxpressure constructs a PravahEnv(sumocfg,
    control_interval=1, sim_end=duration, lock_window=10,
    use_gui=args.sumo_gui) and a MaxPressureController, then loops
    controller.decide_actions(obs) -> env.step(actions) exactly like
    compare_control.py's run_episode() does, but after every step() call
    also does the same Gazebo-sync work the passthrough branch does
    (pulling vehicle/signal state off the same shared module-level traci
    connection PravahEnv itself opened -- no second traci.start()).
    control_interval=1 is deliberate: it means step() advances exactly
    one sim-second in the common case, keeping the same per-second Gazebo
    sync cadence as the passthrough branch, with no changes needed to
    PravahEnv's own stepping logic. Known, accepted exception: during a
    signal transition, one step() call advances yellow_span+1 seconds
    (the mandatory yellow hold is atomic inside step()), so Gazebo sync
    happens in one slightly bigger jump right at that moment, a few times
    per light cycle -- documented, not hidden. Prints
    total_waiting_time/mean_edge_speed periodically from step()'s own
    info dict, so the improvement is visible quantitatively too.

    Small supporting fix in env.py: PravahEnv._base_args() never added
    --start/--quit-on-end, so use_gui=True would launch sumo-gui sitting
    on an unclicked play button forever -- the exact problem
    run_gazebo_twin.py's own passthrough branch already solved for its
    --sumo-gui handling (section 13). Fixed by adding the same two flags
    when constructed with use_gui=True, so --sumo-gui works the same way
    in both --controller branches.

    Both branches verified end-to-end against the real ITO world and the
    dense route file (headless, short duration): no crashes, vehicles
    move, gz sim/TraCI processes clean up correctly on exit.

  THE TWO DELIVERABLE RUN COMMANDS -- same world, same dense route file
  (so the traffic density at ITO is identical), only --controller differs:

    export SUMO_HOME=/home/kreacher/sumo-src
    export PATH=$SUMO_HOME/bin:$PATH
    cd /home/kreacher/Pravah

    # CASE 1 -- before PRAVAH: dense ITO traffic, native/plain traffic
    # light, visible chaos
    python3 scripts/run_gazebo_twin.py \
      --sumocfg sumo/pravah_ito_dense.sumocfg \
      --world gazebo/worlds/pravah_ito.sdf \
      --duration 300 --speed 1 --sumo-gui

    # CASE 2 -- with PRAVAH: identical ITO traffic density, PRAVAH
    # (max-pressure) signal control
    python3 scripts/run_gazebo_twin.py \
      --sumocfg sumo/pravah_ito_dense.sumocfg \
      --world gazebo/worlds/pravah_ito.sdf \
      --duration 300 --speed 1 --sumo-gui --controller maxpressure

  Regenerating the dense route file (only needed after changing the
  hotspot edge set/multiplier or the underlying calibrated data):

    python3 scripts/generate_calibrated_demand.py \
      --net sumo/network/pravah.net.xml \
      --segment-map sumo/network/pravah.net.segment_map.json \
      --csv data/pravah_900to1000_balanced.csv \
      --out sumo/routes/pravah_calibrated_dense.rou.xml \
      --duration 3600 --interval 300 --target-vph 1200 --seed 1 \
      --hotspot-edges 1285520201747431424,1285520202073505792,1285520202709368832,\
1285520202504470528,1285520202525605888,1285520202575904768 \
      --hotspot-multiplier 6

  FOLLOW-UP: "unable to see visually appealing difference made in traffic"

    After watching both cases, the user reported no visible difference
    between them. Root-caused rather than re-tuned blindly:

    world_builder.py's build_world() never set a <gui><camera_pose> in the
    generated SDF. With none, gz-sim falls back to its own hardcoded
    default GUI camera -- confirmed by reading both
    /usr/share/gz/gz-sim8/gui/gui.config and the user's own
    ~/.gz/sim/8/gui.config directly -- which is "-6 0 6 0 0.5 0", a view
    meant for content built near world origin. But this network's real
    SUMO coordinates sit far from the origin (the ITO world's own ground
    plane, centered at 264.7/805.1, doesn't even extend down to y=0). So
    every one of this session's `gz sim` launches opened looking at empty
    space nowhere near the junction -- a brand-new window with no memory
    of any previous manual navigation, so even if the user did fly the
    camera to the junction on one run, there was no guarantee the next
    run's window ended up framed the same way. That alone was sufficient
    to explain "no visible difference," independent of whether the signal
    control actually differed.

    Fixed by adding a <gui> block to build_world()'s output. GUI_TEMPLATE
    is gz-sim's own default plugin set, copied verbatim from that same
    gui.config (matches the <gui> structure of a real shipped world,
    /usr/share/gz/gz-sim8/worlds/boundingbox_camera.sdf) -- not a
    stripped-down reimplementation, so every existing default tool (World
    control, World stats, Entity tree, Screenshot, Spawn, Shapes, etc.)
    stays exactly as before. The only change is camera_pose, computed by
    the new _camera_pose() from where the scene actually is.

    _camera_pose() looks at `center` (the junction the caller explicitly
    scoped the world to) when one was given -- not an average of every
    visible TLS position, which was the first version's bug: the real ITO
    world's second TLS (node "13") sits ~226m away at a real y of 579 vs
    ITO's own 805, so averaging landed the camera at neither junction.
    Caught by checking the actual generated camera_pose against the real
    marker coordinates rather than assuming the formula worked. Falls back
    to a TLS-position average, then the scene's bounding-box centroid,
    only when there's no explicit center at all (a whole-network build).
    Offset/height (radius*0.05, radius*0.083) empirically validated by
    spawning a probe camera at the exact computed pose and reading the
    rendered frame back -- confirmed a clear, well-framed shot of the
    junction, the street light, and the converging roads.

    Also ran a real headless before/after measurement (no Gazebo) to check
    whether the underlying difference was even large enough to be visible
    once framed correctly, over the full 300s dense-demand run, at
    cluster_2_72:

      Case 1 (fixed-time):   peak halted=11  mean halted=4.00  cumulative waiting time=21,694  arrived=198
      Case 2 (PRAVAH):        peak halted= 3  mean halted=0.27  cumulative waiting time=    99  arrived=201

    A 99.5% reduction in cumulative waiting time and a 93% reduction in
    mean queue length, with slightly higher throughput -- the underlying
    effect was always dramatic; the camera bug was the whole story. No
    further demand/multiplier re-tuning was needed.

    tests/test_gazebo_bridge.py gained 4 tests: _camera_pose's center-
    priority behavior (the actual bug), both fallback paths, and (SUMO-
    gated) that build_world()'s real output lands the camera near the
    real ITO junction, not gz-sim's "-6 0 6" default.

  FOLLOW-UP: "why is the motion of sumo and gz jittery?"

    Root cause: SUMO's own default step-length is 1.0 simulated second per
    traci.simulationStep() call. Neither run_gazebo_twin.py nor any
    .sumocfg here ever set --step-length, so every run used that default --
    confirmed directly (grepped every .sumocfg and this script for
    "step-length": no hits). At --speed 1 (real-time), that means both
    sumo-gui's own rendering and the Gazebo bridge only got a new vehicle
    position once per real second, with nothing interpolating in between --
    motion reads as discrete hops rather than continuous movement, in
    both viewers identically (matching exactly what was reported: "sumo
    and gz" both jittery, not just one).

    Fixed with a real --step-length reduction (default now 0.2s = 5
    updates/sec), not a cosmetic client-side interpolation hack -- this
    makes the underlying TraCI stream itself finer-grained, which
    benefits sumo-gui's own redraw rate for free (it redraws whenever we
    call simulationStep(), now 5x/sec instead of 1x/sec) as well as
    Gazebo's.

    This isn't just a CLI flag, though: env.py's step() had three places
    that counted simulationStep() calls 1:1 with seconds (the yellow-
    transition hold and the "rest of control_interval" hold, both
    `range(int(some_seconds))`) -- correct only when step_length=1.0.
    PravahEnv gained a `step_length` param (`_steps_per_second = round(1.0
    / step_length)`) and those two loops now run `round(seconds *
    steps_per_second)` sub-steps instead -- the actual elapsed-seconds
    bookkeeping (`_phase_elapsed`, the 10s lock, `info["elapsed_s"]`) was
    already tracked in real seconds, not step counts, so none of that
    needed to change.

    Also added `step()`'s new optional `on_substep` callback, invoked
    after every individual traci.simulationStep() inside those two loops.
    Needed because run_gazebo_twin.py's --controller maxpressure branch
    only gets one chance to sync Gazebo per env.step() call -- without
    this hook, a whole control_interval's worth of sub-steps (5 of them at
    the new default) would happen invisibly before Gazebo saw anything,
    silently reintroducing the exact 1Hz choppiness step_length was meant
    to fix, just one level up. The --controller none branch doesn't need
    the hook (it calls traci.simulationStep() itself, once per loop
    iteration, so it just runs that loop 5x more often instead).

    tests/test_env.py gained 2 tests: the default step_length is really
    1.0 (documenting the bug's actual precondition), and that a finer
    step_length calls on_substep exactly steps_per_second times per
    control_interval second while info["elapsed_s"] stays correctly in
    seconds regardless.


--------------------------------------------------------------------------------
16. CURRENT FILE LAYOUT
--------------------------------------------------------------------------------

  Pravah/
  |-- README.txt                              (this file)
  |-- pravah_decode_sih_2026_bharat_nirman_ps1.pdf   (submitted architecture deck)
  |-- segment_data_mapper.py                   (untouched -- CUSUM pass, later)
  |-- data/
  |   `-- pravah_900to1000_balanced.csv        (real submitted dataset)
  |-- src/pravah/
  |   |-- __init__.py
  |   |-- sumo_twin/
  |   |   |-- __init__.py
  |   |   |-- network_builder.py               (CSV -> synthetic OSM XML + ways.json)
  |   |   |-- demand.py                        (randomTrips.py wrapper + calibrated-demand generator)
  |   |   |-- env.py                           (Phase A: TraCI control wrapper, now with the 10s lock)
  |   |   |-- segment_map.py                   (edge_id -> original segment_id(s))
  |   |   |-- render.py                        (retired -- TraCI recorder + .mp4 renderer, section 11)
  |   |   |-- controllers.py                   (FixedTimeController, MaxPressureController)
  |   |   |-- tomtom_feed.py                   (discrete CSV samples -> continuous interpolated stream)
  |   |   `-- graph_export.py                  (segment_node_state, SignalEventLogger)
  |   `-- gazebo_bridge/
  |       |-- __init__.py
  |       |-- world_builder.py                 (net.xml -> Gazebo SDF world, optionally center/radius-scoped)
  |       `-- bridge.py                        (live TraCI position + type -> Gazebo pose/color sync)
  |-- scripts/
  |   |-- build_sumo_network.py                (CLI: CSV -> .net.xml + segment_map.json, --force-tls)
  |   |-- generate_demand.py                   (CLI: .net.xml -> .rou.xml, randomTrips.py-based)
  |   |-- run_digital_twin.py                  (CLI: TraCI run + speed summary)
  |   |-- render_video.py                      (retired -- CLI: TraCI run -> .mp4 clip)
  |   |-- compare_control.py                   (CLI: fixed-time vs max-pressure -> comparison.json)
  |   |-- build_showcase.py                    (retired -- CLI: comparison.json + geometry -> showcase.html)
  |   |-- build_gazebo_world.py                (CLI: net.xml -> Gazebo SDF, --center-junction/--radius)
  |   |-- generate_calibrated_demand.py        (CLI: generate_calibrated_routes(), --hotspot-edges/-multiplier(s))
  |   `-- run_gazebo_twin.py                   (CLI: launches gz-sim GUI, drives it live, --speed/--controller/--step-length/--net)
  |-- gazebo/
  |   `-- worlds/
  |       |-- pravah.sdf                       (generated -- whole network, 1,522 road-segment visuals)
  |       `-- pravah_ito.sdf                   (generated -- ITO-scoped, 305 road visuals, 5 per-approach street-light TLS markers)
  |-- sumo/
  |   |-- pravah.sumocfg                       (original network + randomTrips.py demand)
  |   |-- pravah_ito.sumocfg                   (same network + calibrated demand, for the ITO demo)
  |   |-- pravah_ito_dense.sumocfg             (same network + ITO-hotspot-boosted demand, before/after demo)
  |   |-- pravah_ito_graduated.sumocfg         (same network + graduated per-approach demand, queue-controller demo)
  |   |-- pravah_ito_pulse.sumocfg             (same network + a demand pulse on one approach, created/cleared demo)
  |   |-- network/
  |   |   |-- pravah.net.osm.xml               (generated intermediate)
  |   |   |-- pravah.net.ways.json             (generated -- segment_id -> node-id sequence)
  |   |   |-- pravah.net.segment_map.json      (generated -- edge_id -> segment_id(s), 85.6% coverage)
  |   |   `-- pravah.net.xml                   (generated -- 132 edges/99 junctions, 4 signalized incl. ITO)
  |   |-- routes/
  |   |   |-- pravah.rou.trips.xml             (generated intermediate)
  |   |   |-- pravah.rou.xml                   (generated -- 1,200 vehicles, randomTrips.py)
  |   |   |-- pravah_calibrated.rou.calibrated_trips.xml  (generated intermediate)
  |   |   |-- pravah_calibrated.rou.xml        (generated -- ~1,700 vehicles, TomTom-calibrated, 4 vTypes)
  |   |   |-- pravah_calibrated_dense.rou.calibrated_trips.xml  (generated intermediate)
  |   |   |-- pravah_calibrated_dense.rou.xml  (generated -- ITO hotspot-boosted, before/after PRAVAH demo)
  |   |   |-- pravah_calibrated_graduated.rou.calibrated_trips.xml  (generated intermediate)
  |   |   |-- pravah_calibrated_graduated.rou.xml  (generated -- ITO's 3 approaches at 10x/4x/1x, queue-controller demo)
  |   |   |-- pravah_calibrated_pulse.rou.calibrated_trips.xml  (generated intermediate)
  |   |   `-- pravah_calibrated_pulse.rou.xml  (generated -- quiet/surge/quiet pulse on one approach, created/cleared demo)
  |   `-- video/                               (retired outputs, kept, no longer regenerated)
  |       |-- comparison.json
  |       |-- network_geometry.json
  |       |-- showcase.html
  |       `-- pravah_demo.mp4
  `-- tests/
      |-- test_network_builder.py              (4 tests, all passing)
      |-- test_env.py                          (9 tests, all passing -- see sections 9, 14, and 15)
      |-- test_segment_map.py                  (6 tests, all passing -- see section 10)
      |-- test_gazebo_bridge.py                (16 tests, all passing -- see sections 13, 15, and 17)
      |-- test_tomtom_feed.py                  (9 tests, all passing -- see section 14)
      |-- test_demand_calibrated.py            (7 tests, all passing -- see sections 14, 15, 17, and 18)
      |-- test_graph_export.py                 (3 tests, all passing -- see section 14)
      `-- test_controllers.py                  (5 tests, all passing -- see section 17)

  (sumo-src/, the SUMO source checkout + build, lives at
  /home/kreacher/sumo-src -- a sibling of this repo, not inside it.)


--------------------------------------------------------------------------------
17. PER-APPROACH SIGNALS + THE GRADUATED-TRAFFIC / QUEUE-BASED DEMO
--------------------------------------------------------------------------------

  Two asks, handled together since the first is a real prerequisite for
  honestly demonstrating the second: "why is the traffic lights only
  showing in one direction in gazebo" (a real bug), and "show good
  traffic management: 4 differentiated-volume approaches, green goes to
  whichever has the most cars." (Kept to ITO/cluster_2_72's real 3
  approaches per the user's own call -- not literally 4, since that
  junction only has 3.)

  BUG: only one direction shown

    cluster_2_72 actually has 3 distinct incoming approach edges (checked
    directly via TraCI's getControlledLinks() and sumolib's TLS.
    getConnections() on the static net.xml -- both expose the same
    (in_lane, out_lane, link_index) triples). The old world_builder.py
    put exactly ONE recolorable marker per TLS, reading only state[0] --
    which happens to belong to one specific approach (edge
    ...1747431424). In cluster_2_72's real phase 0 ("gGgrrrrGg"), that
    approach and a second one (edge ...2073505792, links 7-8) are both
    green at the same time, while a THIRD approach (edge ...2709368832,
    links 3-6) is always the *opposite* color -- confirmed live, not
    assumed. So the single marker looked like "the light," while one real
    approach never got any visual representation, and was in fact always
    showing the color the marker wasn't.

    Fixed with one marker+pole per real approach, not one per TLS.
    world_builder.py gained tls_approaches(net, tls_id) -- groups a TLS's
    controlled links by their incoming edge, picks one representative
    link_index per edge (every link on the same edge/direction changes
    color together in netconvert's generated programs) and that lane's
    own end point as the marker's position. build_world() now places a
    separate _box_visual + _street_light_pole per approach.
    tls_model_name()/tls_marker_visual() gained a required approach_idx
    param (tls_<id>_<idx>::link::visual). bridge.py's GazeboBridge takes
    a new tls_approaches={tls_id: [approach, ...]} map (the exact same
    per-approach list, computed once from --net) and set_tls_colors()
    publishes one color per approach, reading state[approach["link_
    index"]] instead of always state[0] -- _collapse_state (took the
    whole string) became _collapse_char (takes one already-selected
    character). run_gazebo_twin.py gained --net (needed to build this map
    at startup) and passes it into GazeboBridge; without --net, everything
    falls back to the old single-marker-at-state[0] behavior, so it's
    optional but should always be passed now.

    Verified live end-to-end, not just unit-tested: ran the real dense
    demand through a real TraCI connection with the real bridge attached,
    then captured a camera frame of all 3 of cluster_2_72's markers at
    once -- two showing green, one showing red, simultaneously, matching
    the real "gGgrrrrGg" state exactly.

  FEATURE: graduated per-approach demand + a new QueueBasedController

    demand.py's generate_calibrated_routes() gained hotspot_multipliers:
    {edge_id: multiplier} -- unlike the single hotspot_edges/
    hotspot_multiplier pair (still supported, unchanged), this gives
    several edges of the SAME junction distinctly different volumes in
    one run. Resolved per edge as hotspot_multipliers.get(edge_id,
    hotspot_multiplier if edge_id in hotspot_edges else 1.0). scripts/
    generate_calibrated_demand.py gained --hotspot-multipliers (comma-
    separated edge_id:multiplier pairs).

    sumo/routes/pravah_calibrated_graduated.rou.xml (sumo/pravah_ito_
    graduated.sumocfg points at it): cluster_2_72's 3 real approaches at
    10x / 4x / 1x -- a genuinely heavy, medium, and light side.

    controllers.py gained QueueBasedController: the literal rule asked
    for -- whichever green phase currently serves the largest RAW queue
    (not MaxPressureController's queue-in-minus-queue-out pressure, just
    how many cars are actually waiting) wins, re-decided every control
    step, so a still-busiest approach just keeps winning round after
    round (that's what "keep it green for most of the time" looks like
    in a controller with no separate hold-duration timer) -- the existing
    end-of-phase lock in env.py still applies underneath regardless.

    Measured live, headless, on the graduated demand at cluster_2_72 over
    300s: QueueBasedController gave the heavy+medium approaches' phase
    98.0% of green time (294.0s) vs the light approach's 2.0% (6.0s).
    FixedTimeController, same demand, same duration: 50.3% / 49.7% --
    an even split regardless of the 10x real difference in demand. That
    contrast is the whole demo.

    run_gazebo_twin.py's --controller gained a third choice, queue
    (CONTROLLERS = {"maxpressure": ..., "queue": QueueBasedController}),
    sharing all the same PravahEnv/step_length/on_substep machinery the
    maxpressure branch already had.

    Run command (graduated demand, queue-based controller, --net now
    passed so every approach gets its own honest marker):

      python3 scripts/run_gazebo_twin.py \
        --sumocfg sumo/pravah_ito_graduated.sumocfg \
        --world gazebo/worlds/pravah_ito.sdf \
        --net sumo/network/pravah.net.xml \
        --duration 300 --speed 1 --sumo-gui --controller queue

    tests/test_controllers.py (new file, 5 tests): QueueBasedController
    picks the largest-queue phase, keeps favoring a still-busiest
    approach across repeated calls, switches once the busier side
    changes, sums multiple lanes per phase correctly, and handles several
    TLS independently. tests/test_gazebo_bridge.py gained 3 more tests
    (tls_approaches against the real network, set_tls_colors publishing
    per-approach from the right link_index, and the single-marker
    fallback with no approach map). tests/test_demand_calibrated.py
    gained 1 more (hotspot_multipliers giving 3 edges 3 different exact
    rates in one run).


--------------------------------------------------------------------------------
18. THE DEMAND PULSE: "TRAFFIC CREATED, THEN EFFICIENTLY CLEARED"
--------------------------------------------------------------------------------

  Ask: increase traffic on the right side (as seen from the default
  camera view) and show it being created, then efficiently cleared.

  Identifying "right side": rather than guess from coordinates, spawned a
  probe camera at the exact embedded default camera_pose and uniquely
  colored each of cluster_2_72's 3 approach markers (blue/magenta/cyan),
  then read the rendered frame -- the rightmost marker in that view is
  approach 0, edge 1285520201747431424, which happens to already be the
  "heavy" approach from section 17's graduated demo.

  demand.py's hotspot_multipliers gained schedule support: a value can be
  a list, one multiplier per --interval-sized time chunk (e.g. [1, 150,
  150, 150, 1, 1, 1] with interval=30 -- quiet, then a 90s surge, then
  quiet again), not just one flat constant for the whole run. scripts/
  generate_calibrated_demand.py's --hotspot-multipliers accepts this as a
  pipe-separated schedule, e.g. "edge_id:1|150|150|150|1|1|1".

  Real finding while tuning this, worth recording since it shapes what
  the actual deliverable is: QueueBasedController (section 17) is
  genuinely too effective to let a real pile-up form in the first place,
  even under an extreme, sustained 150x surge (checked directly: peak
  halted count stayed at 4 the entire time). That's not a bug, it's the
  controller doing exactly its job -- but it means a *single* queue-based
  run can't show "created, then cleared" as one visible arc; there's
  nothing dramatic to clear. Also found and worked around a real confound
  while diagnosing this: total network vehicle count (and so, indirectly,
  background load at any junction) never reaches equilibrium within a
  short observation window at this network's calibrated demand rates --
  it just keeps climbing for as long as the run lasts, an effect
  independent of any specific edge's hotspot tuning, which was muddying
  every longer (300s) test with unrelated late-run congestion. Fixed by
  using a lower --target-vph (400, vs. the dense demo's default 1200) and
  a shorter total duration (210s) so the pulse's own effect stays legible
  against a near-flat background instead of a still-rising one.

  The actual deliverable is therefore the honest version of the ask: the
  SAME demand pulse run through both signal-control modes already built
  (section 15/17's --controller flag) --

    sumo/routes/pravah_calibrated_pulse.rou.xml (sumo/pravah_ito_pulse.
    sumocfg points at it): edge 1285520201747431424 quiet for 30s, a 90s
    surge at 150x, quiet for the remaining 90s; --target-vph 400,
    duration 210s.

    # traffic IS created -- native fixed-time light, real pile-up forms:
    # measured directly, peak halted=7, waiting time peaks over 350s per
    # cycle, and it never gets ahead of the surge -- a fresh backup
    # builds every red phase for the whole 90s surge window.
    python3 scripts/run_gazebo_twin.py \
      --sumocfg sumo/pravah_ito_pulse.sumocfg \
      --world gazebo/worlds/pravah_ito.sdf \
      --net sumo/network/pravah.net.xml \
      --duration 210 --speed 1 --sumo-gui --controller none

    # same exact surge, efficiently handled -- PRAVAH's queue-based
    # control: measured directly, peak halted=4 the entire run, largely
    # because it never lets a real backup form at all rather than
    # forming one and draining it -- arguably the more impressive result.
    python3 scripts/run_gazebo_twin.py \
      --sumocfg sumo/pravah_ito_pulse.sumocfg \
      --world gazebo/worlds/pravah_ito.sdf \
      --net sumo/network/pravah.net.xml \
      --duration 210 --speed 1 --sumo-gui --controller queue

  tests/test_demand_calibrated.py gained 1 more test: a hotspot_
  multipliers schedule (list) applies a different, correct multiplier to
  each time chunk in one run, verified against an otherwise-identical
  unscheduled run.


--------------------------------------------------------------------------------
19. NEXT STEPS
--------------------------------------------------------------------------------

  See the accompanying chat messages for the full integration plan (what
  has to change in this simulation before vehicle-detection/model
  integration and RL control can start, and what happens on the simulation
  side after they exist). Updated short version, in order:

    1. DONE -- PravahEnv (src/pravah/sumo_twin/env.py): stable per-step
       observation, safe signal-phase actions with automatic yellow
       transitions, fast traci.load()-based episodic reset, and
       inject_vehicle() as the live-demand hook. See section 9.
    2. DONE -- segment_id -> SUMO edge_id mapping (segment_map.py),
       85.6% coverage, auto-generated on every network build. See
       section 10.
    3. RETIRED -- headless video rendering (render.py, render_video.py)
       and the browser showcase page (build_showcase.py). Both still work
       and are still tested, but get no further work: the user wants to
       watch the simulation live instead of a recording, and a separate
       GUI already exists that this codebase doesn't need to duplicate.
       See sections 11 and 12.
    4. DONE -- FixedTimeController + MaxPressureController
       (controllers.py) and a same-seed comparison harness
       (compare_control.py). Measured ~92% reduction in total network
       waiting time at signals for unchanged throughput/speed on the
       original (whole-network, non-ITO-focused) comparison. See
       section 12.
    5. DONE -- Gazebo bridge, now built out specifically for a live,
       watchable ITO junction scene, not just the earlier foundation:
       center/radius-scoped world generation, vehicles visually distinct
       by type (car/twowheeler/bus/truck, matching SUMO's own vType
       dimensions), TLS markers that live-recolor to match the real
       signal state (verified end-to-end against a real paced TraCI run),
       and real-time (or --speed-adjustable) wall-clock pacing in
       run_gazebo_twin.py so it's actually watchable, not a race-to-finish.
       See section 13. Still not done: the native boundingbox-camera
       sensor for auto-generated detection labels, and any dataset export
       -- both deliberately deferred, orthogonal to "watch it run live."
    6. DONE -- the ITO junction demo's data/control pipeline: TomTom
       interpolation (tomtom_feed.py), whole-network calibrated demand
       with realistic vehicle-type mix (demand.py), ITO forced into a
       real controllable signal (build_sumo_network.py --force-tls), the
       end-of-phase 10-second lock enforced in PravahEnv itself (env.py),
       and the two concrete "give signal changes out in a format" pieces
       (graph_export.py: segment_node_state, SignalEventLogger). See
       section 14.
    7. Still explicitly undecided (per the user, not guessed at): the
       exact interface a separately-built GUI would consume from this
       simulation. segment_node_state() and SignalEventLogger are the
       natural data sources whenever that's known.
    8. GNN-RL controller as a second policy against the same
       controllers.py interface, benchmarked against Max-Pressure the same
       way Max-Pressure was benchmarked against fixed-time here.
    9. The CUSUM anomaly-detection pass (segment_data_mapper.py) remains a
       separate, independent piece of work, not blocking any of the above.

================================================================================
