# Evelyn Mindcraft migration

## Decision

Evelyn's Minecraft runtime is being migrated from the locally modified Voyager
implementation to Mindcraft. The Compose service key and HTTP port remain
`voyager` and `8765` temporarily so the existing launcher and control page do
not break during the runtime swap.

The upstream source is a Git submodule pinned to Mindcraft `v0.1.4`:

- repository: `https://github.com/mindcraft-bots/mindcraft.git`
- commit: `b36eaf7e61b3f6bd031fdb531812b2e3c42b6c73`

Evelyn-specific code is kept outside the submodule in
`external/mindcraft_evelyn`. The Docker build applies the small integration
patch and copies the custom runtime modules into the image.

## Runtime contract

- container name: `evelyn-mindcraft`
- service API: port `8765`
- compatible endpoints: `/health`, `/status`, `/observe`, `/start`, `/stop`,
  and `/goal`
- mutating endpoint boundary: `/start` and `/goal` require an exact fresh
  `minecraft_world_lease.proof.v1`; `/stop` is always allowed
- planner: local `Qwen3-14B-Q4_K_M.gguf`; planning, code-model requests, hash
  embedding, and recovery stay on the local planner by default
- Codex escalation: disabled before token or network access; its separate
  Compose profile remains unavailable until pinned-image tool access is
  verified
- Minecraft version: `1.21.11`
- account authentication: Microsoft
- default mode: normal survival player, not operator
- slash commands: blocked at the Mineflayer chat boundary
- disabled capabilities: cheats, player attacks, hunting, self-defense,
  arbitrary generated actions, generated mode changes, and blueprint commands
- local microphone: unrelated to this runtime and remains controlled by the
  existing Evelyn local-I/O settings

## Persistent data

- Mindcraft bot memory: `bot_memory/mindcraft`
- profiles: `bot_profiles`
- runtime telemetry: `runtime_artifacts/mindcraft/status.json`
- world-action authorization:
  `runtime_artifacts/minecraft_world_lease/status.json`
- runtime log: `runtime_artifacts/logs/mindcraft.log`
- optional Codex Gateway token: dedicated Compose volume
  `codex_gateway_token` (not mounted into the default Mindcraft service)

## Verification evidence

The following image evidence is from the 2026-07-31 pinned Mindcraft build,
before the 2026-08-08 local-default/Codex-boundary source change:

- The Docker image built successfully on Node.js 22.
- The four custom JavaScript entry points passed `node --check` in the image.
- An isolated container returned a healthy Mindcraft service contract without
  starting the Minecraft child process.

Current source evidence:

- Contract tests verify that default Mindcraft startup has no Codex dependency
  or token mount, and that an unverified gateway returns a fixed `503` without
  spawning Codex. This is source-level evidence, not a live Minecraft or Docker
  tool-isolation verification.
- Production dependency audit reports no high or critical findings. Fourteen
  moderate findings remain in the upstream Mineflayer authentication/plugin
  chain, with no safe compatible upstream fix currently proven.

## Runtime generated-code lint gate

The pinned upstream `Coder` uses ESLint before executing generated action code.
The runtime image therefore owns ESLint and its flat-config dependencies as
production dependencies rather than assuming upstream development packages are
present.

The 2026-07-31 hardening does the following:

- pins ESLint `10.8.0`, `@eslint/js` `10.0.1`, `globals` `17.8.0`, and
  `eslint-plugin-no-floating-promise` `2.0.0`;
- runs the actual image `eslint.config.js` during every Docker build;
- requires valid generated code to pass and an unawaited declared async call
  to be rejected by `no-floating-promise/no-floating-promise`;
- catches lint infrastructure failure inside `Coder._lintCode()` and returns a
  fixed fail-closed result without executing generated code or logging the raw
  exception.

Image
`sha256:56963ab15c8f98a9eee454bfdfe1feff14e118e68b85fe84e812ac278122e667`
passed the build-time lint contract, direct patched-Coder allow/reject smoke,
config-missing fail-closed smoke, `npm ls`, Node syntax, Python `compileall`,
and `pip check`. Its production audit is moderate 14, high 0, critical 0.
The image was staged only; the Mindcraft service was not started or replaced.

## Live cutover result

The live cutover was approved and completed on 2026-07-20. The Compose
`voyager` service now runs as `evelyn-mindcraft`; the other Evelyn containers
were not restarted.

Live evidence after the cutover:

- the Microsoft-authenticated `Evelyn_0428` agent connected to the Minecraft
  server on `host.docker.internal:25565` using protocol `1.21.11`;
- telemetry reported `connected=true`, fresh position, health, hunger,
  inventory, and nearby-hostile state;
- the Codex Gateway served successive `GPT-5.5` planner requests;
- the agent completed more than the former two-action startup limit and moved
  through normal-player pathfinding actions;
