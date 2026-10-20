# Validación de fase 0

Fecha de revisión: 2026-10-20, zona America/Lima.

## Entorno observado

- Workspace usado: C:\Users\olive\Proyectos\metalmechanics_custom_erp.
- Repositorio ya existente, main, commit inicial dd4c7b5a7fcdeecda1e8377e09de1fbddaa66f29, sin cambios antes del trabajo.
- Remoto existente: https://github.com/OliverRevilla/metalmechanics_custom_erp.git. No se creó ni alteró ningún remoto del proyecto.
- No se trasladó ni eliminó contenido del workspace de OneDrive.
- Git 2.49.0.windows.1.
- Docker Desktop 4.40.0, cliente/Engine 28.0.4, contexto desktop-linux, servidor linux/amd64.
- Docker Compose v2.34.0-desktop.1.
- WSL 2, distribución predeterminada Ubuntu.
- Las comprobaciones Docker/WSL necesitaron ejecución fuera del sandbox; con ese acceso respondieron correctamente.

## Comprobaciones realizadas

- Clonación de los tres repositorios oficiales y lectura de configuración, código y licencias.
- Selección de tags Frappe/ERPNext y checkout detached; snapshot Docker fijado.
- Verificación del intervalo Frappe declarado por ERPNext y requisitos Python/Node.
- Lectura de infraestructura de referencia y de entidades ERPNext.
- Registro de submódulos y SHA exactos.
- Revisión final de documentación, JSON, gitlinks, limpieza upstream y diferencias del repositorio principal.

Los resultados de la revisión final se contrastan al entregar. Este archivo no acredita pruebas de runtime.

## Límites

No se instalaron dependencias ni herramientas. No se construyeron imágenes ni se iniciaron servicios. No se crearon sitios, volúmenes ni usuarios. No se ejecutó una aplicación ni se probaron migraciones o restauraciones.

Los permisos persistentes del chat originalmente referencian el workspace de OneDrive. Para escrituras en la nueva ruta se solicitaron las ampliaciones técnicas correspondientes. Cada comando de este trabajo posterior al cambio usa explícitamente el nuevo workdir; la asociación del chat en la interfaz no se ha cambiado mediante una herramienta.

## Resultado

Diseño y fuentes preparados para revisión. Compatibilidad declarada verificada; compatibilidad operativa pendiente de fase 1. No se realizó commit ni push del trabajo de fase 0.
