# Frontend Architecture

The frontend is a server-rendered Django shell with a graph-first browser runtime layered on top. The browser enhances the shell; it does not replace it.

## JavaScript Module Responsibilities

- `main.js`: bootstraps the page, coordinates modules, and renders summary, run, and stage state
- `api.js`: requests graph payloads and inspector partials from Django
- `state.js`: stores payload, controls, selection, loading, and error state
- `graph.js`: creates Cytoscape, maps payloads into nodes and edges, and runs layouts
- `graph-effects.js`: manages hover dimming, selection emphasis, and strongest-path focus
- `graph-toolbar.js`: wires the floating toolbar and keyboard shortcuts
- `controls.js`: handles the source form, filters, sample links, and reset behavior
- `inspector.js`: swaps server-rendered node and edge partials into the right rail
- `animations.js`: manages stage pulse and inspector transitions
- `utils.js`: small shared DOM and formatting helpers

## Template Structure

The dashboard template is split into three semantic zones:

- left rail: input, sample launches, filters, event summary, and saved run metadata
- center stage: the Cytoscape workspace plus overlays, HUD, and toolbar
- right inspector: Django-rendered selection details

Initial state is injected with Django `json_script`, which keeps the shell server-rendered without inline JavaScript logic.

## CSS Layering Strategy

- `tokens.css` defines colors, spacing, radius, typography, timing, and easing
- `base.css` defines document foundations
- `layout.css` owns shell geometry and responsive behavior
- `components.css` styles controls, buttons, filters, pills, summary surfaces, and saved-run elements
- `dashboard.css` styles the graph stage and inspector
- `motion.css` contains shimmers, transitions, and stage pulse behavior

## State Flow

1. The user submits a Polymarket URL or clicks a sample market.
2. `api.js` posts the URL to Django.
3. Django returns a persisted run payload with graph data, run metadata, and review output.
4. `state.js` stores the payload and current controls.
5. `graph.js` rebuilds the visible Cytoscape graph and reruns the selected layout.
6. Clicking a node or edge updates shared selection state.
7. `inspector.js` posts that selection back to Django partial endpoints and swaps the returned HTML into the inspector.

Filtering remains client-side for immediate interaction, but the saved run id and run review now come directly from Django, so the shell visibly reflects backend state rather than pretending persistence will exist later.
