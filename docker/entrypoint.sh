#!/bin/sh
set -eu

python /app/scripts/init_db.py
exec uvicorn app.main:app --host 0.0.0.0 --port 8000