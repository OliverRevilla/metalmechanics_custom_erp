# Dependencias y compatibilidad

Análisis estático del 2026-10-20. Los SHA exactos están en [sources.lock.json](sources.lock.json) y en los gitlinks del repositorio principal.

## Selección

| Componente | Selección | Evidencia |
|---|---|---|
| Frappe | v16.52.0 | `vendor/frappe/frappe/__init__.py` |
| ERPNext | v16.51.1 | `vendor/erpnext/erpnext/__init__.py` |
| Compatibilidad Frappe | >=16.50.0,<17.0.0 | ERPNext `pyproject.toml`, sección tool.bench.frappe-dependencies |
| Python | >=3.14,<3.15 | Intersección: Frappe >=3.14,<3.15 y ERPNext >=3.14 |
| Node | >=24; línea 24 propuesta | Frappe package.json; Docker custom/Containerfile |
| MariaDB | línea 11.8 propuesta | CI de Frappe y override oficial Docker |
| Redis | línea 8.6-alpine propuesta | Override Docker; falta prueba de integración |
| Base Linux | Debian trixie propuesta | Docker custom/Containerfile |

Frappe 16.52.0 satisface el intervalo declarado por ERPNext. El builder Docker revisado usa Python 3.14, Node 24 y version-16, y su example.env referencia ERPNext v16.51.1. Esto sustenta la selección, pero no prueba que la instalación ni todas las operaciones funcionen.

No se exige igualdad de minor/patch entre Frappe y ERPNext. No se usan sus ramas develop como selección de producción.

## Fuentes y construcción

`vendor/` es la ubicación canónica de las tres fuentes externas. El código propio estará en `apps/metalmecanica/`. No se mantendrá otro clon persistente bajo un Bench del host. Las copias dentro de las capas de imagen serán artefactos de construcción derivados de esas fuentes.

El Dockerfile oficial custom ejecuta Bench para obtener aplicaciones por URL/rama. No basta copiarlo literalmente para cumplir el requisito de fuentes locales fijadas. En fase 1 se verificará el mecanismo de Bench para consumir las fuentes locales sin volver a resolver ramas remotas.

## Bloqueo pendiente

Los commits externos ya están fijados. No existe todavía un bloqueo completo del entorno:

- Los pyproject contienen rangos de dependencias Python; falta resolver y registrar transitivas y hashes.
- Se conservarán los yarn.lock upstream y se validará el modo de instalación congelada que corresponda.
- Faltan versiones exactas de Bench, instaladores y herramientas de construcción.
- Faltan patch exacto y digest de Python, MariaDB, Redis e imágenes base.
- Los paquetes del sistema también requieren una política de repositorio/snapshot si se exige reconstrucción idéntica.
- Algunas dependencias Git de Frappe ya fijan SHA; deberán conservarse en el inventario.

Fijar un tag Docker sin digest no garantiza inmutabilidad. La fase 1 cerrará estos valores después de comprobar disponibilidad y resolución; no se inventan versiones ni hashes.

## Procedencia y licencias

| Fuente | Licencia identificada | Evidencia local |
|---|---|---|
| Frappe | MIT | vendor/frappe/LICENSE |
| ERPNext | GNU GPL v3; hooks declara v3 | vendor/erpnext/license.txt y erpnext/hooks.py |
| frappe_docker | MIT | vendor/frappe_docker/LICENSE |

Conservar archivos de licencia y atribuciones al preparar artefactos. La licencia de la app propia y las obligaciones ante una eventual distribución a terceros requieren una decisión documentada antes de distribuir. La privacidad del repositorio no sustituye esa revisión. No se ha realizado un análisis jurídico ni un inventario exhaustivo de licencias transitivas.

## Fuentes oficiales consultadas

- [Frappe fijado](https://github.com/frappe/frappe/tree/4515014c87ece6cb20ee91d2cfb121c5cbd1ff4a)
- [ERPNext fijado](https://github.com/frappe/erpnext/tree/5af6561f5aa48104559692fb4fd82f2580e8d338)
- [Docker fijado](https://github.com/frappe/frappe_docker/tree/df2a3b72910634cf0e878b59eccf68a93736bf9d)
