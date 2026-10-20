#!/usr/bin/env python3
"""Observe two natural scheduler executions; run in bench with --site SITE.

Creates only a marked Scheduled Job Type and its native logs. Never enqueues or
runs its method directly. Stops the probe in finally; retains the audit records.
"""
from __future__ import annotations

import argparse
import os
import json
import re
import sys
import traceback
import time
from pathlib import Path

PROBE_NAME = "phase1-scheduler-probe-v1"
METHOD = "frappe.utils.now"
TIMEOUT_SECONDS = 185
POLL_SECONDS = 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", args.site):
        parser.error("site must be a hostname")
    sites_path = Path("sites").resolve()
    if not (sites_path / args.site / "site_config.json").is_file():
        print(json.dumps({"passed": False, "detail": "run_from_bench_root_with_existing_site"}))
        return 1

    deadline = time.monotonic() + TIMEOUT_SECONDS
    started = time.monotonic()
    result = {"passed": False, "probe": PROBE_NAME, "timeout_seconds": TIMEOUT_SECONDS,
              "manual_enqueue": False, "checks": []}
    initialized = False
    owned_probe = False
    cleanup_ok = True
    stage = "import"
    try:
        import frappe
        from frappe.utils import get_datetime, now_datetime
        from frappe.utils.scheduler import DEFAULT_SCHEDULER_TICK, get_scheduler_tick, is_scheduler_inactive

        stage = "init"
        os.chdir(sites_path)
        frappe.init(site=args.site, sites_path=str(sites_path))
        initialized = True
        stage = "connect"
        frappe.connect()
        stage = "set_administrator"
        frappe.set_user("Administrator")
        result["default_scheduler_tick_seconds"] = DEFAULT_SCHEDULER_TICK
        result["effective_scheduler_tick_seconds"] = get_scheduler_tick()
        stage = "scheduler_configuration"
        if get_scheduler_tick() <= 0 or get_scheduler_tick() > 60:
            raise ValueError("scheduler_tick_must_be_between_1_and_60_seconds_for_bounded_probe")
        if is_scheduler_inactive(verbose=False):
            raise ValueError("scheduler_is_inactive")
        if not callable(frappe.get_attr(METHOD)):
            raise ValueError("probe_method_unavailable")

        stage = "ownership_preflight"
        names = frappe.get_all("Scheduled Job Type", filters={"method": METHOD}, pluck="name", limit_page_length=0)
        if any(name != PROBE_NAME for name in names):
            raise ValueError("unmarked_existing_job_with_probe_method_refusing_to_touch")
        exists = frappe.db.exists("Scheduled Job Type", PROBE_NAME)
        if exists:
            doc = frappe.get_doc("Scheduled Job Type", PROBE_NAME)
            # The explicit versioned name marks this probe. Never adopt a native job.
            if doc.method != METHOD or doc.frequency != "Cron" or doc.cron_format != "* * * * *" or not doc.create_log or doc.server_script or doc.scheduler_event or doc.owner != "Administrator":
                raise ValueError("existing_probe_marker_or_configuration_conflict")
            if not doc.stopped:
                raise ValueError("probe_already_active_refusing_concurrent_run")
        else:
            doc = frappe.get_doc({"doctype": "Scheduled Job Type", "method": METHOD,
                                  "frequency": "Cron", "cron_format": "* * * * *",
                                  "create_log": 1, "stopped": 1})
            doc.insert(set_name=PROBE_NAME)
            if doc.name != PROBE_NAME:
                raise ValueError("probe_name_not_preserved")
        owned_probe = True
        # Ignore all prior logs, including an old incomplete log finishing during this run.
        prior_ids = set(frappe.get_all("Scheduled Job Log", filters={"scheduled_job_type": PROBE_NAME}, pluck="name", limit_page_length=0))
        baseline_execution = get_datetime(doc.last_execution) if doc.last_execution else None
        observation_start = now_datetime()
        doc.stopped = 0
        doc.save()
        frappe.db.commit()
        result["checks"].append({"name": "marked_probe_enabled", "passed": True})
        stage = "observe_natural_scheduler_progress"
        observed = {}
        first_execution = None
        complete_rows = []
        while time.monotonic() < deadline:
            # MariaDB repeatable-read must not retain a snapshot across polls.
            frappe.db.rollback()
            logs = frappe.get_all("Scheduled Job Log", filters={"scheduled_job_type": PROBE_NAME},
                                  fields=["name", "status", "creation"], order_by="creation asc", limit_page_length=0)
            fresh = [row for row in logs if row.name not in prior_ids and get_datetime(row.creation) >= observation_start]
            if any(row.status == "Failed" for row in fresh):
                raise ValueError("natural_scheduled_job_failed")
            complete_rows = [row for row in fresh if row.status == "Complete"]
            current = frappe.get_doc("Scheduled Job Type", PROBE_NAME)
            last_execution = get_datetime(current.last_execution) if current.last_execution else None
            for row in complete_rows:
                observed[row.name] = str(row.creation)
            if complete_rows and first_execution is None:
                first_execution = last_execution
            if len(complete_rows) >= 2:
                first, second = complete_rows[0:2]
                ordered = get_datetime(first.creation) < get_datetime(second.creation)
                advanced = last_execution is not None and (baseline_execution is None or last_execution > baseline_execution) and last_execution >= get_datetime(second.creation)
                # Seeing a second completed log and a last_execution at its start proves
                # a new execution even if both completions arrive between two polls.
                if not ordered or not advanced:
                    raise ValueError("execution_timestamps_did_not_advance")
                result["checks"].append({"name": "two_new_complete_logs", "passed": True,
                                         "creation_times": [str(first.creation), str(second.creation)]})
                result["checks"].append({"name": "last_execution_advanced", "passed": True,
                                         "baseline": str(baseline_execution) if baseline_execution else None,
                                         "first_observed": str(first_execution), "latest": str(last_execution)})
                result["passed"] = True
                break
            time.sleep(min(POLL_SECONDS, max(0, deadline - time.monotonic())))
        result["new_complete_log_count"] = len(observed)
        if not result["passed"]:
            result["detail"] = "timeout_waiting_for_two_natural_complete_logs"
    except KeyboardInterrupt:
        result["detail"] = "interrupted"
    except Exception as error:
        safe_reasons = {"scheduler_tick_must_be_between_1_and_60_seconds_for_bounded_probe",
                        "scheduler_is_inactive", "probe_method_unavailable", "probe_name_not_preserved",
                        "unmarked_existing_job_with_probe_method_refusing_to_touch",
                        "existing_probe_marker_or_configuration_conflict",
                        "probe_already_active_refusing_concurrent_run", "natural_scheduled_job_failed",
                        "execution_timestamps_did_not_advance"}
        reason = error.args[0] if type(error) is ValueError and error.args and error.args[0] in safe_reasons else "frappe_probe_error"
        result["detail"] = reason
        result["stage"] = stage
        result["error_type"] = type(error).__name__
        result["stack_frames"] = [{"filename": Path(frame.filename).name, "line": frame.lineno, "function": frame.name} for frame in traceback.extract_tb(error.__traceback__)]
    finally:
        if initialized:
            if owned_probe:
                try:
                    frappe.db.rollback()
                    # Update only our marker row; never delete logs or native jobs.
                    current = frappe.get_doc("Scheduled Job Type", PROBE_NAME)
                    if current.method != METHOD:
                        raise ValueError("probe_method_changed_during_run")
                    current.stopped = 1
                    current.save()
                    frappe.db.commit()
                    result["checks"].append({"name": "probe_stopped_without_deletion", "passed": True})
                except Exception as cleanup_error:
                    result["cleanup_error_type"] = type(cleanup_error).__name__
                    result["cleanup_stack_frames"] = [{"filename": Path(frame.filename).name, "line": frame.lineno, "function": frame.name} for frame in traceback.extract_tb(cleanup_error.__traceback__)]
                    cleanup_ok = False
                    result["checks"].append({"name": "probe_stopped_without_deletion", "passed": False,
                                             "detail": "cleanup_failed_manual_stop_required"})
            try:
                frappe.destroy()
            except Exception:
                cleanup_ok = False
        result["passed"] = bool(result["passed"] and cleanup_ok)
        result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())


