"""Integration checks through Frappe's actual RPC dispatcher on an installed test site."""

import frappe
from frappe.handler import execute_cmd
from frappe.tests import IntegrationTestCase
from frappe.utils import set_request

from metalmecanica import __version__

METHOD = "metalmecanica.api.get_build_info"


class TestBuildInfo(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.original_user = frappe.session.user
		self.addCleanup(frappe.set_user, self.original_user)

	def test_authenticated_user_can_read_runtime_identity(self):
		frappe.set_user("Administrator")
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"build-info-{frappe.generate_hash(length=8)}@example.invalid",
				"first_name": "Build Info Integration Test",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
		self.addCleanup(frappe.db.rollback)
		frappe.set_user(user.name)
		set_request(path=f"/api/method/{METHOD}", method="GET")

		result = execute_cmd(METHOD)

		self.assertEqual(result["app"], "metalmecanica")
		self.assertEqual(result["version"], __version__)
		self.assertEqual(result["site"], frappe.local.site)
		self.assertTrue(result["development_marker"])

	def test_guest_is_rejected_by_rpc_dispatcher(self):
		frappe.set_user("Guest")
		set_request(path=f"/api/method/{METHOD}", method="GET")

		with self.assertRaises(frappe.PermissionError):
			execute_cmd(METHOD)

	def test_authenticated_post_is_rejected(self):
		frappe.set_user("Administrator")
		set_request(path=f"/api/method/{METHOD}", method="POST")

		with self.assertRaises(frappe.PermissionError):
			execute_cmd(METHOD)