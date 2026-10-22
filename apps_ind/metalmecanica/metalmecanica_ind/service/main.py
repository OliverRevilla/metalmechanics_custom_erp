"""Small standalone authentication and setup service.

This module deliberately does not import Frappe or ERPNext. It owns its
tables and authentication flow and can grow into the independent domain API.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pymysql
from fastapi import Cookie, FastAPI, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


DB_HOST = os.getenv("IND_DB_HOST", "db")
DB_PORT = int(os.getenv("IND_DB_PORT", "3306"))
DB_NAME = os.getenv("IND_DB_NAME", "metalmecanica_ind")
DB_USER = os.getenv("IND_DB_USER", "root")
DB_PASSWORD_FILE = Path(os.getenv("IND_DB_PASSWORD_FILE", "/run/secrets/db_root_password"))
ADMIN_USERNAME = os.getenv("IND_ADMIN_USERNAME", "admin")
ADMIN_EMAIL = os.getenv("IND_ADMIN_EMAIL", "admin@example.invalid")
ADMIN_PASSWORD = os.getenv("IND_ADMIN_PASSWORD", "")
SEED_DUMMY_DATA = os.getenv("IND_SEED_DUMMY_DATA", "false").lower() in {"1", "true", "yes", "on"}
SESSION_COOKIE = "ind_session"
SESSION_TTL_SECONDS = 60 * 60 * 8
STATIC_INDEX = Path(__file__).with_name("static") / "index.html"
DATA_ROOT = Path(os.getenv("IND_DATA_ROOT", "/home/frappe/frappe-bench/sites/independent-data"))
ALLOWED_FILE_EXTENSIONS = {".pdf", ".csv", ".txt"}
SERVICE_ORDER_FILE_EXTENSIONS = {".pdf", ".doc", ".docx", ".txt"}
PROJECT_AREAS = {
    "production": "Producción",
    "quality": "Calidad",
    "administration": "Administración",
    "ssoma": "SSOMA",
    "technical-office": "Oficina Técnica",
    "resident": "Residente",
    "planning": "Planeamiento",
}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
IGV_RATE = Decimal("0.18")


def database_password() -> str:
    return DB_PASSWORD_FILE.read_text(encoding="utf-8").strip()


def connect(database: str | None = DB_NAME):
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=database_password(),
        database=database,
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


def ensure_schema() -> None:
    # The database itself is owned by this service, not by Frappe.
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    connection = pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=database_password(),
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        connection.close()

    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS ind_users (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    username VARCHAR(80) NOT NULL UNIQUE,
                    email VARCHAR(255) NOT NULL UNIQUE,
                    password_hash CHAR(128) NOT NULL,
                    password_salt CHAR(32) NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    role VARCHAR(40) NOT NULL DEFAULT 'user',
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB
                """
            )
            cursor.execute("SHOW COLUMNS FROM ind_users LIKE 'role'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE ind_users ADD COLUMN role VARCHAR(40) NOT NULL DEFAULT 'user'")
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS ind_sessions (
                    token_hash CHAR(64) NOT NULL PRIMARY KEY,
                    user_id BIGINT UNSIGNED NOT NULL,
                    expires_at BIGINT UNSIGNED NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT fk_ind_sessions_user
                        FOREIGN KEY (user_id) REFERENCES ind_users(id)
                        ON DELETE CASCADE
                ) ENGINE=InnoDB
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS acc_companies (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(160) NOT NULL,
                    tax_id VARCHAR(40) NULL UNIQUE,
                    description TEXT NULL,
                    default_currency CHAR(3) NOT NULL DEFAULT 'PEN',
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB
                """
            )
            cursor.execute("SHOW COLUMNS FROM acc_companies LIKE 'description'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE acc_companies ADD COLUMN description TEXT NULL AFTER tax_id")
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS acc_fiscal_years (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    company_id BIGINT UNSIGNED NOT NULL,
                    name VARCHAR(80) NOT NULL,
                    starts_on DATE NOT NULL,
                    ends_on DATE NOT NULL,
                    is_closed BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_acc_fiscal_year_company_name (company_id, name),
                    CONSTRAINT fk_acc_fiscal_year_company FOREIGN KEY (company_id) REFERENCES acc_companies(id) ON DELETE CASCADE
                ) ENGINE=InnoDB
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS acc_accounts (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    company_id BIGINT UNSIGNED NOT NULL,
                    parent_id BIGINT UNSIGNED NULL,
                    code VARCHAR(30) NOT NULL,
                    name VARCHAR(160) NOT NULL,
                    root_type VARCHAR(20) NOT NULL,
                    account_type VARCHAR(40) NULL,
                    is_group BOOLEAN NOT NULL DEFAULT FALSE,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_acc_account_company_code (company_id, code),
                    CONSTRAINT fk_acc_account_company FOREIGN KEY (company_id) REFERENCES acc_companies(id) ON DELETE CASCADE,
                    CONSTRAINT fk_acc_account_parent FOREIGN KEY (parent_id) REFERENCES acc_accounts(id) ON DELETE RESTRICT
                ) ENGINE=InnoDB
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS acc_journal_entries (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    company_id BIGINT UNSIGNED NOT NULL,
                    fiscal_year_id BIGINT UNSIGNED NOT NULL,
                    posting_date DATE NOT NULL,
                    voucher_type VARCHAR(40) NOT NULL DEFAULT 'Journal Entry',
                    reference VARCHAR(120) NULL,
                    remarks TEXT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'posted',
                    created_by BIGINT UNSIGNED NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT fk_acc_entry_company FOREIGN KEY (company_id) REFERENCES acc_companies(id) ON DELETE RESTRICT,
                    CONSTRAINT fk_acc_entry_year FOREIGN KEY (fiscal_year_id) REFERENCES acc_fiscal_years(id) ON DELETE RESTRICT,
                    CONSTRAINT fk_acc_entry_user FOREIGN KEY (created_by) REFERENCES ind_users(id) ON DELETE RESTRICT
                ) ENGINE=InnoDB
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS acc_journal_lines (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    journal_entry_id BIGINT UNSIGNED NOT NULL,
                    account_id BIGINT UNSIGNED NOT NULL,
                    description VARCHAR(255) NULL,
                    debit DECIMAL(18,6) NOT NULL DEFAULT 0,
                    credit DECIMAL(18,6) NOT NULL DEFAULT 0,
                    CONSTRAINT fk_acc_line_entry FOREIGN KEY (journal_entry_id) REFERENCES acc_journal_entries(id) ON DELETE CASCADE,
                    CONSTRAINT fk_acc_line_account FOREIGN KEY (account_id) REFERENCES acc_accounts(id) ON DELETE RESTRICT
                ) ENGINE=InnoDB
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS ind_files (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    original_name VARCHAR(255) NOT NULL,
                    stored_name VARCHAR(80) NOT NULL UNIQUE,
                    extension VARCHAR(10) NOT NULL,
                    content_type VARCHAR(120) NULL,
                    size_bytes BIGINT UNSIGNED NOT NULL,
                    uploaded_by BIGINT UNSIGNED NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT fk_ind_files_user FOREIGN KEY (uploaded_by) REFERENCES ind_users(id) ON DELETE RESTRICT
                ) ENGINE=InnoDB
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS sell_customers (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    customer_type VARCHAR(20) NOT NULL,
                    name VARCHAR(180) NOT NULL,
                    document_type VARCHAR(20) NULL,
                    document_number VARCHAR(40) NULL,
                    email VARCHAR(255) NULL,
                    phone VARCHAR(40) NULL,
                    secondary_phone VARCHAR(40) NULL,
                    address VARCHAR(255) NULL,
                    city VARCHAR(100) NULL,
                    contact_name VARCHAR(160) NULL,
                    notes TEXT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_by BIGINT UNSIGNED NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_sell_customer_document (document_type, document_number),
                    CONSTRAINT fk_sell_customer_user FOREIGN KEY (created_by) REFERENCES ind_users(id) ON DELETE RESTRICT
                ) ENGINE=InnoDB
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS sell_quotations (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    customer_id BIGINT UNSIGNED NOT NULL,
                    service_order_id BIGINT UNSIGNED NULL,
                    quote_code VARCHAR(60) NOT NULL UNIQUE,
                    observations TEXT NULL,
                    file_id BIGINT UNSIGNED NULL,
                    created_by BIGINT UNSIGNED NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    CONSTRAINT fk_sell_quote_customer FOREIGN KEY (customer_id) REFERENCES sell_customers(id) ON DELETE RESTRICT,
                    CONSTRAINT fk_sell_quote_file FOREIGN KEY (file_id) REFERENCES ind_files(id) ON DELETE SET NULL,
                    CONSTRAINT fk_sell_quote_user FOREIGN KEY (created_by) REFERENCES ind_users(id) ON DELETE RESTRICT
                ) ENGINE=InnoDB
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS sell_quotation_items (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    quotation_id BIGINT UNSIGNED NOT NULL,
                    item_number INT UNSIGNED NOT NULL,
                    description VARCHAR(255) NOT NULL,
                    unit VARCHAR(40) NOT NULL,
                    quantity DECIMAL(18,6) NOT NULL,
                    unit_cost DECIMAL(18,6) NOT NULL,
                    subtotal DECIMAL(18,6) NOT NULL,
                    CONSTRAINT uq_sell_quote_item_number UNIQUE (quotation_id, item_number),
                    CONSTRAINT fk_sell_quote_item_quote FOREIGN KEY (quotation_id) REFERENCES sell_quotations(id) ON DELETE CASCADE
                ) ENGINE=InnoDB
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS sell_technical_visits (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    customer_id BIGINT UNSIGNED NOT NULL,
                    service_order_id BIGINT UNSIGNED NULL,
                    title VARCHAR(180) NOT NULL,
                    scheduled_at DATETIME NULL,
                    address VARCHAR(255) NOT NULL,
                    city VARCHAR(100) NULL,
                    latitude DECIMAL(10,7) NULL,
                    longitude DECIMAL(10,7) NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'planned',
                    observations TEXT NULL,
                    created_by BIGINT UNSIGNED NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    CONSTRAINT fk_sell_visit_customer FOREIGN KEY (customer_id) REFERENCES sell_customers(id) ON DELETE RESTRICT,
                    CONSTRAINT fk_sell_visit_user FOREIGN KEY (created_by) REFERENCES ind_users(id) ON DELETE RESTRICT
                ) ENGINE=InnoDB
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS sell_service_orders (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    customer_id BIGINT UNSIGNED NOT NULL,
                    order_code VARCHAR(60) NOT NULL UNIQUE,
                    description TEXT NOT NULL,
                    project_type VARCHAR(120) NOT NULL,
                    observations TEXT NULL,
                    approver_name VARCHAR(180) NOT NULL,
                    approver_email VARCHAR(255) NULL,
                    approver_phone VARCHAR(40) NULL,
                    approved_at DATE NOT NULL,
                    project_due_date DATE NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'approved',
                    file_id BIGINT UNSIGNED NULL,
                    created_by BIGINT UNSIGNED NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    CONSTRAINT fk_sell_order_customer FOREIGN KEY (customer_id) REFERENCES sell_customers(id) ON DELETE RESTRICT,
                    CONSTRAINT fk_sell_order_file FOREIGN KEY (file_id) REFERENCES ind_files(id) ON DELETE SET NULL,
                    CONSTRAINT fk_sell_order_user FOREIGN KEY (created_by) REFERENCES ind_users(id) ON DELETE RESTRICT
                ) ENGINE=InnoDB
                """
            )
            cursor.execute("SHOW COLUMNS FROM sell_quotations LIKE 'service_order_id'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE sell_quotations ADD COLUMN service_order_id BIGINT UNSIGNED NULL AFTER customer_id")
            cursor.execute("SHOW COLUMNS FROM sell_technical_visits LIKE 'service_order_id'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE sell_technical_visits ADD COLUMN service_order_id BIGINT UNSIGNED NULL AFTER customer_id")
            cursor.execute("SHOW CREATE TABLE sell_quotations")
            quotation_table = cursor.fetchone()["Create Table"]
            if "fk_sell_quote_order" not in quotation_table:
                cursor.execute("ALTER TABLE sell_quotations ADD CONSTRAINT fk_sell_quote_order FOREIGN KEY (service_order_id) REFERENCES sell_service_orders(id) ON DELETE RESTRICT")
            cursor.execute("SHOW CREATE TABLE sell_technical_visits")
            visit_table = cursor.fetchone()["Create Table"]
            if "fk_sell_visit_order" not in visit_table:
                cursor.execute("ALTER TABLE sell_technical_visits ADD CONSTRAINT fk_sell_visit_order FOREIGN KEY (service_order_id) REFERENCES sell_service_orders(id) ON DELETE SET NULL")
            cursor.execute("SHOW COLUMNS FROM sell_service_orders LIKE 'project_due_date'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE sell_service_orders ADD COLUMN project_due_date DATE NULL AFTER approved_at")
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS proj_projects (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    project_code VARCHAR(60) NOT NULL UNIQUE,
                    name VARCHAR(180) NOT NULL,
                    service_order_id BIGINT UNSIGNED NOT NULL,
                    start_date DATE NOT NULL,
                    planned_end_date DATE NULL,
                    end_date DATE NULL,
                    description TEXT NULL,
                    observations TEXT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'planned',
                    created_by BIGINT UNSIGNED NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    CONSTRAINT fk_proj_project_order FOREIGN KEY (service_order_id) REFERENCES sell_service_orders(id) ON DELETE RESTRICT,
                    CONSTRAINT fk_proj_project_user FOREIGN KEY (created_by) REFERENCES ind_users(id) ON DELETE RESTRICT
                ) ENGINE=InnoDB
                """
            )
            cursor.execute("SHOW COLUMNS FROM proj_projects LIKE 'start_date'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE proj_projects ADD COLUMN start_date DATE NULL AFTER service_order_id")
            cursor.execute("SHOW COLUMNS FROM proj_projects LIKE 'end_date'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE proj_projects ADD COLUMN end_date DATE NULL AFTER start_date")
            cursor.execute("SHOW COLUMNS FROM proj_projects LIKE 'planned_end_date'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE proj_projects ADD COLUMN planned_end_date DATE NULL AFTER start_date")
            cursor.execute("UPDATE proj_projects p JOIN sell_service_orders o ON o.id = p.service_order_id SET p.start_date = o.approved_at, p.planned_end_date = o.project_due_date WHERE p.start_date IS NULL OR p.planned_end_date IS NULL")
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS proj_area_documents (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    project_id BIGINT UNSIGNED NOT NULL,
                    area_key VARCHAR(40) NOT NULL,
                    contact_name VARCHAR(180) NOT NULL,
                    observations TEXT NULL,
                    entered_at DATE NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    approved_at DATE NULL,
                    file_id BIGINT UNSIGNED NULL,
                    created_by BIGINT UNSIGNED NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_proj_area_document (project_id, area_key),
                    CONSTRAINT fk_proj_area_project FOREIGN KEY (project_id) REFERENCES proj_projects(id) ON DELETE CASCADE,
                    CONSTRAINT fk_proj_area_file FOREIGN KEY (file_id) REFERENCES ind_files(id) ON DELETE SET NULL,
                    CONSTRAINT fk_proj_area_user FOREIGN KEY (created_by) REFERENCES ind_users(id) ON DELETE RESTRICT
                ) ENGINE=InnoDB
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS proj_tasks (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    project_id BIGINT UNSIGNED NOT NULL,
                    task_code VARCHAR(80) NOT NULL,
                    name VARCHAR(180) NOT NULL,
                    task_type VARCHAR(30) NOT NULL DEFAULT 'general',
                    area_key VARCHAR(40) NULL,
                    planned_start DATE NULL,
                    planned_end DATE NULL,
                    actual_start DATE NULL,
                    actual_end DATE NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'planned',
                    is_dead_time BOOLEAN NOT NULL DEFAULT FALSE,
                    observations TEXT NULL,
                    created_by BIGINT UNSIGNED NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_proj_task_code (project_id, task_code),
                    CONSTRAINT fk_proj_task_project FOREIGN KEY (project_id) REFERENCES proj_projects(id) ON DELETE CASCADE,
                    CONSTRAINT fk_proj_task_user FOREIGN KEY (created_by) REFERENCES ind_users(id) ON DELETE RESTRICT
                ) ENGINE=InnoDB
                """
            )
            if not ADMIN_PASSWORD:
                raise RuntimeError("IND_ADMIN_PASSWORD must be configured for the independent service")
            cursor.execute("SELECT id FROM ind_users WHERE username = %s", (ADMIN_USERNAME,))
            if not cursor.fetchone():
                salt = secrets.token_hex(16)
                cursor.execute(
                    "INSERT INTO ind_users (username, email, password_hash, password_salt, is_active, role) VALUES (%s, %s, %s, %s, 1, 'admin')",
                    (ADMIN_USERNAME, ADMIN_EMAIL, password_hash(ADMIN_PASSWORD, salt), salt),
                )
            else:
                cursor.execute("UPDATE ind_users SET role = 'admin', is_active = 1 WHERE username = %s", (ADMIN_USERNAME,))
            ensure_project_area_tasks(cursor)
            if SEED_DUMMY_DATA:
                seed_dummy_data(cursor)
    finally:
        connection.close()


