"""Sandboxed compile + run. Never imported by the web server process for execution;
only app/worker.py (a separate process / container) calls run_job().

Isolation layers (strongest available is used automatically):
  * separate worker process, clean environment (no secrets), private temp dir per job
  * rlimits: CPU, address space, file size, processes, open files, no core dumps
  * when the worker runs as root (Docker/systemd): code runs as a dedicated unprivileged uid per worker slot,
    the DB file is 0600 root so it is unreadable, and every leftover process of that uid is killed after the job
  * network: docker-compose runs the judge container with network_mode: none; on Linux-as-root we also unshare
    a fresh network namespace per run.
"""
import os
try:
    import resource
except ImportError:  # Windows: limits come from Job Objects (winjob.py)
    resource = None
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

IS_LINUX = sys.platform.startswith("linux")
IS_WIN = sys.platform == "win32"
IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0

# time_factor: slower runtimes get proportionally more time. as_limit: False where the runtime reserves
# huge virtual memory up front (JVM, V8) and memory is capped with runtime flags instead.
LANGS = {
    "c": {"name": "C (GCC, C11)", "file": "main.c", "compile": ["gcc", "-O2", "-std=c11", "-pipe", "-o", "main", "main.c", "-lm"],
          "run": ["./main"], "time_factor": 1.0, "as_limit": True, "monaco": "c"},
    "cpp": {"name": "C++ (G++, C++17)", "file": "main.cpp", "compile": ["g++", "-O2", "-std=c++17", "-pipe", "-o", "main", "main.cpp"],
            "run": ["./main"], "time_factor": 1.0, "as_limit": True, "monaco": "cpp"},
    "python": {"name": "Python 3", "file": "main.py", "compile": None, "run": ["python3", "-I", "-S", "main.py"],
               "time_factor": 2.5, "as_limit": True, "monaco": "python"},
    "java": {"name": "Java", "file": "Main.java", "compile": ["javac", "-J-Xmx512m", "Main.java"],
             "run": ["java", "-Xss64m", "-XX:+UseSerialGC", "-Xmx{mem}m", "Main"], "time_factor": 2.0, "as_limit": False, "monaco": "java"},
    "javascript": {"name": "JavaScript (Node.js)", "file": "main.js", "compile": None,
                   "run": ["node", "--max-old-space-size={mem}", "main.js"], "time_factor": 2.0, "as_limit": False, "monaco": "javascript"},
    "go": {"name": "Go", "file": "main.go", "compile": ["go", "build", "-o", "main", "main.go"],
           "run": ["./main"], "time_factor": 1.0, "as_limit": False, "monaco": "go"},
    "rust": {"name": "Rust", "file": "main.rs", "compile": ["rustc", "-O", "--edition", "2021", "-o", "main", "main.rs"],
             "run": ["./main"], "time_factor": 1.0, "as_limit": True, "monaco": "rust"},
    # prompt-engineering challenge: the "code" is a prompt; each test input is sent to the local AI and its reply is judged like stdout
    "prompt": {"name": "Prompt (AI challenge)", "file": "prompt.txt", "compile": None, "run": None, "time_factor": 1.0, "as_limit": False, "monaco": "markdown", "ai": True},
}
# predict-the-output / code-tracing questions: the student types the answer; it is compared with the expected text (nothing runs)
LANGS["answer"] = {"name": "Written answer", "file": "answer.txt", "compile": None, "run": None, "time_factor": 1.0, "as_limit": False, "monaco": "plaintext", "text": True}
CODE_LANGS = [k for k, v in LANGS.items() if not v.get("ai") and not v.get("text")]
ANSWER_QTYPES = ("output", "trace", "mcq")

TEMPLATES = {
    "c": '#include <stdio.h>\n\nint main(void) {\n    // read input from stdin, print answer to stdout\n    int n;\n    if (scanf("%d", &n) != 1) return 0;\n    printf("%d\\n", n);\n    return 0;\n}\n',
    "cpp": '#include <bits/stdc++.h>\nusing namespace std;\n\nint main() {\n    ios::sync_with_stdio(false);\n    cin.tie(nullptr);\n    int n;\n    if (!(cin >> n)) return 0;\n    cout << n << "\\n";\n    return 0;\n}\n',
    "python": "import sys\n\ndef main():\n    data = sys.stdin.read().split()\n    # write your solution here\n    print(data[0] if data else '')\n\nmain()\n",
    "java": "import java.util.*;\nimport java.io.*;\n\npublic class Main {\n    public static void main(String[] args) throws IOException {\n        BufferedReader br = new BufferedReader(new InputStreamReader(System.in));\n        String line = br.readLine();\n        System.out.println(line == null ? \"\" : line.trim());\n    }\n}\n",
    "go": "package main\n\nimport (\n\t\"bufio\"\n\t\"fmt\"\n\t\"os\"\n)\n\nfunc main() {\n\treader := bufio.NewReader(os.Stdin)\n\tvar n int\n\tfmt.Fscan(reader, &n)\n\tfmt.Println(n)\n}\n",
    "rust": "use std::io::{self, Read};\n\nfn main() {\n    let mut s = String::new();\n    io::stdin().read_to_string(&mut s).unwrap();\n    let mut it = s.split_whitespace();\n    let n: i64 = it.next().map(|x| x.parse().unwrap()).unwrap_or(0);\n    println!(\"{}\", n);\n}\n",
    "prompt": "You are a precise assistant. The user message is the input of the problem.\nReply with ONLY the required output, nothing else.\n",
    "javascript": "const data = require('fs').readFileSync(0, 'utf8').trim().split(/\\s+/);\n// write your solution here\nconsole.log(data[0] ?? '');\n",
}

