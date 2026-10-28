"""Identity-verified standalone business coordinator lifecycle. Never selects a PID by port."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from coordination_service.settings import atomic_json, provision, unseal


def normalized(value):
    return os.path.normcase(os.path.realpath(value))


def command(port, python=None):
    return [python or sys.executable, "-m", "uvicorn", "coordination_service.main:create_app", "--factory",
            "--host", "127.0.0.1", "--port", str(port), "--workers", "1"]


def verified(state):
    if normalized(state["project"]) != normalized(ROOT) or normalized(state["python"]) != normalized(sys.executable):
        raise ValueError("Coordination center process belongs to another project/Python")
    allowed = {normalized(sys.executable), normalized(sys._base_executable)}
    found = {}

    def check(process, created=None):
        if created is not None and abs(process.create_time()-created) > 0.00001:
            raise ValueError("PID reused; no process stopped")
        args = process.cmdline()
        if (normalized(process.exe()) not in allowed or not args or normalized(args[0]) not in allowed
                or args[1:] != command(state["port"])[1:] or normalized(process.cwd()) != normalized(ROOT)):
            raise ValueError("Coordination center process identity mismatch; no process stopped")
        found[process.pid] = process

    for record in state["processes"]:
        try:
            process = psutil.Process(record["pid"])
            check(process, record["created"])
        except psutil.NoSuchProcess:
            pass
    for parent in list(found.values()):
        for child in parent.children(recursive=True):
            try:
                if child.name().lower() != "conhost.exe":
                    check(child)
            except psutil.NoSuchProcess:
                pass
    return found


def run(args):
    directory = args.data_directory.resolve()
    state_path = directory / "coordination-server.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else None
    if state and Path(state["data_directory"]).resolve() != directory:
        raise ValueError("Coordination center data directory identity mismatch")
    processes = verified(state) if state else {}
    if args.action == "status":
        return {"running": bool(processes), "port": state["port"] if state else None, "pids": list(processes)}
    if args.action == "stop":
        # Check the entire set again before signaling any process.
        if state:
            processes = verified(state)
        for process in reversed(list(processes.values())):
            try:
                process.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(list(processes.values()), timeout=5)
        if alive:
            raise ValueError("Verified monitor processes still alive; state retained")
        if state:
            state_path.unlink()
        return {"running": False, "stopped_verified_processes": list(processes)}
    if args.action == "credentials":
        from app.coordination_client import CoordinationClient
        from monitoring_service.settings import unseal
        connection = json.loads((args.platform_data / "coordination-connection.json").read_text(encoding="utf-8"))
        values = json.loads((args.platform_data / "coordination-module-secrets.json").read_text(encoding="utf-8"))
        settings = json.loads((args.platform_data / "coordination-settings.json").read_text(encoding="utf-8"))
        if args.service_id not in settings["module_ids"] or args.output.exists():
            raise ValueError("Use a configured module and a new private output file")
        destination = args.output.resolve()
        root = ROOT.parent.resolve()
        if not destination.is_relative_to(root / "work"):
            raise ValueError("Credential export must remain in the ignored project work directory")
        atomic_json(destination, {"version":"1.0", "module_id":args.service_id,
                                 "url":connection["url"],"token":unseal(args.platform_data,values[args.service_id])})
        return {"saved_private_file":str(destination),"module_id":args.service_id}
    if args.platform_data is None or args.port is None:
        raise ValueError("Pass --platform-data and --port")
    if processes:
        if state["port"] != args.port:
            raise ValueError("This monitor already uses another port")
        settings = provision(args.platform_data, directory, args.port)
        with httpx.Client(timeout=2, trust_env=False) as client:
            health = client.get(settings["url"] + "/health").json()
        if health.get("service") != "duoflow-coordination" or health.get("center_id") != settings["center_id"]:
            raise ValueError("The recorded monitor is not ready")
        return {"running": True, "already_running": True, "port": args.port}
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", args.port))
    settings = provision(args.platform_data, directory, args.port)
    logs = directory / "logs"
    logs.mkdir(exist_ok=True)
    environment = dict(os.environ, DUOFLOW_COORDINATION_DATA_DIR=str(directory))
    with (logs/"coordination.stdout.log").open("ab") as stdout, (logs/"coordination.stderr.log").open("ab") as stderr:
        process = subprocess.Popen(command(args.port), cwd=ROOT, env=environment, stdout=stdout, stderr=stderr,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    actual = psutil.Process(process.pid)
    state = {"project": str(ROOT), "python": sys.executable, "data_directory": str(directory), "port": args.port,
             "processes": [{"pid": actual.pid, "created": actual.create_time()}]}
    atomic_json(state_path, state)
    for _ in range(40):
        if process.poll() is not None:
            raise ValueError("Coordination center exited; inspect its private logs. State retained")
        try:
            with httpx.Client(timeout=1, trust_env=False) as client:
                health = client.get(settings["url"] + "/health").json()
            if health.get("center_id") == settings["center_id"] and health.get("service") == "duoflow-coordination":
                found = verified(state)
                state["processes"] = [{"pid": p.pid, "created": p.create_time()} for p in found.values()]
                atomic_json(state_path, state)
                return {"running": True, "port": args.port, "center_id": settings["center_id"]}
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(0.2)
    raise ValueError("Coordination center readiness not confirmed; identity state retained")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "status", "credentials"])
    parser.add_argument("--data-directory", type=Path, required=True)
    parser.add_argument("--platform-data", type=Path)
    parser.add_argument("--port", type=int, choices=range(1, 65536), metavar="PORT")
    parser.add_argument("--service-id")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.action == "credentials" and not (args.platform_data and args.service_id and args.output):
        parser.error("credentials requires --platform-data, --service-id and --output")
    if args.platform_data:
        args.platform_data = args.platform_data.resolve()
    try:
        print(json.dumps(run(args), ensure_ascii=False))
    except (ValueError, OSError, KeyError, psutil.Error):
        # Never print config contents, command exceptions or secrets.
        print("Coordination center operation failed. Check the selected instance, process identity, configuration or private logs.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
