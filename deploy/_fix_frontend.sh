#!/bin/bash
set -e
export PATH=/www/server/nodejs/v22.22.0/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

cd /www/wwwroot/excelmanus
git config --global --add safe.directory /www/wwwroot/excelmanus 2>/dev/null || true
git fetch https://github.com/kilolonion/excelmanus main && git reset --hard FETCH_HEAD
echo "[OK] git pull done"

cd web
# Copy standalone assets
mkdir -p .next/standalone/.next
rm -rf .next/standalone/.next/static .next/standalone/public
cp -r .next/static .next/standalone/.next/static 2>/dev/null || true
cp -r public .next/standalone/public 2>/dev/null || true
echo "[OK] standalone assets copied"

# Restart
pkill -f 'next-server' 2>/dev/null || true
sleep 2
PORT=3000 nohup node .next/standalone/server.js > /tmp/excelmanus-web.log 2>&1 &
sleep 3

# Verify
if ss -tlnp 2>/dev/null | grep -q ':3000'; then
  echo "[OK] frontend running on port 3000"
else
  echo "[WARN] frontend may not have started"
fi

curl -s -o /dev/null -w "icon.png: HTTP_%{http_code}\n" http://localhost:3000/icon.png
curl -s -o /dev/null -w "home: HTTP_%{http_code}\n" http://localhost:3000/