OUTPUT_LIMIT = 16 * 1024 * 1024
VERDICT_NAMES = {"AC": "Accepted", "WA": "Wrong Answer", "CE": "Compilation Error", "RE": "Runtime Error",
                 "TLE": "Time Limit Exceeded", "MLE": "Memory Limit Exceeded", "OLE": "Output Limit Exceeded",
                 "IE": "Internal Judge Error", "PENDING": "Pending", "OK": "Ran"}


class JudgeInfraError(Exception):
    """Something wrong with the judge itself (not the student's code). Never turned into WA."""


def student_python():
    return os.environ.get("JUDGE_PYTHON") or (sys.executable if IS_WIN else "python3")


def available_languages():
    out = {}
    for k, L in LANGS.items():
        if L.get("text"):
            out[k] = True
            continue
        if L.get("ai"):
            from . import ai
            out[k] = ai.enabled()
            continue
        tool = (L["compile"] or L["run"])[0]
        out[k] = bool(shutil.which(student_python() if tool == "python3" else tool))
    return out


def sandbox_mode():
    if IS_WIN:
        return "Windows job limits (CPU time, memory, processes, output); network blocked by firewall rule if enabled in the launcher"
    if IS_ROOT and IS_LINUX:
        return "isolated (dedicated uid + rlimits" + (", no network)" if _net_isolated() else ", network namespace)")
    return "reduced (rlimits only - development mode; run the judge via Docker for full isolation)"


def _net_isolated():
    from . import config
    return config.JUDGE_NETWORK_ISOLATED


def _preexec(cpu_s, mem_mb, as_limit, uid):
    def fn():
        os.setsid()
        lim = [
            (resource.RLIMIT_CPU, (cpu_s, cpu_s + 1)),
            (resource.RLIMIT_FSIZE, (OUTPUT_LIMIT, OUTPUT_LIMIT)),
            (resource.RLIMIT_CORE, (0, 0)),
            (resource.RLIMIT_NOFILE, (64, 64)),
        ]
        if as_limit and mem_mb:
            lim.append((resource.RLIMIT_AS, (mem_mb * 1024 * 1024, mem_mb * 1024 * 1024)))
        if uid is not None:
            lim.append((resource.RLIMIT_NPROC, (64, 64)))
        for r, v in lim:
            try:
                resource.setrlimit(r, v)
            except (ValueError, OSError):
                pass
        if uid is not None:
            if IS_LINUX and not _net_isolated() and hasattr(os, "unshare"):
                try:
                    os.unshare(os.CLONE_NEWNET)
                except OSError:
                    pass
            os.setgroups([])
            os.setgid(uid)
            os.setuid(uid)
    return fn


def _kill_uid(uid):
    """As root: kill every process still owned by the sandbox uid (escaped forks, daemons)."""
    if uid is None:
        return
    pid = os.fork()
    if pid == 0:
        try:
            os.setgid(uid)
            os.setuid(uid)
            for _ in range(200):  # repeat: a fork bomb can spawn new processes while we sweep
                os.kill(-1, signal.SIGKILL)
                time.sleep(0.005)
        except OSError:  # ESRCH: nothing left owned by this uid
            pass
        os._exit(0)
    os.waitpid(pid, 0)