def seed_dummy_data(cursor) -> None:
    """Create repeatable development data without touching the stable app."""
    cursor.execute("SELECT id FROM ind_users WHERE username = %s", (ADMIN_USERNAME,))
    admin = cursor.fetchone()
    if not admin:
        return
    admin_id = admin["id"]

    def customer(document_number: str, values: tuple) -> int:
        cursor.execute("SELECT id FROM sell_customers WHERE document_number = %s", (document_number,))
        existing = cursor.fetchone()
        if existing:
            return existing["id"]
        cursor.execute(
            "INSERT INTO sell_customers (customer_type, name, document_type, document_number, email, phone, address, city, contact_name, notes, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            values + (admin_id,),
        )
        return cursor.lastrowid

    customer_one = customer("20600000001", ("company", "Aceros del Pacífico S.A.C.", "RUC", "20600000001", "compras@aceros-pacifico.test", "+51 999 100 200", "Av. Industrial 120", "Lima", "María Torres", "Cliente dummy para probar proyectos"))
    customer_two = customer("10400000002", ("person", "Carlos Mendoza", "DNI", "10400000002", "carlos.mendoza@test", "+51 999 300 400", "Jr. Los Talleres 45", "Arequipa", "Carlos Mendoza", "Cliente dummy persona natural"))

    def service_order(code: str, customer_id: int, values: tuple) -> int:
        cursor.execute("SELECT id FROM sell_service_orders WHERE order_code = %s", (code,))
        existing = cursor.fetchone()
        if existing:
            cursor.execute("UPDATE sell_service_orders SET project_due_date = %s WHERE id = %s AND project_due_date IS NULL", (values[7], existing["id"]))
            return existing["id"]
        cursor.execute(
            "INSERT INTO sell_service_orders (customer_id, order_code, description, project_type, observations, approver_name, approver_email, approver_phone, approved_at, project_due_date, status, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (customer_id, code) + values + (admin_id,),
        )
        return cursor.lastrowid

    order_one = service_order("OS-DUMMY-001", customer_one, ("Fabricación y montaje de estructura metálica", "Estructura metálica", "Recepción dummy para probar fecha de inicio", "Ana Salazar", "ana.salazar@test", "+51 999 111 222", "2026-10-01", "2026-12-15", "approved"))
    order_two = service_order("OS-DUMMY-002", customer_two, ("Mantenimiento de línea de producción", "Mantenimiento industrial", "Orden dummy terminada", "Luis Paredes", "luis.paredes@test", "+51 999 333 444", "2026-09-15", "2026-10-05", "completed"))
    cursor.execute("UPDATE proj_projects p JOIN sell_service_orders o ON o.id = p.service_order_id SET p.planned_end_date = o.project_due_date WHERE p.planned_end_date IS NULL")

    def quotation(code: str, customer_id: int, order_id: int, items: list[tuple]) -> None:
        cursor.execute("SELECT id FROM sell_quotations WHERE quote_code = %s", (code,))
        existing = cursor.fetchone()
        if existing:
            return
        cursor.execute("INSERT INTO sell_quotations (customer_id, service_order_id, quote_code, observations, created_by) VALUES (%s, %s, %s, %s, %s)", (customer_id, order_id, code, "Cotización dummy asociada obligatoriamente a la orden de servicio.", admin_id))
        quotation_id = cursor.lastrowid
        for number, item in enumerate(items, start=1):
            description, unit, quantity, unit_cost = item
            cursor.execute("INSERT INTO sell_quotation_items (quotation_id, item_number, description, unit, quantity, unit_cost, subtotal) VALUES (%s, %s, %s, %s, %s, %s, %s)", (quotation_id, number, description, unit, quantity, unit_cost, Decimal(str(quantity)) * Decimal(str(unit_cost))))

    quotation("COT-DUMMY-001", customer_one, order_one, [("Fabricación de estructura", "UND", 2, 12500), ("Montaje en planta", "SERV", 1, 4800)])
    quotation("COT-DUMMY-002", customer_two, order_two, [("Mantenimiento preventivo", "SERV", 1, 3600)])

    def visit(title: str, customer_id: int, order_id: int | None, scheduled_at: str, address: str, latitude: str | None, longitude: str | None) -> None:
        cursor.execute("SELECT id FROM sell_technical_visits WHERE title = %s AND customer_id = %s", (title, customer_id))
        if cursor.fetchone():
            return
        cursor.execute("INSERT INTO sell_technical_visits (customer_id, service_order_id, title, scheduled_at, address, city, latitude, longitude, status, observations, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'planned', %s, %s)", (customer_id, order_id, title, scheduled_at, address, "Lima", latitude, longitude, "Visita dummy para probar asociación opcional a orden.", admin_id))

    visit("Levantamiento en Planta Norte", customer_one, order_one, "2026-10-05 09:00:00", "Av. Industrial 120", "-12.0464000", "-77.0428000")
    visit("Inspección inicial", customer_two, None, "2026-09-12 15:30:00", "Jr. Los Talleres 45", None, None)

    def project(code: str, name: str, order_id: int, status_value: str, end_date: str | None, description: str) -> int:
        cursor.execute("SELECT id FROM proj_projects WHERE project_code = %s", (code,))
        existing = cursor.fetchone()
        if existing:
            return existing["id"]
        cursor.execute("SELECT approved_at FROM sell_service_orders WHERE id = %s", (order_id,))
        order = cursor.fetchone()
        cursor.execute(
            "INSERT INTO proj_projects (project_code, name, service_order_id, start_date, end_date, description, observations, status, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (code, name, order_id, order["approved_at"], end_date, description, "Registro dummy para validar el directorio y el Gantt.", status_value, admin_id),
        )
        return cursor.lastrowid

    project_one = project("PRJ-DUMMY-001", "Estructura Planta Norte", order_one, "active", None, "Proyecto activo con documentación parcial por áreas.")
    project_two = project("PRJ-DUMMY-002", "Mantenimiento Línea 3", order_two, "completed", "2026-10-10", "Proyecto terminado para validar la fecha de término.")

    dummy_pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"

    def pdf_file(filename: str) -> int:
        stored_name = f"dummy-{filename.lower().replace(' ', '-')}.pdf"
        cursor.execute("SELECT id FROM ind_files WHERE stored_name = %s", (stored_name,))
        existing = cursor.fetchone()
        if existing:
            return existing["id"]
        (DATA_ROOT / stored_name).write_bytes(dummy_pdf)
        cursor.execute("INSERT INTO ind_files (original_name, stored_name, extension, content_type, size_bytes, uploaded_by) VALUES (%s, %s, '.pdf', 'application/pdf', %s, %s)", (filename, stored_name, len(dummy_pdf), admin_id))
        return cursor.lastrowid

    def area(project_id: int, area_key: str, contact: str, entered_at: str, area_status: str, approved_at: str | None, filename: str | None, observations: str) -> None:
        file_id = pdf_file(filename) if filename else None
        cursor.execute("SELECT id FROM proj_area_documents WHERE project_id = %s AND area_key = %s", (project_id, area_key))
        if cursor.fetchone():
            return
        cursor.execute(
            "INSERT INTO proj_area_documents (project_id, area_key, contact_name, observations, entered_at, status, approved_at, file_id, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (project_id, area_key, contact, observations, entered_at, area_status, approved_at, file_id, admin_id),
        )

    for area_key, contact, area_status, approved_at, filename in [
        ("production", "Jorge Ramos", "approved", "2026-10-03", "PRJ-DUMMY-001-Produccion"),
        ("quality", "Patricia León", "in_review", None, "PRJ-DUMMY-001-Calidad"),
        ("administration", "Elena Ruiz", "pending", None, None),
    ]:
        area(project_one, area_key, contact, "2026-10-02", area_status, approved_at, filename, "Documento dummy para validar el flujo del área.")
    for area_key, contact in [("production", "Jorge Ramos"), ("quality", "Patricia León"), ("ssoma", "Raúl Vega")]:
        area(project_two, area_key, contact, "2026-09-16", "approved", "2026-09-18", f"PRJ-DUMMY-002-{area_key}", "Documentación dummy de proyecto terminado.")
    ensure_project_area_tasks(cursor)


