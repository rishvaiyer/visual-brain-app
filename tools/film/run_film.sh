#!/bin/sh
# Films the demo end to end on a Linux box. Run from the repository root.
#   sh tools/film/run_film.sh <python with requests+Pillow> <out dir>
set -e
ROOT="$(pwd)"; OUT="${2:-/tmp/vb-film}"
case "${1:-python3}" in /*) PY="$1";; */*) PY="$ROOT/$1";; *) PY="${1:-python3}";; esac
rm -rf "$OUT"; mkdir -p "$OUT/data" "$OUT/site"
"$PY" tools/film/make_sample_site.py "$OUT/site" "$ROOT"
VISUAL_BRAIN_DATA_DIR="$OUT/data" "$PY" server.py > "$OUT/server.log" 2>&1 &
SERVER=$!
( cd "$OUT/site" && "$PY" -m http.server 8793 --bind 127.0.0.1 > "$OUT/site.log" 2>&1 ) &
SITE=$!
trap 'kill $SERVER $SITE 2>/dev/null || true' EXIT
for i in $(seq 1 40); do curl -sf http://127.0.0.1:8777/api/bootstrap >/dev/null && break; sleep .25; done
node tools/film/film.js extension "$OUT" "$OUT/data/pairing-key.txt"
ffmpeg -y -loglevel error -i "$OUT/demo.webm" -vf "fps=30,format=yuv420p" -c:v libx264 -crf 22 -preset slow -movflags +faststart -an "$OUT/demo.mp4"
ffmpeg -y -loglevel error -ss 00:00:21 -i "$OUT/demo.mp4" -frames:v 1 -q:v 3 "$OUT/demo-poster.jpg"
ls -la "$OUT/demo.mp4" "$OUT/demo-poster.jpg"
