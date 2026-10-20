# Hallazgos de seguridad de dependencias: fase 1

Fecha: 2026-10-20. Consulta de metadatos oficiales de PyPI para los locks de runtime y herramientas. Se revisaron 166 pares paquete/versión, sin consultas fallidas.

La consulta inicial identificó avisos para nueve paquetes de runtime. Se actualizaron las herramientas elegidas por el proyecto: pip 26.2.1, wheel 0.48.0 y setuptools 84.2.0 en el entorno de la aplicación. El entorno Bench permanece separado y tiene sus propias restricciones.

## Pendientes

| Paquete y entorno | Versión fijada | Restricción actual | Referencia de aviso |
|---|---|---|---|
| oauthlib, runtime | 3.3.1 | Frappe ~=3.3.1; corrección declarada 4.0.0 | [GHSA-xpv3-w29h-x7cv](https://osv.dev/vulnerability/GHSA-xpv3-w29h-x7cv) |
| pdfkit, runtime | 1.0.0 | Frappe ~=1.0.0; PyPI no declara versión corregida | [GHSA-9g3x-6x24-vf9f](https://osv.dev/vulnerability/GHSA-9g3x-6x24-vf9f) |
| PyJWT, runtime | 2.13.0 | Frappe ~=2.13.0; correcciones declaradas desde 2.14 | [GHSA-w6j9-cwv2-h6wq](https://osv.dev/vulnerability/GHSA-w6j9-cwv2-h6wq) |
| pypdf, runtime | 6.15.0 | Frappe ==6.15.0 | [GHSA-qv6h-rv94-w285](https://osv.dev/vulnerability/GHSA-qv6h-rv94-w285) |
| WeasyPrint, runtime | 68.0 | Frappe ==68.0; algunos avisos declaran corrección 70.0 | [GHSA-r543-q48m-4c9j](https://osv.dev/vulnerability/GHSA-r543-q48m-4c9j) |
| Werkzeug, runtime | 3.1.6 | Frappe ==3.1.6; corrección declarada 3.1.9 | [GHSA-g6x2-hccm-hh4m](https://osv.dev/vulnerability/GHSA-g6x2-hccm-hh4m) |
| setuptools, herramientas Bench | 81.0.0 | Bench >=71,<82; corrección declarada >=83 | [GHSA-h35f-9h28-mq5c](https://osv.dev/vulnerability/GHSA-h35f-9h28-mq5c) |

La tabla muestra referencias representativas; varios avisos comparten alias GHSA/PYSEC, por lo que no se suman como vulnerabilidades independientes. El informe completo local es deployment/reports/pypi-advisories.json, ignorado por Git.

## Tratamiento

El entorno se restringe a desarrollo local en loopback y no se presenta como apto para producción. Resolver estos avisos requiere evaluar una revisión upstream compatible o una excepción técnica con pruebas; no se modificaron los núcleos ni se forzaron versiones fuera de las restricciones oficiales para ocultar el conflicto.

El uso de Chromium no demuestra que pdfkit o WeasyPrint estén inaccesibles en todos los recorridos de Frappe. No se considera resuelto un aviso solo porque no intervino en una prueba de salud.

## Límites de la revisión

Esta consulta acredita avisos asociados a versiones, no una evaluación completa de explotabilidad en este ERP. Faltan análisis de paquetes del sistema, dependencias JS y código propio, además de las dependencias VCS Gunicorn/PyPika y las aplicaciones locales. No afirmar que una imagen está libre de vulnerabilidades por pasar pruebas funcionales.

Las fuentes primarias de cada versión son los endpoints https://pypi.org/pypi/<paquete>/<version>/json registrados en el informe. Los avisos deben reconsultarse al actualizar las fuentes y antes de desplegar.