def ensure_project_area_tasks(cursor) -> None:
    """Backfill one measurable dead-time task for each project area."""
    cursor.execute("SELECT id FROM ind_users WHERE username = %s", (ADMIN_USERNAME,))
    admin = cursor.fetchone()
    if not admin:
        return
    admin_id = admin["id"]
    cursor.execute("SELECT id, start_date, planned_end_date FROM proj_projects")
    projects = cursor.fetchall()
    for project in projects:
        for area_key, area_name in PROJECT_AREAS.items():
            cursor.execute("SELECT entered_at, approved_at, status FROM proj_area_documents WHERE project_id = %s AND area_key = %s", (project["id"], area_key))
            document = cursor.fetchone()
            doc_status = document["status"] if document else "pending"
            task_status = {"approved": "completed", "rejected": "blocked", "in_review": "in_progress"}.get(doc_status, "planned")
            cursor.execute("SELECT id FROM proj_tasks WHERE project_id = %s AND task_code = %s", (project["id"], f"DOC-{area_key}"))
            existing_task = cursor.fetchone()
            if existing_task:
                cursor.execute("UPDATE proj_tasks SET planned_start = %s, planned_end = %s, actual_start = %s, actual_end = %s, status = %s WHERE id = %s", (project["start_date"], project["planned_end_date"], document["entered_at"] if document else None, document["approved_at"] if document else None, task_status, existing_task["id"]))
                continue
            cursor.execute(
                "INSERT INTO proj_tasks (project_id, task_code, name, task_type, area_key, planned_start, planned_end, actual_start, actual_end, status, is_dead_time, observations, created_by) VALUES (%s, %s, %s, 'documentation', %s, %s, %s, %s, %s, %s, 1, %s, %s)",
                (project["id"], f"DOC-{area_key}", f"Documentación · {area_name}", area_key, project["start_date"], project["planned_end_date"], document["entered_at"] if document else None, document["approved_at"] if document else None, task_status, "Tiempo de espera documental por área.", admin_id),
            )


def password_hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("ascii"), 240_000
    ).hex()


def session_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=12, max_length=200)


class UserUpdateRequest(BaseModel):
    role: str = Field(pattern=r"^(user|accounts|selling|integrations|admin)$")
    is_active: bool


class CompanyRequest(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    tax_id: str | None = Field(default=None, max_length=40)
    description: str | None = Field(default=None, max_length=2000)
    default_currency: str = Field(default="PEN", min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")


class CompanyUpdateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    tax_id: str | None = Field(default=None, max_length=40)
    description: str | None = Field(default=None, max_length=2000)
    default_currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")


class FiscalYearRequest(BaseModel):
    company_id: int
    name: str = Field(min_length=2, max_length=80)
    starts_on: date
    ends_on: date


class AccountRequest(BaseModel):
    company_id: int
    parent_id: int | None = None
    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=2, max_length=160)
    root_type: str = Field(pattern=r"^(Asset|Liability|Equity|Income|Expense)$")
    account_type: str | None = Field(default=None, max_length=40)
    is_group: bool = False


class JournalLineRequest(BaseModel):
    account_id: int
    description: str | None = Field(default=None, max_length=255)
    debit: Decimal = Field(default=Decimal("0"), ge=0)
    credit: Decimal = Field(default=Decimal("0"), ge=0)


class JournalEntryRequest(BaseModel):
    company_id: int
    fiscal_year_id: int
    posting_date: date
    voucher_type: str = Field(default="Journal Entry", max_length=40)
    reference: str | None = Field(default=None, max_length=120)
    remarks: str | None = None
    lines: list[JournalLineRequest] = Field(min_length=2)


class CustomerRequest(BaseModel):
    customer_type: str = Field(pattern=r"^(natural|legal)$")
    name: str = Field(min_length=2, max_length=180)
    document_type: str | None = Field(default=None, max_length=20)
    document_number: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=40)
    secondary_phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=100)
    contact_name: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=4000)


class QuotationItemRequest(BaseModel):
    description: str = Field(min_length=2, max_length=255)
    unit: str = Field(min_length=1, max_length=40)
    quantity: Decimal = Field(gt=0)
    unit_cost: Decimal = Field(ge=0)


class QuotationRequest(BaseModel):
    customer_id: int
    service_order_id: int
    quote_code: str = Field(min_length=1, max_length=60)
    items: list[QuotationItemRequest] = Field(min_length=1)
    observations: str | None = Field(default=None, max_length=4000)


class TechnicalVisitRequest(BaseModel):
    customer_id: int
    service_order_id: int | None = None
    title: str = Field(min_length=2, max_length=180)
    scheduled_at: datetime | None = None
    address: str = Field(min_length=2, max_length=255)
    city: str | None = Field(default=None, max_length=100)
    latitude: Decimal | None = Field(default=None, ge=Decimal("-90"), le=Decimal("90"))
    longitude: Decimal | None = Field(default=None, ge=Decimal("-180"), le=Decimal("180"))
    status: str = Field(default="planned", pattern=r"^(planned|completed|cancelled)$")
    observations: str | None = Field(default=None, max_length=4000)


