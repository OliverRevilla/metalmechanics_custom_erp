# Servicios Docker propuestos

Diseño de fase 0. No hay Compose ni Dockerfile propios ejecutables todavía.

Referencia: `vendor/frappe_docker/compose.yaml`, `images/custom/Containerfile`, `resources/core/` y `overrides/`, en el commit registrado.

| Servicio | Responsabilidad | Persistencia |
|---|---|---|
| frontend | Nginx, assets, proxy HTTP y websocket | Acceso a sitios según permisos requeridos |
| backend | Aplicación WSGI | Sitios y archivos |
| websocket | Eventos en tiempo real | Configuración del sitio |
| queue-short | Trabajos cortos/default según configuración verificada | Redis queue y sitios |
| queue-long | Trabajos largos/default según configuración verificada | Redis queue y sitios |
| scheduler | Programación de tareas | Sitios |
| configurator | Configuración técnica inicial; proceso de una ejecución | Configuración del sitio |
| db | MariaDB | db-data |
| redis-cache | Caché efímera | Ninguna inicialmente |
| redis-queue | Colas y comunicación según Frappe | redis-queue-data |
| bootstrap | Creación/instalación explícita del sitio; perfil manual | Sitios y DB |

Todos los procesos de aplicación usarán una misma imagen propia identificada por digest. El bootstrap no debe crear ni reinstalar sitios automáticamente en cada arranque.

## Un Compose principal

En fase 1 se creará `deployment/docker-compose.yml` con los servicios completos. Las diferencias de desarrollo y producción se resolverán mediante variables y perfiles dentro del archivo principal; no se requerirá concatenar los overrides oficiales para el despliegue final.

Desarrollo: fuentes montadas desde vendor/ y apps/, instalación editable y proceso de reconstrucción de assets verificados. Dependencias, datos y node_modules en almacenamiento Linux para evitar mezclar binarios Windows/Linux.

Producción: fuentes y assets dentro de una imagen inmutable, sin montajes de código desde Windows. No ejecutar servidores de desarrollo.

## Arranque y salud

MariaDB saludable antes de configurar. Redis requiere comprobación ping y dependencias condicionadas; la referencia oficial solo espera arranque para Redis. El configurador debe terminar correctamente antes de los procesos de aplicación.

Añadir salud HTTP del sitio y websocket; comprobar trabajadores, scheduler y una tarea real. Que un contenedor esté ejecutándose no demuestra que el sitio ni los workers funcionen.

## Seguridad y red

Desarrollo publicará únicamente frontend en 127.0.0.1:8080, si está libre. DB y Redis permanecerán en red interna. Producción privada requiere definir acceso VPN/red privada y TLS.

Secretos por archivos ignorados fuera del control de versiones; sin credenciales en Dockerfile, argumentos de build o logs. Sustituir el fallback DB_PASSWORD=123 de la referencia oficial. No montar docker.sock para programar backups.

## Datos y recuperación

Volúmenes nombrados para DB, sitios, cola y, si se requiere, logs. El entrypoint oficial enlaza sites/assets con los assets de imagen: todos los servicios deben coincidir en versión.

Respaldo integral: DB, archivos públicos/privados, configuración y claves necesarias para recuperar datos cifrados. El CLI fijado declara `backup --with-files` en frappe/commands/site.py. La receta backup-cron oficial no activa esa opción; no se adoptará como prueba de respaldo completo.

Objetivo inicial a confirmar con negocio: backup diario, retención de 7 diarios y 4 semanales, copia cifrada fuera del volumen y prueba de restauración en sitio aislado. RPO/RTO pendientes de acuerdo; proponer RPO 24 h y RTO 4 h, sujetos a prueba y necesidades reales. Un backup sin restauración ensayada no acredita recuperación.

## CI/CD futuro

Validar gitlinks y manifiesto; resolver dependencias bloqueadas; construir una vez; generar SBOM y escanear imagen/dependencias/secretos; probar instalación, permisos y migración; promover el mismo digest tras aprobación. No se crean workflows durante fase 0.
