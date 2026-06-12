#!/usr/bin/env bash
# ============================================================
# fix_frontend.sh  —  EduTrack Pro frontend sync fix
# Run from the directory that CONTAINS the smf/ folder:
#   bash fix_frontend.sh
# ============================================================
set -e

# ── 1. Locate smf/ ──────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SMF_DIR=""

# Try common locations
for candidate in \
    "$SCRIPT_DIR/smf" \
    "$SCRIPT_DIR" \
    "$HOME/smf" \
    "$HOME/Desktop/smf" \
    "$HOME/Documents/smf" \
    "$HOME/Downloads/smf"; do
  if [ -f "$candidate/config.py" ] && [ -f "$candidate/api.py" ]; then
    SMF_DIR="$candidate"
    break
  fi
done

if [ -z "$SMF_DIR" ]; then
  echo "❌  Cannot find smf/ folder (looked for config.py + api.py)."
  echo "    Run this script from the folder that contains smf/, or edit SMF_DIR= at the top."
  exit 1
fi

FRONTEND="$SMF_DIR/frontend"
echo "✅  Found backend at : $SMF_DIR"
echo "✅  Frontend path    : $FRONTEND"
echo ""

# ── 2. Sanity-check the frontend dir exists ──────────────────
if [ ! -d "$FRONTEND" ]; then
  echo "❌  $FRONTEND does not exist. Aborting."
  exit 1
fi

# ── 3. Backup old files (safe rollback) ──────────────────────
BACKUP="$FRONTEND/backup_dob_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP"
for f in index.html app.js style.css; do
  [ -f "$FRONTEND/$f" ] && cp "$FRONTEND/$f" "$BACKUP/$f" && echo "  backed up $f → $BACKUP/"
done
echo ""

# ── 4. Detect whether current files are the OLD DOB versions ─
OLD_INDEX=false
OLD_APPJS=false

grep -q "DD-MM-YYYY\|Date of Birth\|dobToISO\|adminDobPicker" "$FRONTEND/index.html" 2>/dev/null && OLD_INDEX=true || true
grep -qE "^(function dobToISO|function onAdminDobInput)" "$FRONTEND/app.js" 2>/dev/null && OLD_APPJS=true || true

echo "  index.html  DOB UI active : $OLD_INDEX"
echo "  app.js      DOB code active: $OLD_APPJS"
echo ""

# ── 5. Overwrite with correct (new) versions ─────────────────
# Strategy: if the *ingss backup files are newer/different, 
# the current files ARE the old ones → swap from backup names.
# Otherwise the archive already has correct files, nothing to swap.

FIXED_ANYTHING=false

if $OLD_INDEX; then
  if [ -f "$FRONTEND/indexiingss.html" ]; then
    # indexiingss is also old — the NEW one must be in a parent frontend/ dir
    # Check one level up
    PARENT_FRONTEND="$(dirname "$SMF_DIR")/frontend"
    if [ -f "$PARENT_FRONTEND/index.html" ] && ! grep -q "DD-MM-YYYY" "$PARENT_FRONTEND/index.html" 2>/dev/null; then
      cp "$PARENT_FRONTEND/index.html" "$FRONTEND/index.html"
      echo "  ✔  Copied NEW index.html from $PARENT_FRONTEND/"
    else
      # Both copies are old — remove DOB section inline
      echo "  ⚠  Both copies appear to be DOB versions."
      echo "     Patching index.html in-place to remove DOB fields..."
      # Sed: replace the DOB input block with a password input block
      python3 - "$FRONTEND/index.html" << 'PYFIX'
import re, sys
path = sys.argv[1]
html = open(path).read()

# Replace admin DOB field with password field
html = re.sub(
    r'(<label>\s*(?:Date of Birth.*?</label>).*?)(</div>\s*</div>)',
    '''<label>Password</label>
          <div class="lf-wrap" style="position:relative;">
            <i class="fa fa-lock" style="color:#4f8ef7"></i>
            <input id="adminPass" type="password" placeholder="Enter your password"
              style="flex:1;background:none;border:none;outline:none;font-size:.95rem;color:inherit;letter-spacing:.06em;"
              autocomplete="current-password"/>
            <button type="button" onclick="(function(btn){var inp=document.getElementById('adminPass');inp.type=inp.type==='password'?'text':'password';btn.querySelector('i').className='fa fa-'+(inp.type==='password'?'eye':'eye-slash');})(this)"
              style="background:none;border:none;cursor:pointer;padding:0 4px;color:#aaa;display:flex;align-items:center;transition:color .2s;"
              title="Toggle password visibility">
              <i class="fa fa-eye"></i>
            </button>
          </div>
          <p style="font-size:.74rem;color:#888;margin:.3rem 0 0">
            <i class="fa fa-circle-info"></i> Default — Admin: <b>Admin@123</b> &nbsp;·&nbsp; HOD: <b>Hod@123</b>
          </p>
        </div>''',
    html, flags=re.DOTALL)