class ServiceOrderRequest(BaseModel):
    customer_id: int
    order_code: str = Field(min_length=1, max_length=60)
    description: str = Field(min_length=2, max_length=4000)
    project_type: str = Field(min_length=2, max_length=120)
    observations: str | None = Field(default=None, max_length=4000)
    approver_name: str = Field(min_length=2, max_length=180)
    approver_email: str | None = Field(default=None, max_length=255)
    approver_phone: str | None = Field(default=None, max_length=40)
    approved_at: date
    project_due_date: date
    status: str = Field(default="approved", pattern=r"^(approved|in_progress|completed|cancelled)$")


class ProjectRequest(BaseModel):
    project_code: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=2, max_length=180)
    service_order_id: int
    end_date: date | None = None
    description: str | None = Field(default=None, max_length=4000)
    observations: str | None = Field(default=None, max_length=4000)
    status: str = Field(default="planned", pattern=r"^(planned|active|completed|cancelled)$")


class ProjectAreaDocumentRequest(BaseModel):
    contact_name: str = Field(min_length=2, max_length=180)
    observations: str | None = Field(default=None, max_length=4000)
    entered_at: date
    status: str = Field(default="pending", pattern=r"^(pending|in_review|approved|rejected)$")
    approved_at: date | None = None


class ProjectTaskRequest(BaseModel):
    project_id: int
    task_code: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=2, max_length=180)
    task_type: str = Field(default="general", pattern=r"^(general|documentation|engineering|production|quality|administration)$")
    area_key: str | None = Field(default=None, max_length=40)
    planned_start: date | None = None
    planned_end: date | None = None
    actual_start: date | None = None
    actual_end: date | None = None
    status: str = Field(default="planned", pattern=r"^(planned|in_progress|blocked|completed|cancelled)$")
    is_dead_time: bool = False
    observations: str | None = Field(default=None, max_length=4000)


