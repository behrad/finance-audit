#!/usr/bin/env bash
# Stop, then start. Args are passed to both (e.g. --docker).
d="$(dirname "$0")"
"$d/stop.sh" "$@" && sleep 1 && exec "$d/start.sh" "$@"
