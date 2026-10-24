# Modelo de datos independiente

Este documento define el modelo de `apps_ind`. La aplicación no reutiliza DocTypes,
ORM, permisos ni tablas de Frappe/ERPNext. Los nombres `acc_*` pertenecen al dominio
contable propio y deben evolucionar mediante migraciones explícitas.

## Principios

- `ind_users` representa identidad y autenticación; no contiene datos contables.
- Cada módulo es dueño de sus tablas y expone su API mediante FastAPI.
- Las relaciones se expresan con claves foráneas reales y no con nombres libres.
- Los importes se almacenan en `DECIMAL(18, 6)`; no se usan `float` para persistencia.
- Un asiento publicado es inmutable en esta primera versión: una corrección futura
  deberá hacerse mediante reversa y un nuevo asiento.
- La autorización inicial es por rol (`admin`, `accounts`); luego se puede reemplazar
  por permisos más finos sin cambiar las relaciones principales.

## Entidades

### Identidad

`ind_users`

| Campo | Tipo | Regla |
|---|---|---|
| `id` | BIGINT | PK |
| `username` | VARCHAR(80) | único |
| `email` | VARCHAR(255) | único |
| `role` | VARCHAR(40) | `admin`, `accounts`, `user`, etc. |
| `is_active` | BOOLEAN | controla el acceso |

### Contabilidad

`acc_companies`

Representa una empresa o entidad contable independiente.

| Campo | Tipo | Regla |
|---|---|---|
| `id` | BIGINT | PK |
| `name` | VARCHAR(160) | obligatorio |
| `tax_id` | VARCHAR(40) | opcional, único si existe |
| `description` | TEXT | descripción operativa |
| `default_currency` | CHAR(3) | ISO 4217, por defecto `PEN` |
| `is_active` | BOOLEAN | no elimina historial |

`acc_fiscal_years`

Periodos fiscales asociados a una compañía.

| Campo | Tipo | Regla |
|---|---|---|
| `id` | BIGINT | PK |
| `company_id` | BIGINT | FK → `acc_companies.id` |
| `name` | VARCHAR(80) | etiqueta visible |
| `starts_on`, `ends_on` | DATE | rango no superpuesto en la aplicación |
| `is_closed` | BOOLEAN | impide nuevos asientos |

`acc_accounts`

Plan de cuentas jerárquico. `parent_id` permite construir árbol; `root_type` permite
clasificar `Asset`, `Liability`, `Equity`, `Income` y `Expense`.

| Campo | Tipo | Regla |
|---|---|---|
| `id` | BIGINT | PK |
| `company_id` | BIGINT | FK → `acc_companies.id` |
| `parent_id` | BIGINT | FK → `acc_accounts.id`, nullable |
| `code` | VARCHAR(30) | único por compañía |
| `name` | VARCHAR(160) | obligatorio |
| `root_type` | VARCHAR(20) | clasificación contable |
| `account_type` | VARCHAR(40) | detalle opcional |
| `is_group` | BOOLEAN | si puede tener hijos |
| `is_active` | BOOLEAN | conserva cuentas históricas |

`acc_journal_entries`

Cabecera del asiento contable.

| Campo | Tipo | Regla |
|---|---|---|
| `id` | BIGINT | PK |
| `company_id` | BIGINT | FK → `acc_companies.id` |
| `fiscal_year_id` | BIGINT | FK → `acc_fiscal_years.id` |
| `posting_date` | DATE | dentro del periodo |
| `voucher_type` | VARCHAR(40) | tipo operativo, por ejemplo `Journal Entry` |
| `reference` | VARCHAR(120) | referencia externa opcional |
| `remarks` | TEXT | glosa |
| `status` | VARCHAR(20) | `draft` o `posted` |
| `created_by` | BIGINT | FK → `ind_users.id` |

`acc_journal_lines`

Líneas del asiento. La suma de débitos debe ser igual a la suma de créditos antes de
publicar un asiento.

