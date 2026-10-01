# 0002. Pin RNS/LXMF versions; do not vendor their source

Status: Accepted
Source: `docs/specs/adapter/spec.md` §11

## Context

The adapter depends on `rns` and `lxmf`, both under the Reticulum License. That licence is
MIT-shaped with two extra conditions: no use in systems built to harm humans, and no use in
creating an AI/ML/LLM training dataset, direct or indirect. The actual licence text lives in
the source header (`RNS/__init__.py`), not in the wheel metadata, so tools that summarise
metadata understate the terms.

A public repository gets scraped, and vendored source inside it could end up feeding a
training corpus, which the licence explicitly forbids.

Separately, `pyproject.toml` declared the dependencies as ranges (`rns>=1.5.4,<2.0`,
`lxmf>=1.1.1,<2.0`). A range can resolve to a version whose source nobody here has read.

## Decision

Declare `rns` and `lxmf` as **exact pinned versions**, and upgrade by bumping the pin and
cutting a release. Do not copy RNS/LXMF source into this repository.

## Consequences

- A published version of the adapter always runs the RNS/LXMF it was tested against.
- The repository carries no third-party source, so it cannot become a training-corpus vector
  for code the licence forbids treating that way.
- Upgrades are deliberate: bump the pin, run the suite against the new version, cut a release.
- The exact-pin work is tracked as an implementation delta (spec §14 / card A-list).
