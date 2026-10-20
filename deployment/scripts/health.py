"""Container health probes. No credentials are printed or passed as arguments."""
import json
import os
from pathlib import Path
import socket
import sys
import urllib.request

BENCH = Path("/home/frappe/frappe-bench")


def main():
    mode = sys.argv[1]
    if mode == "tcp":
        with socket.create_connection((sys.argv[2], int(sys.argv[3])), timeout=3):
            return
    if mode == "http":
        request = urllib.request.Request(
            "http://127.0.0.1:8080/api/method/ping",
            headers={"Host": os.environ["SITE_NAME"]},
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            if response.status != 200 or json.load(response).get("message") != "pong":
                raise RuntimeError("Application ping failed")
        return
    os.chdir(BENCH / "sites")
    import frappe
    if mode == "scheduler":
        from frappe.utils.scheduler import is_schduler_process_running
        if not is_schduler_process_running():
            raise RuntimeError("Scheduler process does not hold its process lock")
    elif mode == "worker":
        from frappe.utils.background_jobs import get_queue, get_workers
        frappe.init(os.environ["SITE_NAME"])
        try:
            workers = get_workers(get_queue(sys.argv[2]))
            if not any(worker.hostname == socket.gethostname() for worker in workers):
                raise RuntimeError("No local registered worker for required queue")
        finally:
            frappe.destroy()
    else:
        raise RuntimeError("Unknown health probe")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Health probe failed: " + type(error).__name__, file=sys.stderr)
        sys.exit(1)
