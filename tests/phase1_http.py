#!/usr/bin/env python3
"""HTTP infrastructure probes; standard library only. Never prints credentials or bodies."""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import re
import sys
import uuid
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

BUILD = "/api/method/metalmecanica.api.get_build_info"
IDENTITY = "/api/method/frappe.auth.get_logged_user"
MAX_BODY = 8 * 1024 * 1024


@dataclass
class Reply:
    status: int
    body: bytes
    content_type: str
    final_url: str

    def json(self):
        try:
            return json.loads(self.body)
        except (ValueError, UnicodeError):
            return {}


class Client:
    def __init__(self, base: str, site: str):
        self.base = base.rstrip("/")
        self.site = site
        self.cookies = http.cookiejar.CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies))

    def call(self, path, method="GET", data=None, extra=None, accept="application/json"):
        headers = {"Host": self.site, "Accept": accept}
        if extra:
            headers.update(extra)
        if isinstance(data, dict):
            data = urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = Request(urljoin(self.base + "/", path), data=data, headers=headers, method=method)
        try:
            response = self.opener.open(req, timeout=60)
        except HTTPError as error:
            response = error
        with response:
            body = response.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise ValueError("response_limit")
            return Reply(response.code, body, response.headers.get_content_type(), response.geturl())


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = {"css": [], "js": []}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        path = values.get("href") if tag == "link" else values.get("src") if tag == "script" else None
        if not path:
            return
        extension = urlsplit(path).path.rsplit(".", 1)[-1].lower()
        if extension in self.paths and "/assets/" in urlsplit(path).path:
            self.paths[extension].append(path)


