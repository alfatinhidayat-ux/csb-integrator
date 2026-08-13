#!/bin/bash
# Shim php: route artisan ke container backend (host tidak punya php).
if [ "$(pwd)" = "/home/csb-backend-api" ]; then
  exec docker exec -w /var/www/html csb-backend-api-app-1 php "$@"
fi
exec docker exec -w "$(pwd)" csb-backend-api-app-1 php "$@"