| Campo | Tipo | Regla |
|---|---|---|
| `id` | BIGINT | PK |
| `journal_entry_id` | BIGINT | FK → `acc_journal_entries.id` |
| `account_id` | BIGINT | FK → `acc_accounts.id` |
| `description` | VARCHAR(255) | detalle |
| `debit`, `credit` | DECIMAL(18,6) | uno de los dos debe ser mayor que cero |

## Relaciones

```text
ind_users ───────┐
                 └── acc_journal_entries ──< acc_journal_lines >── acc_accounts
acc_companies ──< acc_fiscal_years
      │
      ├──────────< acc_accounts ──< acc_accounts (parent_id)
      └──────────< acc_journal_entries
```

## Flujo mínimo de Accounts

1. Crear compañía.
2. Crear periodo fiscal.
3. Crear plan de cuentas, opcionalmente con cuentas padre.
4. Crear asiento con dos o más líneas.
5. Validar que cada línea pertenezca a la misma compañía y que débitos = créditos.
6. Publicar el asiento y mostrarlo en el resumen.

## Limitaciones de la primera versión

- No incluye todavía impuestos, centros de costo, dimensiones contables, multi-moneda
  con tipo de cambio, conciliación bancaria, cuentas por cobrar/pagar, activos fijos,
  presupuestos ni reportes financieros completos.
- No existe aún un motor de periodos superpuestos ni cierre con reversas automáticas;
  el API aplica las validaciones básicas y se ampliará con migraciones.
- No se replica la configuración declarativa de DocTypes de Frappe. La ventaja es que
  el modelo queda explícito y testeable; el costo es que cada nueva capacidad requiere
  diseñar tablas, migraciones, endpoints y pantallas.
- Selling deberá referenciar compañías y cuentas de este módulo mediante sus IDs, no
  duplicar esos datos. La relación comercial se añadirá cuando se diseñe `sell_*`.
- La administración de una compañía vive en `Configuración → Compañía`; Accounts solo
  selecciona la compañía configurada para trabajar sus cuentas y asientos.

## Dominios siguientes

Los siguientes módulos tienen navegación inicial, pero todavía requieren su propio
modelo antes de implementar formularios y operaciones:

- `buy_*` para Compras: proveedores, solicitudes diferenciadas de materiales y
  servicios, cotizaciones, órdenes de compra y facturas.
- `inv_*` para Inventario: items, warehouses, movimientos de stock y reposiciones.
- `prod_*` para Producción: BOM, planes, mano de obra y órdenes de servicio.
- `proj_*` para Gestión de Proyectos: proyectos, tareas, Gantts y timesheets.

Relaciones previstas:

```text
Ventas ──< Órdenes de Servicio ──> Producción
Compras ──> Inventario ──> Producción
Producción ──> Finanzas
Proyectos ──> Finanzas (costos y horas)
```

Producción no creará órdenes de servicio duplicadas: consumirá las órdenes aprobadas
por Ventas mediante una relación explícita. Inventario será el origen de existencias
para Compras y Producción, evitando saldos independientes por módulo.

## Ventas: clientes

La primera entidad implementada del módulo Ventas es `sell_customers`. Representa
tanto personas naturales como personas jurídicas y concentra los datos reutilizables
por cotizaciones, órdenes de servicio y visitas técnicas.

| Campo | Tipo | Regla |
|---|---|---|
| `id` | BIGINT | PK |
| `customer_type` | VARCHAR(20) | `natural` o `legal` |
| `name` | VARCHAR(180) | nombres y apellidos o razón social |
| `document_type` | VARCHAR(20) | DNI, RUC, CE, pasaporte u otro |
| `document_number` | VARCHAR(40) | número de identificación; combinación única cuando existe |
| `email` | VARCHAR(255) | correo de contacto opcional |
| `phone` | VARCHAR(40) | teléfono principal |
| `secondary_phone` | VARCHAR(40) | teléfono alternativo |
| `address` | VARCHAR(255) | dirección fiscal o comercial |
| `city` | VARCHAR(100) | ciudad |
| `contact_name` | VARCHAR(160) | contacto comercial, especialmente para empresas |
| `notes` | TEXT | observaciones internas |
| `is_active` | BOOLEAN | permite conservar el historial sin borrar clientes |
| `created_by` | BIGINT | FK → `ind_users.id` |

