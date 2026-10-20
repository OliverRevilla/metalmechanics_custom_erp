# Validación de fase 1

Validación ejecutada el 20 de octubre de 2026 en `C:\Users\olive\Proyectos\metalmechanics_custom_erp`.

- Imagen propia `metalmecanica-erp:dev`, manifest `sha256:dfe45f611fa01b07d9bbfb072fe17c518395a094d13e20d8323cf6aaa978c507`.
- MariaDB `11.8.9` y Redis `8.6.7-alpine` fijados por digest; frontend publicado solo en `127.0.0.1:8080`.
- Bootstrap repetido idempotente; zona horaria del sitio configurada a `America/Lima` sin sobrescribir una elección existente.
- Todos los servicios de aplicación saludables; probes persistentes de scheduler y workers `short`/`long` pasaron; el probe HTTP de las tres colas pasó.
- Batería HTTP: 19 comprobaciones pasaron después de limpiar el manifiesto cacheado de assets (ping, login, permisos, CSRF, assets y Engine.IO).
- Scheduler: dos ejecuciones y avance de `last_execution` con tick de 60 segundos.
- Backup `20261020T171321Z-cc9e4eeb`: checksums de base de datos, archivos públicos/privados y configuración.
- Restore validado en `recovery-phase1b.localhost`, con base de datos aislada, archivos público/privado y round-trip de contraseña cifrada verificados.

Los reportes reproducibles están en `deployment/reports/` y el bundle local en `deployment/backups/` (ambos ignorados por Git). La primera restauración expuso el comportamiento de prefijos absolutos de tar en este release; el helper de restore ahora reubica solo los prefijos esperados con validación de traversal.

Pendientes antes de producción: escaneo completo de sistema operativo, JavaScript y VCS; revisión de los seis paquetes con versiones fijadas fuera de sus rangos upstream; hashes de artefactos Git no disponibles para todos los paquetes; endurecimiento de secretos, TLS, almacenamiento off-device y operación de backups. No se ha publicado ningún servicio ni se ha modificado el núcleo de Frappe/ERPNext.