- slash-command output remained guarded and the live blocked-command counter
  remained at zero;
- the final regression run passed 45 tests and Compose/Node image checks.

Issues found and fixed during the live cutover:

- restored upstream's runtime `eslint` dependency;
- bound the internal MindServer explicitly to `127.0.0.1` to avoid a
  localhost IPv4/IPv6 mismatch in the container;
- narrowed the persistent bot-data mount to `bots/Evelyn_0428` so upstream
  execution templates remain visible;
- delayed the slash-command guard until Mineflayer plugin injection;
- renamed the local hash embedding model to avoid upstream's legacy
  `local -> ollama` rewrite;
- replaced upstream's hard-coded two-command initialization limit with the
  Evelyn runtime's unlimited survival action loop.
- changed the Mindcraft container healthcheck from `python` to `python3`,
  matching the slim image, and verified Docker reports `healthy`.

The initial starvation condition recovered in the live world. After the final
controller deployment, telemetry held at health `20`, hunger `20`, and 9 bread
while the bot continued moving normally.

## Deterministic survival controller live

A new image was built after the initial cutover with an Evelyn-specific
non-operator survival recovery mode. It is intentionally separate from the
`GPT-5.5` planner and can interrupt planner actions when survival thresholds
are crossed.

The controller provides:

- hostile avoidance before eating or planning, without downward escape paths;
- immediate consumption of safe inventory food;
- upward-only emergency surface escape with normal hand digging and block
  placement allowed, but no commands or operator actions;
- mature crop collection and bread crafting when starving on the surface;
- safe-state wood, planks, sticks, crafting-table, and wooden-pickaxe bootstrap;
- per-action failure counters, exponential cooldowns, and fallback to the next
  viable recovery action;
- survival-controller decision state in runtime telemetry;
- blocking of planner-selected `!attack` and `!digDown` actions.

The first live controller image exposed that generic `moveAway` rebuilt its
movements through `goToGoal`, discarding the Y-floor restriction and descending
five blocks. The final safety patch keeps the constrained movements through
execution and applies the same floor to stepping, digging, and block placement.
It also serializes controller actions so overlapping ticks cannot start the same
recovery concurrently.

Image `sha256:58453a7f4a12028e69a8a261e38a8dc81b3cb21352c0a85c46ec4d7ed47c3c4c`
was deployed to only `evelyn-mindcraft` after explicit approval. Live evidence:

- Docker and the service API report healthy; Minecraft reports connected;
- the first short movement went from `Y=75` to `Y=74`, but a later
  `!moveAway(16)` went from `Y=74` to `Y=72`, proving the action-wide descent
  boundary was still not enforced;
- deterministic hostile flight triggered naturally and succeeded with failure
  count zero;
- health and hunger remained `20/20`, inventory contained 9 bread, and the
  blocked-command counter remained zero;
- the latest runtime segment contained no module/syntax/reference/type errors,
  no slash-command model responses, and exactly one agent child process;
- the dedicated Node suite passed 8 tests and the Python contract/regression
  suites passed 39 tests at that deployment checkpoint;
- the real Codex Gateway port again returned `401 / 401 / 200` for missing,
  incorrect, and correct Bearer credentials.

The second live failure was traced to `maxDropDown=1` limiting each edge rather
than cumulative descent across a path. A follow-up image now filters every A*
neighbor below the action's fixed minimum Y, in addition to the existing step,
dig, place, and single-drop restrictions. Image
`sha256:3a8b7c4398d7d79aa08cb1924a7e9031715979564d22f88bc2b09fb3974526bc`
passed 9 Node tests plus 39 Python tests (48 total), syntax checks, and image
inspection. It was subsequently deployed after explicit approval. The fixed
floor prevented the observed survival-flee paths from descending, but two
manual generic `moveAway` probes were interrupted by hostile-recovery actions,
so neither generic probe completed.

The same hostile-night test exposed a separate priority conflict: upstream
`cowardice`, planner actions, and `evelyn_survival` repeatedly interrupted one
another, and the bot died at approximately `(302, 82, 82)` before respawning at
the saved bed. A new staged image disables the redundant `cowardice` mode,
prevents planner and lower-priority actions from interrupting active Evelyn
survival recovery, still permits manual `!stop` and upstream fire/drowning
self-preservation, and starts food acquisition at hunger 14. Image
`sha256:f673d9f11fe7dfa8a91a14c8da82ce7b19832ff5bff2acfc7a151b6eddc49577`
passed 11 Node tests plus 39 Python tests (50 total) and syntax/profile checks.
It was deployed after explicit approval. Live mode conflicts were removed, but
a normal `moveAway` acceptance probe was still preempted by non-emergency
`bootstrap_tools`. A final staged image makes tool bootstrap idle-only while
preserving immediate hostile, food, and emergency-surface recovery. Image
`sha256:8b2ce37ca87ee37abfa5a0dbd2802ed723e66ea1d28f62f66ac42c2879a2882e`
passed 12 Node tests plus 39 Python tests (51 total). It was deployed to only
`evelyn-mindcraft` after explicit approval.

