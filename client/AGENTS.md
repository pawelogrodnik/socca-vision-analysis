# AGENTS.md — Client

These instructions apply to `client/`.

## Stack

- React + Vite + TypeScript.
- Keep the app lightweight and local-first.
- Prefer plain CSS modules/files or well-structured CSS classes. Do not use inline styles except for truly dynamic canvas/video geometry that cannot be expressed in CSS.
- do not let unused code hanging

## Component architecture

Do not let `App.tsx` become a dumping ground. As the app grows, split by feature:

```text
src/
  api/              API client functions and fetch helpers
  components/       reusable UI components
  features/
    matches/        upload, list, match selection
    calibration/    frame viewer, pitch point editor
    analysis/       analysis form, artifacts panel
    tracks/         future tracklet/player assignment UI
  hooks/            reusable React hooks
  lib/              pure helpers/utilities
  styles/           global styles and design tokens
  types/            shared TypeScript types
```

For this starter, it is acceptable that some code is still in `App.tsx`, but new work should move toward the structure above.

## Separation of concerns

- Components render UI and call callbacks.
- Hooks own UI state and side effects.
- API modules perform network calls only.
- Selectors/formatters/helpers live outside components.
- Do not mix video/canvas calculations directly with unrelated page layout code.
- Do not duplicate API URL construction in multiple components; keep it in one API layer.

## Styling rules

- Do not inline CSS for normal layout/visual styles.
- Use semantic class names, for example `match-card`, `calibration-panel`, `artifact-list`.
- Keep reusable layout utilities small and documented.
- Avoid magic colors scattered in components. Put reusable colors/tokens in CSS.
- Keep canvas drawing styles in one helper when the drawing grows beyond a few lines.

## TypeScript rules

- Avoid `any`. Use explicit domain types from `src/types.ts` or feature-local type files.
- Prefer discriminated unions for modes such as `adapter: 'yolo' | 'motion'`.
- API responses should have typed return values.
- Do not silently ignore failed requests. Surface errors in the UI.
- Keep form parsing explicit: convert strings to numbers at controlled boundaries.

## React rules

- Prefer controlled inputs for forms.
- Avoid unnecessary derived state; use `useMemo` or helper selectors where appropriate.
- Keep effects focused and dependency-safe.
- Do not put large async workflows directly inside render logic.
- If a component exceeds roughly 200-250 lines, split it.

## Canvas/video calibration rules

- Keep image-coordinate math separate from React event handling where possible.
- Store pitch points in image coordinates, not displayed CSS coordinates.
- Preserve click order consistently: `top-left`, `top-right`, `bottom-right`, `bottom-left`.
- Always make it possible to undo/clear calibration points.
- Do not assume the displayed canvas size equals the source image size.

## UX rules for this product

- The user should always know which match is selected.
- The user should see whether pitch config exists before running analysis.
- Long-running analysis should show a clear status.
- Generated artifacts should be easy to open/download.
- Use wording that distinguishes raw `tracker_id` from future real `player_id`.

## Testing/linting expectations

When adding tooling, prefer:

- ESLint for React/TypeScript correctness,
- Prettier for formatting,
- Vitest for pure helper functions and API helpers,
- component tests only when the UI becomes stable enough.

Do not add brittle tests around rapidly changing prototype UI unless they protect important behavior.
