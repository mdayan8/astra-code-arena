"""Machine specs + live usage (stdlib only, Windows/Linux/macOS) and the platform's opening-hours schedule."""
import ctypes
import datetime
import os
import shutil
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

from . import config

_prev = {}


def ram_mb():
    """-> (total_mb, used_mb or None)"""
    try:
        if sys.platform == "win32":
            class MS(ctypes.Structure):
                _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                            ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong), ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ae", ctypes.c_ulonglong)]
            m = MS(); m.len = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.total // 2**20, (m.total - m.avail) // 2**20
        if sys.platform.startswith("linux"):
            info = dict(l.split(":", 1) for l in open("/proc/meminfo"))
            tot, av = int(info["MemTotal"].split()[0]) // 1024, int(info["MemAvailable"].split()[0]) // 1024
            return tot, tot - av
        return int(subprocess.check_output(["sysctl", "-n", "hw.memsize"]).strip()) // 2**20, None
    except Exception:
        return None, None


def cpu_percent():
    """Whole-machine CPU busy % since the previous call (None on first call / unsupported)."""
    try:
        if sys.platform == "win32":
            ft = [ctypes.c_ulonglong() for _ in range(3)]
            ctypes.windll.kernel32.GetSystemTimes(*[ctypes.byref(x) for x in ft])
            idle, total = ft[0].value, ft[1].value + ft[2].value  # kernel time includes idle
        elif sys.platform.startswith("linux"):
            v = [int(x) for x in open("/proc/stat").readline().split()[1:]]
            idle, total = v[3] + v[4], sum(v)
        else:
            return round(min(100.0, os.getloadavg()[0] / (os.cpu_count() or 1) * 100), 1)
        pi, pt = _prev.get("cpu", (idle, total))
        _prev["cpu"] = (idle, total)
        return round(100 * (1 - (idle - pi) / (total - pt)), 1) if total > pt else None
    except Exception:
        return None


def physical_cores():
    """Real cores (hyper-threads share one core, so they are not counted). Linux: /proc/cpuinfo; elsewhere all logical CPUs."""
    try:
        cores = {(b.split("physical id")[1].split("\n")[0], b.split("core id")[1].split("\n")[0])
                 for b in open("/proc/cpuinfo").read().split("\n\n") if "core id" in b}
        return len(cores) or os.cpu_count() or 1
    except (OSError, IndexError):
        return os.cpu_count() or 1


def workers_for(total_mb, phys, threads, ai):
    """Judge workers that fit. RAM: ~2 GB for Ubuntu desktop + web server, ~1.5 GB for the AI model, ~400 MB per worker
    (judge + one student program or compiler). CPU: one worker per real core; keep a core free when there is no hyper-threading."""
    by_ram = int(((total_mb or 4096) - 2000 - (1500 if ai else 0)) // 400)
    by_cpu = phys if threads > phys else phys - 1
    return max(1, min(by_cpu, by_ram, 8))


def specs():
    total, _ = ram_mb()
    threads, phys = os.cpu_count() or 1, physical_cores()
    du = shutil.disk_usage(config.DATA_DIR)
    workers = workers_for(total, phys, threads, bool(config.AI_BASE_URL and config.AI_MODEL))
    return {"cores": phys, "threads": threads, "ram_mb": total, "disk_free_gb": round(du.free / 1e9, 1), "recommended_workers": workers,
            "max_students": workers * 100, "os": sys.platform}


def usage():
    total, used = ram_mb()
    du = shutil.disk_usage(config.DATA_DIR)
    return {"at": int(time.time()), "cpu": cpu_percent(), "ram_total_mb": total, "ram_used_mb": used,
            "ram_pct": round(used / total * 100, 1) if total and used is not None else None, "disk_free_gb": round(du.free / 1e9, 1)}


# ---------------- opening hours ----------------
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _local_now():
    return datetime.datetime.now(ZoneInfo(config.APP_TIMEZONE))


def schedule_state(sched: dict, now=None):
    """sched = {"enabled": bool, "windows": [{"days": [0..6], "start": "HH:MM", "end": "HH:MM"}]} (days 0 = Monday).
    -> (open: bool, next_open: datetime | None)"""
    if not sched or not sched.get("enabled") or not sched.get("windows"):
        return True, None
    now = now or _local_now()

    def hm(s):
        h, m = s.split(":")
        return int(h) * 60 + int(m)
    mins = now.hour * 60 + now.minute
    for w in sched["windows"]:
        if now.weekday() in w.get("days", []) and hm(w["start"]) <= mins < hm(w["end"]):
            return True, None
    for d in range(0, 8):
        day = now + datetime.timedelta(days=d)
        for w in sorted(sched["windows"], key=lambda x: x["start"]):
            if day.weekday() in w.get("days", []):
                start = day.replace(hour=hm(w["start"]) // 60, minute=hm(w["start"]) % 60, second=0, microsecond=0)
                if start > now:
                    return False, start
    return False, None


if __name__ == "__main__":  # self-check
    s = {"enabled": True, "windows": [{"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "17:00"}]}
    tz = ZoneInfo(config.APP_TIMEZONE)
    assert schedule_state(s, datetime.datetime(2026, 11, 18, 10, 0, tzinfo=tz))[0]               # Wed 10:00 open
    closed, nxt = schedule_state(s, datetime.datetime(2026, 11, 18, 18, 0, tzinfo=tz))           # Wed 18:00 closed
    assert not closed and nxt.day == 19 and nxt.hour == 9
    closed, nxt = schedule_state(s, datetime.datetime(2026, 11, 21, 12, 0, tzinfo=tz))           # Sat -> Mon 09:00
    assert not closed and nxt.weekday() == 0
    assert schedule_state({"enabled": False}, None)[0]
    assert workers_for(7720, 6, 12, True) == 6 and workers_for(7720, 6, 12, False) == 6   # i5-12400, 8 GB: 600 students
    assert workers_for(4096, 4, 4, False) == 3 and workers_for(4096, 4, 4, True) == 1     # 4-core 4 GB: AI leaves room for 1
    print("ok", specs(), usage())
