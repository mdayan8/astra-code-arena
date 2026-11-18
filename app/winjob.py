"""Windows execution for the judge: each run is placed in a Job Object that caps memory, process count and CPU time
and kills everything on close. (Windows has no rlimits/uids; network blocking is a firewall rule set up by the launcher.)"""
import ctypes
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]


class BASIC(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]


class EXTENDED(ctypes.Structure):
    _fields_ = [("Basic", BASIC), ("Io", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class ACCOUNTING(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_longlong), ("TotalKernelTime", ctypes.c_longlong), ("ThisPeriodTotalUserTime", ctypes.c_longlong),
                ("ThisPeriodTotalKernelTime", ctypes.c_longlong), ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]


k32.CreateJobObjectW.restype = wintypes.HANDLE
LIMIT_PROCESS_MEMORY, LIMIT_JOB_TIME, LIMIT_ACTIVE_PROCESS, LIMIT_KILL_ON_CLOSE, LIMIT_DIE_ON_EXCEPTION = 0x100, 0x4, 0x8, 0x2000, 0x400
CREATE_SUSPENDED, CREATE_NO_WINDOW = 0x4, 0x08000000


def execute(cmd, cwd, stdin_path, wall_s, cpu_s, mem_mb, as_limit, uid, output_limit):
    out_p, err_p = Path(cwd) / ".out", Path(cwd) / ".err"
    job = k32.CreateJobObjectW(None, None)
    info = EXTENDED()
    info.Basic.LimitFlags = LIMIT_KILL_ON_CLOSE | LIMIT_DIE_ON_EXCEPTION | LIMIT_ACTIVE_PROCESS | LIMIT_JOB_TIME
    info.Basic.ActiveProcessLimit = 32 if cpu_s >= 30 else 8          # compilers spawn helpers; programs may not fork-bomb
    info.Basic.PerJobUserTimeLimit = int(cpu_s * 10_000_000)          # 100 ns units
    if mem_mb:
        info.Basic.LimitFlags |= LIMIT_PROCESS_MEMORY
        info.ProcessMemoryLimit = (mem_mb + (0 if as_limit else 512)) * 1024 * 1024
    k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
    with open(stdin_path, "rb") as fi, open(out_p, "wb") as fo, open(err_p, "wb") as fe:
        t0 = time.monotonic()
        try:
            p = subprocess.Popen(cmd, cwd=cwd, stdin=fi, stdout=fo, stderr=fe, creationflags=CREATE_SUSPENDED | CREATE_NO_WINDOW)
        except OSError as e:
            k32.CloseHandle(job)
            from .judge import JudgeInfraError
            raise JudgeInfraError(f"cannot start {cmd[0]}: {e}")
        k32.AssignProcessToJobObject(job, wintypes.HANDLE(int(p._handle)))
        ntdll.NtResumeProcess(wintypes.HANDLE(int(p._handle)))
        timed_out = ole = False
        while p.poll() is None:
            if time.monotonic() - t0 > wall_s:
                timed_out = True
            elif out_p.stat().st_size >= output_limit:
                ole = True
            if timed_out or ole:
                k32.TerminateJobObject(job, 1)
                p.wait()
                break
            time.sleep(0.01)
    acc, ext = ACCOUNTING(), EXTENDED()
    k32.QueryInformationJobObject(job, 1, ctypes.byref(acc), ctypes.sizeof(acc), None)
    k32.QueryInformationJobObject(job, 9, ctypes.byref(ext), ctypes.sizeof(ext), None)
    k32.TerminateJobObject(job, 1)
    k32.CloseHandle(job)
    cpu_ms = (acc.TotalUserTime + acc.TotalKernelTime) // 10_000
    code = p.returncode or 0
    if code < 0:
        code &= 0xFFFFFFFF
    return {"exit": 0 if code == 0 else 1, "signal": 0, "time_ms": int(cpu_ms), "wall_ms": int((time.monotonic() - t0) * 1000),
            "mem_kb": int(ext.PeakProcessMemoryUsed // 1024), "timed_out": timed_out or cpu_ms > cpu_s * 1000,
            "stdout": out_p.read_bytes()[:output_limit].decode("utf-8", "replace").replace("\r\n", "\n"),
            "stderr": err_p.read_bytes()[:8000].decode("utf-8", "replace"), "ole": ole}
