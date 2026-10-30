#!/bin/zsh
set -e
cd "${0:A:h}"
if curl --silent --fail --max-time 1 http://127.0.0.1:8777/api/bootstrap >/dev/null; then
  open http://127.0.0.1:8777
  echo "Visual Brain is already running. Opened it in your browser."
  exit 0
fi
python3 -c 'import requests, PIL' 2>/dev/null || {
  echo "Python dependencies are missing. Run: python3 -m pip install -r requirements.txt"
  read -k 1 "?Press any key to close."
  exit 1
}
python3 server.py &
server_pid=$!
trap 'kill $server_pid 2>/dev/null || true' EXIT INT TERM
for attempt in {1..50}; do
  if curl --silent --fail --max-time 1 http://127.0.0.1:8777/api/bootstrap >/dev/null; then
    open http://127.0.0.1:8777
    echo "Visual Brain is running at http://127.0.0.1:8777"
    echo "Keep this window open while you use the board. Press Control-C to stop."
    wait $server_pid
    exit $?
  fi
  sleep 0.1
done
echo "Visual Brain could not start. Check whether port 8777 is in use."
exit 1
