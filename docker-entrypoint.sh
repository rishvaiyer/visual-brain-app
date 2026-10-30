#!/bin/sh
# Hands the data volume to the app's unprivileged user, then drops to it.
#
# Railway mounts its volume owned by root, after the image's build-time chown has
# run, so a container that simply declares a USER cannot write the database.
# Running the whole server as root would trade the isolation away instead.
set -e

DATA_DIR="${VISUAL_BRAIN_DATA_DIR:-/data}"
mkdir -p "$DATA_DIR"

if [ "$(id -u)" = "0" ]; then
  chown -R brain:brain "$DATA_DIR" 2>/dev/null || \
    echo "warning: could not take ownership of $DATA_DIR; writes may fail" >&2
  exec gosu brain "$@"
fi

exec "$@"
