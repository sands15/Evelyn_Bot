# Evelyn Project Guidance

## Scope

This repository contains the Evelyn local-first assistant runtime. Treat code,
tests, current-state documents, target architecture, and runtime evidence as
different evidence layers. Do not report a target design as implemented
behavior without checking the current code and tests.

The `docs/` directory is also opened as the developer-facing Obsidian vault.
It is for project knowledge and navigation. It is not Evelyn's runtime memory
store, and it must not receive private transcripts, credentials, generated
memory records, or runtime artifacts.

## Solution Quality

- Default to root-cause, end-state solutions that include verification,
  operational safety, and regression prevention.
- Do not present temporary mitigations as the main solution. Use them only when
  explicitly requested or needed to restore service, label them temporary, and
  state their removal criteria.
- For performance work, optimize the end-to-end user SLO while preserving
  quality, safety, durability, and privacy instead of optimizing one subsystem
  in isolation.
- Simplicity means the minimum necessary end-state architecture, not a knowingly
  incomplete fix.

## Obsidian Working-Memory Loop

Use `docs/` as external project memory while working. Access the Markdown files
directly; do not automate the Obsidian UI for ordinary reading or writing.

### Before a task

1. Read `docs/01_NOW.md`; keep this automatic context small.
2. Use `docs/00_EVELYN_HOME.md` and `docs/DOCUMENTATION_INDEX.md` only for
   routing when the task needs more context.
3. Search by task keywords, then read only matching sections of large files.
   Never load all of `CURRENT_STATE.md`, `ACTIVE_RISKS.md`, or the whole vault
   by default.
4. Verify material current-state claims against code and tests.

### During a task

- After a meaningful checkpoint, record only verified outcomes, decisions,
  blockers, and next actions in `docs/worklog/YYYY-MM-DD.md`.
- Record a durable design or product choice in `docs/02_DECISIONS.md` with its
  rationale and evidence. Do not record every command, retry, or temporary
  error.
- Treat `docs/99_PROJECT_INBOX.md` as user-owned capture. Do not delete or
  promote an inbox item without reviewing it against the project.

### After a task

- Update `docs/01_NOW.md` only when current focus, verified state, blockers, or
  next actions changed. Keep it under roughly 80 lines.
- Link durable notes instead of copying their full contents into `01_NOW.md`.
- Do not write a note merely to say that nothing changed.

## Editing Rules

- Preserve existing Markdown structure, relative paths, YAML frontmatter, and
  Obsidian wiki links.
- Do not edit `docs/.obsidian/` unless the user explicitly asks to change the
  local Obsidian workspace.
- Do not put secrets, tokens, private audio, transcripts, screenshots, memory
  contents, logs, or runtime state into project documentation.
- Keep `docs/00_EVELYN_HOME.md` as navigation and `docs/01_NOW.md` as a compact
  working snapshot, not competing sources of runtime truth.
- Put unreviewed human ideas in `docs/99_PROJECT_INBOX.md`; do not silently
  promote them to current behavior, requirements, or architecture decisions.
- When a material implementation changes, update the closest authoritative
  contract and, when appropriate, `docs/CURRENT_STATE.md` or
  `docs/ACTIVE_RISKS.md` in the same change.
- When adding a durable document, add it to `docs/DOCUMENTATION_INDEX.md` or
  link it from an already indexed owner document.
- Cite paths and verification evidence when summarizing project state.

## Verification

- Use the narrowest relevant checks first, then broader regression checks in
  proportion to the change.
- Do not start live Discord, microphone, Minecraft, Docker, or other external
  services unless the user explicitly asks for that live action.
- Never claim live verification from source tests alone.
