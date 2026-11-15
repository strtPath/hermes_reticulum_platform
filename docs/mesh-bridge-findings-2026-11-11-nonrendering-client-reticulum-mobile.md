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
degrade by showing their markers verbatim: `**bold**`, fenced code blocks, list dashes and
link brackets arrive as literal characters. (The `[pN i/N]` part labels on split replies are a
different thing: our adapter's own plain-text split artifact, present whatever the client does
— see "Consequence" below.)

## Evidence (re-runnable; branch `master`, checked 2026-11-15)

- No renderer field: a repo-wide code search for `FIELD_RENDERER` returns 0 hits —
  `gh api -X GET search/code -f q='repo:thatSFguy/reticulum-mobile-app FIELD_RENDERER' --jq .total_count`
  gives `0`.
- The bubble is a plain text view:
  `androidApp/src/main/kotlin/io/github/thatsfguy/reticulum/android/ui/screens/MessagesScreen.kt:2017-2028`
  renders content as `Text(linkify(content = msg.content, ...))` — a Compose `Text` over a
  link-annotated string, with no markdown pass.
- Rich rendering is credited to micron pages only: `docs/TECHNICAL.md:72` lists
  "NomadNet browser — micron rendering | ✅ rich" — that is the built-in browser, not the
  LXMF message bubble.
- App version: the latest Android release when the report came in was `android-v1.2.129`
  (published 2026-11-09); the peer did not state the exact tag they ran.

## Consequence for the bridge

- Nothing here is a bug in the adapter or the renderer-field work (shipped separately in
  PR #38; A13/PR #37 is the hint string itself); the client simply does not advertise or
  honour any formatting. The `[pN i/N]` part labels work the same way: the adapter declares
  them `RENDERER_PLAIN` for every recipient (`transport.py:747-772`), so that mechanism is
  ours, not something this client opted into.
- The `_PLATFORM_HINT` wording from A13 already covers this case: it tells the model most
  clients render markdown while a plain-text fallback caveat keeps answers legible without
  relying on it.
- A per-destination renderer hint for this client would need client-side capability signalling
  the app does not implement; not carded.

No code change follows. This note exists to satisfy A13 criterion 3 with a named client.
