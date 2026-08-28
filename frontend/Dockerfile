# Frontend: build con pnpm (Corepack) y servido por nginx, que reenvía /api (HTTP y WebSocket) a la API.
FROM node:22-alpine AS build
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml .npmrc ./
COPY scripts ./scripts
RUN corepack pnpm install --frozen-lockfile
COPY . .
RUN corepack pnpm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
