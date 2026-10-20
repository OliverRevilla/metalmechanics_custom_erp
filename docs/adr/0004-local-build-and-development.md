# ADR 0004: Construcción local y desarrollo en Windows

Estado: aplicado durante fase 1; validación operativa registrada por separado.
Fecha: 2026-10-20.

## Decisión

Construir desde las fuentes locales canónicas, copiadas a la imagen sin sus metadatos Git. No usar bench init/get-app para volver a obtener Frappe o ERPNext desde ramas remotas. Registrar aplicaciones locales en sites/apps.txt e instalarlas editables en las rutas definitivas del Bench Linux.

Bench 5.31.0 usa un virtualenv de herramientas separado: su intervalo Click ~=8.2.0 difiere del de Frappe. uv se fija a 0.11.33 dentro del intervalo ~=0.11.6 declarado por Bench. La aplicación utiliza su propio virtualenv Python 3.14.

Las cuatro imágenes base se fijan a digests linux/amd64. Los paquetes Debian se obtienen de un snapshot fechado. Yarn se fija a 1.22.22 y consume locks upstream congelados.

## Alcance de los locks

tools.txt incluye versiones y hashes de distribución. runtime.txt incluye versiones exactas y los SHA Git requeridos por Frappe para Gunicorn/PyPika; no verifica hashes de cada distribución Python. No describir este segundo lock como verificación criptográfica integral. Mejorar esa cobertura antes de una política de distribución que la exija.

## Compose y código local

Un único deployment/docker-compose.yml contiene el despliegue completo. El overlay opcional config/compose.dev.yml solo se usa para desarrollo y monta la app propia; el despliegue con código en imagen utiliza exclusivamente el principal.

Este detalle sustituye la propuesta inicial de resolver todas las diferencias únicamente con variables/perfiles: estos no permiten condicionar de forma clara un montaje de volumen. El requisito de un único Compose principal para el despliegue final se conserva.

## Consecuencias

Sin instalación Python/Node del proyecto en el host Windows ni Bench duplicado. Los cambios Python locales se aplican reiniciando procesos; los assets requieren construcción. Mantener separados los datos persistentes, el código y los secretos.

El entorno utiliza nombres propios de proyecto/red/volúmenes. Las operaciones de recuperación exigen un sitio y una DB nuevos y conservan intactos los archivos originales del respaldo.
