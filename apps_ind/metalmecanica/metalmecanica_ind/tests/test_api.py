import frappe
from frappe.tests import IntegrationTestCase


class TestIndependentBuildInfo(IntegrationTestCase):
	def test_authenticated_build_info(self):
		frappe.set_user("Administrator")
		result = frappe.get_attr("metalmecanica_ind.api.get_build_info")()
		self.assertEqual(result["app"], "metalmecanica_ind")
		self.assertEqual(result["variant"], "independent")
