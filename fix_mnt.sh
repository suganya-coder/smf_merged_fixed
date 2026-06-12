#!/usr/bin/env bash
# Fix for /mnt/storage path
set -e

SMF_DIR="/mnt/storage/Downloads/mouse/smmmm/t/kavi/kavithai/smf/he/smf"
FRONTEND="$SMF_DIR/frontend"

echo "Target : $SMF_DIR"
echo "Frontend: $FRONTEND"
echo ""

if [ ! -d "$FRONTEND" ]; then
  echo "❌ Frontend dir not found at $FRONTEND"
  exit 1
fi

# ── Backup ──────────────────────────────────────────────────
BACKUP="$FRONTEND/backup_dob_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP"
for f in index.html app.js style.css; do
  [ -f "$FRONTEND/$f" ] && cp "$FRONTEND/$f" "$BACKUP/$f" && echo "  backed up $f"
done
echo ""

# ── Check state of files ────────────────────────────────────
echo "── Current state ───────────────────────────────────────"
grep -q "DD-MM-YYYY\|Date of Birth\|adminDobPicker" "$FRONTEND/index.html" 2>/dev/null \
  && echo "  index.html : OLD (DOB UI)" \
  || echo "  index.html : already NEW (password UI)"

grep -qE "^function dobToISO|^function onAdminDobInput" "$FRONTEND/app.js" 2>/dev/null \
  && echo "  app.js     : OLD (DOB functions active)" \
  || echo "  app.js     : already NEW (DOB commented out)"
echo ""

# ── The HOME smf has the fixed files — copy them over ───────
HOME_SMF="/home/lenovo/Downloads/mouse/smmmm/t/kavi/kavithai/smf/he/smf"
HOME_FRONTEND="$HOME_SMF/frontend"

echo "── Copying fixed files from $HOME_FRONTEND ─────────────"
for f in index.html app.js style.css; do
  if [ -f "$HOME_FRONTEND/$f" ]; then
    cp "$HOME_FRONTEND/$f" "$FRONTEND/$f"
    echo "  ✔  copied $f"
  else
    echo "  ⚠  $f not found in $HOME_FRONTEND"
  fi
done
echo ""

# ── Remove stale DOB backup files ───────────────────────────
for stale in indexiingss.html app_ingss.js; do
  if [ -f "$FRONTEND/$stale" ]; then
    rm "$FRONTEND/$stale"
    echo "  🗑  removed $FRONTEND/$stale"
  fi
done

# ── Verify ──────────────────────────────────────────────────
echo ""
echo "── Verification ────────────────────────────────────────"
PASS=true

grep -q "DD-MM-YYYY\|Date of Birth\|adminDobPicker" "$FRONTEND/index.html" 2>/dev/null \
  && { echo "  ❌ index.html still has DOB UI"; PASS=false; } \
  || echo "  ✅ index.html — no DOB fields"

grep -q 'type="password"' "$FRONTEND/index.html" 2>/dev/null \
  && echo "  ✅ index.html — password field present" \
  || { echo "  ❌ index.html — password field MISSING"; PASS=false; }

grep -q 'fa-eye' "$FRONTEND/index.html" 2>/dev/null \
  && echo "  ✅ index.html — eye toggle present" \
  || { echo "  ❌ index.html — eye toggle MISSING"; PASS=false; }

grep -qE "^function dobToISO|^function onAdminDobInput" "$FRONTEND/app.js" 2>/dev/null \
  && { echo "  ❌ app.js still has active DOB functions"; PASS=false; } \
  || echo "  ✅ app.js — DOB functions inactive"

# ── Kill everything and restart from mnt ────────────────────
echo ""
echo "── Restarting server from mnt/storage ──────────────────"
pkill -f "python.*main\.py" 2>/dev/null && echo "  stopped old server(s)" || echo "  (no old server running)"
sleep 2

cd "$SMF_DIR"
echo "4" | nohup python main.py > /tmp/edutrack_mnt.log 2>&1 &
echo "  Server PID: $!"
echo "  Log: /tmp/edutrack_mnt.log"

echo "  Waiting for server..."
for i in $(seq 1 20); do
  sleep 1
  CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/app 2>/dev/null || echo "000")
  if [ "$CODE" = "200" ]; then
    echo "  ✅ Up on port 8000"
    break
  fi
  CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8001/app 2>/dev/null || echo "000")
  if [ "$CODE" = "200" ]; then
    echo "  ✅ Up on port 8001"
    break
  fi
  echo "  ... ($i/20)"
done

# ── Live check ───────────────────────────────────────────────
echo ""
echo "── Live check ──────────────────────────────────────────"
for port in 8000 8001; do
  CONTENT=$(curl -s http://localhost:$port/app 2>/dev/null || echo "")
  if [ -n "$CONTENT" ]; then
    echo -n "  Port $port: "
    echo "$CONTENT" | grep -q "DD-MM-YYYY\|Date of Birth" \
      && echo "❌ OLD DOB UI" \
      || echo "$CONTENT" | grep -q 'type="password"' \
      && echo "✅ NEW password UI" \
      || echo "⚠ unknown"
  fi
done

echo ""
if $PASS; then
  echo "═══════════════════════════════════════════════════════"
  echo "  ✅ ALL CHECKS PASSED"
  echo "  Hard-refresh browser: Ctrl+Shift+R"
  echo "═══════════════════════════════════════════════════════"
else
  echo "═══════════════════════════════════════════════════════"
  echo "  ⚠  Some checks failed — see above"
  echo "  Backup at: $BACKUP"
  echo "═══════════════════════════════════════════════════════"
fi