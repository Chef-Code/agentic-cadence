# Agentic Cadence Visual Identity

## Goal

Make the public repository more memorable and easier to understand at a glance while keeping the tone appropriate for a serious developer tool.

The approved direction is **Serious Infrastructure**: durable, precise, agent-neutral, and practical. The visual system should make the protocol feel trustworthy without turning the repository into a marketing site.

## Audience

Primary readers are engineers and agent operators evaluating whether Agentic Cadence can manage long-running coding-agent work. They need to quickly understand:

- what problem the protocol solves;
- how context handoff works;
- which states gate continuation;
- that the project is agent-neutral, not tied to one vendor;
- that the repository is safe to inspect and use.

## Visual Language

Use a restrained technical palette:

- Deep navy or charcoal for hero/background surfaces.
- Cyan and green accents for continuity, verified state, and successful handoff.
- Amber for coordination states.
- Red only for stop/fail-closed states.
- White or near-white surfaces for diagrams and dense docs.

The style should use crisp rectangular geometry, small-radius cards, compact badges, and clear line diagrams. Avoid decorative gradients, oversized marketing sections, mascot-style imagery, or visual metaphors that obscure the protocol.

## README Scope

The first visual pass should update `README.md` only where it improves comprehension:

- Add a stronger opening tagline: durable handoff and governed continuation for coding agents that outlive one chat window.
- Add compact badges for Python, license, CI, and package name.
- Add a short hero banner image from `docs/assets/`.
- Add a protocol diagram near the top: old context -> signed handoff -> clean square -> fresh agent.
- Add compact state visuals for `PLAY_ON`, `HUDDLE`, and `TIMEOUT`.
- Keep install and first-run commands visible without burying them under branding.

## Assets

Create assets under `docs/assets/`:

- `agentic-cadence-banner.svg`: repository hero/banner.
- `handoff-flow.svg`: protocol handoff diagram.
- `cadence-states.svg`: state summary visual for `PLAY_ON`, `HUDDLE`, and `TIMEOUT`.

SVG is the right format for repository docs because it is inspectable, lightweight, versionable, and renders directly on GitHub. Keep all assets static and dependency-free.

## Accessibility

All visual assets must remain understandable in GitHub light and dark themes:

- Use sufficient color contrast.
- Include readable text directly in SVGs only when it is large enough to render cleanly.
- Provide descriptive alt text in `README.md`.
- Do not rely on color alone; pair state colors with labels.

## Constraints

- Do not add generated binary files unless there is a clear need.
- Do not introduce a docs site or frontend build pipeline for this pass.
- Do not change CLI behavior or packaging metadata.
- Do not make the README substantially longer.
- Keep visual assets public-safe and agent-neutral.

## Verification

After implementation:

- Run `python scripts/public_release_audit.py --history`.
- Run `python scripts/validate_protocol.py`.
- Run `python -m unittest tests.test_ci_checks -v`.
- Run `git diff --check`.
- Manually inspect the README rendered on GitHub or a local Markdown preview to confirm assets display correctly.
