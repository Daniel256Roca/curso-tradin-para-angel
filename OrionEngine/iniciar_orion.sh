#!/usr/bin/env bash
cd "$(dirname "$0")"
echo "Iniciando Orion Engine en http://localhost:8787"
(sleep 2 && xdg-open http://localhost:8787 2>/dev/null || open http://localhost:8787 2>/dev/null) &
python3 server.py
