# Evelyn Landing Page Target

## Goal

Rebuild the current page by preserving the user's sketch skeleton and only adding detail on top of that skeleton.

## Required Layout

- full-screen three-panel composition
- left rail as three stacked modules:
  - small yellow note card on top
  - inline handwritten-style yellow status text in the middle
  - large orange expansion card at the bottom
- center stage as the dominant area with a thin top bar and one very large live model viewport
- right rail as two stacked modules:
  - compact profile and runtime status card on top
  - larger command console/input card on the bottom

## Color Mapping

- yellow boxes map to notes, reminders, and raw world-state annotations
- orange box maps to expandable future data blocks
- green/teal right-side boxes map to profile, runtime state, and command console
- dark green/black center viewport remains mostly open and is where the CSS Evelyn model sits
- keep the box colors from the sketch; add detail inside them rather than inventing new frame colors or moving the boxes

## Visual Direction

- dark teal/ink base with subtle beige influence in the background mix
- visible wireframe-style borders, especially pink/red outer rails
- yellow and orange callout boxes on the left rail
- keep the look closer to a control room mockup than a product hero page
- avoid marketing-landing-page copy blocks; the page should feel like an interface layout first
- the center viewport should dominate attention more than headers, text, or navigation
- preserve the sketch's empty space and box geometry; do not redesign the page into a different layout

## Center Stage

- top strip with Evelyn brand on the left and compact system pills on the right
- large empty viewport feel with the CSS Evelyn model centered inside it
- CSS-only Evelyn model placed inside the center viewport
- model should feel like a live 2.5D placeholder rather than a flat icon
- small overlay annotations are fine, but they must not overwhelm the viewport

## Right Rail

- upper block reserved for profile/identity status
- lower block styled as a command box
- include command chips or sample commands that feel derived from the Discord bot commands
- the lower command area should read like a console, not a marketing CTA

## Implementation Rules

- keep the page static under \`docs/\`
- no framework build step
- mobile layout can stack, but desktop should preserve the three-panel hierarchy
- JavaScript should stay light and only support small interactions such as chip-to-input filling and reveal timing
