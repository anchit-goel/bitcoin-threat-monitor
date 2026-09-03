# Financial Crime Investigation Dashboard & Google Maps Integration

## Overview

This release replaces the previous single-page frontend with the **Financial Crime Investigation Dashboard** built using React 19, TypeScript, Vite, and Tailwind CSS v4. It upgrades the Geographic view to use the **Google Maps API** for real-time cross-border transaction tracking.

---

## Key Changes

### 1. Frontend Replacement
- **Framework**: React 19 + TypeScript + Vite + Tailwind CSS v4.
- **Design Tokens**: Dark slate palette (`#0f1114` background, `#171a1f` panel fills, `#262d38` borders) with strict threat severity color coding (`#c9512a` Critical, `#b87c22` High, `#6e8faa` Medium, `#3e7a60` Low).
- **Core Views**:
  - **Investigation Board**: Actor cards with deterministic 3×3 dot-matrix glyphs, score pips, confidence ratings, and interactive connection line overlays.
  - **Actor-to-Actor Heatmap**: Pairwise transaction intensity matrix with hover tooltips and intensity shading.
  - **Wallet Dossier & Money Trail**: Detailed wallet overview with transaction sparklines, SHAP model decision attribution, and animated multi-hop money trail tracing.

### 2. Real-Time Google Maps API Integration
- Integrated `@googlemaps/js-api-loader` in [`src/GoogleGeoMap.tsx`](file:///Users/anchitgoel/Desktop/sih-hackathon/frontend/src/GoogleGeoMap.tsx).
- **Geodesic Flow Polylines**: Connected transaction origin and destination countries using curved geodesic paths.
- **Dynamic Styling**: Polyline thickness scales dynamically with BTC volume; stroke colors map to threat severity.
- **API Key & Fallback Support**: Configurable via `VITE_GOOGLE_MAPS_API_KEY` with an interactive map loader fallback.

### 3. Cleanup & Optimization
- Removed unused legacy files and redundant lockfiles (`pnpm-lock.yaml`).
- Configured Vite dev server to bind to port `5173`.
- Validated production build (`npm run build` succeeds cleanly in under 200ms).

---

## Verification & Server Access
- **Frontend Dashboard**: [http://localhost:5173/](http://localhost:5173/)
- **Backend FastAPI Service**: [http://localhost:8000](http://localhost:8000)
- **Build Status**: Verified clean build (`vite build` -> `dist/assets/index-BPhVfDzS.js`).