def current_user(token: str | None) -> dict:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required")
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT u.id, u.username, u.email, u.role
                FROM ind_sessions s JOIN ind_users u ON u.id = s.user_id
                WHERE s.token_hash = %s AND s.expires_at > %s AND u.is_active = 1
                """,
                (session_hash(token), int(time.time())),
            )
            user = cursor.fetchone()
    finally:
        connection.close()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    return user


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_schema()
    yield


app = FastAPI(title="Metalmecanica Independent API", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_INDEX.parent), name="static")


@app.get("/health")
def health():
    return {"status": "ok", "service": "metalmecanica_ind", "runtime": "fastapi"}


@app.get("/")
def index():
    return FileResponse(STATIC_INDEX)


@app.post("/auth/register", status_code=status.HTTP_202_ACCEPTED)
def register(payload: RegisterRequest):
    salt = secrets.token_hex(16)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            try:
                cursor.execute(
                    "INSERT INTO ind_users (username, email, password_hash, password_salt, is_active, role) VALUES (%s, %s, %s, %s, 0, 'user')",
                    (payload.username, payload.email, password_hash(payload.password, salt), salt),
                )
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already exists") from error
    finally:
        connection.close()
    return {"pending": True, "message": "Cuenta creada. Un administrador debe activarla antes del primer acceso."}


@app.post("/auth/login")
def login(payload: LoginRequest, response: Response):
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, username, email, role, password_hash, password_salt FROM ind_users WHERE username = %s AND is_active = 1",
                (payload.username,),
            )
            user = cursor.fetchone()
    finally:
        connection.close()
    if not user or not hmac.compare_digest(user["password_hash"], password_hash(payload.password, user["password_salt"])):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = secrets.token_urlsafe(32)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO ind_sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
                (session_hash(token), user["id"], int(time.time()) + SESSION_TTL_SECONDS),
            )
    finally:
        connection.close()
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=SESSION_TTL_SECONDS)
    return {"username": user["username"], "email": user["email"], "role": user["role"]}


@app.post("/auth/logout")
def logout(response: Response, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    if token:
        connection = connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM ind_sessions WHERE token_hash = %s", (session_hash(token),))
        finally:
            connection.close()
    response.delete_cookie(SESSION_COOKIE)
    return {"logged_out": True}


@app.get("/auth/me")
def me(token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    return current_user(token)


def require_admin(token: str | None) -> dict:
    user = current_user(token)
    if user["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return user


@app.get("/admin/users")
def admin_users(token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_admin(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, username, email, role, is_active, created_at FROM ind_users ORDER BY created_at DESC")
            users = cursor.fetchall()
    finally:
        connection.close()
    return {"users": users}


@app.patch("/admin/users/{user_id}")
def update_user(user_id: int, payload: UserUpdateRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    admin = require_admin(token)
    if user_id == admin["id"] and (payload.role != "admin" or not payload.is_active):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No puedes desactivar o quitar el rol de tu propio admin")
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE ind_users SET role = %s, is_active = %s WHERE id = %s", (payload.role, payload.is_active, user_id))
            if cursor.rowcount == 0:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    finally:
        connection.close()
    return {"updated": True}


@app.get("/settings/companies")
def settings_companies(token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_admin(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, name, tax_id, description, default_currency, is_active FROM acc_companies ORDER BY name")
            companies = cursor.fetchall()
    finally:
        connection.close()
    return {"companies": companies}


@app.get("/settings/data/files")
def list_data_files(token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_admin(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT f.id, f.original_name, f.extension, f.content_type, f.size_bytes, f.created_at, u.username AS uploaded_by FROM ind_files f JOIN ind_users u ON u.id = f.uploaded_by ORDER BY f.created_at DESC")
            files = cursor.fetchall()
    finally:
        connection.close()
    return {"files": files}


@app.post("/settings/data/files", status_code=status.HTTP_201_CREATED)
def upload_data_file(file: UploadFile = File(...), token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    user = require_admin(token)
    original_name = Path(file.filename or "").name
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_FILE_EXTENSIONS:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Only PDF, CSV and TXT files are allowed")
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File exceeds the 25 MB limit")
    stored_name = f"{secrets.token_hex(24)}{extension}"
    (DATA_ROOT / stored_name).write_bytes(content)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO ind_files (original_name, stored_name, extension, content_type, size_bytes, uploaded_by) VALUES (%s, %s, %s, %s, %s, %s)", (original_name, stored_name, extension, file.content_type, len(content), user["id"]))
            file_id = cursor.lastrowid
    except Exception:
        (DATA_ROOT / stored_name).unlink(missing_ok=True)
        raise
    finally:
        connection.close()
    return {"id": file_id, "name": original_name, "size_bytes": len(content)}


@app.get("/settings/data/files/{file_id}")
def download_data_file(file_id: int, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_admin(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT original_name, stored_name FROM ind_files WHERE id = %s", (file_id,))
            file_record = cursor.fetchone()
    finally:
        connection.close()
    if not file_record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    path = DATA_ROOT / file_record["stored_name"]
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stored file not found")
    return FileResponse(path, filename=file_record["original_name"])


@app.delete("/settings/data/files/{file_id}")
def delete_data_file(file_id: int, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_admin(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT stored_name FROM ind_files WHERE id = %s", (file_id,))
            file_record = cursor.fetchone()
            if not file_record:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
            cursor.execute("DELETE FROM ind_files WHERE id = %s", (file_id,))
    finally:
        connection.close()
    (DATA_ROOT / file_record["stored_name"]).unlink(missing_ok=True)
    return {"deleted": True}


def require_accounts(token: str | None) -> dict:
    user = current_user(token)
    if user["role"] not in {"admin", "accounts"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Accounts role required")
    return user


def require_sales(token: str | None) -> dict:
    user = current_user(token)
    if user["role"] not in {"admin", "selling"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ventas role required")
    return user


@app.get("/sales/customers")
def sales_customers(token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, customer_type, name, document_type, document_number, email, phone, secondary_phone, address, city, contact_name, notes, is_active, created_at FROM sell_customers ORDER BY name")
            customers = cursor.fetchall()
    finally:
        connection.close()
    return {"customers": customers}


@app.post("/sales/customers", status_code=status.HTTP_201_CREATED)
def create_sales_customer(payload: CustomerRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    user = require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            try:
                cursor.execute("INSERT INTO sell_customers (customer_type, name, document_type, document_number, email, phone, secondary_phone, address, city, contact_name, notes, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (payload.customer_type, payload.name, payload.document_type or None, payload.document_number or None, payload.email or None, payload.phone or None, payload.secondary_phone or None, payload.address or None, payload.city or None, payload.contact_name or None, payload.notes or None, user["id"]))
                customer_id = cursor.lastrowid
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A customer with this document already exists") from error
    finally:
        connection.close()
    return {"id": customer_id, "name": payload.name}


@app.patch("/sales/customers/{customer_id}")
def update_sales_customer(customer_id: int, payload: CustomerRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            try:
                cursor.execute("UPDATE sell_customers SET customer_type = %s, name = %s, document_type = %s, document_number = %s, email = %s, phone = %s, secondary_phone = %s, address = %s, city = %s, contact_name = %s, notes = %s WHERE id = %s", (payload.customer_type, payload.name, payload.document_type or None, payload.document_number or None, payload.email or None, payload.phone or None, payload.secondary_phone or None, payload.address or None, payload.city or None, payload.contact_name or None, payload.notes or None, customer_id))
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A customer with this document already exists") from error
            if cursor.rowcount == 0:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")
    finally:
        connection.close()
    return {"updated": True, "id": customer_id}


def parse_quotation_payload(payload_json: str) -> QuotationRequest:
    try:
        return QuotationRequest.model_validate_json(payload_json)
    except Exception as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid quotation data") from error


def store_quotation_pdf(file: UploadFile, user_id: int) -> tuple[int, str]:
    original_name = Path(file.filename or "").name
    extension = Path(original_name).suffix.lower()
    if extension != ".pdf":
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Quotation attachment must be a PDF")
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File exceeds the 25 MB limit")
    stored_name = f"{secrets.token_hex(24)}.pdf"
    (DATA_ROOT / stored_name).write_bytes(content)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO ind_files (original_name, stored_name, extension, content_type, size_bytes, uploaded_by) VALUES (%s, %s, '.pdf', %s, %s, %s)", (original_name, stored_name, file.content_type, len(content), user_id))
            return cursor.lastrowid, original_name
    except Exception:
        (DATA_ROOT / stored_name).unlink(missing_ok=True)
        raise
    finally:
        connection.close()


@app.get("/sales/quotations")
def sales_quotations(token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT q.id, q.customer_id, q.service_order_id, o.order_code, c.name AS customer_name, q.quote_code, q.observations, q.file_id, f.original_name AS file_name, q.created_at, q.updated_at FROM sell_quotations q JOIN sell_customers c ON c.id = q.customer_id LEFT JOIN sell_service_orders o ON o.id = q.service_order_id LEFT JOIN ind_files f ON f.id = q.file_id ORDER BY q.created_at DESC")
            quotations = cursor.fetchall()
            cursor.execute("SELECT id, quotation_id, item_number, description, unit, quantity, unit_cost, subtotal FROM sell_quotation_items ORDER BY quotation_id, item_number")
            items = cursor.fetchall()
    finally:
        connection.close()
    items_by_quote = {}
    for item in items:
        items_by_quote.setdefault(item["quotation_id"], []).append(item)
    for quotation in quotations:
        quotation["items"] = items_by_quote.get(quotation["id"], [])
        quotation["subtotal"] = sum((item["subtotal"] for item in quotation["items"]), Decimal("0"))
        quotation["igv"] = quotation["subtotal"] * IGV_RATE
        quotation["total"] = quotation["subtotal"] + quotation["igv"]
    return {"quotations": quotations}


@app.post("/sales/quotations", status_code=status.HTTP_201_CREATED)
def create_sales_quotation(payload_json: str = Form(...), file: UploadFile | None = File(default=None), token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    user = require_sales(token)
    payload = parse_quotation_payload(payload_json)
    connection = connect()
    stored_file_id = None
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM sell_customers WHERE id = %s AND is_active = 1", (payload.customer_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")
            cursor.execute("SELECT id FROM sell_service_orders WHERE id = %s AND status <> 'cancelled'", (payload.service_order_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service order not found or cancelled")
        if file:
            stored_file_id, _ = store_quotation_pdf(file, user["id"])
        with connection.cursor() as cursor:
            try:
                cursor.execute("INSERT INTO sell_quotations (customer_id, service_order_id, quote_code, observations, file_id, created_by) VALUES (%s, %s, %s, %s, %s, %s)", (payload.customer_id, payload.service_order_id, payload.quote_code, payload.observations or None, stored_file_id, user["id"]))
                quotation_id = cursor.lastrowid
                for item_number, item in enumerate(payload.items, start=1):
                    subtotal = item.quantity * item.unit_cost
                    cursor.execute("INSERT INTO sell_quotation_items (quotation_id, item_number, description, unit, quantity, unit_cost, subtotal) VALUES (%s, %s, %s, %s, %s, %s, %s)", (quotation_id, item_number, item.description, item.unit, item.quantity, item.unit_cost, subtotal))
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A quotation with this code already exists") from error
    except Exception:
        if stored_file_id:
            cleanup_connection = connect()
            stored = None
            try:
                with cleanup_connection.cursor() as cursor:
                    cursor.execute("SELECT stored_name FROM ind_files WHERE id = %s", (stored_file_id,))
                    stored = cursor.fetchone()
                    cursor.execute("DELETE FROM ind_files WHERE id = %s", (stored_file_id,))
            finally:
                cleanup_connection.close()
            if stored:
                (DATA_ROOT / stored["stored_name"]).unlink(missing_ok=True)
        raise
    finally:
        connection.close()
    return {"id": quotation_id, "quote_code": payload.quote_code}


@app.patch("/sales/quotations/{quotation_id}")
def update_sales_quotation(quotation_id: int, payload: QuotationRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM sell_quotations WHERE id = %s", (quotation_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quotation not found")
            cursor.execute("SELECT id FROM sell_service_orders WHERE id = %s AND status <> 'cancelled'", (payload.service_order_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service order not found or cancelled")
            try:
                cursor.execute("UPDATE sell_quotations SET customer_id = %s, service_order_id = %s, quote_code = %s, observations = %s WHERE id = %s", (payload.customer_id, payload.service_order_id, payload.quote_code, payload.observations or None, quotation_id))
                cursor.execute("DELETE FROM sell_quotation_items WHERE quotation_id = %s", (quotation_id,))
                for item_number, item in enumerate(payload.items, start=1):
                    cursor.execute("INSERT INTO sell_quotation_items (quotation_id, item_number, description, unit, quantity, unit_cost, subtotal) VALUES (%s, %s, %s, %s, %s, %s, %s)", (quotation_id, item_number, item.description, item.unit, item.quantity, item.unit_cost, item.quantity * item.unit_cost))
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A quotation with this code already exists") from error
    finally:
        connection.close()
    return {"updated": True, "id": quotation_id}


@app.get("/sales/quotations/{quotation_id}/file")
def download_quotation_file(quotation_id: int, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT f.original_name, f.stored_name FROM sell_quotations q JOIN ind_files f ON f.id = q.file_id WHERE q.id = %s", (quotation_id,))
            file_record = cursor.fetchone()
    finally:
        connection.close()
    if not file_record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quotation PDF not found")
    path = DATA_ROOT / file_record["stored_name"]
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stored file not found")
    return FileResponse(path, filename=file_record["original_name"], media_type="application/pdf")


def quotation_pdf(quotation: dict, items: list[dict]) -> bytes:
    subtotal = sum((item["subtotal"] for item in items), Decimal("0"))
    igv = subtotal * IGV_RATE
    total = subtotal + igv
    buffer = io.BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm)
    styles = getSampleStyleSheet()
    story = [Paragraph("COTIZACIÓN", styles["Title"]), Spacer(1, 5 * mm), Paragraph(f"Código: {quotation['quote_code']}", styles["Normal"]), Paragraph(f"Cliente: {quotation['customer_name']}", styles["Normal"]), Spacer(1, 6 * mm)]
    table_data = [["Item", "Descripción", "Unidad", "Cantidad", "Costo unitario", "Subtotal"]]
    for item in items:
        table_data.append([str(item["item_number"]), str(item["description"]), str(item["unit"]), f"{item['quantity']:.2f}", f"{item['unit_cost']:.2f}", f"{item['subtotal']:.2f}"])
    table = Table(table_data, colWidths=[12 * mm, 70 * mm, 22 * mm, 22 * mm, 30 * mm, 30 * mm], repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#087f78")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), .35, colors.HexColor("#dbe5e8")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (0, 0), (0, -1), "CENTER"), ("ALIGN", (3, 1), (-1, -1), "RIGHT"), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, 0), 7), ("TOPPADDING", (0, 0), (-1, 0), 7)]))
    story.extend([table, Spacer(1, 6 * mm), Paragraph(f"Subtotal: {subtotal:.2f}", styles["Normal"]), Paragraph(f"IGV (18%): {igv:.2f}", styles["Normal"]), Paragraph(f"Total de cotización: {total:.2f}", styles["Heading3"])])
    if quotation.get("observations"):
        story.extend([Spacer(1, 5 * mm), Paragraph("Observaciones", styles["Heading3"]), Paragraph(str(quotation["observations"]), styles["Normal"])])
    document.build(story)
    return buffer.getvalue()


@app.get("/sales/quotations/{quotation_id}/pdf")
def download_generated_quotation_pdf(quotation_id: int, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT q.id, q.quote_code, q.observations, c.name AS customer_name FROM sell_quotations q JOIN sell_customers c ON c.id = q.customer_id WHERE q.id = %s", (quotation_id,))
            quotation = cursor.fetchone()
            if quotation:
                cursor.execute("SELECT item_number, description, unit, quantity, unit_cost, subtotal FROM sell_quotation_items WHERE quotation_id = %s ORDER BY item_number", (quotation_id,))
                items = cursor.fetchall()
    finally:
        connection.close()
    if not quotation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quotation not found")
    pdf = quotation_pdf(quotation, items)
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="cotizacion-{quotation["quote_code"]}.pdf"'})


@app.get("/sales/technical-visits")
def sales_technical_visits(token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT v.id, v.customer_id, v.service_order_id, o.order_code, c.name AS customer_name, v.title, v.scheduled_at, v.address, v.city, v.latitude, v.longitude, v.status, v.observations, v.created_at, v.updated_at FROM sell_technical_visits v JOIN sell_customers c ON c.id = v.customer_id LEFT JOIN sell_service_orders o ON o.id = v.service_order_id ORDER BY COALESCE(v.scheduled_at, v.created_at) DESC")
            visits = cursor.fetchall()
    finally:
        connection.close()
    return {"visits": visits}


@app.post("/sales/technical-visits", status_code=status.HTTP_201_CREATED)
def create_sales_technical_visit(payload: TechnicalVisitRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    user = require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM sell_customers WHERE id = %s AND is_active = 1", (payload.customer_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")
            if payload.service_order_id:
                cursor.execute("SELECT id FROM sell_service_orders WHERE id = %s AND status <> 'cancelled'", (payload.service_order_id,))
                if not cursor.fetchone():
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service order not found or cancelled")
            cursor.execute("INSERT INTO sell_technical_visits (customer_id, service_order_id, title, scheduled_at, address, city, latitude, longitude, status, observations, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (payload.customer_id, payload.service_order_id, payload.title, payload.scheduled_at, payload.address, payload.city or None, payload.latitude, payload.longitude, payload.status, payload.observations or None, user["id"]))
            visit_id = cursor.lastrowid
    finally:
        connection.close()
    return {"id": visit_id, "title": payload.title}


@app.patch("/sales/technical-visits/{visit_id}")
def update_sales_technical_visit(visit_id: int, payload: TechnicalVisitRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM sell_technical_visits WHERE id = %s", (visit_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Technical visit not found")
            cursor.execute("SELECT id FROM sell_customers WHERE id = %s AND is_active = 1", (payload.customer_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")
            if payload.service_order_id:
                cursor.execute("SELECT id FROM sell_service_orders WHERE id = %s AND status <> 'cancelled'", (payload.service_order_id,))
                if not cursor.fetchone():
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service order not found or cancelled")
            cursor.execute("UPDATE sell_technical_visits SET customer_id = %s, service_order_id = %s, title = %s, scheduled_at = %s, address = %s, city = %s, latitude = %s, longitude = %s, status = %s, observations = %s WHERE id = %s", (payload.customer_id, payload.service_order_id, payload.title, payload.scheduled_at, payload.address, payload.city or None, payload.latitude, payload.longitude, payload.status, payload.observations or None, visit_id))
    finally:
        connection.close()
    return {"updated": True, "id": visit_id}


def parse_service_order_payload(payload_json: str) -> ServiceOrderRequest:
    try:
        return ServiceOrderRequest.model_validate_json(payload_json)
    except Exception as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid service order data") from error


def store_service_order_document(file: UploadFile, user_id: int) -> int:
    original_name = Path(file.filename or "").name
    extension = Path(original_name).suffix.lower()
    if extension not in SERVICE_ORDER_FILE_EXTENSIONS:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Supported documents: PDF, DOC, DOCX and TXT")
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File exceeds the 25 MB limit")
    stored_name = f"{secrets.token_hex(24)}{extension}"
    (DATA_ROOT / stored_name).write_bytes(content)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO ind_files (original_name, stored_name, extension, content_type, size_bytes, uploaded_by) VALUES (%s, %s, %s, %s, %s, %s)", (original_name, stored_name, extension, file.content_type, len(content), user_id))
            return cursor.lastrowid
    except Exception:
        (DATA_ROOT / stored_name).unlink(missing_ok=True)
        raise
    finally:
        connection.close()


@app.get("/sales/service-orders")
def sales_service_orders(token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT o.id, o.customer_id, c.name AS customer_name, o.order_code, o.description, o.project_type, o.observations, o.approver_name, o.approver_email, o.approver_phone, o.approved_at, o.project_due_date, o.status, o.file_id, f.original_name AS file_name, o.created_at, o.updated_at FROM sell_service_orders o JOIN sell_customers c ON c.id = o.customer_id LEFT JOIN ind_files f ON f.id = o.file_id ORDER BY o.approved_at DESC, o.created_at DESC")
            orders = cursor.fetchall()
    finally:
        connection.close()
    return {"orders": orders}


@app.get("/sales/dashboard")
def sales_dashboard(token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS value FROM sell_customers WHERE is_active = 1")
            active_customers = cursor.fetchone()["value"]
            cursor.execute("SELECT COUNT(DISTINCT q.id) AS value, COALESCE(SUM(i.subtotal), 0) AS subtotal FROM sell_quotations q LEFT JOIN sell_quotation_items i ON i.quotation_id = q.id")
            quotation_summary = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) AS value FROM sell_service_orders WHERE status <> 'cancelled'")
            service_orders = cursor.fetchone()["value"]
            cursor.execute("SELECT COUNT(*) AS value FROM sell_service_orders WHERE status IN ('approved', 'in_progress')")
            orders_in_progress = cursor.fetchone()["value"]
            cursor.execute("SELECT COUNT(*) AS value FROM sell_service_orders WHERE project_due_date BETWEEN CURDATE() AND DATE_ADD(CURDATE(), INTERVAL 30 DAY) AND status <> 'cancelled'")
            orders_due_soon = cursor.fetchone()["value"]
            cursor.execute("SELECT COUNT(*) AS value FROM sell_technical_visits WHERE status = 'planned'")
            pending_visits = cursor.fetchone()["value"]
    finally:
        connection.close()
    subtotal = Decimal(str(quotation_summary["subtotal"] or 0))
    return {
        "active_customers": active_customers,
        "quotations": quotation_summary["value"],
        "quotation_subtotal": subtotal,
        "quotation_total": subtotal + (subtotal * IGV_RATE),
        "service_orders": service_orders,
        "orders_in_progress": orders_in_progress,
        "orders_due_soon": orders_due_soon,
        "pending_visits": pending_visits,
    }


@app.post("/sales/service-orders", status_code=status.HTTP_201_CREATED)
def create_sales_service_order(payload_json: str = Form(...), file: UploadFile | None = File(default=None), token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    user = require_sales(token)
    payload = parse_service_order_payload(payload_json)
    if payload.project_due_date and payload.project_due_date < payload.approved_at:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Project due date cannot be before reception date")
    connection = connect()
    stored_file_id = None
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM sell_customers WHERE id = %s AND is_active = 1", (payload.customer_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")
        if file:
            stored_file_id = store_service_order_document(file, user["id"])
        with connection.cursor() as cursor:
            try:
                cursor.execute("INSERT INTO sell_service_orders (customer_id, order_code, description, project_type, observations, approver_name, approver_email, approver_phone, approved_at, project_due_date, status, file_id, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (payload.customer_id, payload.order_code, payload.description, payload.project_type, payload.observations or None, payload.approver_name, payload.approver_email or None, payload.approver_phone or None, payload.approved_at, payload.project_due_date, payload.status, stored_file_id, user["id"]))
                order_id = cursor.lastrowid
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A service order with this code already exists") from error
    except Exception:
        if stored_file_id:
            cleanup_connection = connect()
            try:
                with cleanup_connection.cursor() as cursor:
                    cursor.execute("SELECT stored_name FROM ind_files WHERE id = %s", (stored_file_id,))
                    stored = cursor.fetchone()
                    cursor.execute("DELETE FROM ind_files WHERE id = %s", (stored_file_id,))
            finally:
                cleanup_connection.close()
            if stored:
                (DATA_ROOT / stored["stored_name"]).unlink(missing_ok=True)
        raise
    finally:
        connection.close()
    return {"id": order_id, "order_code": payload.order_code}


@app.patch("/sales/service-orders/{order_id}")
def update_sales_service_order(order_id: int, payload: ServiceOrderRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    if payload.project_due_date and payload.project_due_date < payload.approved_at:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Project due date cannot be before reception date")
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM sell_service_orders WHERE id = %s", (order_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service order not found")
            cursor.execute("SELECT id FROM sell_customers WHERE id = %s AND is_active = 1", (payload.customer_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")
            try:
                cursor.execute("UPDATE sell_service_orders SET customer_id = %s, order_code = %s, description = %s, project_type = %s, observations = %s, approver_name = %s, approver_email = %s, approver_phone = %s, approved_at = %s, project_due_date = %s, status = %s WHERE id = %s", (payload.customer_id, payload.order_code, payload.description, payload.project_type, payload.observations or None, payload.approver_name, payload.approver_email or None, payload.approver_phone or None, payload.approved_at, payload.project_due_date, payload.status, order_id))
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A service order with this code already exists") from error
    finally:
        connection.close()
    return {"updated": True, "id": order_id}


@app.get("/sales/service-orders/{order_id}/file")
def download_service_order_file(order_id: int, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT f.original_name, f.stored_name, f.content_type FROM sell_service_orders o JOIN ind_files f ON f.id = o.file_id WHERE o.id = %s", (order_id,))
            file_record = cursor.fetchone()
    finally:
        connection.close()
    if not file_record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service order document not found")
    path = DATA_ROOT / file_record["stored_name"]
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stored file not found")
    return FileResponse(path, filename=file_record["original_name"], media_type=file_record["content_type"] or "application/octet-stream")


def parse_project_area_payload(payload_json: str) -> ProjectAreaDocumentRequest:
    try:
        return ProjectAreaDocumentRequest.model_validate_json(payload_json)
    except Exception as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid project area data") from error


def store_project_area_pdf(file: UploadFile, user_id: int) -> int:
    original_name = Path(file.filename or "").name
    if Path(original_name).suffix.lower() != ".pdf":
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Area documentation must be a PDF")
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File exceeds the 25 MB limit")
    stored_name = f"{secrets.token_hex(24)}.pdf"
    (DATA_ROOT / stored_name).write_bytes(content)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO ind_files (original_name, stored_name, extension, content_type, size_bytes, uploaded_by) VALUES (%s, %s, '.pdf', %s, %s, %s)", (original_name, stored_name, file.content_type, len(content), user_id))
            return cursor.lastrowid
    except Exception:
        (DATA_ROOT / stored_name).unlink(missing_ok=True)
        raise
    finally:
        connection.close()


@app.get("/projects/service-orders")
def projects_service_orders(token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, order_code, description, approved_at, project_due_date, status FROM sell_service_orders WHERE status <> 'cancelled' ORDER BY approved_at DESC, order_code")
            orders = cursor.fetchall()
    finally:
        connection.close()
    return {"orders": orders}


@app.get("/projects/projects")
def projects_list(token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT p.id, p.project_code, p.name, p.service_order_id, o.order_code, p.start_date, p.planned_end_date, p.end_date, CASE WHEN p.end_date IS NOT NULL THEN DATEDIFF(p.end_date, p.start_date) ELSE DATEDIFF(p.planned_end_date, CURDATE()) END AS days_remaining, p.description, p.observations, p.status, p.created_at, p.updated_at, COUNT(d.id) AS documentation_count, SUM(CASE WHEN d.file_id IS NOT NULL THEN 1 ELSE 0 END) AS documents_uploaded FROM proj_projects p JOIN sell_service_orders o ON o.id = p.service_order_id LEFT JOIN proj_area_documents d ON d.project_id = p.id GROUP BY p.id ORDER BY p.created_at DESC")
            projects = cursor.fetchall()
    finally:
        connection.close()
    return {"projects": projects}


@app.get("/projects/dashboard")
def projects_dashboard(token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS value FROM proj_projects")
            total_projects = cursor.fetchone()["value"]
            cursor.execute("SELECT COUNT(*) AS value FROM proj_projects WHERE status IN ('planned', 'active')")
            active_projects = cursor.fetchone()["value"]
            cursor.execute("SELECT COUNT(*) AS value FROM proj_projects WHERE status = 'completed'")
            completed_projects = cursor.fetchone()["value"]
            cursor.execute("SELECT COUNT(*) AS value FROM proj_projects WHERE planned_end_date < CURDATE() AND end_date IS NULL AND status NOT IN ('completed', 'cancelled')")
            overdue_projects = cursor.fetchone()["value"]
            cursor.execute("SELECT COUNT(*) AS value FROM proj_tasks")
            total_tasks = cursor.fetchone()["value"]
            cursor.execute("SELECT COUNT(*) AS value FROM proj_tasks WHERE status IN ('in_progress', 'blocked')")
            active_tasks = cursor.fetchone()["value"]
            cursor.execute("SELECT COUNT(*) AS value FROM proj_tasks WHERE is_dead_time = 1")
            dead_time_tasks = cursor.fetchone()["value"]
            cursor.execute("SELECT COALESCE(SUM(CASE WHEN is_dead_time = 1 AND COALESCE(actual_start, planned_start) IS NOT NULL THEN DATEDIFF(COALESCE(actual_end, CURDATE()), COALESCE(actual_start, planned_start)) + 1 ELSE 0 END), 0) AS value FROM proj_tasks")
            dead_time_days = cursor.fetchone()["value"]
            cursor.execute("SELECT COUNT(*) AS total, COALESCE(SUM(status = 'approved'), 0) AS approved FROM proj_area_documents")
            documentation = cursor.fetchone()
            cursor.execute("SELECT p.project_code, p.name, p.status, p.start_date, p.planned_end_date, p.end_date, COUNT(t.id) AS task_count, SUM(CASE WHEN t.status = 'completed' THEN 1 ELSE 0 END) AS completed_tasks, SUM(CASE WHEN t.is_dead_time = 1 THEN 1 ELSE 0 END) AS dead_time_tasks FROM proj_projects p LEFT JOIN proj_tasks t ON t.project_id = p.id GROUP BY p.id ORDER BY p.planned_end_date IS NULL, p.planned_end_date, p.project_code")
            portfolio = cursor.fetchall()
    finally:
        connection.close()
    return {
        "total_projects": total_projects,
        "active_projects": active_projects,
        "completed_projects": completed_projects,
        "overdue_projects": overdue_projects,
        "total_tasks": total_tasks,
        "active_tasks": active_tasks,
        "dead_time_tasks": dead_time_tasks,
        "dead_time_days": dead_time_days,
        "documentation_total": documentation["total"],
        "documentation_approved": documentation["approved"],
        "portfolio": portfolio,
    }


@app.get("/projects/gantt")
def projects_gantt(project_id: int | None = None, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, project_code, name, start_date, planned_end_date, end_date, status FROM proj_projects ORDER BY project_code")
            projects = cursor.fetchall()
            selected = project_id or (projects[0]["id"] if projects else None)
            tasks = []
            project = next((item for item in projects if item["id"] == selected), None)
            if project:
                cursor.execute("SELECT id, project_id, task_code, name, task_type, area_key, planned_start, planned_end, actual_start, actual_end, status, is_dead_time, observations FROM proj_tasks WHERE project_id = %s ORDER BY COALESCE(planned_start, %s), COALESCE(planned_end, %s), task_code", (selected, project["start_date"], project["start_date"]))
                tasks = cursor.fetchall()
    finally:
        connection.close()
    return {"projects": projects, "project": project, "tasks": tasks, "today": date.today()}


@app.post("/projects/projects", status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    user = require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, approved_at, project_due_date FROM sell_service_orders WHERE id = %s", (payload.service_order_id,))
            service_order = cursor.fetchone()
            if not service_order:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service order not found")
            end_date = payload.end_date if payload.status == "completed" else None
            if payload.status == "completed" and end_date is None:
                end_date = date.today()
            try:
                cursor.execute("INSERT INTO proj_projects (project_code, name, service_order_id, start_date, planned_end_date, end_date, description, observations, status, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (payload.project_code, payload.name, payload.service_order_id, service_order["approved_at"], service_order.get("project_due_date"), end_date, payload.description or None, payload.observations or None, payload.status, user["id"]))
                project_id = cursor.lastrowid
                ensure_project_area_tasks(cursor)
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A project with this code already exists") from error
    finally:
        connection.close()
    return {"id": project_id, "project_code": payload.project_code}


@app.patch("/projects/projects/{project_id}")
def update_project(project_id: int, payload: ProjectRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM proj_projects WHERE id = %s", (project_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
            cursor.execute("SELECT id, approved_at, project_due_date FROM sell_service_orders WHERE id = %s", (payload.service_order_id,))
            service_order = cursor.fetchone()
            if not service_order:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service order not found")
            end_date = payload.end_date if payload.status == "completed" else None
            if payload.status == "completed" and end_date is None:
                end_date = date.today()
            try:
                cursor.execute("UPDATE proj_projects SET project_code = %s, name = %s, service_order_id = %s, start_date = %s, planned_end_date = %s, end_date = %s, description = %s, observations = %s, status = %s WHERE id = %s", (payload.project_code, payload.name, payload.service_order_id, service_order["approved_at"], service_order.get("project_due_date"), end_date, payload.description or None, payload.observations or None, payload.status, project_id))
                cursor.execute("UPDATE proj_tasks SET planned_start = %s, planned_end = %s WHERE project_id = %s AND task_type = 'documentation'", (service_order["approved_at"], service_order.get("project_due_date"), project_id))
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A project with this code already exists") from error
    finally:
        connection.close()
    return {"updated": True, "id": project_id}


@app.get("/projects/tasks")
def projects_tasks(token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT t.id, t.project_id, p.project_code, p.name AS project_name, t.task_code, t.name, t.task_type, t.area_key, t.planned_start, t.planned_end, t.actual_start, t.actual_end, t.status, t.is_dead_time, t.observations, t.created_at, t.updated_at FROM proj_tasks t JOIN proj_projects p ON p.id = t.project_id ORDER BY COALESCE(t.planned_start, p.start_date), t.project_id, t.task_code")
            tasks = cursor.fetchall()
            cursor.execute("SELECT id, project_code, name, start_date, planned_end_date, end_date FROM proj_projects ORDER BY project_code")
            projects = cursor.fetchall()
    finally:
        connection.close()
    return {"tasks": tasks, "projects": projects}


@app.post("/projects/tasks", status_code=status.HTTP_201_CREATED)
def create_project_task(payload: ProjectTaskRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    user = require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM proj_projects WHERE id = %s", (payload.project_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
            try:
                cursor.execute("INSERT INTO proj_tasks (project_id, task_code, name, task_type, area_key, planned_start, planned_end, actual_start, actual_end, status, is_dead_time, observations, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (payload.project_id, payload.task_code, payload.name, payload.task_type, payload.area_key or None, payload.planned_start, payload.planned_end, payload.actual_start, payload.actual_end, payload.status, payload.is_dead_time, payload.observations or None, user["id"]))
                task_id = cursor.lastrowid
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A task with this code already exists in the project") from error
    finally:
        connection.close()
    return {"id": task_id, "task_code": payload.task_code}


@app.patch("/projects/tasks/{task_id}")
def update_project_task(task_id: int, payload: ProjectTaskRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    user = require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM proj_tasks WHERE id = %s", (task_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
            cursor.execute("SELECT id FROM proj_projects WHERE id = %s", (payload.project_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
            try:
                cursor.execute("UPDATE proj_tasks SET project_id = %s, task_code = %s, name = %s, task_type = %s, area_key = %s, planned_start = %s, planned_end = %s, actual_start = %s, actual_end = %s, status = %s, is_dead_time = %s, observations = %s WHERE id = %s", (payload.project_id, payload.task_code, payload.name, payload.task_type, payload.area_key or None, payload.planned_start, payload.planned_end, payload.actual_start, payload.actual_end, payload.status, payload.is_dead_time, payload.observations or None, task_id))
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A task with this code already exists in the project") from error
    finally:
        connection.close()
    return {"updated": True, "id": task_id}


@app.get("/projects/projects/{project_id}/documentation")
def project_documentation(project_id: int, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM proj_projects WHERE id = %s", (project_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
            cursor.execute("SELECT d.id, d.area_key, d.contact_name, d.observations, d.entered_at, d.status, d.approved_at, d.file_id, f.original_name AS file_name FROM proj_area_documents d LEFT JOIN ind_files f ON f.id = d.file_id WHERE d.project_id = %s", (project_id,))
            records = {record["area_key"]: record for record in cursor.fetchall()}
    finally:
        connection.close()
    areas = []
    for area_key, area_name in PROJECT_AREAS.items():
        record = records.get(area_key, {"id": None, "area_key": area_key, "contact_name": None, "observations": None, "entered_at": None, "status": "pending", "approved_at": None, "file_id": None, "file_name": None})
        record["area_name"] = area_name
        areas.append(record)
    return {"areas": areas}


@app.put("/projects/projects/{project_id}/documentation/{area_key}")
def save_project_area_document(project_id: int, area_key: str, payload_json: str = Form(...), file: UploadFile | None = File(default=None), token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    user = require_sales(token)
    if area_key not in PROJECT_AREAS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project area not found")
    payload = parse_project_area_payload(payload_json)
    connection = connect()
    new_file_id = None
    old_file = None
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM proj_projects WHERE id = %s", (project_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
            cursor.execute("SELECT id, file_id FROM proj_area_documents WHERE project_id = %s AND area_key = %s", (project_id, area_key))
            current = cursor.fetchone()
        if file:
            new_file_id = store_project_area_pdf(file, user["id"])
        file_id = new_file_id if new_file_id else (current["file_id"] if current else None)
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO proj_area_documents (project_id, area_key, contact_name, observations, entered_at, status, approved_at, file_id, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE contact_name = VALUES(contact_name), observations = VALUES(observations), entered_at = VALUES(entered_at), status = VALUES(status), approved_at = VALUES(approved_at), file_id = VALUES(file_id)", (project_id, area_key, payload.contact_name, payload.observations or None, payload.entered_at, payload.status, payload.approved_at, file_id, user["id"]))
            task_status = {"approved": "completed", "rejected": "blocked", "in_review": "in_progress"}.get(payload.status, "planned")
            cursor.execute("UPDATE proj_tasks SET actual_start = %s, actual_end = %s, status = %s, observations = %s WHERE project_id = %s AND area_key = %s AND task_type = 'documentation'", (payload.entered_at, payload.approved_at, task_status, payload.observations or "Tiempo de espera documental por área.", project_id, area_key))
            if new_file_id and current and current["file_id"]:
                cursor.execute("SELECT stored_name FROM ind_files WHERE id = %s", (current["file_id"],))
                old_file = cursor.fetchone()
                cursor.execute("DELETE FROM ind_files WHERE id = %s", (current["file_id"],))
    except Exception:
        if new_file_id:
            cleanup_connection = connect()
            stored = None
            try:
                with cleanup_connection.cursor() as cursor:
                    cursor.execute("SELECT stored_name FROM ind_files WHERE id = %s", (new_file_id,))
                    stored = cursor.fetchone()
                    cursor.execute("DELETE FROM ind_files WHERE id = %s", (new_file_id,))
            finally:
                cleanup_connection.close()
            if stored:
                (DATA_ROOT / stored["stored_name"]).unlink(missing_ok=True)
        raise
    finally:
        connection.close()
    if old_file:
        (DATA_ROOT / old_file["stored_name"]).unlink(missing_ok=True)
    return {"saved": True, "project_id": project_id, "area_key": area_key}


@app.get("/projects/projects/{project_id}/documentation/{area_key}/file")
def download_project_area_file(project_id: int, area_key: str, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    if area_key not in PROJECT_AREAS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project area not found")
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT f.original_name, f.stored_name, f.content_type FROM proj_area_documents d JOIN ind_files f ON f.id = d.file_id WHERE d.project_id = %s AND d.area_key = %s", (project_id, area_key))
            file_record = cursor.fetchone()
    finally:
        connection.close()
    if not file_record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Area PDF not found")
    path = DATA_ROOT / file_record["stored_name"]
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stored file not found")
    return FileResponse(path, filename=file_record["original_name"], media_type="application/pdf")


@app.get("/accounts/summary")
def accounts_summary(token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_accounts(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS total FROM acc_companies WHERE is_active = 1")
            companies = cursor.fetchone()["total"]
            cursor.execute("SELECT COUNT(*) AS total FROM acc_accounts WHERE is_active = 1")
            accounts = cursor.fetchone()["total"]
            cursor.execute("SELECT COUNT(*) AS total FROM acc_journal_entries WHERE status = 'posted'")
            entries = cursor.fetchone()["total"]
            cursor.execute("SELECT id, name, default_currency FROM acc_companies WHERE is_active = 1 ORDER BY name")
            company_rows = cursor.fetchall()
    finally:
        connection.close()
    return {"companies": companies, "accounts": accounts, "posted_entries": entries, "company_list": company_rows}


@app.get("/accounts/companies")
def accounts_companies(token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_accounts(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, name, tax_id, default_currency, is_active FROM acc_companies ORDER BY name")
            companies = cursor.fetchall()
    finally:
        connection.close()
    return {"companies": companies}


@app.post("/settings/companies", status_code=status.HTTP_201_CREATED)
def create_company(payload: CompanyRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_admin(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            try:
                cursor.execute("INSERT INTO acc_companies (name, tax_id, description, default_currency) VALUES (%s, %s, %s, %s)", (payload.name, payload.tax_id or None, payload.description, payload.default_currency))
                company_id = cursor.lastrowid
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Company name or tax ID already exists") from error
    finally:
        connection.close()
    return {"id": company_id, "name": payload.name}


@app.patch("/settings/companies/{company_id}")
def update_company(company_id: int, payload: CompanyUpdateRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_admin(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            try:
                cursor.execute("UPDATE acc_companies SET name = %s, tax_id = %s, description = %s, default_currency = %s WHERE id = %s", (payload.name, payload.tax_id or None, payload.description, payload.default_currency, company_id))
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Company tax ID already exists") from error
            if cursor.rowcount == 0:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    finally:
        connection.close()
    return {"updated": True, "id": company_id}


@app.get("/accounts/fiscal-years")
def fiscal_years(company_id: int, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_accounts(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, company_id, name, starts_on, ends_on, is_closed FROM acc_fiscal_years WHERE company_id = %s ORDER BY starts_on DESC", (company_id,))
            years = cursor.fetchall()
    finally:
        connection.close()
    return {"fiscal_years": years}


@app.post("/accounts/fiscal-years", status_code=status.HTTP_201_CREATED)
def create_fiscal_year(payload: FiscalYearRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_accounts(token)
    if payload.starts_on > payload.ends_on:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Fiscal year dates are invalid")
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM acc_companies WHERE id = %s AND is_active = 1", (payload.company_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
            cursor.execute("SELECT id FROM acc_fiscal_years WHERE company_id = %s AND starts_on <= %s AND ends_on >= %s", (payload.company_id, payload.ends_on, payload.starts_on))
            if cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Fiscal year overlaps an existing period")
            cursor.execute("INSERT INTO acc_fiscal_years (company_id, name, starts_on, ends_on) VALUES (%s, %s, %s, %s)", (payload.company_id, payload.name, payload.starts_on, payload.ends_on))
            year_id = cursor.lastrowid
    finally:
        connection.close()
    return {"id": year_id, "name": payload.name}


@app.get("/accounts/accounts")
def accounts_list(company_id: int, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_accounts(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, company_id, parent_id, code, name, root_type, account_type, is_group, is_active FROM acc_accounts WHERE company_id = %s ORDER BY code", (company_id,))
            accounts = cursor.fetchall()
    finally:
        connection.close()
    return {"accounts": accounts}


@app.post("/accounts/accounts", status_code=status.HTTP_201_CREATED)
def create_account(payload: AccountRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_accounts(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM acc_companies WHERE id = %s AND is_active = 1", (payload.company_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
            if payload.parent_id:
                cursor.execute("SELECT id, company_id, is_group FROM acc_accounts WHERE id = %s", (payload.parent_id,))
                parent = cursor.fetchone()
                if not parent or parent["company_id"] != payload.company_id:
                    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Parent account does not belong to the company")
                if not parent["is_group"]:
                    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Parent account must be a group")
            try:
                cursor.execute("INSERT INTO acc_accounts (company_id, parent_id, code, name, root_type, account_type, is_group) VALUES (%s, %s, %s, %s, %s, %s, %s)", (payload.company_id, payload.parent_id, payload.code, payload.name, payload.root_type, payload.account_type, payload.is_group))
                account_id = cursor.lastrowid
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account code already exists in this company") from error
    finally:
        connection.close()
    return {"id": account_id, "code": payload.code, "name": payload.name}


@app.get("/accounts/journal-entries")
def journal_entries(company_id: int, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_accounts(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT e.id, e.posting_date, e.voucher_type, e.reference, e.remarks, e.status, COALESCE(SUM(l.debit), 0) AS total FROM acc_journal_entries e JOIN acc_journal_lines l ON l.journal_entry_id = e.id WHERE e.company_id = %s GROUP BY e.id ORDER BY e.posting_date DESC, e.id DESC LIMIT 50", (company_id,))
            entries = cursor.fetchall()
    finally:
        connection.close()
    return {"entries": entries}


@app.post("/accounts/journal-entries", status_code=status.HTTP_201_CREATED)
def create_journal_entry(payload: JournalEntryRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    user = require_accounts(token)
    if any((line.debit > 0) == (line.credit > 0) for line in payload.lines):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Each line must contain either debit or credit")
    total_debit = sum((line.debit for line in payload.lines), Decimal("0"))
    total_credit = sum((line.credit for line in payload.lines), Decimal("0"))
    if total_debit <= 0 or total_debit != total_credit:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Journal entry must be balanced")
    connection = connect()
    try:
        connection.begin()
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, starts_on, ends_on, is_closed FROM acc_fiscal_years WHERE id = %s AND company_id = %s", (payload.fiscal_year_id, payload.company_id))
            fiscal_year = cursor.fetchone()
            if not fiscal_year:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fiscal year not found for company")
            if fiscal_year["is_closed"] or not (fiscal_year["starts_on"] <= payload.posting_date <= fiscal_year["ends_on"]):
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Posting date is outside an open fiscal year")
            account_ids = [line.account_id for line in payload.lines]
            placeholders = ",".join(["%s"] * len(account_ids))
            cursor.execute(f"SELECT id FROM acc_accounts WHERE company_id = %s AND is_active = 1 AND id IN ({placeholders})", [payload.company_id, *account_ids])
            valid_accounts = {row["id"] for row in cursor.fetchall()}
            if valid_accounts != set(account_ids):
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="All accounts must belong to the company")
            cursor.execute("INSERT INTO acc_journal_entries (company_id, fiscal_year_id, posting_date, voucher_type, reference, remarks, status, created_by) VALUES (%s, %s, %s, %s, %s, %s, 'posted', %s)", (payload.company_id, payload.fiscal_year_id, payload.posting_date, payload.voucher_type, payload.reference, payload.remarks, user["id"]))
            entry_id = cursor.lastrowid
            for line in payload.lines:
                cursor.execute("INSERT INTO acc_journal_lines (journal_entry_id, account_id, description, debit, credit) VALUES (%s, %s, %s, %s, %s)", (entry_id, line.account_id, line.description, line.debit, line.credit))
        connection.commit()
    except HTTPException:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {"id": entry_id, "status": "posted", "total": total_debit}


@app.get("/modules")
def modules(token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    user = current_user(token)
    available_modules = [
            {"key": "overview", "label": "Resumen", "status": "active", "description": "Estado de la plataforma independiente."},
            {"key": "accounts", "label": "Finanzas", "status": "active", "description": "Dashboard, cuentas y operación financiera propia."},
            {"key": "selling", "label": "Ventas", "status": "planned", "description": "Clientes, cotizaciones y órdenes de servicio."},
            {"key": "purchases", "label": "Compras", "status": "planned", "description": "Proveedores, solicitudes, cotizaciones y órdenes de compra."},
            {"key": "inventory", "label": "Inventario", "status": "planned", "description": "Items, almacenes, flujo de stock y reposiciones."},
            {"key": "production", "label": "Producción", "status": "planned", "description": "BOM, planes, órdenes de servicio y mano de obra."},
            {"key": "projects", "label": "Gestión de Proyectos", "status": "planned", "description": "Proyectos, tareas, Gantts y timesheets."},
            {"key": "integrations", "label": "Integraciones", "status": "active", "description": "Webhooks y APIs externas."},
            {"key": "settings", "label": "Configuración", "status": "active", "description": "Compañía, organización, accesos, email y data."},
        ]
    if user["role"] != "admin":
        available_modules = [module for module in available_modules if module["key"] != "settings"]
    return {
        "user": user["username"],
        "modules": available_modules,
    }


@app.get("/overview")
def overview(token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    user = current_user(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS total FROM ind_users WHERE is_active = 1")
            users = cursor.fetchone()["total"]
    finally:
        connection.close()
    return {
        "user": user["username"],
        "cards": [
            {"label": "Usuarios activos", "value": users, "tone": "teal"},
            {"label": "Módulos activos", "value": 3, "tone": "blue"},
            {"label": "Eventos pendientes", "value": 0, "tone": "amber"},
        ],
    }
