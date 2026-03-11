# Frontend Architecture

The frontend is a server-rendered Django shell with a graph-first browser runtime layered on top. The browser enhances the shell; it does not replace it.

## JavaScript Module Responsibilities

- `main.js`: bootstraps the page, coordinates modules, and renders summary, run, and stage state
- `api.js`: requests graph payloads, saved runs, review actions, export payloads, and share URLs
- `state.js`: stores payload, controls, selection, loading, and error state
- `graph.js`: creates Cytoscape, maps payloads into nodes and edges, and runs layouts
- `graph-effects.js`: manages hover dimming, selection emphasis, and strongest-path focus
- `graph-toolbar.js`: wires the floating toolbar and keyboard shortcuts
- `controls.js`: handles the source form, filters, sample links, and reset behavior
- `inspector.js`: renders node and edge profiles directly from graph payload data
- `animations.js`: manages stage pulse and inspector transitions
- `utils.js`: small shared DOM and formatting helpers

## Template Structure

The dashboard template is split into three semantic zones:

- left rail: input, sample launches, filters, event summary, and saved run metadata
- center stage: the Cytoscape workspace plus overlays, HUD, and toolbar
- right inspector: live selection details rendered from the active graph payload

Initial state is injected with Django `json_script`, which keeps the shell server-rendered without inline JavaScript logic.

## CSS Layering Strategy

- `tokens.css` defines colors, spacing, radius, typography, timing, and easing
- `base.css` defines document foundations
- `layout.css` owns shell geometry and responsive behavior
- `components.css` styles shared controls, buttons, filters, chips, cards, and input elements
- `panels.css` styles panel-specific surfaces such as summaries, the inspector, legends, and loading steps
- `overlays.css` styles the run-history drawer, shortcut overlay, toast system, and HUD utility pills
- `dashboard.css` styles the graph stage and page-specific stage states
- `motion.css` contains shimmers, transitions, and stage pulse behavior

## State Flow

1. The user submits a Polymarket URL or clicks a sample market.
2. `api.js` posts the URL to Django.
3. Django returns a persisted run payload with graph data, run metadata, and review output.
4. `state.js` stores the payload and current controls.
5. `graph.js` rebuilds the visible Cytoscape graph and reruns the selected layout.
6. Hovering or clicking a node or edge updates shared selection state.
7. `inspector.js` renders the current selection directly into the inspector for immediate feedback.

Filtering remains client-side for immediate interaction, while saved runs, run review data, and URL resolution still come directly from Django.