Final live acceptance evidence:

- image ID matched `sha256:8b2ce37ca87ee37abfa5a0dbd2802ed723e66ea1d28f62f66ac42c2879a2882e`;
- Docker/API/Minecraft and Codex Gateway all reported healthy/connected;
- live mode state reported `evelyn_survival=ON`, `cowardice=OFF`, and
  `cheat=OFF`;
- an isolated normal-player `!moveAway(4)` completed from approximately
  `(228.37, Y=71, 130.5)` to `(231.53, Y=72, 134.5)`, satisfying the fixed
  floor without OP, slash commands, or safety-mode disabling;
- the normal indefinite survival self-prompt was restored after the probe;
- a subsequent 35-second observation retained health/hunger `20/20`, no new
  death, no hostiles, no blocked commands, no slash responses, no module
  errors, and no `cowardice` actions while the bot continued moving;
- the live Codex Gateway security contract had already passed `401/401/200`
  for missing, incorrect, and valid Bearer credentials and was not changed by
  the survival patches.

This proves the deployment and the targeted path/priority contracts. It does
not prove indefinite survival over many Minecraft day/night cycles; continue
to treat long-duration survival, shelter construction, and reliable wood/food
bootstrap as operational follow-up rather than completed acceptance.

Other remaining limitations: tool bootstrap currently cools down when no logs
are within its 48-block scan. The `GPT-5.5` planner continues normal exploration
between retries. Daytime or occupied-bed attempts are non-fatal upstream action
errors but can temporarily waste planner turns.

## Staged hostile-response replacement (2026-07-23)

Longer live logs invalidated the earlier assumption that a resolved inverted
path goal was sufficient evidence of a successful escape. In the latest agent
session, 34 hostile-flight actions were logged as successful with zero failures,
while the agent still died once to a spider and never executed an attack.

The staged replacement removes the Evelyn-specific direct `GoalInvert` hostile
implementation. One `handle_hostile` decision now owns fight-or-flight and
delegates mechanics to Mindcraft's upstream `avoidEnemies()` and `defendSelf()`
skills, backed by `mineflayer-pathfinder` and `mineflayer-pvp`.

The local policy is intentionally narrow:

- fight only one nearby melee hostile when the bot has a melee weapon, health
  is at least 14, and hunger is at least 8;
- flee when unarmed, weak, outnumbered, outside melee range, or facing a
  ranged/high-risk hostile such as a creeper, skeleton, or this server's
  spear-capable husk;
- reject an external action's success result while a hostile remains within 18
  blocks;
- require the original target to be gone before recording a fight as successful;
- hold the same survival action for a two-second threat-free stabilization
  window before returning control to the planner;
- make failed hostile handling eligible again after 500 ms (effectively the
  next 1.5-second controller tick) instead of entering the generic exponential
  recovery cooldown.

Staged image `sha256:b4be79199b9e46957d3fd48934089484d0161bc5a0010317055d9aba37762f81`
passed 18 Node decision/contract tests, 4 focused Mindcraft Python contracts,
14 Compose/runtime contracts, JavaScript syntax/import checks, and image
inspection. A broader 1,282-test unittest discovery had one unrelated import
error because the selected Python environment lacks Voyager's optional
`gymnasium` package; the focused Mindcraft and Docker suites passed separately.
The dependency audit remains at zero high/critical and 11 moderate findings in
the existing Mineflayer authentication/plugin chain.

The user approved deployment on 2026-07-23. Only `evelyn-mindcraft` was
recreated; `evelyn-codex-gateway` retained its original container and start
time. The service came up healthy in the safe stopped state, then the approved
`POST /start` path launched the Mindcraft child. Live evidence after reconnect:

- running image exactly matched
  `sha256:b4be79199b9e46957d3fd48934089484d0161bc5a0010317055d9aba37762f81`;
- Docker health was `healthy`, restart count was zero, port 8765 was listening,
  and Minecraft reported `running=true`, `connected=true`, and fresh telemetry;
- the loaded controller snapshot exposed the new `hostileId`, `hostileCount`,
  `hasMeleeWeapon`, `hostile_tactic`, and `hostile_verification` fields;
- container source hash matched the staged/host source hash
  `3d4877219a5b0aae050a69a7c6a9c6095cd16470c428d882a4c45cba103c6ee3`;
- no hostile appeared during the immediate post-deployment observation, so the
  new natural fight/flight action is deployed but not yet proven against a live
  mob encounter.

