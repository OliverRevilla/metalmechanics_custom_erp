"""Deployment operations for the pinned Frappe v16 source.

Run with the Bench virtualenv. Our helpers read credentials from secret files
and do not add them to subprocess arguments or environment variables. This
statement does not guarantee the behavior of the upstream APIs they invoke.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tarfile
import time
import uuid

BENCH = Path("/home/frappe/frappe-bench")
SITES = BENCH / "sites"
BACKUPS = Path("/backups")
APPS = ["frappe"] + os.environ.get("REQUIRED_APPS", "erpnext,metalmecanica").split(",")


def site_name(value):
    if not value or len(value) > 120 or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*[a-z0-9]", value):
        raise RuntimeError("A valid lower-case site hostname is required")
    if ".." in value:
        raise RuntimeError("Invalid site hostname")
    return value


def secret(name):
    value = (Path("/run/secrets") / name).read_text(encoding="utf-8").rstrip("\r\n")
    if not value:
        raise RuntimeError("Required secret file is empty: " + name)
    return value


def save_json(path, data):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


@contextmanager
def operation_lock():
    SITES.mkdir(parents=True, exist_ok=True)
    with (SITES / ".metalmecanica-operation.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def run_bench(site, *arguments):
    subprocess.run(["bench", "--site", site, *arguments], cwd=BENCH, check=True)


@contextmanager
def connection(site):
    import frappe
    frappe.init(site, sites_path=str(SITES))
    try:
        frappe.connect()
        yield frappe
    finally:
        frappe.destroy()


def configure():
    path = SITES / "common_site_config.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data.update({
        "db_host": "db", "db_port": 3306,
        "redis_cache": "redis://redis-cache:6379",
        "redis_queue": "redis://redis-queue:6379",
        "redis_socketio": "redis://redis-queue:6379",
        "socketio_port": 9000,
        "scheduler_tick_interval": 60,
        "server_script_enabled": False, "developer_mode": 0,
        "chromium_path": "/usr/lib/chromium/chromium-headless-shell",
    })
    save_json(path, data)
    available = sorted(path.name for path in (BENCH / "apps").iterdir()
                       if path.is_dir() and (path / path.name).is_dir())
    missing = set(APPS) - set(available)
    if missing:
        raise RuntimeError("Required application sources are missing: " + ",".join(sorted(missing)))
    (SITES / "apps.txt").write_text("\n".join(available) + "\n", encoding="utf-8")
    print("Bench configuration ready")


def initialize_timezone(site):
    from zoneinfo import ZoneInfo
    from frappe.utils import get_datetime, get_system_timezone
    previous = None
    with connection(site) as frappe:
        settings = frappe.get_single("System Settings")
        if settings.time_zone:
            return
        previous = get_system_timezone()
        # Use the single-value update path: a fresh ERPNext site may leave
        # unrelated mandatory singleton fields (for example language) empty.
        # Saving the whole singleton would make this deployment-only setting
        # fail with MandatoryError.
        frappe.db.set_single_value("System Settings", "time_zone", "America/Lima")
        frappe.db.commit()
    # A fresh site can already have scheduler anchors under Frappe's fallback zone.
    # Convert only future anchors; do not alter business documents or historical logs.
    adjusted = 0
    with connection(site) as frappe:
        now = datetime.now(ZoneInfo("America/Lima")).replace(tzinfo=None)
        for row in frappe.get_all("Scheduled Job Type", fields=["name", "last_execution", "creation"]):
            anchor = get_datetime(row.last_execution or row.creation)
            if anchor > now:
                converted = anchor.replace(tzinfo=ZoneInfo(previous)).astimezone(
                    ZoneInfo("America/Lima")).replace(tzinfo=None)
                job = frappe.get_doc("Scheduled Job Type", row.name)
                job.last_execution = converted
                job.save()
                adjusted += 1
        frappe.db.commit()
    save_json(SITES / site / ".metalmecanica-locale.json", {
        "previous_fallback": previous, "time_zone": "America/Lima",
        "converted_scheduler_anchors": adjusted,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })



def enable_scheduler(site):
    initialize_timezone(site)
    from frappe.installer import update_site_config
    from frappe.utils.scheduler import enable_scheduler as enable
    with connection(site) as frappe:
        enable()
        frappe.db.set_single_value("System Settings", "enable_telemetry", 0)
        frappe.db.commit()
        update_site_config("pause_scheduler", 0)
        update_site_config("maintenance_mode", 0)


def verify_apps(site):
    with connection(site) as frappe:
        missing = set(APPS) - set(frappe.get_installed_apps())
        if missing:
            raise RuntimeError("Site is partially installed; missing: " + ",".join(sorted(missing)))


def create_site(site, install_apps):
    # Click command has no pass_context decorator in the pinned v16 source.
    # Calling its callback keeps passwords out of the process command line.
    from frappe.commands.site import new_site
    new_site.callback(
        site=site, db_type="mariadb", db_host="db", db_port=3306,
        db_root_username="root", db_root_password=secret("db_root_password"),
        admin_password=secret("admin_password"),
        mariadb_user_host_login_scope="%", install_app=install_apps,
        force=False, set_default=False,
    )


def bootstrap(site):
    path = SITES / site
    marker = path / ".metalmecanica-bootstrap.json"
    if path.exists():
        if not marker.is_file():
            raise RuntimeError("Existing unmanaged or incomplete site; inspect manually. No repair or overwrite performed.")
        verify_apps(site)
        enable_scheduler(site)
        print("Existing complete site verified")
        return
    create_site(site, APPS[1:])
    verify_apps(site)
    enable_scheduler(site)
    save_json(marker, {"site": site, "apps": APPS, "completed_at": datetime.now(timezone.utc).isoformat()})
    print("Site bootstrap complete: " + site)


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def backup(site):
    from frappe.utils.backups import scheduled_backup, get_or_generate_backup_encryption_key
    verify_apps(site)
    bundle = BACKUPS / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    bundle.mkdir(mode=0o700, parents=True, exist_ok=False)
    with connection(site) as frappe:
        # Generate the key before copying site_config, if backup encryption is enabled.
        encrypted = bool(frappe.get_system_settings("encrypt_backup"))
        if encrypted:
            get_or_generate_backup_encryption_key()
        backup_object = scheduled_backup(
            ignore_files=False, ignore_conf=True, compress=True, force=True,
            backup_path_db=str(bundle / "database.sql.gz"),
            backup_path_files=str(bundle / "public.tgz"),
            backup_path_private_files=str(bundle / "private.tgz"),
            backup_path_conf=str(bundle / "site_config.json"),
        )
        installed = frappe.get_installed_apps()
        files = {
            "database": Path(backup_object.backup_path_db),
            "public": Path(backup_object.backup_path_files),
            "private": Path(backup_object.backup_path_private_files),
            "config": Path(backup_object.backup_path_conf),
        }
    for path in files.values():
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError("Backup did not produce every required file")
        path.chmod(0o600)
    save_json(bundle / "manifest.json", {
        "format": 1, "source_site": site, "apps": installed, "encrypted": encrypted,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": {key: {"name": path.name, "sha256": digest(path)} for key, path in files.items()},
    })
    print("Complete backup bundle: " + bundle.name)


def load_bundle(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value or ""):
        raise RuntimeError("BACKUP_BUNDLE must be a directory name under /backups")
    directory = (BACKUPS / value).resolve()
    if directory.parent != BACKUPS.resolve():
        raise RuntimeError("Backup path escapes /backups")
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("format") != 1 or set(manifest.get("files", {})) != {"database", "public", "private", "config"}:
        raise RuntimeError("Unsupported or incomplete backup manifest")
    files = {}
    for key, record in manifest["files"].items():
        path = (directory / record["name"]).resolve()
        if path.parent != directory or not path.is_file() or digest(path) != record["sha256"]:
            raise RuntimeError("Backup file failed path or checksum validation")
        files[key] = path
    return manifest, files


@contextmanager
def restore_artifacts(manifest, files, original_config):
    """Stage private copies; upstream restore/extraction may rename or delete files."""
    with tempfile.TemporaryDirectory(prefix="metalmecanica-restore-") as temporary:
        staged = {}
        for key in ("database", "public", "private"):
            source = Path(temporary) / files[key].name
            shutil.copyfile(files[key], source)
            source.chmod(0o600)
            if manifest.get("encrypted"):
                passphrase = original_config.get("backup_encryption_key")
                if not isinstance(passphrase, str) or not passphrase or "\n" in passphrase or "\r" in passphrase:
                    raise RuntimeError("Encrypted bundle requires a valid backup encryption key")
                destination = source.with_name("decrypted-" + source.name)
                subprocess.run(
                    ["gpg", "--batch", "--yes", "--pinentry-mode", "loopback",
                     "--passphrase-fd", "0", "--decrypt", "--output", str(destination), str(source)],
                    input=(passphrase + "\n").encode("utf-8"), check=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                )
                destination.chmod(0o600)
                staged[key] = destination
            else:
                staged[key] = source
        yield staged


def extract_restored_files(target, public_archive, private_archive):
    """Extract only Frappe file payloads, with an explicit traversal check.

    Some pinned Frappe releases preserve absolute bench paths in the file
    tarballs and therefore do not place them under a newly named site. The
    database restore still succeeds, but File rows then point to empty paths.
    Normalize those two known prefixes into the target site ourselves.
    """
    site_root = (SITES / target).resolve()
    for archive, prefix, destination in (
        (public_archive, "/public/files/", site_root / "public" / "files"),
        (private_archive, "/private/files/", site_root / "private" / "files"),
    ):
        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle.getmembers():
                if not member.isfile():
                    continue
                marker = member.name.replace("\\", "/")
                index = marker.find(prefix)
                if index < 0:
                    raise RuntimeError("Backup file archive contains an unexpected path")
                relative = Path(marker[index + len(prefix):])
                if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
                    raise RuntimeError("Backup file archive failed traversal validation")
                output = (destination / relative).resolve()
                if output.parent != destination.resolve():
                    raise RuntimeError("Backup file archive escaped its destination")
                source = bundle.extractfile(member)
                if source is None:
                    raise RuntimeError("Backup file archive member could not be read")
                output.write_bytes(source.read())
                output.chmod(0o600)


def restore(primary_site):
    target = site_name(os.environ.get("RESTORE_SITE_NAME", ""))
    # Validate all original paths and checksums before staging or changing a site.
    manifest, files = load_bundle(os.environ.get("BACKUP_BUNDLE", ""))
    if target in {primary_site, manifest["source_site"]} or (SITES / target).exists():
        raise RuntimeError("Restore requires a NEW site, different from the primary and backup source sites")
    if not set(manifest["apps"]).issubset(set((SITES / "apps.txt").read_text().splitlines())):
        raise RuntimeError("The image does not contain every application required by the backup")
    original_config = json.loads(files["config"].read_text())
    existing_databases = {
        json.loads(path.read_text()).get("db_name")
        for path in SITES.glob("*/site_config.json")
    }
    # Keep private copies alive through restore and migrate. Originals remain read-only inputs.
    with restore_artifacts(manifest, files, original_config) as staged:
        # Create an isolated empty database first. Never copy source database credentials.
        create_site(target, [])
        destination = SITES / target / "site_config.json"
        config = json.loads(destination.read_text())
        if not config.get("db_name") or config["db_name"] in existing_databases | {original_config.get("db_name")}:
            raise RuntimeError("Destination database isolation check failed")
        for key in ("encryption_key", "backup_encryption_key"):
            if key in original_config:
                config[key] = original_config[key]
        config.update({"pause_scheduler": 1, "maintenance_mode": 0, "mute_emails": True})
        save_json(destination, config)
        import frappe
        frappe.destroy()
        frappe.init(target, sites_path=str(SITES))
        try:
            from frappe.commands.site import _restore
            _restore(
                site=target, sql_file_path=str(staged["database"]),
                db_root_username="root", db_root_password=secret("db_root_password"),
                admin_password=secret("admin_password"), force=False, verbose=False,
                encryption_key=None,
                with_public_files=str(staged["public"]), with_private_files=str(staged["private"]),
            )
            extract_restored_files(target, staged["public"], staged["private"])
        finally:
            frappe.destroy()
        with connection(target) as frappe:
            frappe.db.set_single_value("System Settings", "enable_telemetry", 0)
            frappe.db.commit()
        run_bench(target, "migrate")
        verify_apps(target)
        # Keep the restored dataset isolated from scheduled work and outgoing email.
        config = json.loads(destination.read_text())
        config.update({"pause_scheduler": 1, "maintenance_mode": 0, "mute_emails": True})
        save_json(destination, config)
        save_json(SITES / target / ".metalmecanica-restore.json", {
            "source_site": manifest["source_site"], "bundle": os.environ["BACKUP_BUNDLE"],
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
    print("Backup restored into isolated site: " + target)


def check(site):
    verify_apps(site)
    from frappe.utils.background_jobs import enqueue
    from frappe.utils.scheduler import is_scheduler_inactive
    with connection(site) as frappe:
        if is_scheduler_inactive(verbose=False):
            raise RuntimeError("Scheduler is disabled, paused, or site is in maintenance")
        jobs = {queue: enqueue("frappe.ping", queue=queue, timeout=30) for queue in ("short", "default", "long")}
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            statuses = {queue: job.get_status(refresh=True) for queue, job in jobs.items()}
            if all(status == "finished" for status in statuses.values()):
                print("Installed apps, database, scheduler configuration, and all three queues verified")
                return
            if any(status in {"failed", "stopped", "canceled"} for status in statuses.values()):
                raise RuntimeError("Queue execution probe failed")
            time.sleep(1)
    raise RuntimeError("Queue execution probes timed out; verify worker containers")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["configure", "bootstrap", "backup", "restore", "check"])
    operation = parser.parse_args().operation
    os.umask(0o077)
    os.chdir(SITES)
    site = site_name(os.environ.get("SITE_NAME", "metalmecanica.localhost"))
    with operation_lock():
        if operation == "configure":
            configure()
        else:
            globals()[operation](site)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Deliberately avoid tracebacks with locals or secret-bearing exception text.
        print("Deployment operation failed: " + type(error).__name__, file=sys.stderr)
        if isinstance(error, RuntimeError):
            print(str(error), file=sys.stderr)
        sys.exit(1)
