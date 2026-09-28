#!/bin/sh
set -eu

mkdir -p /app/runtime/browser-profile /app/runtime/sessions
password_file=/app/runtime/vnc_password
if [ ! -s "$password_file" ]; then
    python -c 'import secrets; print(secrets.token_hex(8))' > "$password_file"
    chmod 600 "$password_file"
fi

Xvfb :99 -screen 0 1440x900x24 -ac +extension GLX +render -noreset &
sleep 1
x11vnc -display :99 -passwdfile "$password_file" -forever -shared -rfbport 5900 -quiet &
websockify --web=/usr/share/novnc 6080 localhost:5900 &

exec chromium \
    --no-sandbox \
    --disable-dev-shm-usage \
    --disable-gpu \
    --no-first-run \
    --disable-default-apps \
    --remote-debugging-address=0.0.0.0 \
    --remote-debugging-port=9222 \
    --remote-allow-origins=* \
    --user-data-dir=/app/runtime/browser-profile \
    --window-size=1400,820 \
    about:blank
