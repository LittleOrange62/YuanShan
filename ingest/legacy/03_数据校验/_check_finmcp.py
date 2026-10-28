#!/usr/bin/env python3
"""Check finmcp service and fin_data.db status from data_in workspace."""
import os, sys, subprocess, urllib.request, json

WORKING = "/app/working/workspaces/data_in"

def log(msg):
    print(f"[CHECK] {msg}")

# 1. Check finmcp HTTP endpoint
log("Checking finmcp HTTP endpoint...")
try:
    req = urllib.request.Request(
        "http://192.168.176.68:8765/mcp",
        data=b'{"jsonrpc":"2.0","method":"list_tools","id":1}',
        headers={"Content-Type": "application/json"}
    )
    resp = urllib.request.urlopen(req, timeout=5)
    log(f"HTTP {resp.status}: {resp.read().decode()[:200]}")
except Exception as e:
    log(f"Endpoint error: {e}")

# 2. Find all .db files in /app/working (excluding browser cache)
log("\nSearching for .db files...")
from pathlib import Path
db_files = []
for p in Path("/app/working").rglob("*.db"):
    if "browser" not in str(p) and "GPUCache" not in str(p):
        db_files.append(str(p))
db_files.sort()
for f in db_files:
    size = os.path.getsize(f) if os.path.isfile(f) else 0
    log(f"  {f}  ({size/1024:.1f} KB)")

# 3. Check sqlite3 availability
log("\nChecking sqlite3...")
try:
    r = subprocess.run(["which", "sqlite3"], capture_output=True, text=True, timeout=5)
    log(f"  which sqlite3: {r.stdout.strip() or 'not found'}")
    if r.stdout.strip():
        r2 = subprocess.run(["sqlite3", "--version"], capture_output=True, text=True, timeout=5)
        log(f"  version: {r2.stdout.strip()}")
except Exception as e:
    log(f"  sqlite3 check failed: {e}")

# 4. Try python sqlite3 module
log("\nTrying Python sqlite3 module...")
try:
    import sqlite3
    log(f"  sqlite3 module version: {sqlite3.sqlite_version}")
except Exception as e:
    log(f"  sqlite3 module error: {e}")

# 5. Try to open any found fin_*.db
log("\nAttempting to open fin_*.db files...")
for f in db_files:
    if "fin_" in os.path.basename(f):
        try:
            conn = sqlite3.connect(f)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 5")
            tables = cur.fetchall()
            log(f"  {os.path.basename(f)}: tables = {[t[0] for t in tables]}")
            conn.close()
        except Exception as e:
            log(f"  {os.path.basename(f)}: open error: {e}")

log("\n=== Check complete ===")
