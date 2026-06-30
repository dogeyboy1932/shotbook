# Handoff to Claude

## Summary
This handoff documents the UI and architecture changes made so far for the book-to-video reader experience.

## What changed

### 1. Frontend reader layout
- The reader workspace now uses a stronger two-panel layout on larger screens.
- The text reader remains the primary column.
- The studio/results panel sits beside it for query output and generation state.

### 2. Query output styling
- The query output is now shown as structured, contained sections instead of a single long blob of text.
- Each paragraph result is presented as an expandable panel.
- The scene handoff preview is shown as its own structured section.

### 3. Video / generation panel
- The video preview is now presented as a secondary panel beneath the query output.
- The UI clearly shows when a scene is ready for handoff to the GPU/rendering path.
- The panel includes:
  - query output
  - scene summary
  - world anchors
  - shot plan
  - video preview / render status

### 4. API layer direction
- The frontend API layer was updated to support a UI-first architecture.
- It prefers direct client-side calls when the relevant environment variables are present.
- It still preserves fallback support for the existing local API path during development.

### 5. Architecture decision
- We kept FastAPI in place as a safe fallback/orchestration layer.
- The current UI is now structured so it can work with either:
  - a direct client-side data/VM flow, or
  - the existing backend-driven flow.

## Current intended flow
1. User selects a passage in the reader.
2. The UI requests context/query data.
3. The UI can show the structured query output and scene handoff preview.
4. The user can trigger generation.
5. The video/rendering flow is represented in the UI as a secondary preview panel.

## Important note
The application is now set up so that the frontend is more visually structured and the generation/handoff state is easier to understand, while still allowing the existing backend architecture to remain available.

## Files touched
- frontend/src/pages/Reader.tsx
- frontend/src/components/ContextPanel.tsx
- frontend/src/api.ts

## Verification
The frontend was rebuilt successfully with:

```bash
cd frontend && bun run build
```
