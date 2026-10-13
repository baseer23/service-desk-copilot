#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python - "${1:-}" <<'PY'
import json
import os
import shutil
import signal
import subprocess
import sys
import uuid
from pathlib import Path
from dotenv import set_key
from backend.app.core.config import Settings
from backend.app.core.azure_usage import AzureUsageGuard
s=Settings()
guard=AzureUsageGuard(s)
guard.disable()
env=Path('.env')
if env.exists():
    backup=guard.path.parent / ('emergency-env-backup-'+uuid.uuid4().hex+'.private')
    shutil.copyfile(env,backup)
    backup.chmod(0o600)
    set_key(str(env),'AZURE_EMERGENCY_DISABLE','true')
    env.chmod(0o600)
def process(pid):
    value=subprocess.run(['ps','-p',str(pid),'-o','ppid=,args='],capture_output=True,text=True,check=False)
    parts=value.stdout.strip().split(None,1)
    return (int(parts[0]),parts[1]) if len(parts)==2 else (0,'')
path=Path(sys.argv[1]) if sys.argv[1] else None
stopped=False
if path and path.is_file():
    pids=json.loads(path.read_text())
    parent=int(pids['parent'])
    _,command=process(parent)
    if 'bash scripts/dev.sh' in command:
        for name,expected in [('backend','backend.app.main:app'),('frontend','node_modules/.bin/vite')]:
            pid=int(pids[name])
            ppid,args=process(pid)
            if ppid==parent and expected in args:
                os.kill(pid,signal.SIGTERM)
        os.kill(parent,signal.SIGTERM)
        stopped=True
print(json.dumps({'azure_usage_disabled':True,'verified_dev_servers_stopped':stopped}))
PY
