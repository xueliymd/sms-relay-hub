#!/bin/sh
set -eu

alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port "${APP_PORT:-39200}" --workers 1
