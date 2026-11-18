"""Sandbox isolation check. Run INSIDE the judge container (as root, like production):
    docker compose run --rm judge python tests/sandbox_check.py
Each probe is a hostile student program; every one must be contained."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config, db, judge  # noqa: E402

db.init()  # creates /data/astra.db with 0600 if missing
T = [{"id": 1, "ordinal": 1, "input": "", "expected": None, "visibility": "hidden", "weight": 1}]
PROBES = {
    "read the database": ("python", f"print(open({str(config.DB_PATH)!r},'rb').read(16))"),
    "read root's environment": ("python", "print(open('/proc/1/environ').read()[:80])"),
    "network access": ("python", "import socket\ns=socket.create_connection(('1.1.1.1',80),timeout=3)\nprint('CONNECTED')"),
    "write outside workdir": ("python", "open('/app/pwned','w').write('x')\nprint('WROTE')"),
    "fork bomb": ("c", "#include <unistd.h>\nint main(){for(;;)fork();}"),
    "escaped background child": ("python", "import os,time\nif os.fork()==0:\n    os.setsid(); time.sleep(2); open('/tmp/astra-judge/escaped','w')\nprint('parent done')"),
    "huge output": ("c", '#include <stdio.h>\nint main(){for(;;)puts("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA");}'),
    "memory hog (C)": ("c", "#include <stdlib.h>\n#include <string.h>\nint main(){char*p=malloc(1<<30);memset(p,1,1<<30);return p[5];}"),
    "C++ stdc++ header": ("cpp", "#include <bits/stdc++.h>\nint main(){std::vector<int> v{3,1,2};std::sort(v.begin(),v.end());std::cout<<v[0];}"),
}
assert judge.IS_ROOT, "run this as root inside the judge container"
print("sandbox:", judge.sandbox_mode())
bad = 0
for name, (lang, code) in PROBES.items():
    r = judge.run_job({"language": lang, "code": code, "time_limit_ms": 1000, "memory_mb": 128}, T, Path("/tmp/astra-judge/probe"), uid=config.JUDGE_UID_BASE + 99)
    res = r["results"][0] if r["results"] else {}
    out = (res.get("output") or "")[:120].replace("\n", " ")
    leaked = any(x in out for x in ("SQLite", "CONNECTED", "WROTE", "PATH=", "HOSTNAME"))
    if name == "C++ stdc++ header":
        leaked = out.strip() != "1"
    bad += leaked
    print(f"{'LEAK' if leaked else 'ok  '}  {name:28s} verdict={r['verdict']:4s} {res.get('verdict', '')}  out={out[:60]!r} err={((res.get('stderr') or r['compile_output'] or '').strip().splitlines() or [''])[-1][:80]!r}")
import time
time.sleep(4)
print("ok    escaped child killed" if not Path("/tmp/astra-judge/escaped").exists() else "LEAK  escaped child survived")
print("ok    /app untouched" if not Path("/app/pwned").exists() else "LEAK  wrote into /app")
sys.exit(1 if bad else 0)