Relaciones previstas:

```text
ind_users ──< sell_customers
sell_customers ──< sell_quotations
sell_service_orders ──< sell_quotations
sell_customers ──< sell_service_orders
sell_service_orders ──o< sell_technical_visits (opcional)
sell_service_orders ──< prod_service_orders (referencia, no duplicación)
```

La pantalla `Ventas → Clientes` permite registrar y editar la ficha. La validación
actual distingue el tipo de cliente y evita repetir la combinación de tipo y número
de documento. Todavía no se valida el documento contra SUNAT, RENIEC u otro servicio
externo; esa integración se podrá agregar después sin cambiar la relación principal.

## Ventas: cotizaciones

`sell_quotations` almacena la cabecera de una cotización y `sell_quotation_items`
almacena sus líneas. El número de ítem no lo ingresa el usuario: el API lo genera
secuencialmente en el orden recibido, comenzando en 1.

| Entidad/campo | Tipo | Regla |
|---|---|---|
| `sell_quotations.customer_id` | BIGINT | FK → `sell_customers.id` |
| `sell_quotations.service_order_id` | BIGINT | FK → `sell_service_orders.id`, obligatorio |
| `sell_quotations.quote_code` | VARCHAR(60) | código único de cotización |
| `sell_quotations.observations` | TEXT | condiciones u observaciones comerciales |
| `sell_quotations.file_id` | BIGINT | FK → `ind_files.id`, PDF opcional |
| `sell_quotation_items.item_number` | INT | secuencia por cotización: 1, 2, 3… |
| `sell_quotation_items.description` | VARCHAR(255) | detalle del producto o servicio |
| `sell_quotation_items.unit` | VARCHAR(40) | UND, KG, H, SERVICIO, etc. |
| `sell_quotation_items.quantity` | DECIMAL(18,6) | mayor que cero |
| `sell_quotation_items.unit_cost` | DECIMAL(18,6) | mayor o igual a cero |
| `sell_quotation_items.subtotal` | DECIMAL(18,6) | cantidad × costo unitario |

La pantalla `Ventas → Cotizaciones` muestra el directorio, permite crear y editar
cotizaciones desde un formulario superpuesto, calcula subtotales y total, y permite
adjuntar un PDF de hasta 25 MB. El PDF queda en el almacenamiento persistente de la
instancia independiente y se referencia desde la cotización; no se copia el archivo
en la base de datos. El resumen aplica IGV de 18% sobre el subtotal:

```text
subtotal = suma de subtotales de los ítems
igv      = subtotal × 0.18
total    = subtotal + igv
```

También existe un PDF generado por la aplicación para descargar la cotización con su
detalle e importes. Es distinto del PDF adjunto que el usuario puede conservar como
respaldo original.

## Ventas: visitas técnicas

`sell_technical_visits` representa una visita de campo asociada a un cliente. La
dirección es obligatoria; la latitud y longitud son opcionales y pueden registrarse
haciendo clic en un mapa Leaflet o escribiéndolas manualmente.

| Campo | Tipo | Regla |
|---|---|---|
| `customer_id` | BIGINT | FK → `sell_customers.id`, obligatorio |
| `service_order_id` | BIGINT | FK → `sell_service_orders.id`, opcional |
| `title` | VARCHAR(180) | motivo o título de la visita |
| `scheduled_at` | DATETIME | fecha y hora opcionales |
| `address` | VARCHAR(255) | dirección de ejecución, obligatoria |
| `city` | VARCHAR(100) | ciudad opcional |
| `latitude` | DECIMAL(10,7) | rango -90 a 90, opcional |
| `longitude` | DECIMAL(10,7) | rango -180 a 180, opcional |
| `status` | VARCHAR(20) | `planned`, `completed` o `cancelled` |
| `observations` | TEXT | indicaciones y notas |
| `created_by` | BIGINT | FK → `ind_users.id` |

La pantalla `Ventas → Visitas Técnicas` muestra el directorio y usa un formulario
superpuesto para crear o editar visitas. El mapa usa Leaflet y OpenStreetMap; no se
obliga a registrar coordenadas si la dirección textual es suficiente.

