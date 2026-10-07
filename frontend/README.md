# Hydro Twin frontend

React + TypeScript dashboard for the Hydro Twin platform, built with Vite and
MapLibre GL. It renders the dam inventory, terrain/flood map layers and the
dam digital-twin panels, and talks to the FastAPI backend.

## Commands

```bash
npm install
npm run dev       # dev server → http://localhost:5173
npm run build     # type-check (tsc -b) + production build → dist/
npm run lint      # ESLint
npm run preview   # preview the production build
```

## Notes

- The API base URL is `http://127.0.0.1:8000/api`
  (`src/services/api.ts`); start the backend first (see the root `README.md`).
- The backend only allows CORS from `http://localhost:5173`.

## Structure

```
src/
├── main.tsx / App.tsx     # app entry, routing/shell
├── pages/                 # route-level screens (Dashboard)
├── components/            # shared UI
│   ├── ScenarioPanel.tsx  # simulation scenario controls
│   ├── MapToolBar.tsx     # map toolbar
│   └── dam/               # dam digital-twin detail panels
├── map/FloodMap.tsx       # MapLibre GL map
├── services/api.ts        # axios client
├── types/                 # TypeScript models
├── utils/                 # helpers
└── assets/                # static images
```
