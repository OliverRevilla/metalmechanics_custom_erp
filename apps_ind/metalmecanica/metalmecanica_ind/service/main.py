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
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS hr_workers (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    worker_code VARCHAR(40) NOT NULL UNIQUE,
                    worker_type VARCHAR(20) NOT NULL DEFAULT 'internal',
                    full_name VARCHAR(180) NOT NULL,
                    document_type VARCHAR(20) NULL,
                    document_number VARCHAR(40) NULL,
                    email VARCHAR(255) NULL,
                    phone VARCHAR(40) NULL,
                    role_title VARCHAR(120) NOT NULL,
                    specialty VARCHAR(160) NULL,
                    hire_date DATE NULL,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    notes TEXT NULL,
                    created_by BIGINT UNSIGNED NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_hr_worker_document (document_type, document_number),
                    CONSTRAINT fk_hr_worker_user FOREIGN KEY (created_by) REFERENCES ind_users(id) ON DELETE RESTRICT
                ) ENGINE=InnoDB
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS hr_engagements (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    worker_id BIGINT UNSIGNED NOT NULL,
                    project_id BIGINT UNSIGNED NULL,
                    engagement_type VARCHAR(20) NOT NULL DEFAULT 'internal',
                    start_date DATE NOT NULL,
                    end_date DATE NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'active',
                    cost_unit VARCHAR(20) NOT NULL DEFAULT 'month',
                    rate DECIMAL(18,6) NOT NULL DEFAULT 0,
                    estimated_hours DECIMAL(18,6) NULL,
                    notes TEXT NULL,
                    created_by BIGINT UNSIGNED NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    CONSTRAINT fk_hr_engagement_worker FOREIGN KEY (worker_id) REFERENCES hr_workers(id) ON DELETE RESTRICT,
                    CONSTRAINT fk_hr_engagement_project FOREIGN KEY (project_id) REFERENCES proj_projects(id) ON DELETE SET NULL,
                    CONSTRAINT fk_hr_engagement_user FOREIGN KEY (created_by) REFERENCES ind_users(id) ON DELETE RESTRICT
                ) ENGINE=InnoDB
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS hr_payroll_periods (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    period_code VARCHAR(20) NOT NULL UNIQUE,
                    starts_on DATE NOT NULL,
                    ends_on DATE NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'draft',
                    notes TEXT NULL,
                    created_by BIGINT UNSIGNED NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT fk_hr_payroll_user FOREIGN KEY (created_by) REFERENCES ind_users(id) ON DELETE RESTRICT
                ) ENGINE=InnoDB
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS hr_payroll_lines (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    period_id BIGINT UNSIGNED NOT NULL,
                    worker_id BIGINT UNSIGNED NOT NULL,
                    engagement_id BIGINT UNSIGNED NULL,
                    days_worked DECIMAL(18,6) NOT NULL DEFAULT 0,
                    hours_worked DECIMAL(18,6) NOT NULL DEFAULT 0,
                    gross_amount DECIMAL(18,6) NOT NULL DEFAULT 0,
                    employer_cost DECIMAL(18,6) NOT NULL DEFAULT 0,
                    payment_status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    notes TEXT NULL,
                    created_by BIGINT UNSIGNED NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT fk_hr_line_period FOREIGN KEY (period_id) REFERENCES hr_payroll_periods(id) ON DELETE CASCADE,
                    CONSTRAINT fk_hr_line_worker FOREIGN KEY (worker_id) REFERENCES hr_workers(id) ON DELETE RESTRICT,
                    CONSTRAINT fk_hr_line_engagement FOREIGN KEY (engagement_id) REFERENCES hr_engagements(id) ON DELETE SET NULL,
                    CONSTRAINT fk_hr_line_user FOREIGN KEY (created_by) REFERENCES ind_users(id) ON DELETE RESTRICT
                ) ENGINE=InnoDB
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS buy_suppliers (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    supplier_type VARCHAR(20) NOT NULL DEFAULT 'legal',
                    name VARCHAR(180) NOT NULL,
                    document_type VARCHAR(20) NULL,
                    document_number VARCHAR(40) NULL,
                    supply_type VARCHAR(20) NOT NULL DEFAULT 'products',
                    email VARCHAR(255) NULL,
                    phone VARCHAR(40) NULL,
                    secondary_phone VARCHAR(40) NULL,
                    address VARCHAR(255) NULL,
                    city VARCHAR(100) NULL,
                    contact_name VARCHAR(160) NULL,
                    observations TEXT NULL,
                    relationship_started_on DATE NULL,
                    last_contact_on DATE NULL,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_by BIGINT UNSIGNED NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_buy_supplier_document (document_number),
                    CONSTRAINT fk_buy_supplier_user FOREIGN KEY (created_by) REFERENCES ind_users(id) ON DELETE RESTRICT
                ) ENGINE=InnoDB
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS buy_items (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    item_type VARCHAR(20) NOT NULL,
                    item_code VARCHAR(60) NOT NULL,
                    name VARCHAR(180) NOT NULL,
                    description TEXT NULL,
                    unit VARCHAR(40) NOT NULL DEFAULT 'UND',
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_by BIGINT UNSIGNED NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_buy_item_type_code (item_type, item_code),
                    CONSTRAINT fk_buy_item_user FOREIGN KEY (created_by) REFERENCES ind_users(id) ON DELETE RESTRICT
                ) ENGINE=InnoDB
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS buy_purchase_requests (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    request_type VARCHAR(20) NOT NULL,
                    request_code VARCHAR(80) NOT NULL,
                    item_id BIGINT UNSIGNED NOT NULL,
                    supplier_id BIGINT UNSIGNED NOT NULL,
                    description TEXT NOT NULL,
                    presentation VARCHAR(180) NULL,
                    quantity DECIMAL(18,6) NOT NULL DEFAULT 1,
                    unit VARCHAR(40) NOT NULL DEFAULT 'UND',
                    requested_on DATE NOT NULL,
                    required_by DATE NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'draft',
                    observations TEXT NULL,
                    created_by BIGINT UNSIGNED NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_buy_request_type_code (request_type, request_code),
                    CONSTRAINT fk_buy_request_item FOREIGN KEY (item_id) REFERENCES buy_items(id) ON DELETE RESTRICT,
                    CONSTRAINT fk_buy_request_supplier FOREIGN KEY (supplier_id) REFERENCES buy_suppliers(id) ON DELETE RESTRICT,
                    CONSTRAINT fk_buy_request_user FOREIGN KEY (created_by) REFERENCES ind_users(id) ON DELETE RESTRICT
                ) ENGINE=InnoDB
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS buy_purchase_request_items (
                    request_id BIGINT UNSIGNED NOT NULL,
                    item_id BIGINT UNSIGNED NOT NULL,
                    quantity DECIMAL(18,6) NOT NULL DEFAULT 1,
                    unit VARCHAR(40) NOT NULL DEFAULT 'UND',
                    presentation VARCHAR(180) NULL,
                    PRIMARY KEY (request_id, item_id),
                    CONSTRAINT fk_buy_request_item_request FOREIGN KEY (request_id) REFERENCES buy_purchase_requests(id) ON DELETE CASCADE,
                    CONSTRAINT fk_buy_request_item_item FOREIGN KEY (item_id) REFERENCES buy_items(id) ON DELETE RESTRICT
                ) ENGINE=InnoDB
                """
            )
            cursor.execute("INSERT IGNORE INTO buy_purchase_request_items (request_id, item_id, quantity, unit, presentation) SELECT id, item_id, quantity, unit, presentation FROM buy_purchase_requests")
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS buy_purchase_quotations (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    request_id BIGINT UNSIGNED NOT NULL,
                    quote_code VARCHAR(80) NOT NULL,
                    supplier_id BIGINT UNSIGNED NOT NULL,
                    quoted_on DATE NOT NULL,
                    valid_until DATE NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'received',
                    total_amount DECIMAL(18,6) NULL,
                    observations TEXT NULL,
                    created_by BIGINT UNSIGNED NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_buy_quote_request_code (request_id, quote_code),
                    CONSTRAINT fk_buy_quote_request FOREIGN KEY (request_id) REFERENCES buy_purchase_requests(id) ON DELETE RESTRICT,
                    CONSTRAINT fk_buy_quote_supplier FOREIGN KEY (supplier_id) REFERENCES buy_suppliers(id) ON DELETE RESTRICT,
                    CONSTRAINT fk_buy_quote_user FOREIGN KEY (created_by) REFERENCES ind_users(id) ON DELETE RESTRICT
                ) ENGINE=InnoDB
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS buy_purchase_orders (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    order_code VARCHAR(80) NOT NULL UNIQUE,
                    quotation_id BIGINT UNSIGNED NOT NULL,
                    ordered_on DATE NOT NULL,
                    expected_on DATE NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'draft',
                    total_amount DECIMAL(18,6) NULL,
                    observations TEXT NULL,
                    created_by BIGINT UNSIGNED NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    CONSTRAINT fk_buy_order_quotation FOREIGN KEY (quotation_id) REFERENCES buy_purchase_quotations(id) ON DELETE RESTRICT,
                    CONSTRAINT fk_buy_order_user FOREIGN KEY (created_by) REFERENCES ind_users(id) ON DELETE RESTRICT
                ) ENGINE=InnoDB
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS buy_purchase_invoices (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    invoice_code VARCHAR(80) NOT NULL UNIQUE,
                    order_id BIGINT UNSIGNED NOT NULL,
                    invoice_date DATE NOT NULL,
                    due_date DATE NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'received',
                    currency CHAR(3) NOT NULL DEFAULT 'PEN',
                    subtotal DECIMAL(18,6) NOT NULL DEFAULT 0,
                    tax_rate DECIMAL(5,4) NOT NULL DEFAULT 0.1800,
                    tax_amount DECIMAL(18,6) NOT NULL DEFAULT 0,
                    total_amount DECIMAL(18,6) NOT NULL DEFAULT 0,
                    observations TEXT NULL,
                    created_by BIGINT UNSIGNED NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    CONSTRAINT fk_buy_invoice_order FOREIGN KEY (order_id) REFERENCES buy_purchase_orders(id) ON DELETE RESTRICT,
                    CONSTRAINT fk_buy_invoice_user FOREIGN KEY (created_by) REFERENCES ind_users(id) ON DELETE RESTRICT
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
    customer_three = customer("20600000003", ("company", "Constructora Horizonte S.A.C.", "RUC", "20600000003", "proyectos@horizonte.test", "+51 999 310 410", "Av. Javier Prado 2250", "Lima", "Patricia Salas", "Cliente industrial con proyectos de ampliación."))
    customer_four = customer("20600000004", ("company", "Planta Industrial del Sur S.A.", "RUC", "20600000004", "ingenieria@planta-sur.test", "+51 999 320 420", "Av. Parra 615", "Arequipa", "Jorge Cárdenas", "Cliente recurrente de mantenimiento y estructuras."))
    customer_five = customer("20600000005", ("company", "Inversiones Costa Norte S.R.L.", "RUC", "20600000005", "compras@costa-norte.test", "+51 999 330 430", "Av. España 1040", "Trujillo", "Renzo Valdivia", "Cliente de infraestructura y obras civiles."))
    customer_six = customer("10400000006", ("person", "Elena Vargas", "DNI", "10400000006", "elena.vargas@test", "+51 999 340 440", "Calle Los Cedros 180", "Lima", "Elena Vargas", "Cliente persona natural para servicio de adecuación."))

    def supplier(document_number: str, values: tuple) -> None:
        cursor.execute("SELECT id FROM buy_suppliers WHERE document_number = %s", (document_number,))
        if cursor.fetchone():
            return
        cursor.execute(
            "INSERT INTO buy_suppliers (supplier_type, name, document_type, document_number, supply_type, email, phone, secondary_phone, address, city, contact_name, observations, relationship_started_on, last_contact_on, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            values + (admin_id,),
        )

    supplier("20600000011", ("legal", "Aceros Andinos S.A.C.", "RUC", "20600000011", "products", "ventas@aceros-andinos.test", "+51 999 501 100", None, "Av. Néstor Gambetta 880", "Callao", "Rosa Medina", "Proveedor dummy de planchas, perfiles y consumibles de acero.", "2026-08-10", "2026-10-18"))
    supplier("20600000012", ("legal", "Logística Ruta Norte S.R.L.", "RUC", "20600000012", "services", "operaciones@ruta-norte.test", "+51 999 502 200", "+51 999 502 201", "Av. Argentina 1550", "Lima", "Diego Salazar", "Proveedor dummy de fletes y transporte de materiales.", "2026-09-02", "2026-10-15"))
    supplier("10400000013", ("natural", "Lucía Paredes", "DNI", "10400000013", "services", "lucia.paredes@test", "+51 999 503 300", None, "Calle Las Flores 220", "Lima", "Lucía Paredes", "Asistencia técnica independiente de TI.", "2026-09-20", "2026-10-20"))

    def item(item_type: str, code: str, name: str, description: str, unit: str) -> int:
        cursor.execute("SELECT id FROM buy_items WHERE item_type = %s AND item_code = %s", (item_type, code))
        existing = cursor.fetchone()
        if existing:
            return existing["id"]
        cursor.execute("INSERT INTO buy_items (item_type, item_code, name, description, unit, created_by) VALUES (%s, %s, %s, %s, %s, %s)", (item_type, code, name, description, unit, admin_id))
        return cursor.lastrowid

    material_steel_stainless = item("material", "MAT-AC-INX", "Acero inoxidable", "Planchas, tubos y perfiles de acero inoxidable.", "KG")
    material_steel_carbon = item("material", "MAT-AC-CARB", "Acero al carbono", "Planchas, perfiles y elementos de acero al carbono.", "KG")
    material_paint = item("material", "MAT-PINT", "Pintura industrial", "Pintura anticorrosiva y acabados para estructuras.", "GLN")
    service_freight = item("service", "SRV-FLETE", "Fletes", "Transporte de materiales y equipos.", "SERV")
    service_accounting = item("service", "SRV-CONT", "Contabilidad", "Servicio contable y tributario externo.", "MES")
    service_notary = item("service", "SRV-NOT", "Notarías", "Trámites y legalizaciones notariales.", "SERV")

    cursor.execute("SELECT id FROM buy_suppliers WHERE document_number = %s", ("20600000011",))
    steel_supplier = cursor.fetchone()["id"]
    cursor.execute("SELECT id FROM buy_suppliers WHERE document_number = %s", ("20600000012",))
    freight_supplier = cursor.fetchone()["id"]

    def purchase_request(request_type: str, code: str, item_id: int, supplier_id: int, description: str, presentation: str, quantity: str, unit: str, requested_on: str, required_by: str, status_value: str) -> int | None:
        cursor.execute("SELECT id FROM buy_purchase_requests WHERE request_type = %s AND request_code = %s", (request_type, code))
        existing = cursor.fetchone()
        if existing:
            return existing["id"]
        cursor.execute("INSERT INTO buy_purchase_requests (request_type, request_code, item_id, supplier_id, description, presentation, quantity, unit, requested_on, required_by, status, observations, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (request_type, code, item_id, supplier_id, description, presentation, quantity, unit, requested_on, required_by, status_value, "Solicitud dummy para validar el flujo de Compras.", admin_id))
        request_id = cursor.lastrowid
        cursor.execute("INSERT IGNORE INTO buy_purchase_request_items (request_id, item_id, quantity, unit, presentation) VALUES (%s, %s, %s, %s, %s)", (request_id, item_id, quantity, unit, presentation))
        return request_id

    material_request_id = purchase_request("material", "SOL-MAT-001", material_steel_stainless, steel_supplier, "Compra para fabricación de componentes de planta.", "Plancha 304 · 2 mm · 1.50 x 3.00 m", "120", "KG", "2026-10-18", "2026-10-30", "requested")
    if material_request_id:
        cursor.execute("INSERT IGNORE INTO buy_purchase_request_items (request_id, item_id, quantity, unit, presentation) VALUES (%s, %s, %s, %s, %s)", (material_request_id, material_steel_carbon, "80", "KG", "Plancha A36 · 3 mm · 1.50 x 3.00 m"))
    purchase_request("material", "SOL-MAT-002", material_paint, steel_supplier, "Pintura para protección de estructura metálica.", "Galón · anticorrosivo epóxico · gris", "25", "GLN", "2026-10-20", "2026-11-05", "draft")
    purchase_request("service", "SOL-SRV-001", service_freight, freight_supplier, "Traslado de perfiles desde almacén del proveedor.", "Camión baranda · Lima Metropolitana", "2", "SERV", "2026-10-19", "2026-10-25", "requested")

    # Synthetic Peruvian-style purchasing data for analysis screens. These names are intentionally fictional.
    extra_suppliers = [
        ("20600000101", "Aceros del Pacífico Industrial S.A.C.", "products", "Callao", "Planchas y perfiles ASTM A36."),
        ("20600000102", "Metalúrgica Andina del Sur S.R.L.", "products", "Arequipa", "Acero inoxidable, galvanizado y cortes."),
        ("20600000103", "Pinturas Costa Verde S.A.C.", "products", "Lima", "Pinturas epóxicas, poliuretano y anticorrosivos."),
        ("20600000104", "Transporte Carga Norte S.A.C.", "services", "Trujillo", "Fletes interprovinciales y carga pesada."),
        ("20600000105", "Nube Andina Tecnología S.A.C.", "services", "Lima", "Equipos, nube, soporte y licenciamiento."),
        ("20600000106", "Gestión Operativa Peruana S.R.L.", "services", "Lima", "Servicios administrativos y contables."),
        ("20600000107", "ERP Talleres del Perú S.A.C.", "services", "Lima", "Implementación, soporte y capacitación ERP."),
        ("20600000108", "Impresos del Centro S.A.C.", "services", "Huancayo", "Impresiones corporativas y señalética."),
        ("20600000109", "Mercado Industrial Creativo S.A.C.", "services", "Lima", "Marketing B2B, contenidos y campañas."),
    ]
    supplier_ids = {}
    for number, name, supply_type, city, notes in extra_suppliers:
        supplier(number, ("legal", name, "RUC", number, supply_type, f"contacto@{number}.test", "+51 999 600 100", None, f"Av. Industrial {number[-3:]}", city, "Contacto Comercial", notes, "2026-01-15", "2026-10-20"))
        cursor.execute("SELECT id FROM buy_suppliers WHERE document_number = %s", (number,))
        supplier_ids[number] = cursor.fetchone()["id"]

    extra_items = [
        ("MAT-AC-A36", "Acero al carbono ASTM A36", "Plancha estructural A36 de 3 mm.", "KG"),
        ("MAT-AC-A572", "Acero estructural ASTM A572", "Planchas de alta resistencia para estructuras.", "KG"),
        ("MAT-AC-GALV", "Acero galvanizado", "Planchas galvanizadas para cobertura y ductos.", "KG"),
        ("MAT-AC-304", "Acero inoxidable AISI 304", "Plancha y tubo inoxidable para ambientes corrosivos.", "KG"),
        ("MAT-AC-316", "Acero inoxidable AISI 316", "Material inoxidable para alta exigencia química.", "KG"),
        ("MAT-PINT-EPOX", "Pintura epóxica industrial", "Recubrimiento de dos componentes para acero.", "GLN"),
        ("MAT-PINT-PU", "Pintura poliuretano", "Acabado poliuretano para estructuras metálicas.", "GLN"),
        ("SRV-FLETE-INTER", "Flete interprovincial", "Transporte de acero y equipos entre ciudades.", "SERV"),
        ("SRV-TEC-SOP", "Soporte tecnológico", "Mesa de ayuda, equipos y soporte de infraestructura.", "MES"),
        ("SRV-ADM-GEST", "Gestión administrativa", "Servicio de apoyo administrativo y documental.", "MES"),
        ("SRV-ERP-IMPL", "Implementación ERP", "Parametrización, capacitación y soporte ERP.", "SERV"),
        ("SRV-IMP-CORP", "Impresiones corporativas", "Formatos, catálogos, tarjetas y señalética.", "SERV"),
        ("SRV-MKT-B2B", "Marketing industrial B2B", "Contenido comercial y campañas digitales.", "MES"),
    ]
    item_ids = {code: item("material" if code.startswith("MAT-") else "service", code, name, description, unit) for code, name, description, unit in extra_items}
    extra_requests = [
        ("material", "SOL-MAT-003", "MAT-AC-A36", "20600000101", "Planchas para fabricación de columnas y vigas.", "Plancha 3 mm · 1.50 x 3.00 m", "450", "KG", "2026-08-12", "2026-08-28", "completed"),
        ("material", "SOL-MAT-004", "MAT-AC-A572", "20600000101", "Material para refuerzo de estructura principal.", "Plancha 6 mm · A572 Gr 50", "620", "KG", "2026-08-30", "2026-09-15", "ordered"),
        ("material", "SOL-MAT-005", "MAT-AC-GALV", "20600000102", "Cobertura y canaletas del taller.", "Plancha galvanizada 1.2 mm", "280", "KG", "2026-09-08", "2026-09-25", "quoted"),
        ("material", "SOL-MAT-006", "MAT-AC-304", "20600000102", "Componentes para zona de proceso.", "Plancha 304 · 2 mm", "190", "KG", "2026-09-21", "2026-10-08", "received"),
        ("material", "SOL-MAT-007", "MAT-PINT-EPOX", "20600000103", "Protección anticorrosiva de estructura.", "Kit epóxico gris · 5 galones", "18", "GLN", "2026-09-25", "2026-10-10", "completed"),
        ("material", "SOL-MAT-008", "MAT-PINT-PU", "20600000103", "Acabado final de equipos fabricados.", "Poliuretano industrial azul", "12", "GLN", "2026-10-02", "2026-10-20", "requested"),
        ("service", "SOL-SRV-002", "SRV-FLETE-INTER", "20600000104", "Traslado de perfiles a planta del cliente.", "Camión plataforma · Lima–Arequipa", "3", "SERV", "2026-08-20", "2026-09-03", "completed"),
        ("service", "SOL-SRV-003", "SRV-TEC-SOP", "20600000105", "Soporte mensual para puestos y red.", "Bolsa de 40 horas", "1", "MES", "2026-09-01", "2026-09-05", "ordered"),
        ("service", "SOL-SRV-004", "SRV-ADM-GEST", "20600000106", "Apoyo documental y seguimiento de compras.", "Servicio mensual administrativo", "2", "MES", "2026-09-10", "2026-09-15", "completed"),
        ("service", "SOL-SRV-005", "SRV-ERP-IMPL", "20600000107", "Configuración del módulo de compras.", "Implementación fase 1", "1", "SERV", "2026-09-18", "2026-10-15", "quoted"),
        ("service", "SOL-SRV-006", "SRV-IMP-CORP", "20600000108", "Material impreso para ventas y planta.", "Catálogos y señalética", "1", "SERV", "2026-10-01", "2026-10-18", "received"),
        ("service", "SOL-SRV-007", "SRV-MKT-B2B", "20600000109", "Campaña comercial para clientes industriales.", "Plan mensual de contenidos", "1", "MES", "2026-10-05", "2026-10-25", "draft"),
    ]
    request_ids = {}
    for kind, code, item_code, supplier_number, description, presentation, quantity, unit, requested_on, required_by, state in extra_requests:
        request_ids[code] = purchase_request(kind, code, item_ids[item_code], supplier_ids[supplier_number], description, presentation, quantity, unit, requested_on, required_by, state)

    extra_quotes = [
        ("COT-COMP-001", "SOL-MAT-003", "20600000101", "2026-08-14", "2026-08-24", "selected", "18450.00"),
        ("COT-COMP-002", "SOL-MAT-004", "20600000101", "2026-09-01", "2026-09-12", "selected", "32980.00"),
        ("COT-COMP-003", "SOL-MAT-005", "20600000102", "2026-09-10", "2026-09-20", "received", "14620.00"),
        ("COT-COMP-004", "SOL-MAT-006", "20600000102", "2026-09-23", "2026-10-03", "selected", "21850.00"),
        ("COT-COMP-005", "SOL-MAT-007", "20600000103", "2026-09-27", "2026-10-07", "selected", "3960.00"),
        ("COT-COMP-006", "SOL-MAT-008", "20600000103", "2026-10-04", "2026-10-14", "received", "2840.00"),
        ("COT-COMP-007", "SOL-SRV-002", "20600000104", "2026-08-22", "2026-09-01", "selected", "5850.00"),
        ("COT-COMP-008", "SOL-SRV-003", "20600000105", "2026-09-03", "2026-09-12", "selected", "4200.00"),
        ("COT-COMP-009", "SOL-SRV-004", "20600000106", "2026-09-12", "2026-09-22", "selected", "7600.00"),
        ("COT-COMP-010", "SOL-SRV-005", "20600000107", "2026-09-20", "2026-10-05", "received", "18500.00"),
        ("COT-COMP-011", "SOL-SRV-006", "20600000108", "2026-10-03", "2026-10-12", "selected", "3180.00"),
        ("COT-COMP-012", "SOL-SRV-007", "20600000109", "2026-10-07", "2026-10-20", "received", "4500.00"),
    ]
    quote_ids = {}
    for quote_code, request_code, supplier_number, quoted_on, valid_until, state, total in extra_quotes:
        cursor.execute("SELECT id FROM buy_purchase_quotations WHERE quote_code = %s", (quote_code,))
        existing = cursor.fetchone()
        if existing:
            quote_ids[request_code] = existing["id"]
            continue
        cursor.execute("INSERT INTO buy_purchase_quotations (request_id, quote_code, supplier_id, quoted_on, valid_until, status, total_amount, observations, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", (request_ids[request_code], quote_code, supplier_ids[supplier_number], quoted_on, valid_until, state, total, "Cotización sintética para analizar el flujo de compras.", admin_id))
        quote_ids[request_code] = cursor.lastrowid

    extra_orders = [
        ("OC-COMP-001", "SOL-MAT-003", "2026-08-15", "2026-08-29", "received", "18450.00"),
        ("OC-COMP-002", "SOL-MAT-004", "2026-09-02", "2026-09-17", "received", "32980.00"),
        ("OC-COMP-003", "SOL-MAT-006", "2026-09-24", "2026-10-07", "confirmed", "21850.00"),
        ("OC-COMP-004", "SOL-MAT-007", "2026-09-28", "2026-10-11", "received", "3960.00"),
        ("OC-COMP-005", "SOL-SRV-002", "2026-08-23", "2026-09-04", "received", "5850.00"),
        ("OC-COMP-006", "SOL-SRV-003", "2026-09-04", "2026-10-04", "confirmed", "4200.00"),
        ("OC-COMP-007", "SOL-SRV-004", "2026-09-13", "2026-10-13", "received", "7600.00"),
        ("OC-COMP-008", "SOL-SRV-005", "2026-09-21", "2026-10-21", "issued", "18500.00"),
        ("OC-COMP-009", "SOL-SRV-006", "2026-10-04", "2026-10-19", "received", "3180.00"),
    ]
    order_ids = {}
    for order_code, request_code, ordered_on, expected_on, state, total in extra_orders:
        cursor.execute("SELECT id FROM buy_purchase_orders WHERE order_code = %s", (order_code,))
        existing = cursor.fetchone()
        if existing:
            order_ids[order_code] = existing["id"]
            continue
        cursor.execute("INSERT INTO buy_purchase_orders (order_code, quotation_id, ordered_on, expected_on, status, total_amount, observations, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", (order_code, quote_ids[request_code], ordered_on, expected_on, state, total, "Orden sintética para analizar estados y costos.", admin_id))
        order_ids[order_code] = cursor.lastrowid

    extra_invoices = [
        ("F001-00010001", "OC-COMP-001", "2026-08-30", "2026-09-29", "paid", "PEN", "18450.00"),
        ("F001-00010002", "OC-COMP-002", "2026-09-18", "2026-10-18", "paid", "PEN", "32980.00"),
        ("F001-00010003", "OC-COMP-003", "2026-10-08", "2026-11-07", "validated", "PEN", "21850.00"),
        ("F001-00010004", "OC-COMP-004", "2026-10-12", "2026-11-11", "paid", "PEN", "3960.00"),
        ("F001-00010005", "OC-COMP-005", "2026-09-05", "2026-10-05", "paid", "PEN", "5850.00"),
        ("F001-00010006", "OC-COMP-006", "2026-10-05", "2026-11-04", "received", "PEN", "4200.00"),
        ("F001-00010007", "OC-COMP-007", "2026-10-14", "2026-11-13", "validated", "PEN", "7600.00"),
        ("F001-00010008", "OC-COMP-009", "2026-10-20", "2026-11-19", "received", "PEN", "3180.00"),
    ]
    for invoice_code, order_code, invoice_date, due_date, state, currency, subtotal in extra_invoices:
        cursor.execute("SELECT id FROM buy_purchase_invoices WHERE invoice_code = %s", (invoice_code,))
        if cursor.fetchone():
            continue
        subtotal_value = Decimal(subtotal)
        tax_value = (subtotal_value * Decimal("0.18")).quantize(Decimal("0.01"))
        cursor.execute("INSERT INTO buy_purchase_invoices (invoice_code, order_id, invoice_date, due_date, status, currency, subtotal, tax_rate, tax_amount, total_amount, observations, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (invoice_code, order_ids[order_code], invoice_date, due_date, state, currency, subtotal_value, Decimal("0.18"), tax_value, subtotal_value + tax_value, "Factura sintética para analizar crédito fiscal y pagos.", admin_id))

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
    order_three = service_order("OS-DUMMY-003", customer_three, ("Ampliación de nave industrial", "Estructura metálica", "Orden convertida desde la cotización COT-DUMMY-003.", "Patricia Salas", "patricia.salas@horizonte.test", "+51 999 311 411", "2026-08-20", "2026-11-30", "in_progress"))
    order_four = service_order("OS-DUMMY-004", customer_four, ("Mantenimiento preventivo de planta", "Mantenimiento industrial", "Servicio recurrente con visita de supervisión.", "Jorge Cárdenas", "jorge.cardenas@planta-sur.test", "+51 999 321 421", "2026-09-10", "2026-10-30", "approved"))
    order_five = service_order("OS-DUMMY-005", customer_five, ("Fabricación de plataformas de acceso", "Fabricación especial", "Orden originada por una propuesta de infraestructura.", "Renzo Valdivia", "renzo.valdivia@costa-norte.test", "+51 999 331 431", "2026-10-02", "2027-01-15", "approved"))
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
    quotation("COT-DUMMY-003", customer_three, order_three, [("Estructura principal", "KG", 1800, 8.5), ("Montaje y soldadura", "SERV", 1, 18500)])
    quotation("COT-DUMMY-004", customer_four, order_four, [("Mantenimiento de equipos", "SERV", 1, 12800), ("Pintura de seguridad", "GLN", 18, 185)])
    quotation("COT-DUMMY-005", customer_five, order_five, [("Plataformas metálicas", "UND", 4, 6800), ("Instalación", "SERV", 1, 5200)])
    quotation("COT-DUMMY-006", customer_three, None, [("Diseño de mezzanine", "SERV", 1, 9400), ("Memoria de cálculo", "SERV", 1, 3800)])
    quotation("COT-DUMMY-007", customer_six, None, [("Adecuación de taller", "SERV", 1, 7200)])

    def visit(title: str, customer_id: int, order_id: int | None, scheduled_at: str, address: str, latitude: str | None, longitude: str | None) -> None:
        cursor.execute("SELECT id FROM sell_technical_visits WHERE title = %s AND customer_id = %s", (title, customer_id))
        if cursor.fetchone():
            return
        cursor.execute("INSERT INTO sell_technical_visits (customer_id, service_order_id, title, scheduled_at, address, city, latitude, longitude, status, observations, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'planned', %s, %s)", (customer_id, order_id, title, scheduled_at, address, "Lima", latitude, longitude, "Visita dummy para probar asociación opcional a orden.", admin_id))

    visit("Levantamiento en Planta Norte", customer_one, order_one, "2026-10-05 09:00:00", "Av. Industrial 120", "-12.0464000", "-77.0428000")
    visit("Inspección inicial", customer_two, None, "2026-09-12 15:30:00", "Jr. Los Talleres 45", None, None)
    visit("Levantamiento de nave industrial", customer_three, order_three, "2026-08-08 10:00:00", "Av. Separadora Industrial 890", "-12.0201000", "-76.9902000")
    visit("Inspección de equipos críticos", customer_four, order_four, "2026-09-03 09:30:00", "Av. Parra 615", "-16.4090000", "-71.5375000")
    visit("Evaluación de plataforma", customer_five, None, "2026-09-25 14:00:00", "Panamericana Norte km 560", "-8.1090000", "-79.0215000")
    visit("Revisión de alcance", customer_three, None, "2026-10-18 11:00:00", "Av. Separadora Industrial 890", "-12.0201000", "-76.9902000")

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

    def worker(code: str, worker_type: str, full_name: str, document_type: str, document_number: str, role_title: str, specialty: str, hire_date: str) -> int:
        cursor.execute("SELECT id FROM hr_workers WHERE worker_code = %s", (code,))
        existing = cursor.fetchone()
        if existing:
            return existing["id"]
        cursor.execute("INSERT INTO hr_workers (worker_code, worker_type, full_name, document_type, document_number, email, phone, role_title, specialty, hire_date, notes, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (code, worker_type, full_name, document_type, document_number, f"{code.lower()}@jhingenieros.test", "+51 999 7" + code[-3:], role_title, specialty, hire_date, "Registro dummy de RR.HH. para analizar dotación y costo por proyecto.", admin_id))
        return cursor.lastrowid

    worker_rows = [
        ("EMP-001", "internal", "Ana Salazar", "DNI", "40100001", "Gerente de Operaciones", "Gestión de proyectos", "2022-01-10"),
        ("EMP-002", "internal", "Jorge Ramos", "DNI", "40100002", "Jefe de Producción", "Estructuras metálicas", "2022-03-15"),
        ("EMP-003", "internal", "Patricia León", "DNI", "40100003", "Responsable de Calidad", "Control de calidad", "2022-06-01"),
        ("EMP-004", "internal", "Elena Ruiz", "DNI", "40100004", "Administradora", "Administración y finanzas", "2023-01-09"),
        ("EMP-005", "internal", "Luis Paredes", "DNI", "40100005", "Ingeniero Mecánico", "Diseño y mantenimiento", "2023-02-20"),
        ("EMP-006", "internal", "María Torres", "DNI", "40100006", "Ejecutiva Comercial", "Ventas industriales", "2023-05-08"),
        ("EMP-007", "internal", "Raúl Vega", "DNI", "40100007", "Supervisor SSOMA", "Seguridad y medio ambiente", "2023-08-14"),
        ("EMP-008", "internal", "Diego Salazar", "DNI", "40100008", "Logística", "Compras y almacenes", "2024-01-15"),
        ("EMP-009", "internal", "Lucía Paredes", "DNI", "40100009", "Analista TI", "Sistemas y soporte", "2024-04-01"),
        ("EMP-010", "internal", "Carlos Mendoza", "DNI", "40100010", "Técnico de Mantenimiento", "Mantenimiento industrial", "2024-07-22"),
        ("EMP-011", "internal", "Sofía Cárdenas", "DNI", "40100011", "Ingeniera de Oficina Técnica", "Planos y metrados", "2025-02-03"),
        ("EMP-012", "internal", "Renzo Valdivia", "DNI", "40100012", "Comprador Senior", "Abastecimiento", "2025-06-16"),
        ("TMP-001", "temporary", "Miguel Huamán", "DNI", "70100001", "Soldador homologado", "Soldadura MIG y TIG", "2026-08-01"),
        ("TMP-002", "temporary", "Rosa Medina", "DNI", "70100002", "Montajista", "Montaje de estructuras", "2026-08-15"),
        ("TMP-003", "temporary", "Andrés Quispe", "DNI", "70100003", "Pintor industrial", "Recubrimientos epóxicos", "2026-09-01"),
        ("TMP-004", "temporary", "Valeria Soto", "DNI", "70100004", "Ayudante de obra", "Apoyo de fabricación", "2026-09-10"),
        ("TMP-005", "temporary", "Héctor Flores", "DNI", "70100005", "Operador de maquinaria", "Corte y plegado", "2026-09-18"),
        ("TMP-006", "temporary", "Nadia Castillo", "DNI", "70100006", "Topógrafa", "Levantamientos en campo", "2026-10-01"),
    ]
    worker_ids = {row[0]: worker(*row) for row in worker_rows}

    def engagement(code: str, project_id: int | None, start_date: str, end_date: str | None, status_value: str, cost_unit: str, rate: str, hours: str | None) -> int:
        cursor.execute("SELECT id FROM hr_engagements WHERE worker_id = %s AND project_id <=> %s AND start_date = %s", (worker_ids[code], project_id, start_date))
        existing = cursor.fetchone()
        if existing:
            return existing["id"]
        cursor.execute("INSERT INTO hr_engagements (worker_id, project_id, engagement_type, start_date, end_date, status, cost_unit, rate, estimated_hours, notes, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (worker_ids[code], project_id, "temporary" if code.startswith("TMP-") else "internal", start_date, end_date, status_value, cost_unit, rate, hours, "Asignación dummy para analizar dedicación y costo de mano de obra.", admin_id))
        return cursor.lastrowid

    engagement_ids = {}
    for code, project_id, start_date, end_date, state, unit, rate, hours in [
        ("EMP-001", project_one, "2026-01-01", None, "active", "month", "8500", "160"),
        ("EMP-002", project_one, "2026-01-01", None, "active", "month", "6200", "160"),
        ("EMP-002", project_two, "2026-09-01", "2026-10-10", "completed", "month", "6200", "120"),
        ("EMP-003", project_one, "2026-02-01", None, "active", "month", "4800", "160"),
        ("EMP-004", None, "2026-01-01", None, "active", "month", "4200", "160"),
        ("EMP-005", project_one, "2026-04-01", None, "active", "month", "5600", "160"),
        ("EMP-005", project_two, "2026-09-15", "2026-10-10", "completed", "month", "5600", "80"),
        ("EMP-006", None, "2026-01-01", None, "active", "month", "3900", "160"),
        ("EMP-007", project_one, "2026-05-01", None, "active", "month", "4400", "160"),
        ("EMP-008", None, "2026-01-01", None, "active", "month", "3600", "160"),
        ("EMP-009", None, "2026-01-01", None, "active", "month", "4500", "160"),
        ("EMP-010", project_two, "2026-09-15", "2026-10-10", "completed", "month", "4100", "120"),
        ("EMP-011", project_one, "2026-06-01", None, "active", "month", "5200", "160"),
        ("EMP-012", None, "2026-01-01", None, "active", "month", "4700", "160"),
        ("TMP-001", project_one, "2026-08-01", "2026-11-30", "active", "hour", "38", "420"),
        ("TMP-001", project_two, "2026-09-15", "2026-10-10", "completed", "hour", "38", "96"),
        ("TMP-002", project_one, "2026-08-15", "2026-11-30", "active", "day", "180", "360"),
        ("TMP-003", project_one, "2026-09-01", "2026-11-15", "active", "day", "165", "280"),
        ("TMP-004", project_two, "2026-09-10", "2026-10-10", "completed", "day", "120", "180"),
        ("TMP-005", project_one, "2026-09-18", "2026-12-15", "active", "hour", "42", "320"),
        ("TMP-006", project_one, "2026-10-01", "2026-10-31", "completed", "day", "220", "80"),
    ]:
        engagement_ids[f"{code}-{project_id}-{start_date}"] = engagement(code, project_id, start_date, end_date, state, unit, rate, hours)

    cursor.execute("SELECT id FROM hr_payroll_periods WHERE period_code = '2026-10'")
    payroll_period = cursor.fetchone()
    if not payroll_period:
        cursor.execute("INSERT INTO hr_payroll_periods (period_code, starts_on, ends_on, status, notes, created_by) VALUES ('2026-10', '2026-10-01', '2026-10-31', 'draft', 'Planilla dummy para visualizar costo interno y temporal.', %s)", (admin_id,))
        payroll_period_id = cursor.lastrowid
    else:
        payroll_period_id = payroll_period["id"]
    for code, engagement_key, days, hours, gross, employer_cost, payment in [
        ("EMP-001", f"EMP-001-{project_one}-2026-01-01", 31, 160, "8500", "10200", "pending"),
        ("EMP-002", f"EMP-002-{project_one}-2026-01-01", 31, 160, "6200", "7440", "pending"),
        ("EMP-003", f"EMP-003-{project_one}-2026-02-01", 31, 160, "4800", "5760", "pending"),
        ("EMP-004", f"EMP-004-None-2026-01-01", 31, 160, "4200", "5040", "pending"),
        ("TMP-001", f"TMP-001-{project_one}-2026-08-01", 22, 176, "6688", "6688", "paid"),
        ("TMP-002", f"TMP-002-{project_one}-2026-08-15", 18, 144, "3240", "3240", "pending"),
        ("TMP-003", f"TMP-003-{project_one}-2026-09-01", 20, 160, "3300", "3300", "pending"),
        ("TMP-005", f"TMP-005-{project_one}-2026-09-18", 16, 128, "5376", "5376", "pending"),
    ]:
        cursor.execute("SELECT id FROM hr_payroll_lines WHERE period_id = %s AND worker_id = %s AND engagement_id = %s", (payroll_period_id, worker_ids[code], engagement_ids[engagement_key]))
        if cursor.fetchone():
            continue
        cursor.execute("INSERT INTO hr_payroll_lines (period_id, worker_id, engagement_id, days_worked, hours_worked, gross_amount, employer_cost, payment_status, notes, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Línea dummy para análisis de planilla.', %s)", (payroll_period_id, worker_ids[code], engagement_ids[engagement_key], days, hours, gross, employer_cost, payment, admin_id))

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
    role: str = Field(pattern=r"^(user|accounts|selling|hr|integrations|admin)$")
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


class SupplierRequest(BaseModel):
    supplier_type: str = Field(default="legal", pattern=r"^(natural|legal)$")
    name: str = Field(min_length=2, max_length=180)
    document_type: str | None = Field(default=None, max_length=20)
    document_number: str | None = Field(default=None, max_length=40)
    supply_type: str = Field(default="products", pattern=r"^(products|services|both)$")
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=40)
    secondary_phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=100)
    contact_name: str | None = Field(default=None, max_length=160)
    observations: str | None = Field(default=None, max_length=4000)
    relationship_started_on: date | None = None
    last_contact_on: date | None = None
    is_active: bool = True


class PurchaseItemRequest(BaseModel):
    item_type: str = Field(pattern=r"^(material|service)$")
    item_code: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=2, max_length=180)
    description: str | None = Field(default=None, max_length=4000)
    unit: str = Field(default="UND", min_length=1, max_length=40)
    is_active: bool = True


class PurchaseRequestLine(BaseModel):
    item_id: int
    quantity: Decimal = Field(gt=0)
    unit: str = Field(default="UND", min_length=1, max_length=40)
    presentation: str | None = Field(default=None, max_length=180)


class PurchaseRequest(BaseModel):
    request_type: str = Field(pattern=r"^(material|service)$")
    request_code: str = Field(min_length=1, max_length=80)
    items: list[PurchaseRequestLine] = Field(default_factory=list)
    item_id: int | None = None
    supplier_id: int
    description: str = Field(min_length=2, max_length=4000)
    presentation: str | None = Field(default=None, max_length=180)
    quantity: Decimal | None = Field(default=None, gt=0)
    unit: str = Field(default="UND", min_length=1, max_length=40)
    requested_on: date
    required_by: date | None = None
    status: str = Field(default="draft", pattern=r"^(draft|requested|quoted|ordered|completed|cancelled)$")
    observations: str | None = Field(default=None, max_length=4000)


class PurchaseQuotationRequest(BaseModel):
    request_id: int
    quote_code: str = Field(min_length=1, max_length=80)
    supplier_id: int
    quoted_on: date
    valid_until: date | None = None
    status: str = Field(default="received", pattern=r"^(received|selected|rejected|cancelled)$")
    total_amount: Decimal | None = Field(default=None, ge=0)
    observations: str | None = Field(default=None, max_length=4000)


class PurchaseOrderRequest(BaseModel):
    order_code: str = Field(min_length=1, max_length=80)
    quotation_id: int
    ordered_on: date
    expected_on: date | None = None
    status: str = Field(default="draft", pattern=r"^(draft|issued|confirmed|received|cancelled)$")
    total_amount: Decimal | None = Field(default=None, ge=0)
    observations: str | None = Field(default=None, max_length=4000)


class PurchaseInvoiceRequest(BaseModel):
    invoice_code: str = Field(min_length=1, max_length=80)
    order_id: int
    invoice_date: date
    due_date: date | None = None
    status: str = Field(default="received", pattern=r"^(draft|received|validated|paid|cancelled)$")
    currency: str = Field(default="PEN", min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    subtotal: Decimal = Field(ge=0)
    observations: str | None = Field(default=None, max_length=4000)


class QuotationItemRequest(BaseModel):
    description: str = Field(min_length=2, max_length=255)
    unit: str = Field(min_length=1, max_length=40)
    quantity: Decimal = Field(gt=0)
    unit_cost: Decimal = Field(ge=0)


class QuotationRequest(BaseModel):
    customer_id: int
    service_order_id: int | None = None
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


class HRWorkerRequest(BaseModel):
    worker_code: str = Field(min_length=2, max_length=40)
    worker_type: str = Field(default="internal", pattern=r"^(internal|temporary)$")
    full_name: str = Field(min_length=2, max_length=180)
    document_type: str | None = Field(default=None, max_length=20)
    document_number: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=40)
    role_title: str = Field(min_length=2, max_length=120)
    specialty: str | None = Field(default=None, max_length=160)
    hire_date: date | None = None
    is_active: bool = True
    notes: str | None = Field(default=None, max_length=4000)


class HREngagementRequest(BaseModel):
    worker_id: int
    project_id: int | None = None
    engagement_type: str = Field(default="internal", pattern=r"^(internal|temporary)$")
    start_date: date
    end_date: date | None = None
    status: str = Field(default="active", pattern=r"^(planned|active|completed|cancelled)$")
    cost_unit: str = Field(default="month", pattern=r"^(hour|day|month|project)$")
    rate: Decimal = Field(default=Decimal("0"), ge=0)
    estimated_hours: Decimal | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=4000)


class HRPayrollPeriodRequest(BaseModel):
    period_code: str = Field(min_length=4, max_length=20)
    starts_on: date
    ends_on: date
    status: str = Field(default="draft", pattern=r"^(draft|approved|paid|closed)$")
    notes: str | None = Field(default=None, max_length=4000)


class HRPayrollLineRequest(BaseModel):
    period_id: int
    worker_id: int
    engagement_id: int | None = None
    days_worked: Decimal = Field(default=Decimal("0"), ge=0)
    hours_worked: Decimal = Field(default=Decimal("0"), ge=0)
    gross_amount: Decimal = Field(default=Decimal("0"), ge=0)
    employer_cost: Decimal = Field(default=Decimal("0"), ge=0)
    payment_status: str = Field(default="pending", pattern=r"^(pending|paid|hold)$")
    notes: str | None = Field(default=None, max_length=4000)


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


def require_hr(token: str | None) -> dict:
    user = current_user(token)
    if user["role"] not in {"admin", "hr"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="RR.HH. role required")
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


@app.get("/hr/workers")
def hr_workers(token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_hr(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, worker_code, worker_type, full_name, document_type, document_number, email, phone, role_title, specialty, hire_date, is_active, notes, created_at FROM hr_workers ORDER BY is_active DESC, full_name")
            workers = cursor.fetchall()
    finally:
        connection.close()
    return {"workers": workers}


@app.post("/hr/workers", status_code=status.HTTP_201_CREATED)
def create_hr_worker(payload: HRWorkerRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    user = require_hr(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            try:
                cursor.execute("INSERT INTO hr_workers (worker_code, worker_type, full_name, document_type, document_number, email, phone, role_title, specialty, hire_date, is_active, notes, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (payload.worker_code, payload.worker_type, payload.full_name, payload.document_type or None, payload.document_number or None, payload.email or None, payload.phone or None, payload.role_title, payload.specialty or None, payload.hire_date, payload.is_active, payload.notes or None, user["id"]))
                worker_id = cursor.lastrowid
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="El código o documento del trabajador ya existe") from error
    finally:
        connection.close()
    return {"id": worker_id, "worker_code": payload.worker_code}


@app.patch("/hr/workers/{worker_id}")
def update_hr_worker(worker_id: int, payload: HRWorkerRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_hr(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            try:
                cursor.execute("UPDATE hr_workers SET worker_code = %s, worker_type = %s, full_name = %s, document_type = %s, document_number = %s, email = %s, phone = %s, role_title = %s, specialty = %s, hire_date = %s, is_active = %s, notes = %s WHERE id = %s", (payload.worker_code, payload.worker_type, payload.full_name, payload.document_type or None, payload.document_number or None, payload.email or None, payload.phone or None, payload.role_title, payload.specialty or None, payload.hire_date, payload.is_active, payload.notes or None, worker_id))
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="El código o documento del trabajador ya existe") from error
            if cursor.rowcount == 0:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trabajador no encontrado")
    finally:
        connection.close()
    return {"updated": True, "id": worker_id}


@app.get("/hr/engagements")
def hr_engagements(token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_hr(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT e.id, e.worker_id, e.project_id, w.worker_code, w.full_name, w.worker_type, e.engagement_type, e.start_date, e.end_date, e.status, e.cost_unit, e.rate, e.estimated_hours, e.notes, p.project_code, p.name AS project_name FROM hr_engagements e JOIN hr_workers w ON w.id = e.worker_id LEFT JOIN proj_projects p ON p.id = e.project_id ORDER BY e.start_date DESC, w.full_name")
            engagements = cursor.fetchall()
            cursor.execute("SELECT id, project_code, name, status FROM proj_projects ORDER BY project_code")
            projects = cursor.fetchall()
            cursor.execute("SELECT id, worker_code, full_name, worker_type FROM hr_workers WHERE is_active = 1 ORDER BY full_name")
            workers = cursor.fetchall()
    finally:
        connection.close()
    return {"engagements": engagements, "projects": projects, "workers": workers}


@app.post("/hr/engagements", status_code=status.HTTP_201_CREATED)
def create_hr_engagement(payload: HREngagementRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    user = require_hr(token)
    if payload.engagement_type == "temporary" and not payload.project_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="La mano de obra temporal debe estar asignada a un proyecto")
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM hr_workers WHERE id = %s AND is_active = 1", (payload.worker_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trabajador no encontrado o inactivo")
            if payload.project_id:
                cursor.execute("SELECT id FROM proj_projects WHERE id = %s", (payload.project_id,))
                if not cursor.fetchone():
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proyecto no encontrado")
            cursor.execute("INSERT INTO hr_engagements (worker_id, project_id, engagement_type, start_date, end_date, status, cost_unit, rate, estimated_hours, notes, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (payload.worker_id, payload.project_id, payload.engagement_type, payload.start_date, payload.end_date, payload.status, payload.cost_unit, payload.rate, payload.estimated_hours, payload.notes or None, user["id"]))
            engagement_id = cursor.lastrowid
    finally:
        connection.close()
    return {"id": engagement_id}


@app.patch("/hr/engagements/{engagement_id}")
def update_hr_engagement(engagement_id: int, payload: HREngagementRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    user = require_hr(token)
    if payload.engagement_type == "temporary" and not payload.project_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="La mano de obra temporal debe estar asignada a un proyecto")
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE hr_engagements SET worker_id = %s, project_id = %s, engagement_type = %s, start_date = %s, end_date = %s, status = %s, cost_unit = %s, rate = %s, estimated_hours = %s, notes = %s WHERE id = %s", (payload.worker_id, payload.project_id, payload.engagement_type, payload.start_date, payload.end_date, payload.status, payload.cost_unit, payload.rate, payload.estimated_hours, payload.notes or None, engagement_id))
            if cursor.rowcount == 0:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asignación no encontrada")
    finally:
        connection.close()
    return {"updated": True, "id": engagement_id}


@app.get("/hr/payroll")
def hr_payroll(token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_hr(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, period_code, starts_on, ends_on, status, notes, created_at FROM hr_payroll_periods ORDER BY starts_on DESC")
            periods = cursor.fetchall()
            cursor.execute("SELECT l.id, l.period_id, l.worker_id, l.engagement_id, w.worker_code, w.full_name, w.worker_type, l.days_worked, l.hours_worked, l.gross_amount, l.employer_cost, l.payment_status, l.notes FROM hr_payroll_lines l JOIN hr_workers w ON w.id = l.worker_id ORDER BY l.period_id DESC, w.full_name")
            lines = cursor.fetchall()
            cursor.execute("SELECT id, worker_code, full_name FROM hr_workers WHERE is_active = 1 ORDER BY full_name")
            workers = cursor.fetchall()
            cursor.execute("SELECT id, period_code FROM hr_payroll_periods ORDER BY starts_on DESC")
            period_options = cursor.fetchall()
    finally:
        connection.close()
    return {"periods": periods, "lines": lines, "workers": workers, "period_options": period_options}


@app.post("/hr/payroll/periods", status_code=status.HTTP_201_CREATED)
def create_hr_payroll_period(payload: HRPayrollPeriodRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    user = require_hr(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            try:
                cursor.execute("INSERT INTO hr_payroll_periods (period_code, starts_on, ends_on, status, notes, created_by) VALUES (%s, %s, %s, %s, %s, %s)", (payload.period_code, payload.starts_on, payload.ends_on, payload.status, payload.notes or None, user["id"]))
                period_id = cursor.lastrowid
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="El periodo de planilla ya existe") from error
    finally:
        connection.close()
    return {"id": period_id, "period_code": payload.period_code}


@app.post("/hr/payroll/lines", status_code=status.HTTP_201_CREATED)
def create_hr_payroll_line(payload: HRPayrollLineRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    user = require_hr(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO hr_payroll_lines (period_id, worker_id, engagement_id, days_worked, hours_worked, gross_amount, employer_cost, payment_status, notes, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (payload.period_id, payload.worker_id, payload.engagement_id, payload.days_worked, payload.hours_worked, payload.gross_amount, payload.employer_cost, payload.payment_status, payload.notes or None, user["id"]))
            line_id = cursor.lastrowid
    finally:
        connection.close()
    return {"id": line_id}


@app.get("/hr/reports")
def hr_reports(date_from: date | None = None, date_to: date | None = None, project_id: int | None = None, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_hr(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            conditions = ["1=1"]
            params: list = []
            if date_from:
                conditions.append("e.start_date >= %s")
                params.append(date_from)
            if date_to:
                conditions.append("(e.end_date IS NULL OR e.end_date <= %s)")
                params.append(date_to)
            if project_id:
                conditions.append("e.project_id = %s")
                params.append(project_id)
            where = " AND ".join(conditions)
            cursor.execute("SELECT COUNT(*) AS value FROM hr_workers WHERE is_active = 1 AND worker_type = 'internal'")
            internal_count = cursor.fetchone()["value"]
            cursor.execute("SELECT COUNT(*) AS value FROM hr_workers WHERE is_active = 1 AND worker_type = 'temporary'")
            temporary_count = cursor.fetchone()["value"]
            cursor.execute(f"SELECT COUNT(*) AS value, COALESCE(SUM(CASE WHEN w.worker_type = 'temporary' THEN 1 ELSE 0 END), 0) AS temporary FROM hr_engagements e JOIN hr_workers w ON w.id = e.worker_id WHERE {where} AND e.status IN ('planned','active')", params)
            engagement_summary = cursor.fetchone()
            cursor.execute(f"SELECT p.name AS label, COUNT(*) AS assignments, COALESCE(SUM(e.rate * COALESCE(e.estimated_hours, 1)), 0) AS estimated_cost FROM hr_engagements e LEFT JOIN proj_projects p ON p.id = e.project_id WHERE {where} GROUP BY p.id, p.name ORDER BY estimated_cost DESC", params)
            project_costs = cursor.fetchall()
            cursor.execute(f"SELECT w.full_name AS label, w.worker_type, COUNT(e.id) AS assignments, COALESCE(SUM(e.rate * COALESCE(e.estimated_hours, 1)), 0) AS estimated_cost FROM hr_engagements e JOIN hr_workers w ON w.id = e.worker_id WHERE {where} GROUP BY w.id, w.full_name, w.worker_type ORDER BY estimated_cost DESC LIMIT 10", params)
            worker_costs = cursor.fetchall()
            cursor.execute("SELECT pp.period_code AS period, COALESCE(SUM(l.employer_cost), 0) AS total, COUNT(l.id) AS line_count FROM hr_payroll_periods pp LEFT JOIN hr_payroll_lines l ON l.period_id = pp.id GROUP BY pp.id, pp.period_code ORDER BY pp.starts_on")
            payroll_monthly = cursor.fetchall()
            cursor.execute("SELECT e.status, COUNT(*) AS value FROM hr_engagements e WHERE " + where + " GROUP BY e.status ORDER BY value DESC", params)
            engagement_statuses = cursor.fetchall()
            cursor.execute("SELECT id, project_code, name FROM proj_projects ORDER BY project_code")
            projects = cursor.fetchall()
    finally:
        connection.close()
    return {"totals": {"internal": internal_count, "temporary": temporary_count, "active_assignments": engagement_summary["value"], "temporary_assignments": engagement_summary["temporary"]}, "project_costs": project_costs, "worker_costs": worker_costs, "payroll_monthly": payroll_monthly, "engagement_statuses": engagement_statuses, "projects": projects}


@app.get("/purchases/suppliers")
def purchases_suppliers(token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, supplier_type, name, document_type, document_number, supply_type, email, phone, secondary_phone, address, city, contact_name, observations, relationship_started_on, last_contact_on, is_active, created_at, updated_at FROM buy_suppliers ORDER BY is_active DESC, name")
            suppliers = cursor.fetchall()
    finally:
        connection.close()
    return {"suppliers": suppliers}


@app.post("/purchases/suppliers", status_code=status.HTTP_201_CREATED)
def create_purchases_supplier(payload: SupplierRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    user = require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            try:
                cursor.execute("INSERT INTO buy_suppliers (supplier_type, name, document_type, document_number, supply_type, email, phone, secondary_phone, address, city, contact_name, observations, relationship_started_on, last_contact_on, is_active, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (payload.supplier_type, payload.name, payload.document_type or None, payload.document_number or None, payload.supply_type, payload.email or None, payload.phone or None, payload.secondary_phone or None, payload.address or None, payload.city or None, payload.contact_name or None, payload.observations or None, payload.relationship_started_on, payload.last_contact_on, payload.is_active, user["id"]))
                supplier_id = cursor.lastrowid
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A supplier with this document already exists") from error
    finally:
        connection.close()
    return {"id": supplier_id, "name": payload.name}


@app.patch("/purchases/suppliers/{supplier_id}")
def update_purchases_supplier(supplier_id: int, payload: SupplierRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            try:
                cursor.execute("UPDATE buy_suppliers SET supplier_type = %s, name = %s, document_type = %s, document_number = %s, supply_type = %s, email = %s, phone = %s, secondary_phone = %s, address = %s, city = %s, contact_name = %s, observations = %s, relationship_started_on = %s, last_contact_on = %s, is_active = %s WHERE id = %s", (payload.supplier_type, payload.name, payload.document_type or None, payload.document_number or None, payload.supply_type, payload.email or None, payload.phone or None, payload.secondary_phone or None, payload.address or None, payload.city or None, payload.contact_name or None, payload.observations or None, payload.relationship_started_on, payload.last_contact_on, payload.is_active, supplier_id))
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A supplier with this document already exists") from error
            if cursor.rowcount == 0:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Supplier not found")
    finally:
        connection.close()
    return {"updated": True, "id": supplier_id}


@app.get("/purchases/items")
def purchases_items(item_type: str, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    if item_type not in {"material", "service"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid purchase item type")
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, item_type, item_code, name, description, unit, is_active, created_at, updated_at FROM buy_items WHERE item_type = %s ORDER BY is_active DESC, name", (item_type,))
            items = cursor.fetchall()
    finally:
        connection.close()
    return {"items": items}


@app.post("/purchases/items", status_code=status.HTTP_201_CREATED)
def create_purchases_item(payload: PurchaseItemRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    user = require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            try:
                cursor.execute("INSERT INTO buy_items (item_type, item_code, name, description, unit, is_active, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s)", (payload.item_type, payload.item_code, payload.name, payload.description or None, payload.unit, payload.is_active, user["id"]))
                item_id = cursor.lastrowid
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An item with this code already exists") from error
    finally:
        connection.close()
    return {"id": item_id, "item_code": payload.item_code}


@app.patch("/purchases/items/{item_id}")
def update_purchases_item(item_id: int, payload: PurchaseItemRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            try:
                cursor.execute("UPDATE buy_items SET item_type = %s, item_code = %s, name = %s, description = %s, unit = %s, is_active = %s WHERE id = %s", (payload.item_type, payload.item_code, payload.name, payload.description or None, payload.unit, payload.is_active, item_id))
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An item with this code already exists") from error
            if cursor.rowcount == 0:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    finally:
        connection.close()
    return {"updated": True, "id": item_id}


@app.get("/purchases/requests")
def purchases_requests(request_type: str, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    if request_type not in {"material", "service"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid purchase request type")
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT r.id, r.request_type, r.request_code, r.supplier_id, s.name AS supplier_name, r.description, r.requested_on, r.required_by, r.status, r.observations, r.created_at, r.updated_at FROM buy_purchase_requests r JOIN buy_suppliers s ON s.id = r.supplier_id WHERE r.request_type = %s ORDER BY r.requested_on DESC, r.created_at DESC", (request_type,))
            requests = cursor.fetchall()
            cursor.execute("SELECT ri.request_id, ri.item_id, ri.quantity, ri.unit, ri.presentation, i.item_code, i.name AS item_name, i.unit AS item_default_unit FROM buy_purchase_request_items ri JOIN buy_items i ON i.id = ri.item_id WHERE i.item_type = %s ORDER BY ri.request_id, i.name", (request_type,))
            lines = cursor.fetchall()
            cursor.execute("SELECT id, supplier_type, name, document_number, supply_type, is_active FROM buy_suppliers WHERE is_active = 1 ORDER BY name")
            suppliers = cursor.fetchall()
    finally:
        connection.close()
    grouped = {request["id"]: [] for request in requests}
    for line in lines:
        grouped.setdefault(line["request_id"], []).append(line)
    for request in requests:
        request["items"] = grouped.get(request["id"], [])
        request["item_summary"] = " · ".join(f"{line['item_name']} ({line['quantity']} {line['unit']})" for line in request["items"])
    return {"requests": requests, "suppliers": suppliers}


@app.post("/purchases/requests", status_code=status.HTTP_201_CREATED)
def create_purchases_request(payload: PurchaseRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    user = require_sales(token)
    lines = payload.items or ([PurchaseRequestLine(item_id=payload.item_id, quantity=payload.quantity, unit=payload.unit, presentation=payload.presentation)] if payload.item_id and payload.quantity else [])
    if not lines:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="At least one purchase item is required")
    connection = connect()
    try:
        with connection.cursor() as cursor:
            item_ids = [line.item_id for line in lines]
            placeholders = ",".join(["%s"] * len(item_ids))
            cursor.execute(f"SELECT id FROM buy_items WHERE id IN ({placeholders}) AND item_type = %s", (*item_ids, payload.request_type))
            if len(cursor.fetchall()) != len(set(item_ids)):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="One or more purchase items are invalid for this request type")
            cursor.execute("SELECT id FROM buy_suppliers WHERE id = %s AND is_active = 1", (payload.supplier_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active supplier not found")
            try:
                first = lines[0]
                cursor.execute("INSERT INTO buy_purchase_requests (request_type, request_code, item_id, supplier_id, description, presentation, quantity, unit, requested_on, required_by, status, observations, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (payload.request_type, payload.request_code, first.item_id, payload.supplier_id, payload.description, first.presentation, first.quantity, first.unit, payload.requested_on, payload.required_by, payload.status, payload.observations or None, user["id"]))
                request_id = cursor.lastrowid
                cursor.executemany("INSERT INTO buy_purchase_request_items (request_id, item_id, quantity, unit, presentation) VALUES (%s, %s, %s, %s, %s)", [(request_id, line.item_id, line.quantity, line.unit, line.presentation) for line in lines])
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A purchase request with this code already exists") from error
    finally:
        connection.close()
    return {"id": request_id, "request_code": payload.request_code}


@app.patch("/purchases/requests/{request_id}")
def update_purchases_request(request_id: int, payload: PurchaseRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    lines = payload.items or ([PurchaseRequestLine(item_id=payload.item_id, quantity=payload.quantity, unit=payload.unit, presentation=payload.presentation)] if payload.item_id and payload.quantity else [])
    if not lines:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="At least one purchase item is required")
    connection = connect()
    try:
        with connection.cursor() as cursor:
            item_ids = [line.item_id for line in lines]
            placeholders = ",".join(["%s"] * len(item_ids))
            cursor.execute(f"SELECT id FROM buy_items WHERE id IN ({placeholders}) AND item_type = %s", (*item_ids, payload.request_type))
            if len(cursor.fetchall()) != len(set(item_ids)):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="One or more purchase items are invalid for this request type")
            cursor.execute("SELECT id FROM buy_suppliers WHERE id = %s AND is_active = 1", (payload.supplier_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active supplier not found")
            try:
                first = lines[0]
                cursor.execute("UPDATE buy_purchase_requests SET request_type = %s, request_code = %s, item_id = %s, supplier_id = %s, description = %s, presentation = %s, quantity = %s, unit = %s, requested_on = %s, required_by = %s, status = %s, observations = %s WHERE id = %s", (payload.request_type, payload.request_code, first.item_id, payload.supplier_id, payload.description, first.presentation, first.quantity, first.unit, payload.requested_on, payload.required_by, payload.status, payload.observations or None, request_id))
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A purchase request with this code already exists") from error
            if cursor.rowcount == 0:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Purchase request not found")
            cursor.execute("DELETE FROM buy_purchase_request_items WHERE request_id = %s", (request_id,))
            cursor.executemany("INSERT INTO buy_purchase_request_items (request_id, item_id, quantity, unit, presentation) VALUES (%s, %s, %s, %s, %s)", [(request_id, line.item_id, line.quantity, line.unit, line.presentation) for line in lines])
    finally:
        connection.close()
    return {"updated": True, "id": request_id}


@app.get("/purchases/quotations")
def purchases_quotations(request_type: str, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    if request_type not in {"material", "service"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid purchase quotation type")
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT q.id, q.request_id, r.request_code, r.request_type, q.quote_code, q.supplier_id, s.name AS supplier_name, q.quoted_on, q.valid_until, q.status, q.total_amount, q.observations, q.created_at FROM buy_purchase_quotations q JOIN buy_purchase_requests r ON r.id = q.request_id JOIN buy_suppliers s ON s.id = q.supplier_id WHERE r.request_type = %s ORDER BY q.quoted_on DESC, q.created_at DESC", (request_type,))
            quotations = cursor.fetchall()
            cursor.execute("SELECT id, request_code, description, status FROM buy_purchase_requests WHERE request_type = %s ORDER BY requested_on DESC", (request_type,))
            requests = cursor.fetchall()
            cursor.execute("SELECT id, name, document_number FROM buy_suppliers WHERE is_active = 1 ORDER BY name")
            suppliers = cursor.fetchall()
    finally:
        connection.close()
    return {"quotations": quotations, "requests": requests, "suppliers": suppliers}


@app.post("/purchases/quotations", status_code=status.HTTP_201_CREATED)
def create_purchases_quotation(payload: PurchaseQuotationRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    user = require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT r.id FROM buy_purchase_requests r WHERE r.id = %s", (payload.request_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Purchase request not found")
            cursor.execute("SELECT id FROM buy_suppliers WHERE id = %s AND is_active = 1", (payload.supplier_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active supplier not found")
            try:
                cursor.execute("INSERT INTO buy_purchase_quotations (request_id, quote_code, supplier_id, quoted_on, valid_until, status, total_amount, observations, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", (payload.request_id, payload.quote_code, payload.supplier_id, payload.quoted_on, payload.valid_until, payload.status, payload.total_amount, payload.observations or None, user["id"]))
                quotation_id = cursor.lastrowid
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A quotation with this code already exists for the request") from error
    finally:
        connection.close()
    return {"id": quotation_id, "quote_code": payload.quote_code}


@app.patch("/purchases/quotations/{quotation_id}")
def update_purchases_quotation(quotation_id: int, payload: PurchaseQuotationRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE buy_purchase_quotations SET request_id = %s, quote_code = %s, supplier_id = %s, quoted_on = %s, valid_until = %s, status = %s, total_amount = %s, observations = %s WHERE id = %s", (payload.request_id, payload.quote_code, payload.supplier_id, payload.quoted_on, payload.valid_until, payload.status, payload.total_amount, payload.observations or None, quotation_id))
            if cursor.rowcount == 0:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Purchase quotation not found")
    finally:
        connection.close()
    return {"updated": True, "id": quotation_id}


@app.get("/purchases/orders")
def purchases_orders(token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT o.id, o.order_code, o.quotation_id, q.quote_code, r.request_code, s.name AS supplier_name, o.ordered_on, o.expected_on, o.status, o.total_amount, o.observations, o.created_at, o.updated_at FROM buy_purchase_orders o JOIN buy_purchase_quotations q ON q.id = o.quotation_id JOIN buy_purchase_requests r ON r.id = q.request_id JOIN buy_suppliers s ON s.id = q.supplier_id ORDER BY o.ordered_on DESC, o.created_at DESC"
            )
            orders = cursor.fetchall()
            cursor.execute(
                "SELECT q.id, q.quote_code, r.request_code, s.name AS supplier_name, q.total_amount, q.status FROM buy_purchase_quotations q JOIN buy_purchase_requests r ON r.id = q.request_id JOIN buy_suppliers s ON s.id = q.supplier_id WHERE q.status NOT IN ('rejected', 'cancelled') ORDER BY q.quoted_on DESC, q.created_at DESC"
            )
            quotations = cursor.fetchall()
    finally:
        connection.close()
    return {"orders": orders, "quotations": quotations}


@app.post("/purchases/orders", status_code=status.HTTP_201_CREATED)
def create_purchases_order(payload: PurchaseOrderRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    user = require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, supplier_id, total_amount, status FROM buy_purchase_quotations WHERE id = %s AND status NOT IN ('rejected', 'cancelled')", (payload.quotation_id,))
            quotation = cursor.fetchone()
            if not quotation:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quotation not found or not available for an order")
            try:
                cursor.execute("INSERT INTO buy_purchase_orders (order_code, quotation_id, ordered_on, expected_on, status, total_amount, observations, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", (payload.order_code, payload.quotation_id, payload.ordered_on, payload.expected_on, payload.status, payload.total_amount if payload.total_amount is not None else quotation["total_amount"], payload.observations or None, user["id"]))
                order_id = cursor.lastrowid
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A purchase order with this code already exists") from error
    finally:
        connection.close()
    return {"id": order_id, "order_code": payload.order_code}


@app.patch("/purchases/orders/{order_id}")
def update_purchases_order(order_id: int, payload: PurchaseOrderRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, total_amount FROM buy_purchase_quotations WHERE id = %s AND status NOT IN ('rejected', 'cancelled')", (payload.quotation_id,))
            quotation = cursor.fetchone()
            if not quotation:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quotation not found or not available for an order")
            try:
                cursor.execute("UPDATE buy_purchase_orders SET order_code = %s, quotation_id = %s, ordered_on = %s, expected_on = %s, status = %s, total_amount = %s, observations = %s WHERE id = %s", (payload.order_code, payload.quotation_id, payload.ordered_on, payload.expected_on, payload.status, payload.total_amount if payload.total_amount is not None else quotation["total_amount"], payload.observations or None, order_id))
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A purchase order with this code already exists") from error
            if cursor.rowcount == 0:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Purchase order not found")
    finally:
        connection.close()
    return {"updated": True, "id": order_id}


@app.get("/purchases/invoices")
def purchases_invoices(token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT i.id, i.invoice_code, i.order_id, o.order_code, q.quote_code, s.name AS supplier_name, i.invoice_date, i.due_date, i.status, i.currency, i.subtotal, i.tax_rate, i.tax_amount, i.total_amount, i.observations, i.created_at, i.updated_at FROM buy_purchase_invoices i JOIN buy_purchase_orders o ON o.id = i.order_id JOIN buy_purchase_quotations q ON q.id = o.quotation_id JOIN buy_suppliers s ON s.id = q.supplier_id ORDER BY i.invoice_date DESC, i.created_at DESC"
            )
            invoices = cursor.fetchall()
            cursor.execute(
                "SELECT o.id, o.order_code, q.quote_code, s.name AS supplier_name, o.total_amount FROM buy_purchase_orders o JOIN buy_purchase_quotations q ON q.id = o.quotation_id JOIN buy_suppliers s ON s.id = q.supplier_id WHERE o.status <> 'cancelled' ORDER BY o.ordered_on DESC, o.created_at DESC"
            )
            orders = cursor.fetchall()
    finally:
        connection.close()
    return {"invoices": invoices, "orders": orders}


@app.post("/purchases/invoices", status_code=status.HTTP_201_CREATED)
def create_purchases_invoice(payload: PurchaseInvoiceRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    user = require_sales(token)
    tax_rate = Decimal("0.18")
    tax_amount = (payload.subtotal * tax_rate).quantize(Decimal("0.01"))
    total_amount = payload.subtotal + tax_amount
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM buy_purchase_orders WHERE id = %s AND status <> 'cancelled'", (payload.order_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Purchase order not found or cancelled")
            try:
                cursor.execute("INSERT INTO buy_purchase_invoices (invoice_code, order_id, invoice_date, due_date, status, currency, subtotal, tax_rate, tax_amount, total_amount, observations, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (payload.invoice_code, payload.order_id, payload.invoice_date, payload.due_date, payload.status, payload.currency, payload.subtotal, tax_rate, tax_amount, total_amount, payload.observations or None, user["id"]))
                invoice_id = cursor.lastrowid
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A purchase invoice with this code already exists") from error
    finally:
        connection.close()
    return {"id": invoice_id, "invoice_code": payload.invoice_code, "tax_amount": tax_amount, "total_amount": total_amount}


@app.patch("/purchases/invoices/{invoice_id}")
def update_purchases_invoice(invoice_id: int, payload: PurchaseInvoiceRequest, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    tax_rate = Decimal("0.18")
    tax_amount = (payload.subtotal * tax_rate).quantize(Decimal("0.01"))
    total_amount = payload.subtotal + tax_amount
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM buy_purchase_orders WHERE id = %s AND status <> 'cancelled'", (payload.order_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Purchase order not found or cancelled")
            try:
                cursor.execute("UPDATE buy_purchase_invoices SET invoice_code = %s, order_id = %s, invoice_date = %s, due_date = %s, status = %s, currency = %s, subtotal = %s, tax_rate = %s, tax_amount = %s, total_amount = %s, observations = %s WHERE id = %s", (payload.invoice_code, payload.order_id, payload.invoice_date, payload.due_date, payload.status, payload.currency, payload.subtotal, tax_rate, tax_amount, total_amount, payload.observations or None, invoice_id))
            except pymysql.err.IntegrityError as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A purchase invoice with this code already exists") from error
            if cursor.rowcount == 0:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Purchase invoice not found")
    finally:
        connection.close()
    return {"updated": True, "id": invoice_id, "tax_amount": tax_amount, "total_amount": total_amount}


@app.get("/purchases/reports")
def purchases_reports(date_from: date | None = None, date_to: date | None = None, supplier_id: int | None = None, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The start date cannot be after the end date")
    connection = connect()
    try:
        with connection.cursor() as cursor:
            invoice_conditions = ["i.status <> 'cancelled'"]
            invoice_params = []
            if date_from:
                invoice_conditions.append("i.invoice_date >= %s")
                invoice_params.append(date_from)
            if date_to:
                invoice_conditions.append("i.invoice_date <= %s")
                invoice_params.append(date_to)
            if supplier_id:
                invoice_conditions.append("s.id = %s")
                invoice_params.append(supplier_id)
            invoice_scope = " AND ".join(invoice_conditions)
            invoice_joins = "FROM buy_purchase_invoices i JOIN buy_purchase_orders o ON o.id = i.order_id JOIN buy_purchase_quotations q ON q.id = o.quotation_id JOIN buy_suppliers s ON s.id = q.supplier_id"
            cursor.execute(f"SELECT COUNT(i.id) AS invoices, COALESCE(SUM(i.subtotal), 0) AS subtotal, COALESCE(SUM(i.tax_amount), 0) AS tax_credit, COALESCE(SUM(i.total_amount), 0) AS total {invoice_joins} WHERE {invoice_scope}", invoice_params)
            totals = cursor.fetchone()
            order_conditions = ["o.status NOT IN ('cancelled', 'received')"]
            order_params = []
            if date_from:
                order_conditions.append("o.ordered_on >= %s")
                order_params.append(date_from)
            if date_to:
                order_conditions.append("o.ordered_on <= %s")
                order_params.append(date_to)
            if supplier_id:
                order_conditions.append("s.id = %s")
                order_params.append(supplier_id)
            cursor.execute(f"SELECT COUNT(o.id) AS value FROM buy_purchase_orders o JOIN buy_purchase_quotations q ON q.id = o.quotation_id JOIN buy_suppliers s ON s.id = q.supplier_id WHERE {' AND '.join(order_conditions)}", order_params)
            open_orders = cursor.fetchone()["value"]
            request_conditions = ["r.status IN ('draft', 'requested', 'quoted')"]
            request_params = []
            if date_from:
                request_conditions.append("r.requested_on >= %s")
                request_params.append(date_from)
            if date_to:
                request_conditions.append("r.requested_on <= %s")
                request_params.append(date_to)
            if supplier_id:
                request_conditions.append("s.id = %s")
                request_params.append(supplier_id)
            cursor.execute(f"SELECT COUNT(r.id) AS value FROM buy_purchase_requests r JOIN buy_suppliers s ON s.id = r.supplier_id WHERE {' AND '.join(request_conditions)}", request_params)
            pending_requests = cursor.fetchone()["value"]
            cursor.execute(f"SELECT s.name AS label, COUNT(i.id) AS invoices, COALESCE(SUM(i.total_amount), 0) AS total {invoice_joins} WHERE {invoice_scope} GROUP BY s.id, s.name ORDER BY total DESC LIMIT 8", invoice_params)
            suppliers = cursor.fetchall()
            cursor.execute(f"SELECT r.request_type AS category, COUNT(i.id) AS invoices, COALESCE(SUM(i.total_amount), 0) AS total {invoice_joins} JOIN buy_purchase_requests r ON r.id = q.request_id WHERE {invoice_scope} GROUP BY r.request_type ORDER BY total DESC", invoice_params)
            categories = cursor.fetchall()
            cursor.execute(f"SELECT DATE_FORMAT(i.invoice_date, '%%Y-%%m') AS period, COUNT(i.id) AS invoices, COALESCE(SUM(i.total_amount), 0) AS total {invoice_joins} WHERE {invoice_scope} GROUP BY period ORDER BY period", invoice_params)
            monthly = cursor.fetchall()
            cursor.execute(f"SELECT i.status AS label, COUNT(i.id) AS value {invoice_joins} WHERE {invoice_scope} GROUP BY i.status ORDER BY value DESC", invoice_params)
            statuses = cursor.fetchall()
            cursor.execute("SELECT id, name FROM buy_suppliers WHERE is_active = 1 ORDER BY name")
            supplier_options = cursor.fetchall()
    finally:
        connection.close()
    return {"totals": totals, "open_orders": open_orders, "pending_requests": pending_requests, "suppliers": suppliers, "categories": categories, "monthly": monthly, "statuses": statuses, "supplier_options": supplier_options}


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
    payload.service_order_id = payload.service_order_id or None
    connection = connect()
    stored_file_id = None
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM sell_customers WHERE id = %s AND is_active = 1", (payload.customer_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")
            if payload.service_order_id:
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
    payload.service_order_id = payload.service_order_id or None
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM sell_quotations WHERE id = %s", (quotation_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quotation not found")
            if payload.service_order_id:
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


@app.get("/sales/reports")
def sales_reports(date_from: date | None = None, date_to: date | None = None, customer_id: int | None = None, token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    require_sales(token)
    quote_conditions = ["1=1"]
    quote_params: list = []
    if date_from:
        quote_conditions.append("DATE(q.created_at) >= %s")
        quote_params.append(date_from)
    if date_to:
        quote_conditions.append("DATE(q.created_at) <= %s")
        quote_params.append(date_to)
    if customer_id:
        quote_conditions.append("q.customer_id = %s")
        quote_params.append(customer_id)
    quote_where = " AND ".join(quote_conditions)

    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(DISTINCT q.id) AS quotations, COALESCE(SUM(i.subtotal), 0) AS subtotal FROM sell_quotations q LEFT JOIN sell_quotation_items i ON i.quotation_id = q.id WHERE {quote_where}", quote_params)
            quote_totals = cursor.fetchone()
            cursor.execute(f"SELECT COUNT(DISTINCT q.id) AS total, COUNT(DISTINCT CASE WHEN q.service_order_id IS NOT NULL THEN q.id END) AS converted FROM sell_quotations q WHERE {quote_where}", quote_params)
            conversion = cursor.fetchone()
            cursor.execute(f"SELECT c.name AS label, COUNT(DISTINCT q.id) AS quotations, COALESCE(SUM(i.subtotal), 0) AS total FROM sell_quotations q JOIN sell_customers c ON c.id = q.customer_id LEFT JOIN sell_quotation_items i ON i.quotation_id = q.id WHERE {quote_where} GROUP BY c.id, c.name ORDER BY total DESC LIMIT 8", quote_params)
            top_customers = cursor.fetchall()
            cursor.execute(f"SELECT DATE_FORMAT(q.created_at, '%%Y-%%m') AS period, COUNT(DISTINCT q.id) AS quotations, COALESCE(SUM(i.subtotal), 0) AS total FROM sell_quotations q LEFT JOIN sell_quotation_items i ON i.quotation_id = q.id WHERE {quote_where} GROUP BY period ORDER BY period", quote_params)
            monthly = cursor.fetchall()
            order_conditions = ["1=1"]
            order_params: list = []
            if date_from:
                order_conditions.append("o.approved_at >= %s")
                order_params.append(date_from)
            if date_to:
                order_conditions.append("o.approved_at <= %s")
                order_params.append(date_to)
            if customer_id:
                order_conditions.append("o.customer_id = %s")
                order_params.append(customer_id)
            cursor.execute(f"SELECT o.status, COUNT(*) AS value FROM sell_service_orders o WHERE {' AND '.join(order_conditions)} GROUP BY o.status ORDER BY value DESC", order_params)
            order_statuses = cursor.fetchall()
            visit_conditions = ["1=1"]
            visit_params: list = []
            if date_from:
                visit_conditions.append("DATE(COALESCE(v.scheduled_at, v.created_at)) >= %s")
                visit_params.append(date_from)
            if date_to:
                visit_conditions.append("DATE(COALESCE(v.scheduled_at, v.created_at)) <= %s")
                visit_params.append(date_to)
            if customer_id:
                visit_conditions.append("v.customer_id = %s")
                visit_params.append(customer_id)
            cursor.execute(f"SELECT v.status, COUNT(*) AS value FROM sell_technical_visits v WHERE {' AND '.join(visit_conditions)} GROUP BY v.status ORDER BY value DESC", visit_params)
            visit_statuses = cursor.fetchall()
            cursor.execute("SELECT id, name FROM sell_customers WHERE is_active = 1 ORDER BY name")
            customer_options = cursor.fetchall()
            cursor.execute(f"SELECT COUNT(*) AS value FROM sell_customers WHERE is_active = 1")
            active_customers = cursor.fetchone()["value"]
            cursor.execute(f"SELECT COUNT(*) AS value FROM sell_service_orders o WHERE o.status <> 'cancelled' AND {' AND '.join(order_conditions)}", order_params)
            order_total = cursor.fetchone()["value"]
            cursor.execute(f"SELECT COUNT(*) AS value FROM sell_technical_visits v WHERE {' AND '.join(visit_conditions)}", visit_params)
            visit_total = cursor.fetchone()["value"]
    finally:
        connection.close()
    subtotal = Decimal(str(quote_totals["subtotal"] or 0))
    quotation_count = int(quote_totals["quotations"] or 0)
    converted = int(conversion["converted"] or 0)
    return {
        "totals": {"customers": active_customers, "quotations": quotation_count, "subtotal": subtotal, "igv": subtotal * IGV_RATE, "total": subtotal + subtotal * IGV_RATE, "orders": order_total, "visits": visit_total},
        "conversion": {"total": int(conversion["total"] or 0), "converted": converted, "pending": max(quotation_count - converted, 0), "rate": (converted / quotation_count * 100) if quotation_count else 0},
        "customers": top_customers,
        "monthly": monthly,
        "order_statuses": order_statuses,
        "visit_statuses": visit_statuses,
        "customer_options": customer_options,
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
            {"key": "hr", "label": "RR.HH.", "status": "active", "description": "Personal, mano de obra temporal, asignaciones y planilla."},
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
