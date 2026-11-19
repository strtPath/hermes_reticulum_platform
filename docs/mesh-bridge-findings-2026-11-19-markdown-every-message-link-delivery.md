# Findings: markdown for every reply — link-mode LXMF delivery vs chunking (2026-11-19)

Live state (verified on the wire 2026-11-19):

- Single-packet replies (<=368 bytes) stamped `FIELD_RENDERER=RENDERER_MARKDOWN`
  render correctly on Columba v2.3.5-beta (bold, inline code, strikethrough,
  bullets) and on NomadNet. Confirmed by a 250-byte probe.
- Replies over one packet are split by `delivery.py` into `[pN i/N]`-labeled
  parts declaring `RENDERER_PLAIN` by design (`transport.py::_stamp_renderer`).
  Columba shows them raw — this is the "markdown gets ruined on long replies"
  complaint. `PART_LABEL_RE` lives at `delivery.py:66`.
- Progress/thinking streaming sends separate one-shot LXMF messages with no
  renderer field at all (`docs/specs/adapter/spec.md:732-733`) — a
  streaming-ON user sees literal markup on every interim.

## The mechanism we're not using

We split bodies because we deliver **propagated/direct** messages: one packet
per part, reassembly only ever visual, client-side. LXMF itself already
solves large messages — over an **RNS Link**:

- LXMF 1.3.1 `LXMessage.py` docstring (lines 75-95): single packet = 368 B;
  over a link the LXMF router sequences larger content and transfers it
  **automatically as RNS resources**, reassembling it back into ONE
  `LXMessage` on the receiving router before the UI layer ever sees it.
- `LXMessage.py:30` defines `OPPORTUNISTIC` delivery; `:114` accepts it as
  `desired_method`; `:407-419` shows opportunistic tries link-based delivery
  and falls back on its own when no link is available.
- `LXMRouter.py:1596-1603` tears the delivery link down after idle — no
  long-lived session is required.

Our adapter never picks that method: `_select_method`
(`src/hermes_reticulum/plugin/transport.py:600-633`) returns only DIRECT or
PROPAGATED, and `LXMessage(..., desired_method=method)` at `:683-696` is the
single dispatch point. One knob, one call site.

## What link-mode buys

A 1 KB reply over a link arrives as a single reassembled `LXMessage` with its
`FIELD_RENDERER` intact — Columba/NomadNet show **one markdown bubble**, no
labels, no fence surgery. Columba already rides links for attachments and
calls (`ConversationLinkManager.kt`, `rns-api` Link model, embedded Python
RNS backend), so this path is exercised in the wild today by its own image
messages, which exceed one packet. sfguy's app is unaffected either way
(`mesh-bridge-findings-2026-11-11-nonrendering-client-reticulum-mobile.md`).

## Options

1. **Opportunistic delivery for over-budget replies (recommended).**
   `_select_method` returns `LXMessage.OPPORTUNISTIC` when the body exceeds
   the packet budget; LXMF handles setup/transfer/teardown/fallback.
   Chunker stays untouched as the fallback path. Blast radius ~6 lines.
   No upstream dependency. Ship behind a knob (`auto` = today's behavior).
2. **Upstream Columba patch (independent, ~5 lines).** Strip `^\[p\d+ \d+/\d+\] `
   in the plain-text branch and render the body as markdown — fixes parts
   from ANY sender even in propagation mode, but yields three rendered
   bubbles, not one. Markdown machinery already exists since v2.1.0-beta
   (PR #1064).
3. **Part numbering in a custom LXMF special field.** Unknown fields are
   ignored on the wire, but NomadNet's reassembly parses our in-body labels —
   moving them breaks our best-supported client's join. Skip.
4. **Upstream reassembly in other clients.** `[pN i/N]` is our invention;
   no client but NomadNet has any reason to join it. Lowest leverage. Skip.

## Radio cost (914.875 MHz SF11 BW250 CR5)

Calibration: the 2026-11-10 live test measured ~56 s per full-size payload
packet at SF11 (airtime model puts a 368 B payload in the 30-55 s band;
the measured number is the budget to plan against).

- Propagation today, 1.1 KB reply: 3 full packets ≈ 2.8-3 min, unconfirmed.
- Link-mode same body: 3 full packets + ~6 mostly-small control packets —
  small packets cost a fraction of a full one, so roughly **+1-2 min per
  reply at SF11**, with guaranteed delivery and automatic retry for free.
- At SF9/SF8 the same traffic is ~4-8x cheaper; operator note: with
  thinking/tool-visibility off, only final outputs cross the radio, which
  is exactly this workload.

## Recommendation

Ship (1) as a config knob defaulting to today's behavior, flip it on for
link-capable peers; open (2) upstream at torlando-tech/columba so plain
parts render regardless of our transport choice. Do not touch the
in-body label contract — it is NomadNet's join key.

Open decisions for the peer (strtPath):

- Knob default: keep `auto` (status quo) or flip straight to opportunistic?
- Open the Columba upstream patch, or wait on link-mode?
- Should interim progress sends also carry `FIELD_RENDERER` (they don't
  today — every interim is literal markup)?
