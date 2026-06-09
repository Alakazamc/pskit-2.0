# Frontend

Vue 3 workbench UI for PSKit 2.0.

## Pages

- `/`: public product overview.
- `/about`: public architecture overview.
- `/login`: username/password login.
- `/register`: open registration; first registered user becomes admin on the backend.
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

The A6000 host used during development currently does not have Node/npm installed, so build the frontend on a machine with Node 20+ and deploy the generated `dist/` separately, or install Node on A6000 before running `scripts/build_frontend.sh`.
