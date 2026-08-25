# Frontend Source

Vue 3 application source for the PSKit 2.0 workbench.

- `lib/api.ts`: cookie-based API client and SSE agent stream reader.
- `stores/auth.ts`: authenticated user state.
- `router.ts`: public, authenticated, and admin-only route guards.
- `views/AgentView.vue`: agent chat, tool events, RAG sources, artifacts, and task status.
- `views/TasksView.vue`, `ToolsView.vue`, `DoctorView.vue`: operational pages for model jobs, tool catalog, and runtime health.
