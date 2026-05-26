# Evelyn Page Distribution Target

## Goal

Make the Evelyn landing page reachable from Discord with a stable command.

## Required Shape

- the page remains a static site under `docs/`
- deployment should be GitHub Pages-friendly
- Discord command should send a public URL, not a local filesystem path
- URL should support explicit override by config, with a deterministic GitHub-derived fallback

## Fallback Rules

1. use configured `EVELYN_PAGE_URL` when present
2. otherwise derive `https://<owner>.github.io/<repo>/` from git remote origin when possible
3. if neither exists, fail clearly instead of pretending the page is reachable
