# Design Principles

ChaosWing should feel like a focused analyst tool, not a generic admin panel and not a marketing page. The UI is dark, restrained, and intentional so the graph remains the visual center of gravity.

## Visual Hierarchy

- The graph canvas is the dominant surface because it is the core product interaction.
- The left panel guides the workflow: input first, controls second, summary third.
- The right panel stays quiet until a selection is made, then becomes the detail layer.
- Accent color is reserved for primary actions, focus states, and meaningful graph emphasis.

## Spacing Scale

The interface uses a tokenized spacing system from `0.25rem` through `3rem`. Cards, panel padding, chip gaps, and toolbar spacing all draw from that same scale so the app feels deliberate instead of stitched together.

## Color System

- Near-dark shell backgrounds keep visual noise low.
- Slightly elevated panel surfaces create depth without relying on heavy gradients.
- Node types each receive one distinct color, but edge lines stay more restrained.
- Success, warning, and danger colors are used sparingly for status and validation.

## Accessibility Expectations

- Text contrast should remain comfortably readable against all surfaces.
- Inputs, buttons, and chips must remain keyboard reachable.
- Visible focus states are required and should not be removed for aesthetic reasons.
- Empty, loading, and error states should communicate progress without relying on color alone.

## Interaction Principles

- Prefer progressive disclosure over always-visible detail.
- Hover can enhance discovery, but click must remain the primary path for inspection.
- Controls should update the graph immediately without a page refresh.
- Motion should support orientation, not distract from analysis.
- Reset and refresh actions must be easy to find because graph work is exploratory by nature.
