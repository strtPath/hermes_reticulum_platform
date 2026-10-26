# Finding 2026-10-26: shared rnsd service + stack upgrade + delivery verified

Stack change made 2026-10-26 while chasing a stale-path / no-delivery problem on the
mesh bridge. Recorded here because the topology changed and more than one agent works this
repo — a future session must not assume the old in-process RNS layout.

## What changed

- **RNS now runs as its own user service (`rnsd.service`, `reticulum-env/bin/rnsd -s`).**
  The bridge no longer owns an in-process Reticulum instance. RNS config has
  `share_instance = Yes`, so `hermes-reticulum.service` connects to the shared rnsd
  instance. Verified: rnstatus shows the shared instance `Serving: 1 program` (the bridge).
  Restart order matters: stop/start rnsd before the bridge, and both via discrete
  stop → drain → start (a single `systemctl --user restart` wedges rendezvous state).
- **Stack versions bumped in BOTH the bridge venv and the `reticulum-env` tool venv**,
  within the bridge's pins: rns 1.5.4 → **1.5.7**, lxmf 1.1.1 → **1.2.1**. (NomadNet
  was installed during the upgrade but removed again — the project does not use it; it
  was only ever considered as a self-test client with the agent on its own ACL list.)
- **RNode LoRa config corrected**: two enabled RNode interfaces now both at
  914.875 MHz / SF 11 / BW 250 kHz / CR 5 (the V4 on one serial path, the V3 on another),
  matching the remote transport's radio so LoRa-LoRa links. A disabled factory-default
  block (910 MHz / SF9 / 125 kHz) is the mismatch trap — a peer tuned to that can't link.

## Verified end-to-end delivery (the goal)

Full round-trip confirmed in the bridge journal after the fix: `Received LXMF`
(`sig=valid`) → `Reply dispatched seq=N` → `Downlink ack seq=N state=delivered
(DELIVERED)`. That is the radio + shared rnsd + ACL + Hermes + reply path working as one.

## Gotchas (both are Hermes-side, not mesh)

1. **A stale resumed session can fail with `permission_denied` regardless of a healthy
   mesh.** The bridge shells out to `hermes chat -q ... -c <session>`. If the session it
   resumes has a wedged runtime session-store worker scope (a session from before the
   upgrade), Hermes exits `permission_denied` and the bridge sends its generic "error
   processing message" reply — even though delivery and ACL are perfectly fine. **`/new`
   clears it**: starting a fresh session resets the scope. Diagnose by reproducing the
   exact `hermes chat -q -c <thread>` command directly; if it errors but a fresh session
   works, the fix is `/new`, not a mesh change.
2. **Provider-key drift**: the mesh model is referenced as `custom:hecatev` (lowercase),
   but the `providers:` dict key is `hecateump`. A fresh session can hit
   `invalid_params` resolving the model even though the local llama-server (127.0.0.1,
   localhost loopback) answers fine (verified with a direct completion, `reasoning_effort: xhigh`). `custom:`
   vs `providers:` drift is a latent config inconsistency — worth reconciling so the
   bridge doesn't depend on a session quirk.

## Latency note

A one-word turn took ~64 s over LoRa (received → reply dispatched) — that's the local
Qwen model reasoning, not an error or a bridge fault. Built-in cost of this radio link.

## Live adapter test 2026-10-26 — inbound works, OUTBOUND DELIVERY DOES NOT FIRE

Commissioned the platform adapter (NOT the bridge) against the live shared rnsd instance,
display name `gory_adapter_test`, fresh identity. Status: the adapter receives, runs the
model, PERSISTS the reply — but the gateway never calls `adapter.send()`.

Verified good:
- Inbound: `hello` / `rns test message` reached the adapter (`platform=reticulum`), passed
  ACL, drove a real model turn (session store shows replies "hey" / "Got it, round-trip
  works." with reasoning).
- Model: fires on the GPU and answers correctly.
- **Outbound transport works**: a direct probe sent from the adapter's own
  `ReticulumTransport` (shared instance, `require_shared_instance=True`) to the peer hash
  returned `True`, `last_send_was_direct=True`, and the peer RECEIVED it over the radio.

The defect (gateway — adapter handoff): the reply is computed and persisted but never
transmitted. Evidence: NO `[Reticulum] Sending response` log line (base.py:4358 logs it
before every final send on every platform; it never fires for Reticulum), and the
`delivery_obligations` table stays empty (the ledgered send records before sending).
So `deliver_response → _send_final_text → send_final_ledgered` is not reached for the
Reticulum platform. Root cause is upstream of the transport: the gateway isn't invoking
the adapter's send on the settled reply. The adapter's own send() is proven functional by
the probe, so the fix lives in the gateway's delivery handoff, not in the adapter's
transport.

(First contact also drew a non-model "home channel is set" intro from the gateway on the
new channel; a second message + `/sethome` produced the persisted-but-unsent replies above.)

## File that describes the system

AGENTS.md routing is authoritative for repo shape; this finding records the runtime
topology delta (shared rnsd) that the specs predate. If specs are touched again, fold the
"RNS as a service" topology into the main spec rather than leaving it only here.