# Seguridad y QA

Plan de fase 0. No se han ejecutado pruebas de aplicación ni escaneos de vulnerabilidades. No existe una certificación de ausencia de vulnerabilidades.

## Roles propuestos

| Rol interno | Acceso principal | Restricción |
|---|---|---|
| Administrador técnico | Usuarios, configuración y mantenimiento | Cuenta privilegiada separada del trabajo diario |
| Gerencia | Visión de cartera, costos y autorizaciones | Acciones sensibles auditadas |
| Comercial | Clientes, oportunidades y cotizaciones | Sin conciliación de pagos |
| Oficina técnica interna | Revisiones, BOM y cambios técnicos | Sin aprobar sus propias excepciones cuando aplique |
| Operaciones | Tareas, fabricación y avance | Proyectos asignados |
| Almacén/compras | Materiales y documentos logísticos | Sin aprobación financiera global |
| Finanzas | Facturas, pagos, vencimientos y conciliación | Permisos financieros explícitos |

Una persona puede acumular roles en una empresa pequeña, pero las combinaciones se revisarán con el propietario. Las áreas del contratante no son estos roles internos.

Usar DocPerm y User Permission como base y restricciones adicionales por proyecto cuando sea necesario. Probar documentos, listas, reportes, adjuntos, exportaciones y API con usuarios ordinarios; Administrator no demuestra aislamiento.

## Controles

- Validación y autorización de transiciones en servidor; no confiar en controles visuales.
- Evitar get_all, ignore_permissions y escrituras directas en recorridos de usuario sin justificación.
- Adjuntos de contratos y conformidades privados, sometidos a autorización y respaldo.
- Activar track_changes en datos críticos y registrar revisiones/decisiones explícitas. Version no es un registro inmutable por sí solo.
- Credenciales y claves de cifrado fuera de Git, con acceso limitado y recuperación documentada.
- Sin exposición directa de MariaDB/Redis ni montaje de docker.sock.
- No crear endpoints públicos para aprobadores externos en el MVP.

## Riesgos observados en referencias oficiales

| Hallazgo | Evidencia | Tratamiento previsto |
|---|---|---|
| Password DB de ejemplo 123 | compose.mariadb.yaml | Secreto obligatorio, sin fallback |
| Etiquetas/herramientas mutables | Containerfile y overrides | Versiones exactas, hashes/digests |
| Backup incompleto por defecto | compose.backup-cron.yaml | Incluir archivos y validar recuperación |
| Acceso al socket Docker | compose.backup-cron.yaml | Programación sin ese montaje |
| Seguimiento de cambios condicional | Frappe Document.save_version | Revisiones explícitas y pruebas |
| Posible doble contabilización de costos | Mezcla inventario/compras/facturas | Conciliación con origen de cada importe |

Estas observaciones son riesgos de configuración/diseño; no se presentan como CVE comprobadas. El inventario transitivo, SBOM y contraste con avisos de seguridad se harán sobre dependencias e imagen resueltas antes del despliegue.

## Pruebas previstas

1. Instalación limpia y salud: DB, Redis, sitio, acceso, websocket, scheduler y tarea de cola.
2. Permisos: acceso cruzado entre proyectos, listas, reportes, API, archivos y exportaciones.
3. ETO: revisión, rechazo, sustitución, evidencia y cambios de alcance; invalidación de aprobaciones cuando corresponda.
4. Costos: consumo, compra, devolución, cancelación y cambio de moneda sin doble conteo.
5. Caja: hito parcial, factura parcial, pago parcial, vencimiento y cancelación; sin duplicados ante reintentos.
6. Concurrencia: dos ediciones y transición repetida sin perder decisiones ni generar facturas duplicadas.
7. Migración: sitio con datos representativos, backup previo, migrate y comprobación de invariantes.
8. Recuperación: restaurar DB, archivos privados/públicos y claves en entorno aislado; medir RPO/RTO.

La fase 1 ejecutará las pruebas de infraestructura. Las pruebas de negocio se incorporarán con cada comportamiento implementado.
