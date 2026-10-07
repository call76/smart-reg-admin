#!/bin/sh
cd "$(dirname "$0")"
python3 ml_service/api.py & python3 app.py & sleep 2
echo "KIZA-AGRI running at http://127.0.0.1:5000 (Ctrl+C to stop)"; wait
