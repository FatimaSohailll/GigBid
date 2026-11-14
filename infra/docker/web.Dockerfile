# GigBid web app (Next.js). Build from the repository root:
#   docker build -f infra/docker/web.Dockerfile -t gigbid-web .
# The browser talks only to this server; /api/* is forwarded to the API, so the session cookie
# stays first-party. Next.js fixes that forward address at build time, hence the build argument.
FROM node:24.11.0-alpine AS build
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web ./
ARG API_URL=http://api:8000
ENV API_URL=$API_URL NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:24.11.0-alpine
WORKDIR /web
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY --from=build /web/package.json /web/package-lock.json ./
RUN npm ci --omit=dev
COPY --from=build /web/.next ./.next
USER node
EXPOSE 3000
CMD ["npx", "next", "start", "-p", "3000", "-H", "0.0.0.0"]