class Suite:
    def __init__(self):
        self.checks = []

    def record(self, name, passed, detail, status=None):
        item = {"name": name, "passed": bool(passed), "detail": detail}
        if status is not None:
            item["http_status"] = status
        self.checks.append(item)

    def run(self, name, fn):
        try:
            fn()
        except (URLError, TimeoutError, OSError):
            self.record(name, False, "transport_or_file_error")
        except Exception:
            # Exception strings may contain request URLs, headers, or server bodies.
            self.record(name, False, "probe_error")

    def expect(self, name, reply, condition, detail):
        self.record(name, condition, detail, reply.status)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--site", required=True)
    parser.add_argument("--password-file", type=Path, default=Path("deployment/.secrets/admin_password"))
    parser.add_argument("--expected-marker", default="metalmecanica-dev-001")
    args = parser.parse_args()
    parts = urlsplit(args.base_url)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        parser.error("base-url must be an HTTP(S) origin without credentials, path, query or fragment")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*(?::[0-9]+)?", args.site):
        parser.error("site must be a hostname, optionally with port")
    suite = Suite()
    guest, auth = Client(args.base_url, args.site), Client(args.base_url, args.site)

    def ping():
        r = guest.call("/api/method/ping")
        suite.expect("ping", r, r.status == 200 and r.json().get("message") == "pong", "guest_ping_returns_pong")
    suite.run("ping", ping)

    def denied_build():
        r = guest.call(BUILD)
        suite.expect("guest_build_denied", r, r.status in (401, 403), "unauthenticated_build_access_denied")
    suite.run("guest_build_denied", denied_build)

    # Missing resource names avoid touching an existing Company even if authorization fails.
    missing = "phase1-nonexistent-" + uuid.uuid4().hex
    for method, path, data in (
        ("GET", "/api/resource/Company?limit_page_length=1", None),
        ("POST", "/api/resource/Company", {"company_name": missing}),
        ("PUT", "/api/resource/Company/" + missing, {"company_name": missing}),
        ("DELETE", "/api/resource/Company/" + missing, None),
    ):
        name = "guest_company_" + method.lower() + "_denied"
        def check(method=method, path=path, data=data, name=name):
            r = guest.call(path, method=method, data=data)
            allowed = (401, 403, 404) if method in ("PUT", "DELETE") else (401, 403)
            detail = "missing_target_rejected_permission_not_proven" if r.status == 404 else "guest_crud_requires_authorization"
            suite.expect(name, r, r.status in allowed, detail)
        suite.run(name, check)

    logged_in = False
    def login():
        nonlocal logged_in
        password = args.password_file.read_text(encoding="utf-8").rstrip("\r\n")
        if not password:
            suite.record("login", False, "password_file_empty")
            return
        r = auth.call("/api/method/login", method="POST", data={"usr": "Administrator", "pwd": password})
        del password
        logged_in = r.status == 200 and any(c.name == "sid" and c.value not in ("", "Guest") for c in auth.cookies)
        suite.expect("login", r, logged_in, "administrator_session_cookie_established")
    suite.run("login", login)

    if logged_in:
        def identity():
            r = auth.call(IDENTITY)
            suite.expect("authenticated_identity", r, r.status == 200 and r.json().get("message") == "Administrator", "session_identifies_administrator")
        suite.run("authenticated_identity", identity)

        def build():
            r = auth.call(BUILD)
            info = r.json().get("message", {})
            valid = isinstance(info, dict) and info.get("app") == "metalmecanica" and info.get("site") == args.site and info.get("development_marker") == args.expected_marker
            suite.expect("authenticated_build_marker", r, r.status == 200 and valid, "mounted_app_marker_and_site_match")
        suite.run("authenticated_build_marker", build)

        def company():
            r = auth.call("/api/resource/Company?limit_page_length=1")
            suite.expect("authenticated_company_read", r, r.status == 200 and isinstance(r.json().get("data"), list), "authenticated_document_api_returns_list")
        suite.run("authenticated_company_read", company)

        def csrf():
            r = auth.call("/desk", accept="text/html")
            match = re.search(rb'frappe\.csrf_token\s*=\s*"([^"\r\n]+)"', r.body)
            suite.expect("desk_csrf_token", r, r.status == 200 and match is not None, "desk_bootstraps_session_csrf_token")
            if not match:
                suite.record("csrf_negative", False, "blocked_by_missing_desk_token")
                suite.record("csrf_positive", False, "blocked_by_missing_desk_token")
                return
            token = match.group(1).decode("ascii")
            negative = auth.call(IDENTITY, method="POST", data={})
            suite.expect("csrf_negative", negative, negative.status == 400 and negative.json().get("exc_type") == "CSRFTokenError", "post_without_token_rejected_after_desk_boot")
            positive = auth.call(IDENTITY, method="POST", data={}, extra={"X-Frappe-CSRF-Token": token})
            suite.expect("csrf_positive", positive, positive.status == 200 and positive.json().get("message") == "Administrator", "same_post_with_token_succeeds")
        suite.run("csrf", csrf)

        def spoof():
            r = auth.call(BUILD, extra={"X-Frappe-Site-Name": "phase1-unknown-" + uuid.uuid4().hex + ".invalid"})
            info = r.json().get("message", {})
            unchanged = r.status == 200 and isinstance(info, dict) and info.get("site") == args.site and info.get("development_marker") == args.expected_marker
            rejected = r.status in (400, 403, 404, 421)
            suite.expect("site_header_spoof", r, unchanged or rejected, "client_site_header_overwritten_or_rejected")
        suite.run("site_header_spoof", spoof)
    else:
        for name in ("authenticated_identity", "authenticated_build_marker", "authenticated_company_read", "desk_csrf_token", "csrf_negative", "csrf_positive", "site_header_spoof"):
            suite.record(name, False, "blocked_by_login")

    def assets():
        r = guest.call("/login", accept="text/html")
        suite.expect("login_html", r, r.status == 200 and r.content_type == "text/html", "login_page_available")
        links = Assets()
        links.feed(r.body.decode("utf-8", errors="replace"))
        for kind in ("css", "js"):
            candidates = [p for p in links.paths[kind] if not urlsplit(p).netloc or urlsplit(p).netloc in (args.site, parts.netloc)]
            if not candidates:
                suite.record("asset_" + kind, False, "no_local_asset_reference_in_login_html")
                continue
            # Use the actual referenced asset path; all requests stay on the selected origin.
            path = urlsplit(candidates[0])
            resource = path.path + (("?" + path.query) if path.query else "")
            response = guest.call(resource, accept="*/*")
            expected_types = {"text/css"} if kind == "css" else {"application/javascript", "text/javascript", "application/x-javascript"}
            body = response.body.lstrip().lower()
            valid = response.status == 200 and response.content_type in expected_types and len(body) > 20 and not body.startswith((b"<!doctype html", b"<html")) and "/assets/" in urlsplit(response.final_url).path
            suite.expect("asset_" + kind, response, valid, "referenced_asset_served_with_expected_type_and_content")
    suite.run("assets", assets)

    def websocket():
        # Engine.IO polling handshake tests frontend routing to Socket.IO, not WebSocket upgrade.
        r = guest.call("/socket.io/?EIO=4&transport=polling&t=" + uuid.uuid4().hex, accept="*/*")
        packet = r.body.split(b"\x1e", 1)[0]
        try:
            payload = json.loads(packet[1:]) if packet.startswith(b"0") else {}
        except (ValueError, UnicodeError):
            payload = {}
        valid = r.status == 200 and isinstance(payload.get("sid"), str) and bool(payload["sid"]) and isinstance(payload.get("pingInterval"), (int, float)) and isinstance(payload.get("pingTimeout"), (int, float)) and "websocket" in payload.get("upgrades", [])
        suite.expect("engineio4_polling_handshake", r, valid, "engineio_open_packet_and_websocket_upgrade_advertised")
        if valid:
            # Close this disposable polling session without logging its sid.
            close = guest.call("/socket.io/?" + urlencode({"EIO": "4", "transport": "polling", "sid": payload["sid"]}), method="POST", data=b"1", extra={"Content-Type": "text/plain;charset=UTF-8"}, accept="*/*")
            suite.expect("engineio_session_close", close, close.status == 200, "disposable_engineio_session_closed")
    suite.run("engineio4_polling_handshake", websocket)

    passed = all(check["passed"] for check in suite.checks)
    print(json.dumps({"passed": passed, "checks": suite.checks}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