open(path, 'w').write(html)
print("  Patched.")
PYFIX
    fi
  fi
  FIXED_ANYTHING=true
fi

if $OLD_APPJS; then
  PARENT_FRONTEND="$(dirname "$SMF_DIR")/frontend"
  if [ -f "$PARENT_FRONTEND/app.js" ] && ! grep -qE "^function dobToISO" "$PARENT_FRONTEND/app.js" 2>/dev/null; then
    cp "$PARENT_FRONTEND/app.js" "$FRONTEND/app.js"
    echo "  ✔  Copied NEW app.js from $PARENT_FRONTEND/"
    FIXED_ANYTHING=true
  else
    # Comment out active DOB functions in app.js
    echo "  ⚠  Commenting out active DOB functions in app.js..."
    python3 - "$FRONTEND/app.js" << 'PYFIX'
import re, sys
path = sys.argv[1]
js = open(path).read()
# Comment out top-level DOB helper functions
for fn in ['dobToISO', 'isoToDisplay', 'showDobHint', 'onAdminDobInput', 'onAdminDobPick', 'onFacDobInput', 'onFacDobPick']:
    # Match: function NAME(...) { ... } at top level (non-nested)
    pattern = rf'(^function {fn}\b.*?^\}})'
    js = re.sub(pattern, lambda m: '\n'.join('// ' + l for l in m.group(0).split('\n')), js, flags=re.MULTILINE|re.DOTALL)
open(path, 'w').write(js)
print("  Patched.")
PYFIX
    FIXED_ANYTHING=true
  fi
fi

# ── 6. Remove stale DOB backup files ──────────────────────────
for stale in indexiingss.html app_ingss.js; do
  if [ -f "$FRONTEND/$stale" ]; then
    rm "$FRONTEND/$stale"
    echo "  🗑  Removed stale file: $FRONTEND/$stale"
  fi
done
echo ""

# ── 7. Verify the fixed files ─────────────────────────────────
echo "── Verification ────────────────────────────────────────"
PASS=true

if grep -q "DD-MM-YYYY\|Date of Birth\|adminDobPicker" "$FRONTEND/index.html" 2>/dev/null; then
  echo "  ❌  index.html still contains DOB UI"
  PASS=false
else
  echo "  ✅  index.html  — no DOB fields"
fi

if grep -q 'type="password"' "$FRONTEND/index.html" 2>/dev/null; then
  echo "  ✅  index.html  — password field present"
else
  echo "  ❌  index.html  — password field MISSING"
  PASS=false
fi

if grep -q 'fa-eye' "$FRONTEND/index.html" 2>/dev/null; then
  echo "  ✅  index.html  — eye toggle present"
else
  echo "  ❌  index.html  — eye toggle MISSING"
  PASS=false
fi

if grep -qE "^function dobToISO|^function onAdminDobInput" "$FRONTEND/app.js" 2>/dev/null; then
  echo "  ❌  app.js still has active DOB functions"
  PASS=false
else
  echo "  ✅  app.js      — DOB functions inactive"
fi

echo ""

# ── 8. Kill old server and restart ───────────────────────────
echo "── Restarting server ───────────────────────────────────"
pkill -f "python.*main\.py" 2>/dev/null && echo "  stopped old server" || echo "  (no old server running)"
sleep 1

cd "$SMF_DIR"
echo "  Starting: python main.py (auto-selects API option 4)..."
# Pipe "4\n" to auto-select option 4 (API server)
echo "4" | nohup python main.py > /tmp/edutrack_server.log 2>&1 &
SERVER_PID=$!
echo "  Server PID: $SERVER_PID"
echo "  Log file  : /tmp/edutrack_server.log"

# Wait for server to be ready
echo "  Waiting for server..."
for i in $(seq 1 15); do
  sleep 1
  if curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/app | grep -q "200"; then
    echo "  ✅  Server is up at http://localhost:8000/app"
    break
  fi
  echo "  ... ($i/15)"
done

echo ""
echo "── Live check ──────────────────────────────────────────"
CONTENT=$(curl -s http://localhost:8000/app 2>/dev/null || echo "")
if echo "$CONTENT" | grep -q "DD-MM-YYYY\|Date of Birth\|adminDobPicker"; then
  echo "  ❌  LIVE: server still serving DOB UI"
elif echo "$CONTENT" | grep -q 'type="password"'; then
  echo "  ✅  LIVE: server serving NEW password UI"
else
  echo "  ⚠   Could not verify (server may still be starting)"
  echo "      Check: curl -s http://localhost:8000/app | grep -i password"
fi

echo ""
if $PASS; then
  echo "═══════════════════════════════════════════════════════"
  echo "  ✅  ALL CHECKS PASSED"
  echo "  Open: http://localhost:8000/app  (hard-refresh: Ctrl+Shift+R)"
  echo "═══════════════════════════════════════════════════════"
else
  echo "═══════════════════════════════════════════════════════"
  echo "  ⚠   Some checks failed — see above."
  echo "  Backed-up originals at: $BACKUP"
  echo "═══════════════════════════════════════════════════════"
fi