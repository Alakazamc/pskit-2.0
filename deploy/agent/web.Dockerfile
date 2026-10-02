FROM node:22.21.1-bookworm-slim@sha256:25b3eb23a00590b7499f2a2ce939322727fcce1b15fdd69754fcd09536a3ae2c AS web-build
WORKDIR /app
COPY new_frontend/package.json new_frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY new_frontend/ ./
RUN VITE_AUTH_MODE=supabase VITE_API_BASE_URL=/api/v1 npm run build

FROM nginx:1.28.0-alpine@sha256:30f1c0d78e0ad60901648be663a710bdadf19e4c10ac6782c235200619158284
COPY deploy/agent/web.conf /etc/nginx/conf.d/default.conf
COPY --from=web-build /app/dist/ /usr/share/nginx/html/
EXPOSE 80
