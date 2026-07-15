#!/bin/sh
# Fix volume ownership (Fly mounts /data as root), then drop to the non-root
# app user.
set -e
if [ "$(id -u)" = "0" ]; then
    mkdir -p /data
    chown mm:mm /data
    exec gosu mm "$@"
fi
exec "$@"
