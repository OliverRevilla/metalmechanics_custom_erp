# Plan de fase 1 y recursos sujetos a aprobación

El prompt requiere aprobar el diseño antes de fase 1 y aprobar recursos antes de construir imágenes o crear volúmenes. Este documento concreta la propuesta; no autoriza por sí mismo la ejecución.

## Archivos previstos

- apps/metalmecanica/: aplicación Frappe propia y pruebas básicas.
- deployment/Dockerfile y deployment/docker-compose.yml.
- deployment/config/: configuración no secreta y ejemplos sin credenciales reales.
- deployment/scripts/: inicialización, verificación, respaldo y restauración.
- Archivos de bloqueo de dependencias, digests y .dockerignore.
- Actualización de documentación y reglas Git para excluir secretos y datos locales.
- Configuración local secreta ignorada por Git, cuya ubicación exacta se definirá al implementarla.

No modificar núcleos Frappe/ERPNext ni borrar o reinstalar sitios existentes.

## Recursos previstos

| Recurso | Propuesta |
|---|---|
| Proyecto Compose | metalmecanica-dev |
| Imagen local propia | metalmecanica-erp:dev, con digest registrado |
| Sitio de desarrollo | metalmecanica.localhost, sujeto a resolución comprobada |
| Acceso HTTP | 127.0.0.1:8080, sujeto a disponibilidad |
| Volúmenes de datos | metalmecanica-dev_db-data, metalmecanica-dev_sites, metalmecanica-dev_redis-queue-data |
| Volúmenes técnicos | Entorno Python y dependencias JS Linux; nombres al cerrar el Compose |
| Red interna | Red privada del proyecto Compose |

La construcción descargará dependencias e imágenes base y consumirá disco. Antes de ejecutar se comprobarán recursos disponibles y se presentarán nombres definitivos si cambian respecto a esta propuesta. No publicar DB ni Redis.

## Secuencia

1. Verificar Bench y cerrar versiones exactas, locks/digests y consumo de fuentes locales.
2. Crear estructura de la app propia usando el flujo Frappe verificado.
3. Preparar Dockerfile, Compose principal y configuración revisables.
4. Construir imagen propia tras la aprobación de recursos aplicable.
5. Inicializar un sitio nuevo e instalar Frappe, ERPNext y metalmecanica.
6. Verificar salud de todos los procesos, permisos básicos y persistencia.
7. Cambiar un recurso propio mínimo y comprobar que se refleja en desarrollo.
8. Ensayar respaldo/restauración y documentar resultados y límites.

La aprobación de fase 0 no se interpreta como autorización para construir imágenes, crear volúmenes o implementar reglas de negocio todavía.
