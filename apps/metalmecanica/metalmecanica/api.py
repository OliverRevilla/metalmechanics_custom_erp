"""Authenticated diagnostics for validating the development bind mount."""

import frappe
from frappe import _

from metalmecanica import __version__

# Change this value on the host to verify that the container loads local sources.
DEVELOPMENT_MARKER = "metalmecanica-dev-001"


@frappe.whitelist(methods=["GET"])
def get_build_info() -> dict[str, str]:
	"""Return minimal runtime identity to an authenticated caller."""
	if not frappe.session.user or frappe.session.user == "Guest":
		frappe.throw(_("Login is required to read build information."), frappe.PermissionError)

	return {
		"app": "metalmecanica",
		"version": __version__,
		"site": frappe.local.site,
		"development_marker": DEVELOPMENT_MARKER,
	}