## Ventas: órdenes de servicio

`sell_service_orders` representa una orden aprobada por un cliente y será el punto
de entrada del flujo de caja. La fecha de aprobación es obligatoria y se muestra en
el directorio para ordenar y consultar las órdenes por su inicio comercial.

| Campo | Tipo | Regla |
|---|---|---|
| `customer_id` | BIGINT | FK → `sell_customers.id`, obligatorio |
| `order_code` | VARCHAR(60) | ID único de la orden de servicio |
| `description` | TEXT | alcance o descripción de la orden |
| `project_type` | VARCHAR(120) | tipo de proyecto |
| `observations` | TEXT | condiciones y notas adicionales |
| `approver_name` | VARCHAR(180) | persona que aprobó, obligatorio |
| `approver_email` | VARCHAR(255) | correo del aprobador |
| `approver_phone` | VARCHAR(40) | teléfono del aprobador |
| `approved_at` | DATE | fecha de aprobación, obligatoria |
| `project_due_date` | DATE | fecha final comprometida del proyecto |
| `status` | VARCHAR(20) | aprobada, en ejecución, completada o cancelada |
| `file_id` | BIGINT | FK → `ind_files.id`, documento opcional |

La pantalla `Ventas → Órdenes de Servicio` muestra el directorio y utiliza un
formulario superpuesto para crear o editar órdenes. Se pueden adjuntar documentos
PDF, DOC, DOCX o TXT de hasta 25 MB. La orden queda preparada para enlazarse
posteriormente con Producción y Finanzas sin duplicar al cliente.

## Gestión de Proyectos: proyectos y documentación por áreas

`proj_projects` representa el proyecto y exige una orden de servicio asociada.
`proj_area_documents` contiene una ficha independiente por cada área que recibe
documentación:

```text
Proyecto
├── Producción
├── Calidad
├── Administración
├── SSOMA
├── Oficina Técnica
├── Residente
└── Planeamiento
```

### `proj_projects`

| Campo | Tipo | Regla |
|---|---|---|
| `project_code` | VARCHAR(60) | ID único del proyecto |
| `name` | VARCHAR(180) | nombre del proyecto |
| `service_order_id` | BIGINT | FK → `sell_service_orders.id`, obligatorio |
| `start_date` | DATE | fecha de inicio tomada automáticamente de `sell_service_orders.approved_at` |
| `planned_end_date` | DATE NULL | fecha final comprometida tomada de la orden de servicio |
| `end_date` | DATE NULL | fecha de término; vacía mientras el proyecto no esté completado |
| `description` | TEXT | alcance general |
| `observations` | TEXT | notas generales |
| `status` | VARCHAR(20) | planificado, activo, completado o cancelado |

### `proj_area_documents`

| Campo | Tipo | Regla |
|---|---|---|
| `project_id` | BIGINT | FK → `proj_projects.id` |
| `area_key` | VARCHAR(40) | área única dentro del proyecto |
| `contact_name` | VARCHAR(180) | responsable o contacto del área |
| `observations` | TEXT | observaciones de la documentación |
| `entered_at` | DATE | fecha de ingreso, obligatoria |
| `status` | VARCHAR(20) | pendiente, en revisión, aprobado o rechazado |
| `approved_at` | DATE | fecha de aprobación opcional |
| `file_id` | BIGINT | FK → `ind_files.id`, PDF sustentatorio opcional |

`Gestión de Proyectos → Proyectos` muestra el directorio y permite gestionar la
documentación de cada área desde una ficha superpuesta. Cada área conserva su propio
contacto, fechas, estado y PDF, evitando mezclar aprobaciones entre departamentos.

Cada proyecto genera además una tarea documental por área en `proj_tasks`, marcada
como `is_dead_time = true`. Su fecha real de inicio se sincroniza con `entered_at`
y su fecha real de término con `approved_at`; así la documentación no se confunde
con avance productivo y puede medirse en el Gantt y en KPIs.

### `proj_tasks`

