FROM node:24-bookworm-slim AS build
WORKDIR /app
COPY package.json bun.lock ./
RUN npm install --no-package-lock --no-audit --no-fund
COPY . .
ARG VITE_API_BASE_URL=http://localhost:8000
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npx vite build --config vite.docker.config.ts

FROM node:24-bookworm-slim
ENV NODE_ENV=production HOST=0.0.0.0 PORT=3000
WORKDIR /app
COPY --from=build /app/.output ./.output
USER node
EXPOSE 3000
CMD ["node", ".output/server/index.mjs"]
