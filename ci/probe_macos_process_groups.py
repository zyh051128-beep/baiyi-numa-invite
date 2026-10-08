#!/usr/bin/env python3
"""Probe only self-created macOS process groups; no secrets or MCP required."""
import argparse
import errno
import json
import os
import platform
import selectors
import signal
import subprocess
import sys
import time


def group_rows(pgid):
    result = subprocess.run(["/bin/ps", "-axo", "pid=,pgid=,stat="],
                            capture_output=True, text=True, timeout=3, check=False)
    if result.returncode:
        return {"query_ok": False, "exit_code": result.returncode}
    rows = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
            return {"query_ok": False, "error": "Unexpected ps output format"}
        if int(parts[1]) == pgid:
            rows.append({"pid": int(parts[0]), "pgid": int(parts[1]), "stat": parts[2]})
    return {"query_ok": True, "own_group_members": rows}


def group_signal(pgid, sig):
    try:
        os.killpg(pgid, sig)
        return {"ok": True}
    except OSError as exc:
        return {"ok": False, "errno": exc.errno,
                "error": errno.errorcode.get(exc.errno, type(exc).__name__)}


def probe(descendant):
    code = "import subprocess,sys,time\n"
    if descendant:
        code += "subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
    code += "print('READY',flush=True)\ntime.sleep(60)\n"
    process = subprocess.Popen([sys.executable, "-B", "-c", code],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    pgid = process.pid
    result = {"own_pgid": pgid, "descendant_created": descendant}
    owned = False
    try:
        if pgid <= 1 or pgid == os.getpgrp() or os.getpgid(pgid) != pgid:
            raise RuntimeError("Unexpected child process group; no group signal sent")
        owned = True
        with selectors.DefaultSelector() as select:
            select.register(process.stdout, selectors.EVENT_READ)
            if not select.select(3) or process.stdout.readline().strip() != b"READY":
                raise RuntimeError("Synthetic child did not become ready")
        result["before"] = group_rows(pgid)
        result["sigterm"] = group_signal(pgid, signal.SIGTERM)
        result["returncode_after_reap"] = process.wait(timeout=3)
        result["immediate_killpg_zero"] = group_signal(pgid, 0)
        result["immediate_ps"] = group_rows(pgid)
        time.sleep(0.1)
        result["delayed_killpg_zero"] = group_signal(pgid, 0)
        result["delayed_ps"] = group_rows(pgid)
    finally:
        # Same dedicated group only. Probe never searches/kills by process name.
        if owned:
            result["final_sigkill"] = group_signal(pgid, signal.SIGKILL)
        elif process.poll() is None:
            process.terminate()
        process.wait(timeout=3)
        process.stdout.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    if platform.system() != "Darwin" or not 1 <= args.repeat <= 20:
        parser.error("Requires macOS and --repeat between 1 and 20")
    report = {"platform": platform.platform(), "results": []}
    for descendant in (False, True):
        for _ in range(args.repeat):
            report["results"].append(probe(descendant))
    print(json.dumps(report, separators=(",", ":")))


if __name__ == "__main__":
    main()
