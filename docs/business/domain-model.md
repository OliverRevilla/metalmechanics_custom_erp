# Modelo de dominio propuesto

Evidencia: DocTypes JSON y controladores de ERPNext v16.51.1. Entidades nuevas sujetas a validar brechas en un sitio de desarrollo.

## Reutilización

| Concepto | Entidad nativa | Uso o extensión |
|---|---|---|
| Cliente/contacto | Customer, Contact, Address | Maestros únicos para comercial y aprobadores |
| Negociación | Opportunity, Quotation, Sales Order | Documentos comerciales y alcance vendido |
| Proyecto y tareas | Project, Task | Cliente, pedido, responsable, avance y etapas |
| Materiales | Item, UOM, Warehouse | Maestros y almacenes existentes |
| Ingeniería/fabricación | BOM, Work Order, Job Card, Operation | Listas de materiales y ejecución de taller |
| Movimientos y compras | Stock Entry, Purchase Order, Purchase Receipt, Purchase Invoice | Consumo, recepción y compromisos |
| Contrato | Contract | Términos y referencia nativa document_type/document_name a Project u otro documento |
| Presupuesto | Budget, Cost Center, Project | Evaluar límites nativos antes de consolidación ETO |
| Pagos previstos | Payment Terms Template, Payment Schedule | Cuotas y vencimientos, con vínculo propio al hito |
| Facturación y cobro | Sales Invoice, Payment Entry | Registro financiero autoritativo |
| Evidencia | File, Communication y vínculos | Archivos privados y procedencia verificable |

Task ya dispone de is_milestone: evaluar su reutilización antes de crear un hito financiero propio. Contract permite referenciar Project mediante document_type/document_name.

Project tiene customer, sales_order, cost_center y percent_complete. Work Order enlaza project y sales_order. Sales Invoice tiene project y payment_schedule. Esto permite unir el circuito sin recrear dichas entidades.

Contract incluye identificación del firmante, términos, referencias y lista de cumplimiento. No equivale automáticamente a una cadena de aprobaciones de varias áreas externas.

## Extensiones candidatas

| Entidad propuesta | Relaciones y datos mínimos |
|---|---|
| ETO Engineering Revision | Project, identificador de revisión, planos/evidencias, responsable, estado, revisión sustituida |
| ETO Budget Baseline | Project, revisión de presupuesto, partidas y aprobación; evaluar primero Budget y sus límites |
| ETO Change Request | Project, revisión/alcance afectado, motivo, impacto costo/plazo, decisión y responsable |
| ETO Execution Stage | Project/Task, modalidad taller/contratante, ubicación, secuencia y responsable; evaluar primero Custom Fields en Task |
| ETO Payment Milestone | Project, Contract, condiciones, importe/moneda, fecha prevista, factura(s) vinculada(s) |
| ETO External Approval | Hito o revisión, área externa, Contact, solicitante interno, recepción, decisión y evidencia |

Los nombres son propuestas, no DocTypes creados. Para cada candidato se comparará configuración estándar vs DocType propio. No crear un segundo maestro de clientes, inventario, facturas ni pagos.

## Relaciones e invariantes

Un Project tiene revisiones, etapas y hitos. Un hito puede requerir múltiples conformidades de áreas externas. Las conformidades deben identificar el documento y la revisión aprobados; un archivo suelto no basta.

Una revisión liberada conserva su contenido y vínculos; una nueva revisión se registra separadamente. Los cambios deben especificar qué aprobaciones se invalidan y por qué.

Permitir facturación parcial o varios documentos vinculados sin duplicar el monto previsto. Derivar saldos de facturas y pagos nativos, evitando un segundo saldo manual.

Payment Schedule no enlaza de forma nativa una tarea o hito técnico en el esquema revisado. La integración deberá mantener un único vencimiento financiero autoritativo y una relación trazable con el hito.

## Costos y tablero

Separar presupuesto original, presupuesto vigente, compromiso abierto y costo real. Definir cómo se descuentan compromisos que se convierten en facturas y cómo se tratan cancelaciones y devoluciones. Definir si las horas proceden de Job Card o Timesheet para no contar dos veces la misma mano de obra.

Project.gross_margin no constituye directamente el margen ETO solicitado: su implementación usa importes facturados y determinados costos acumulados. Se revisará el origen contable de cada importe antes de construir informes para no sumar dos veces consumo, compras y facturas.

Proyección básica: hitos facturables todavía no facturados + saldos de facturas por vencimiento, ajustados por pagos y vínculos. Mostrar supuestos y moneda; no mezclar cobro previsto con ingreso reconocido.

## Evidencia local principal

- vendor/erpnext/erpnext/projects/doctype/project/
- vendor/erpnext/erpnext/projects/doctype/task/
- vendor/erpnext/erpnext/manufacturing/doctype/work_order/
- vendor/erpnext/erpnext/crm/doctype/contract/
- vendor/erpnext/erpnext/accounts/doctype/payment_schedule/
- vendor/erpnext/erpnext/accounts/doctype/sales_invoice/
- vendor/erpnext/erpnext/stock/doctype/stock_entry/
