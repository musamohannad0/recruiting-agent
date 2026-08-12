# Interface Direction

This is an operating interface for one candidate and a long-running search agent.
It should feel like serious, well-made software—not an AI landing page.

## Principles

- The interface recedes. Search state, evidence, decisions, and next actions lead.
- Agentic behavior is communicated through status, provenance, checkpoints, and
  explanations. Never use a mascot, glowing object, magic language, or decorative AI motif.
- Prefer a flat hierarchy: page, section, row. Use cards only for genuinely independent
  objects, not as the default container for every piece of content.
- Use spacing, type weight, and rules before shadows, color fills, or rounded containers.
- Keep the palette neutral. Blue marks selection or a primary action; green, amber, and
  red are reserved for operational state.
- Motion confirms an action or explains a state change. No ambient animation.
- Copy is direct and factual. Say what the system is doing, what evidence it used, and
  what the candidate needs to decide.

## Component Rules

- Radius: 6–10px. Pills only for compact machine states.
- Shadow: none by default; one subtle elevation for transient overlays.
- Buttons: explicit labels, 36–40px height, immediate pending feedback.
- Lists over card grids for operational data; tabular numerals for counts and scores.
- Every async state has readable status text, not only a spinner.
- Preserve keyboard focus, reduced motion, and comfortable touch targets.
