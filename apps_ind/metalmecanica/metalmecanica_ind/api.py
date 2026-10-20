import frappe


@frappe.whitelist(methods=["GET"])
def get_build_info():
	if not frappe.session.user or frappe.session.user == "Guest":
		frappe.throw("Login is required to read independent build information.", frappe.PermissionError)
	return {
		"app": "metalmecanica_ind",
		"version": "0.0.1",
		"site": frappe.local.site,
		"variant": "independent",
		"development_marker": "metalmecanica-independent-001",
	}
