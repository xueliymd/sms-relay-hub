FROM node:22-alpine AS web-build
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/ /app/
COPY deploy/entrypoint.sh /usr/local/bin/sms-relay-entrypoint
COPY --from=web-build /web/dist /app/app/static
RUN pip install --no-cache-dir . \
    && addgroup --system relay \
    && adduser --system --ingroup relay relay \
    && chmod 0555 /usr/local/bin/sms-relay-entrypoint \
    && chown -R relay:relay /app
USER relay
EXPOSE 39200
ENTRYPOINT ["/usr/local/bin/sms-relay-entrypoint"]