| Campo | Tipo | Regla |
|---|---|---|
| `project_id` | BIGINT | FK → `proj_projects.id`, obligatorio |
| `task_code` | VARCHAR(80) | código único dentro del proyecto |
| `name` | VARCHAR(180) | nombre de la tarea |
| `task_type` | VARCHAR(30) | general, documentación, ingeniería, producción, calidad o administración |
| `area_key` | VARCHAR(40) | área relacionada, opcional |
| `planned_start`, `planned_end` | DATE | ventana planificada |
| `actual_start`, `actual_end` | DATE | ejecución efectiva |
| `status` | VARCHAR(20) | planificada, en ejecución, bloqueada, completada o cancelada |
| `is_dead_time` | BOOLEAN | identifica esperas documentales o improductivas |

En desarrollo, `INDEPENDENT_SEED_DUMMY_DATA=true` crea datos repetibles para probar
clientes, órdenes de servicio, proyectos activos y terminados, documentación parcial
por áreas y PDFs sustentatorios. El proyecto activo conserva `end_date` vacío; el
proyecto terminado tiene fecha de término para validar el Gantt.

## KPIs y Gantt

`GET /ind-api/sales/dashboard` expone clientes activos, cotizaciones, subtotal y
total con IGV, órdenes de servicio, órdenes en curso, órdenes que vencen en 30 días
y visitas técnicas pendientes. Son indicadores operativos; la conversión comercial
se incorporará cuando se formalice el estado de aprobación de las cotizaciones.

`GET /ind-api/projects/dashboard` expone proyectos activos, completados y vencidos,
tareas activas, tareas y días de tiempo muerto, además de documentación aprobada
frente al total y un resumen por proyecto.

`GET /ind-api/projects/gantt?project_id=<id>` devuelve el proyecto elegido y sus
tareas con fechas planificadas y reales. El frontend dibuja el diagrama por proyecto;
las tareas documentales aparecen como tiempo muerto y las tareas completadas se
colorean de forma distinta. La fecha final comprometida proviene de la Orden de
Servicio y la fecha de término real solo se registra al cerrar el proyecto.

## Compras

### `buy_suppliers`

| Campo | Tipo | Regla |
|---|---|---|
| `supplier_type` | VARCHAR(20) | persona natural o jurídica |
| `name` | VARCHAR(180) | razón social o nombre |
| `document_type`, `document_number` | VARCHAR | RUC, DNI u otro documento; el número es único |
| `supply_type` | VARCHAR(20) | productos, servicios o ambos |
| `email`, `phone`, `secondary_phone` | VARCHAR | datos de contacto |
| `address`, `city` | VARCHAR | ubicación del proveedor |
| `contact_name` | VARCHAR(160) | contacto comercial o técnico |
| `observations` | TEXT | materiales, cobertura, condiciones y notas |
| `relationship_started_on` | DATE | fecha de inicio de relación |
| `last_contact_on` | DATE | última comunicación registrada |
| `is_active` | BOOLEAN | permite conservar proveedores históricos sin mostrarlos como activos |

`Compras → Proveedores` usa este directorio para registrar tanto proveedores de
productos —por ejemplo acero— como prestadores de servicios —por ejemplo fletes o
asistencia técnica de TI— sin mezclar proveedores con clientes.

### `buy_items`, `buy_purchase_requests` y `buy_purchase_request_items`

`buy_items` mantiene catálogos separados por `item_type` (`material` o `service`),
con código, nombre, descripción y unidad base. `buy_purchase_requests` relaciona
`buy_purchase_requests` relaciona cada solicitud con un proveedor y conserva
código, descripción, fechas, estado y observaciones. Sus líneas viven en
`buy_purchase_request_items`, que permite asociar uno o varios items a una misma
solicitud, cada uno con cantidad, unidad y presentación. `item_id`, cantidad y
unidad en la tabla principal se mantienen como compatibilidad con los registros
anteriores; las nuevas operaciones usan siempre las líneas.

### `buy_purchase_quotations`

Cada cotización responde obligatoriamente a una solicitud mediante `request_id`.
También registra proveedor, código de cotización, fecha, vigencia, estado, total y
observaciones. La cinta de Materiales y Servicios concentra las vistas de Items,
Solicitudes y Cotizaciones, eliminando las antiguas subsecciones separadas de
cotizaciones.
