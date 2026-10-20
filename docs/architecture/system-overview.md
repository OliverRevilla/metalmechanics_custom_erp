# Diseño del sistema

Estado: propuesta de fase 0 para aprobación. Fecha: 2026-10-20.

## Arquitectura

Monolito modular: una aplicación Frappe, ERPNext y la extensión `metalmecanica` comparten un sitio y una base MariaDB. Los distintos contenedores representan procesos del mismo producto. Menos de diez usuarios internos; sin Kubernetes, microservicios, Azure ni modelos de IA en el MVP.

Flujo técnico: navegador -> frontend Nginx -> backend WSGI; websocket para eventos. Backend, scheduler y trabajadores comparten versión, configuración del sitio y MariaDB. Redis separa caché y cola de trabajo. Solo el frontend publica un puerto local durante desarrollo.

La interfaz inicial será Frappe Desk con formularios, workflows, informes y espacios de trabajo. No se propone un frontend independiente en esta fase.

## Límites funcionales

| Área | Base reutilizable | Extensión propia propuesta |
|---|---|---|
| Comercial | Customer, Contact, Opportunity, Quotation, Sales Order | Vínculos y validaciones ETO |
| Proyectos e ingeniería | Project, Task, BOM, File | Revisión técnica y control de cambios de alcance |
| Taller y obra | Work Order, Job Card, Task | Etapas y ubicación de ejecución |
| Inventario y costos | Item, Warehouse, Stock Entry, compras, Budget | Línea base y consolidación ETO conciliable |
| Contratos y caja | Contract, Payment Terms, Sales Invoice, Payment Entry | Hitos condicionados y evidencias de conformidad |
| Identidad | User, Role, DocPerm, User Permission | Restricción por proyecto y separación de funciones |

Estos límites son módulos de la aplicación propia, no servicios independientes. No se eliminarán módulos internos de ERPNext.

## Extensiones comprobadas en Frappe v16.52.0

- `frappe/app.py`: autenticación y despacho HTTP/API.
- `frappe/model/document.py`: guardado, permisos, concurrencia y eventos; las reglas de negocio pasarán por el ciclo ORM.
- `frappe/model/base_document.py`: admite `extend_doctype_class`; priorizar eventos y extensiones pequeñas antes de reemplazar controladores.
- `frappe/api/v1.py` y `frappe/handler.py`: CRUD y métodos expuestos. La whitelist no reemplaza los permisos de negocio.
- `frappe/utils/fixtures.py`: configuración exportable con filtros. Los DocTypes propios pertenecen al código de la app.
- `frappe/model/document.py`: Version depende de `track_changes`; no constituye por sí solo una revisión técnica inmutable.
- `frappe/database/database.py`: `set_value` omite eventos ORM. No utilizarlo para eludir validaciones de estados o costos.

Los paths anteriores son relativos a `vendor/frappe/` en el commit fijado. No se proponen cambios en los núcleos externos.

## Trazabilidad

Cotización -> pedido -> proyecto -> revisiones y etapas -> fabricación/obra -> conformidades -> hitos -> facturas -> pagos. Las relaciones con inventario y compras conservan el documento nativo de origen.

Las aprobaciones del contratante se registrarán por usuarios internos con área, Contact, revisión, fecha de recepción y evidencia privada. No se asumirá que un contacto externo es un User. Registrar una evidencia no equivale a autenticar electrónicamente al aprobador.

## Evolución

Integraciones futuras podrán usar API y trabajos asíncronos con permisos explícitos e idempotencia. No se añadirán componentes de IA anticipadamente.
