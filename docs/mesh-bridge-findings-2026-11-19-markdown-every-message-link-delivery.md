# Findings: markdown for every reply — link-mode LXMF delivery vs chunking (2026-11-19)

Live state (verified on the wire 2026-11-19):

- Single-packet replies (<=368 bytes) stamped `FIELD_RENDERER=RENDERER_MARKDOWN`
  render correctly on Columba v2.3.5-beta (bold, inline code, strikethrough,
  bullets) and on NomadNet. Confirmed by a 250-byte probe.
- Replies over one packet are split by `delivery.py` into `[pN i/N]`-labeled
  parts declaring `RENDERER_PLAIN` by design (`transport.py::_stamp_renderer`).
  Columba shows them raw — this is the "markdown gets ruined on long replies"
  complaint. `PART_LABEL_RE` lives at `delivery.py:82`.
- Progress/thinking streaming sends separate one-shot LXMF messages with no
  renderer field at all (`docs/specs/adapter/spec.md:732-733`) — a
  streaming-ON user sees literal markup on every interim.

## The mechanism we're not using

We split bodies because we deliver **propagated/direct** messages: one packet
per part, reassembly only ever visual, client-side. LXMF itself already
solves large messages — over an **RNS Link**:

- LXMF 1.3.1 `LXMessage.py:75-96`: single-packet content limits are
  context-dependent — 287 B for encrypted packets (:75-80), 319 B over a
  link (:86-90), 368 B for plain (`PLAIN_PACKET_MAX_CONTENT`, :91-96). Our
  368 B send budget (`delivery._BLOCK_CONTENT_BUDGET`) IS that plain-packet
  value, carried over verbatim from `bridge/downlink.py:43`. It is a packing
  policy, not a promise that each part fits one encrypted packet: an
  encrypted DM gets 287 B / 319 B, and anything larger rides a resource
  either way. The LXMF router sequences larger content and transfers it
  **automatically as RNS resources**, reassembling it back into ONE
  `LXMessage` on the receiving router before the UI layer ever sees it.
- `LXMessage.py:412-416`: an `OPPORTUNISTIC` body above the encrypted-packet
  limit is rewritten to `DIRECT` at pack time and sequenced as a resource —
  so oversized bodies already take the link path whenever one exists,
  without the sender naming the method. `:625-634` (`__resource_concluded`)
  and `:645-651` (`__link_packet_timed_out`) tear the delivery link down on
  completion or timeout — no long-lived session is required.

Our adapter's `_select_method` (`src/hermes_reticulum/plugin/transport.py:600-631`)
already returns DIRECT whenever a path exists (`:610-611`), and
`LXMessage(..., desired_method=method)` at `:704-710` is the single dispatch
point. The feature is therefore adapter-side only: stop chunking the body and
LXMF's own oversized-body handling carries it whole.

## What link-mode buys

A 1 KB reply over a link arrives as a single reassembled `LXMessage` with its
`FIELD_RENDERER` intact — Columba/NomadNet show **one markdown bubble**, no
labels, no fence surgery. Columba already rides links for attachments and
calls (`ConversationLinkManager.kt`, `rns-api` Link model, embedded Python
RNS backend), so this path is exercised in the wild today by its own image
messages, which exceed one packet. sfguy's app is unaffected either way
(`mesh-bridge-findings-2026-11-11-nonrendering-client-reticulum-mobile.md`).

## Options

1. **Send over-budget replies whole; let LXMF carry them (shipped).** The
   adapter skips the chunker for over-budget bodies when the knob is on;
   `_select_method`'s existing path-aware DIRECT covers the link case and
   LXMF sequences the oversized body as a resource on its own. Chunker
   stays untouched as the fallback when the one-body send fails outright.
   No transport change, no upstream dependency.
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
  (The control-packet count is modelled from the link/resource handshake,
  not measured end-to-end; treat the +1-2 min as an estimate.)
- Failure path: a one-body send that fails outright re-probes for a path
  when chunking starts (`transport.py:564-598`, `PATH_WAIT_SECONDS = 15`),
  so fallback latency is roughly 15 s + 2.5 s + 15 s before the first chunk
  moves. The number an operator will ask about when a reply takes minutes.
- No path at send time: the single oversized body is **propagated** (goes
  to the propagation node as a resource), not chunked. The chunk fallback
  only applies when the one-body send fails outright (`send_to` False).
- At SF9/SF8 the same traffic is ~4-8x cheaper; operator note: with
  thinking/tool-visibility off, only final outputs cross the radio, which
  is exactly this workload.

## Recommendation

Ship (1) with a knob, default **on**: `HERMES_RNS_LINK_MODE=off`
(also `0`/`false`/`no`) forces the chunker; anything else means link mode,
and an unrecognised value logs once so a typo cannot silently opt in.
Open (2) upstream at torlando-tech/columba so plain
parts render regardless of our transport choice. Do not touch the
in-body label contract — it is NomadNet's join key.

Open decisions for the peer (strtPath):

- Knob default: **resolved 2026-11-19** — shipped ON after the live
  407-byte reply arrived as one markdown bubble on Columba v2.3.5-beta;
  `HERMES_RNS_LINK_MODE=off` restores the chunker.
- Open the Columba upstream patch, or wait on link-mode?
- Should interim progress sends also carry `FIELD_RENDERER` (they don't
  today — every interim is literal markup)?

## Open items (review round, 2026-11-19)

- **No ceiling on a link-mode body.** One oversized reply is one RNS
  resource; at SF11 that can be tens of minutes on air and a cancel during
  a resource transfer cannot recall it. Chunking at least allowed a stop
  between parts. Next step: `HERMES_RNS_LINK_MODE_MAX_BYTES`, above which
  the send falls back to the chunker. Not built in this PR.
- **Dead branch in the chunker.** `_chunk_content`'s docstring promises
  `None` when content cannot be carried, and `chunk_for_send` never returns
  `None`, so `parts is None -> map_chunk_overflow` never fires. The answer
  to "what happens to a 100 KB reply" is the item above, not this branch.
- **Payload identity.** Receipts carry the payload text, not an id: the
  transport's callback wrapper (`transport.py::_extract_payload`) strips the
  `LXMessage`. Two peers with identical text in flight cannot be told apart.
  The adapter attributes a receipt from the routing map, then a surviving
  supersede mark, then a unique pending entry, then the last send with a
  warning. When the mark names one chat and a live entry belongs to another,
  the mark wins and the receipt is dropped: a replaced attempt must never
  clear or fail a live peer's record. The cost is that the live entry stays
  unconfirmed until the mark for that payload is cleared or the sweep reports
  it. `LXMessage.hash` is available where the callbacks are registered, so a
  message-id key would close this properly.
