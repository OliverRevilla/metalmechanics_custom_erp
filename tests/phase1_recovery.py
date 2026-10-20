#!/usr/bin/env python3
"""Seed/verify technical backup fixtures in an initialized Frappe bench.

Run with the bench virtualenv Python from the bench root. Does not create business
records, change login passwords, replace existing fixtures, or remove any data.
"""
from __future__ import annotations

import argparse
import os
import hashlib
import hmac
import json
import re
import sys
import traceback
from pathlib import Path

FIXTURES = (
    ("phase1-recovery-public.txt", 0, b"Metalmecanica phase1 public recovery fixture v1\n"),
    ("phase1-recovery-private.txt", 1, b"Metalmecanica phase1 private recovery fixture v1\n"),
)
PASSWORD_FIELD = "phase1_recovery_probe"
# Synthetic test value only; never used as an Administrator login password.
PROBE_SECRET = "metalmecanica-phase1-synthetic-encryption-recovery-v1"


def digest(content):
    return hashlib.sha256(content.encode("utf-8") if isinstance(content, str) else content).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seed", "verify"))
    parser.add_argument("--site", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", args.site):
        parser.error("site must be a hostname")
    sites_path = Path("sites").resolve()
    if not (sites_path / args.site / "site_config.json").is_file():
        print(json.dumps({"passed": False, "action": args.action, "detail": "run_from_bench_root_with_existing_site"}))
        return 1
    initialized = False
    checks = []
    stage = "import"
    try:
        import frappe
        from frappe.query_builder import Table
        from frappe.utils.password import get_decrypted_password, set_encrypted_password

        stage = "init"
        os.chdir(sites_path)
        frappe.init(site=args.site, sites_path=str(sites_path))
        initialized = True
        stage = "connect"
        frappe.connect()
        stage = "set_administrator"
        frappe.set_user("Administrator")
        if not frappe.db.exists("User", "Administrator"):
            raise ValueError("administrator_missing")
        if args.action == "verify" and not frappe.conf.get("encryption_key"):
            raise ValueError("encryption_key_missing")

        stage = "file_preflight"
        # Preflight every existing fixture before making any changes.
        found = {}
        for filename, private, expected in FIXTURES:
            rows = frappe.get_all("File", filters={"file_name": filename}, pluck="name", limit_page_length=0)
            if len(rows) > 1:
                raise ValueError("duplicate_fixture_files")
            if rows:
                doc = frappe.get_doc("File", rows[0])
                if doc.is_folder or int(doc.is_private) != private:
                    raise ValueError("fixture_privacy_conflict")
                if not hmac.compare_digest(digest(doc.get_content()), digest(expected)):
                    raise ValueError("fixture_content_conflict")
                expected_prefix = "/private/files/" if private else "/files/"
                if not (doc.file_url or "").startswith(expected_prefix):
                    raise ValueError("fixture_storage_conflict")
                found[filename] = doc
            elif args.action == "verify":
                raise ValueError("fixture_file_missing")

        stage = "password_preflight"
        auth_table = Table("__Auth")
        existing_secret = (frappe.qb.from_(auth_table).select(auth_table.encrypted)
                           .where((auth_table.doctype == "User") & (auth_table.name == "Administrator")
                                  & (auth_table.fieldname == PASSWORD_FIELD)).run())
        if existing_secret:
            if len(existing_secret) != 1 or existing_secret[0][0] != 1:
                raise ValueError("fixture_password_conflict")
            if not frappe.conf.get("encryption_key"):
                raise ValueError("encryption_key_missing")
            actual = get_decrypted_password("User", "Administrator", fieldname=PASSWORD_FIELD)
            if not hmac.compare_digest(actual, PROBE_SECRET):
                raise ValueError("fixture_password_conflict")
        elif args.action == "verify":
            raise ValueError("fixture_password_missing")

        stage = "seed_or_verify"
        if args.action == "seed":
            for filename, private, content in FIXTURES:
                if filename not in found:
                    # Explicit File names; do not use save_file's content-hash filename suffix.
                    inserted = frappe.get_doc({"doctype": "File", "file_name": filename,
                                               "is_private": private, "content": content}).insert()
                    found[filename] = frappe.get_doc("File", inserted.name)
            if not existing_secret:
                set_encrypted_password("User", "Administrator", PROBE_SECRET, fieldname=PASSWORD_FIELD)

        for filename, private, expected in FIXTURES:
            doc = found[filename]
            valid = int(doc.is_private) == private and hmac.compare_digest(digest(doc.get_content()), digest(expected))
            checks.append({"name": filename, "passed": valid, "is_private": bool(private),
                           "sha256": digest(expected)})
        actual = get_decrypted_password("User", "Administrator", fieldname=PASSWORD_FIELD)
        checks.append({"name": "encrypted_password_roundtrip", "passed": hmac.compare_digest(actual, PROBE_SECRET)})
        passed = all(c["passed"] for c in checks)
        if args.action == "seed" and passed:
            frappe.db.commit()
        else:
            frappe.db.rollback()
        print(json.dumps({"passed": passed, "action": args.action, "checks": checks}, indent=2))
        return 0 if passed else 1
    except Exception as error:
        # Never emit exception text/traceback: Frappe errors can include database or secret context.
        if initialized:
            try:
                frappe.db.rollback()
            except Exception:
                pass
        safe_reasons = {"administrator_missing", "encryption_key_missing", "duplicate_fixture_files",
                        "fixture_privacy_conflict", "fixture_content_conflict", "fixture_storage_conflict",
                        "fixture_file_missing", "fixture_password_conflict", "fixture_password_missing"}
        detail = error.args[0] if type(error) is ValueError and error.args and error.args[0] in safe_reasons else "frappe_or_fixture_error"
        print(json.dumps({"passed": False, "action": args.action, "detail": detail, "checks": checks,
                          "stage": stage, "error_type": type(error).__name__,
                          "stack_frames": [{"filename": Path(frame.filename).name, "line": frame.lineno, "function": frame.name} for frame in traceback.extract_tb(error.__traceback__)]}))
        return 1
    finally:
        if initialized:
            frappe.destroy()


if __name__ == "__main__":
    sys.exit(main())