def execute(cmd, cwd, stdin_path, wall_s, cpu_s, mem_mb, as_limit, uid):
    """Run one process. Returns dict(exit, signal, time_ms, mem_kb, timed_out, stdout, stderr, ole)."""
    if IS_WIN:
        from . import winjob
        return winjob.execute(cmd, cwd, stdin_path, wall_s, cpu_s, mem_mb, as_limit, uid, OUTPUT_LIMIT)
    out_p, err_p = Path(cwd) / ".out", Path(cwd) / ".err"
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(cwd), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
           "GOCACHE": str(Path(cwd) / ".gocache"), "GOPATH": str(Path(cwd) / ".go"), "GO111MODULE": "off", "GOTOOLCHAIN": "local"}
    if not IS_LINUX:  # macOS dev: keep homebrew/java paths
        env["PATH"] = os.environ.get("PATH", env["PATH"])
        if os.environ.get("JAVA_HOME"):
            env["JAVA_HOME"] = os.environ["JAVA_HOME"]
    with open(stdin_path, "rb") as fi, open(out_p, "wb") as fo, open(err_p, "wb") as fe:
        if uid is not None:
            for p in (out_p, err_p):
                os.chown(p, uid, uid)
        t0 = time.monotonic()
        try:
            p = subprocess.Popen(cmd, cwd=cwd, stdin=fi, stdout=fo, stderr=fe, env=env, close_fds=True,
                                 preexec_fn=_preexec(cpu_s, mem_mb, as_limit, uid))
        except OSError as e:
            raise JudgeInfraError(f"cannot start {cmd[0]}: {e}")
        timed_out = False
        status = ru = None
        while True:
            pid, status, ru = os.wait4(p.pid, os.WNOHANG)
            if pid:
                break
            if time.monotonic() - t0 > wall_s:
                timed_out = True
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except OSError:
                    pass
                _, status, ru = os.wait4(p.pid, 0)
                break
            time.sleep(0.005)
        p.returncode = 0  # reaped by wait4; stop Popen from trying again
        wall_ms = int((time.monotonic() - t0) * 1000)
    try:
        os.killpg(p.pid, signal.SIGKILL)  # stray children in the group
    except OSError:
        pass
    cpu_ms = int((ru.ru_utime + ru.ru_stime) * 1000)
    mem_kb = ru.ru_maxrss // (1024 if sys.platform == "darwin" else 1)
    sig = os.WTERMSIG(status) if os.WIFSIGNALED(status) else 0
    ext = os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1
    size = out_p.stat().st_size
    return {
        "exit": ext, "signal": sig, "time_ms": max(cpu_ms, 0), "wall_ms": wall_ms, "mem_kb": mem_kb, "timed_out": timed_out,
        "stdout": out_p.read_bytes()[:OUTPUT_LIMIT].decode("utf-8", "replace"),
        "stderr": err_p.read_bytes()[:8000].decode("utf-8", "replace"),
        "ole": size >= OUTPUT_LIMIT or sig == getattr(signal, "SIGXFSZ", -1),
    }