The persisted planner memory still contains historical prose claiming that
`flee_hostile` succeeded. That text does not drive the deterministic controller,
but it may continue to bias planner suggestions until normal memory updates
replace it; do not treat the old prose as current controller telemetry.

The first natural hostile event after deployment exercised `handle_hostile`.
Mindcraft's upstream avoidance skill reported `Moved 18 away from enemies`, but
the Evelyn verifier correctly recorded the action as failed instead of accepting
the upstream result blindly. The bot did not die, the hostile subsequently left
the 18-block scan, and no legacy `flee_hostile` action ran after the new spawn.

That event also exposed an observability-only defect: the next planner tick
overwrote `hostile_tactic` and `hostile_verification`, so the retained status
could not distinguish `unsafe_after_action` from `threat_returned`. A follow-up
image now merges periodic snapshots into the previous controller state and logs
the tactic and verification reason on every hostile completion. Image
`sha256:39dbc062a6f57cbe6eeb926e96d7f0115e17f03f8ce3df11ade3561c78471d58`
passed 19 Node tests, focused Python contracts, syntax/import checks, and
host/image source-hash comparison (`b313276570c029b9a1a20b1a4b6778492a0e6244cf6bf068f3f707fec8061946`).
The user approved the second restart and only `evelyn-mindcraft` was recreated.
The running image now exactly matches `sha256:39dbc062...`; Docker reports
healthy/restart count zero, Minecraft reports connected with fresh telemetry,
and the container source hash matches `b3132765...`. `evelyn-codex-gateway`
again retained its original container and start time. No hostile appeared in
the immediate post-reconnect window, so detailed tactic/verification retention
will be confirmed on the next natural encounter.

An unrelated but urgent survival risk was visible after reconnect: health was
approximately `3.76`, hunger was `0`, and the inventory contained 9 wheat but
no edible food. `acquire_food` repeatedly failed because bread crafting requires
a crafting table and the deterministic recovery does not currently navigate to
one. This predates the combat replacement. The staged normal-player remediation
is documented below, but do not report live survival as stable until its
acceptance scenario passes.

## Dependency consolidation (2026-08-01)

The staged Mindcraft runtime no longer installs the legacy `mineflayer-pvp`
plugin in parallel with `@nxg-org/mineflayer-custom-pvp`. The only legacy API
surface used by Mindcraft was `bot.pvp.attack/stop`; it now points at the custom
plugin's verified `bot.swordpvp` implementation. The legacy plugin and its
`mineflayer-utils` branch are absent from the generated lockfile and image.

The isolated image build passed `npm ci`, all overlay patches, runtime lint,
the pinned custom-PvP API smoke, and an offline image inspection. The focused
Python Mindcraft contracts passed 15/15. The first dependency-only image exposed
one pre-existing Goal Manager assertion failure: `escape_to_surface` incorrectly
required an actionable hostile before claiming movement ownership. That allowed
planner movement to interrupt underground or water escape when no hostile was
present.

The follow-up separates the two recovery phases. `escape_to_surface` owns
movement until its bounded failure budget is exhausted, while `handle_hostile`
owns movement only while an actionable hostile remains. The high-failure
planner handoff remains intact. Focused ownership tests passed 4/4, the complete
Node suite passed 84/84, and the Python Mindcraft contracts passed 15/15. The
production audit remains moderate 12, high 0, critical 0. The final staged image
is `sha256:6e6b95a5e87efcb5187df3d0ae53478740f4c35ee025db172ecc3669b4690f37`.
It was inspected only; no Minecraft process, account login, or live service was
started.

## Evidence-gated wheat food recovery (2026-08-01)

The earlier live starvation incident had nine wheat but no edible item or
crafting table. The food-priority candidate previously remained a generic
`obtain #food` goal, so it could not select the crafting prerequisites already
implied by the inventory.

The staged Goal Manager now derives a bounded recovery chain from current
inventory: obtain one log only when needed, craft four planks, craft and retain
a workbench, then craft as many as three bread recipes from carried wheat. Each
step advances only after its inventory predicate changes; a success-shaped
result string without the corresponding world-state change does not advance the
chain. The active step and completed evidence survive a Goal Manager restart.
Wood species are preserved when choosing the planks recipe, including stripped
logs, stems, and hyphae.

Focused food-chain tests passed 4/4, the complete Node suite passed 86/86, and
the Python Mindcraft contracts passed 15/15. Image
`sha256:1017243d8844055ec06f9dac53ca89381ba4406bba1943df00a4b4266faa4c8a`
also passed the build-time lint/custom-PvP gates, complete Node suite, offline
production dependency-tree check, and Goal Manager syntax check. It was staged
only; no Minecraft process, account login, or live service was started. Real
starvation recovery remains an explicit live acceptance scenario.
