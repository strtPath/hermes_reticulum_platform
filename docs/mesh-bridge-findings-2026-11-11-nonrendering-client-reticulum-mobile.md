# Findings: a non-rendering client seen — `reticulum-mobile-app` (A13 criterion 3)

Date: 2026-11-11. Card: A13 (`docs/specs/adapter/tasks.md`), acceptance criterion 3 — "a client
known not to render markdown ... the degradation is recorded (a findings note naming the
client), not discovered later as a bug." This is that note.

## Observation

A peer switched their mesh client mid-conversation and reported that markdown still does not
render. The client is `reticulum-mobile-app` (github.com/thatSFguy/reticulum-mobile-app), a
native mobile app for Reticulum/LXMF.

## What the client does

Repo feature set reviewed 2026-11-11: message bubbles are rendered as native Kotlin/Swift text
views. There is no markdown renderer, no renderer selection field, and no plain/markdown
capability signalling anywhere in the feature list. Markdown replies from the bridge therefore
degrade by showing their markers verbatim: `**bold**`, fenced code ``` lines, list dashes and
`[pN i/N]` part labels all arrive as literal characters.

## Consequence for the bridge

- Nothing here is a bug in the adapter or the renderer-field work (that work shipped in A13,
  PR #37); the client simply does not advertise or honour any formatting.
- The `_PLATFORM_HINT` wording from A13 already covers this case: it tells the model most
  clients render markdown while a plain-text fallback caveat keeps answers legible without
  relying on it.
- A per-destination renderer hint for this client would need client-side capability signalling
  the app does not implement; not carded.

No code change follows. This note exists to satisfy A13 criterion 3 with a named client.
