# Guía DevOps del proyecto

Esta guía define cómo se cambia, valida y entrega el ERP desde el repositorio `C:\Users\olive\Proyectos\metalmechanics_custom_erp`. El repositorio es un monolito modular: el código propio está en `apps/metalmecanica/`, la operación está en `deployment/`, y Frappe/ERPNext/`frappe_docker` se consumen como submódulos fijados.

## Ramas y flujo de trabajo

Se mantienen tres tipos de rama:

- `main`: estado revisado y candidato a despliegue. Solo recibe cambios mediante pull request desde `dev`.
- `dev`: integración continua del equipo. Recibe pull requests desde una rama `feature/*` y debe permanecer construible.
- `feature/<tema>`: trabajo aislado para una mejora, corrección o cambio de infraestructura. Se crea desde `dev` y se elimina después de fusionar.

El flujo normal es:

1. Actualizar el repositorio y submódulos sin mover el workspace: `git switch dev` y `git submodule update --init --recursive`.
2. Crear la rama: `git switch -c feature/<tema>`.
3. Cambiar únicamente el área correspondiente. No editar `vendor/frappe`, `vendor/erpnext` ni `vendor/frappe_docker`; una actualización de upstream se hace en un cambio separado y revisado.
4. Ejecutar `.\deployment\erp.ps1 -Action Init`, seguido de `.\deployment\erp.ps1 -Action Build`, `Start` y `Verify` cuando el cambio afecte a la imagen o al Compose.
5. Ejecutar las pruebas de la app y las pruebas técnicas de `tests/`. Revisar `git diff`, los locks y que no haya secretos.
6. Abrir un pull request de `feature/*` a `dev`. El workflow `dev.yml` debe terminar correctamente antes de fusionar.
7. Después de la validación funcional, abrir un pull request de `dev` a `main`. El workflow `main.yml` reconstruye la imagen y valida el paquete candidato.
8. Etiquetar la versión en `main` solo después de la revisión y registrar el cambio en la documentación de arquitectura.

No se hace push automático a un registro ni despliegue productivo: no existe todavía un registro, entorno ni secreto de producción aprobado. Los workflows producen una validación reproducible y una imagen local de CI; el paso de publicación se añadirá cuando se defina ese contrato.

## Cambios en la aplicación propia

1. Crear o modificar código en `apps/metalmecanica/` siguiendo la estructura Frappe.
2. Añadir una prueba junto al módulo afectado en `apps/metalmecanica/metalmecanica/tests/`.
3. Si se añade un DocType, comprobar permisos, fixtures, migración y compatibilidad con un sitio nuevo.
4. Para cambios Python en modo desarrollo usar `compose.dev.yml`; reiniciar los servicios afectados. Para JavaScript/CSS reconstruir los assets con `-Action Build`.
5. Ejecutar `bench --site <sitio> run-tests --app metalmecanica` dentro de la imagen y después `.\deployment\erp.ps1 -Action Verify`.
6. Documentar cualquier cambio de contrato en `docs/` y actualizar el marcador de build solo cuando corresponda.

## Cambios de despliegue y dependencias

1. Revisar primero `docs/architecture/development.md`, `sources.lock.json` y el ADR aplicable.
2. Para una actualización de Frappe/ERPNext o de una imagen, cambiar el gitlink/tag y el lock en un commit explícito; nunca ejecutar `git submodule update --remote` durante una construcción.
3. Para dependencias Python editar `deployment/lock-inputs/*.in`, resolver con `.\deployment\erp.ps1 -Action Resolve` y revisar el diff de `deployment/locks/`.
4. Construir la imagen con `.\deployment\erp.ps1 -Action Build`; la construcción debe pasar `uv pip check` y conservar los digests fijados.
5. Validar Compose, salud de servicios, colas, scheduler y backup/restore antes de fusionar.

## GitHub Actions

`.github/workflows/dev.yml` se ejecuta en pull requests hacia `dev` y en pushes a `dev`. Comprueba submódulos, construye la imagen amd64 con los locks versionados, ejecuta comprobaciones Python básicas y valida el Compose con secretos efímeros de CI.

`.github/workflows/main.yml` se ejecuta en pull requests hacia `main` y en pushes a `main`. Repite la construcción desde cero lógico, inspecciona etiquetas y revisiones upstream, valida Compose y deja un artefacto de metadatos de la imagen. No publica credenciales ni despliega.

Un workflow fallido bloquea la fusión. Los runners usan secretos efímeros; los secretos locales (`deployment/.secrets/`, `deployment/.env`) y los datos de backup están ignorados por Git.

## Operación local y recuperación

- Inicio: `.\deployment\erp.ps1 -Action Start`.
- Estado: `.\deployment\erp.ps1 -Action Status`.
- Verificación: `.\deployment\erp.ps1 -Action Verify`.
- Backup: `.\deployment\erp.ps1 -Action Backup`.
- Restauración: `.\deployment\erp.ps1 -Action Restore -BackupBundle <ruta> -RestoreSite <sitio-nuevo>`.
- Logs: `.\deployment\erp.ps1 -Action Logs`.

La restauración siempre usa un nombre de sitio nuevo. No se eliminan volúmenes como parte de una actualización normal. Cualquier operación de producción deberá tener runbook, ventana de cambio, backup verificable y aprobación separada.

## Criterios de aceptación

Un cambio está listo cuando la rama pasa el workflow correspondiente, el árbol no contiene secretos, los submódulos están en los commits declarados, la imagen se construye, el Compose es válido y las pruebas relacionadas dejan evidencia en la documentación o en el pull request.

Los detalles por área están en [apps/metalmecanica/DEVOPS.md](apps/metalmecanica/DEVOPS.md), [deployment/DEVOPS.md](deployment/DEVOPS.md), [docs/DEVOPS.md](docs/DEVOPS.md), [tests/DEVOPS.md](tests/DEVOPS.md) y [vendor/DEVOPS.md](vendor/DEVOPS.md).