def normalize(s: str) -> str:
    lines = [ln.rstrip() for ln in s.replace("\r\n", "\n").split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def classify(r, tl_ms, mem_mb, lang):
    if r["ole"]:
        return "OLE"
    if r["timed_out"] or r["time_ms"] > tl_ms or r["signal"] == getattr(signal, "SIGXCPU", -1):
        return "TLE"
    near_mem = mem_mb and r["mem_kb"] >= mem_mb * 1024 * 0.95
    if "MemoryError" in r["stderr"] or "OutOfMemoryError" in r["stderr"] or "heap out of memory" in r["stderr"]:
        return "MLE"
    if r["signal"] or r["exit"] != 0:
        return "MLE" if near_mem or (lang in ("c", "cpp") and "bad_alloc" in r["stderr"]) else "RE"
    if near_mem and mem_mb:
        return "MLE"
    return None


def run_job(job, tests, workdir: Path, uid=None):
    """job: dict(language, code, time_limit_ms, memory_mb). tests: list of dict(id, ordinal, input, expected, visibility, weight).
    expected=None means "just capture output" (custom-input runs / filling expected outputs from the reference).
    Returns dict(verdict, compile_output, results[list], time_ms, memory_kb)."""
    lang = job["language"]
    L = LANGS.get(lang)
    if not L:
        raise JudgeInfraError(f"unknown language {lang}")
    if not available_languages().get(lang):
        raise JudgeInfraError(f"{L['name']} toolchain is not installed on the judge")
    if L.get("text"):
        return _answer_job(job, tests)
    if L.get("ai"):
        return _prompt_job(job, tests)
    if workdir.exists():
        shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True)
    try:
        if uid is not None:
            os.chown(workdir, uid, uid)
        os.chmod(workdir, 0o700)
        src = workdir / L["file"]
        src.write_text(job["code"])
        stdin = workdir / ".in"
        stdin.write_text("")
        if uid is not None:
            for f in (src, stdin):
                os.chown(f, uid, uid)
        mem = int(job["memory_mb"])
        if IS_WIN:  # .exe outputs, absolute paths, bundled python for student code
            fix = lambda a: str(workdir / "main.exe") if a == "./main" else "main.exe" if a == "main" else student_python() if a == "python3" else a
            L = {**L, "compile": [fix(a) for a in L["compile"]] if L["compile"] else None, "run": [fix(a) for a in L["run"]]}
        if L["compile"]:
            r = execute(L["compile"], workdir, stdin, 60, 60, 2048, lang in ("c", "cpp"), uid)
            if r["timed_out"] or r["exit"] != 0:
                msg = (r["stderr"] or r["stdout"] or ("compilation timed out" if r["timed_out"] else "compilation failed"))
                return {"verdict": "CE", "compile_output": _clean(msg, workdir)[:6000], "results": [], "time_ms": 0, "memory_kb": 0}
        tl = int(job["time_limit_ms"] * L["time_factor"])
        cpu_s = tl // 1000 + 1
        cmd = [a.replace("{mem}", str(mem)) for a in L["run"]]
        as_mem = mem + (64 if lang == "python" else 16)  # interpreter baseline
        results, worst, tmax, mmax = [], None, 0, 0
        for t in tests:
            stdin.write_text(t["input"] or "")
            r = execute(cmd, workdir, stdin, tl / 1000 * 3 + 3, cpu_s, as_mem, L["as_limit"], uid)  # CPU rlimit stops busy loops; wall cap only catches sleepers (generous: first exec can be slow)
            v = classify(r, tl, mem, lang)
            if v is None:
                if t.get("expected") is None:
                    v = "OK"
                else:
                    v = "AC" if normalize(r["stdout"]) == normalize(t["expected"]) else "WA"
            tmax, mmax = max(tmax, r["time_ms"]), max(mmax, r["mem_kb"])
            results.append({"test_case_id": t.get("id"), "ordinal": t["ordinal"], "visibility": t.get("visibility", "hidden"),
                            "weight": t.get("weight", 1), "verdict": v, "time_ms": r["time_ms"], "memory_kb": r["mem_kb"],
                            "output": r["stdout"][:20000], "stderr": _clean(r["stderr"], workdir)[:2000]})
            if v not in ("AC", "OK") and worst is None:
                worst = v
        return {"verdict": worst or ("AC" if any(t.get("expected") is not None for t in tests) else "OK"),
                "compile_output": "", "results": results, "time_ms": tmax, "memory_kb": mmax}
    finally:
        _kill_uid(uid)
        shutil.rmtree(workdir, ignore_errors=True)


def _answer_job(job, tests):
    """Written answer (predict the output / trace the code): exact match after trimming trailing spaces and blank lines."""
    ans = job["code"][:20000]
    results, worst = [], None
    for t in tests:
        v = "OK" if t.get("expected") is None else ("AC" if normalize(ans) == normalize(t["expected"]) else "WA")
        results.append({"test_case_id": t.get("id"), "ordinal": t["ordinal"], "visibility": t.get("visibility", "hidden"), "weight": t.get("weight", 1),
                        "verdict": v, "time_ms": 0, "memory_kb": 0, "output": ans, "stderr": ""})
        worst = worst or (v if v not in ("AC", "OK") else None)
    return {"verdict": worst or ("AC" if any(t.get("expected") is not None for t in tests) else "OK"),
            "compile_output": "", "results": results, "time_ms": 0, "memory_kb": 0}


def _prompt_job(job, tests):
    """Student prompt = system message, test input = user message, AI reply judged exactly like program output (temperature 0)."""
    from . import ai
    prompt = job["code"][:6000]
    results, worst, tmax = [], None, 0
    for t in tests:
        t0 = time.time()
        try:
            out = ai.chat_text(prompt, t["input"] or "", max_tokens=600, temperature=0)
        except Exception as e:  # AI down/busy: infra problem, the worker retries; never a wrong answer
            raise JudgeInfraError(f"AI model unavailable: {e}")
        ms = int((time.time() - t0) * 1000)
        v = "OK" if t.get("expected") is None else ("AC" if normalize(out) == normalize(t["expected"]) else "WA")
        tmax = max(tmax, ms)
        results.append({"test_case_id": t.get("id"), "ordinal": t["ordinal"], "visibility": t.get("visibility", "hidden"),
                        "weight": t.get("weight", 1), "verdict": v, "time_ms": ms, "memory_kb": 0, "output": out[:20000], "stderr": ""})
        if v not in ("AC", "OK") and worst is None:
            worst = v
    return {"verdict": worst or ("AC" if any(t.get("expected") is not None for t in tests) else "OK"),
            "compile_output": "", "results": results, "time_ms": tmax, "memory_kb": 0}


def _clean(s, workdir):
    return s.replace(str(workdir) + "/", "").replace(str(workdir), ".")
