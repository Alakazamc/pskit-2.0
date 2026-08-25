# Frontend

Vue 3 workbench UI for PSKit 2.0.

## Pages

- `/`: public product overview.
- `/about`: public architecture overview.
- `/login`: username/password login.
- `/register`: bootstrap registration; by default only the first user can register and becomes admin.
- `/agent`: authenticated Agent chat with sessions, tool events, artifacts, tasks, and RAG sources.
- `/tasks`: authenticated task list.
- `/tools`: authenticated tool catalog.
- `/admin/doctor`: admin-only runtime doctor.

## Development

```bash
npm install
npm run dev
```

The Vite dev server proxies `/api` to `http://127.0.0.1:10706`.

## Build

```bash
npm run build
```

Use Node 22 and `npm ci` for a lockfile-reproducible build. The production Docker
image builds the frontend automatically and serves the generated assets through
FastAPI.
