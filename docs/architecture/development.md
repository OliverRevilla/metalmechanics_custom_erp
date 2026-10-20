# Entorno de desarrollo: fase 1

Workspace autorizado: `C:\Users\olive\Proyectos\metalmechanics_custom_erp`.

## Gestión

Ejecutar desde PowerShell 7, con Docker Desktop en contenedores Linux:

```powershell
.\deployment\erp.ps1 -Action Init
.\deployment\erp.ps1 -Action Resolve
.\deployment\erp.ps1 -Action Build
.\deployment\erp.ps1 -Action Start
.\deployment\erp.ps1 -Action Status
.\deployment\erp.ps1 -Action Verify
```

Resolve vuelve a seleccionar transitivas dentro de los intervalos permitidos: usarlo únicamente al actualizar los locks de forma revisada. Build consume los locks existentes y verifica los commits y la limpieza de upstream. No ejecutar Resolve como paso automático en cada construcción.

Dev usa el Compose principal y el overlay `deployment/config/compose.dev.yml`, que monta la aplicación propia. `-Mode Image` utiliza solo el principal, con código incorporado en imagen. La instalación local todavía no constituye un despliegue de producción.

Acceso local previsto: [ERP](http://metalmecanica.localhost:8080). Si la resolución de *.localhost no está disponible, el frontend también selecciona el sitio fijado al acceder a [127.0.0.1:8080](http://127.0.0.1:8080).

Usuario inicial: Administrator. La contraseña está en `deployment/.secrets/admin_password`; leerla localmente para iniciar sesión. No incluirla en Git, capturas ni reportes. Los servicios ordinarios no reciben este secreto como archivo Docker.

## Recursos

Proyecto Compose `metalmecanica-dev`; imagen `metalmecanica-erp:dev`; red `metalmecanica-dev_default`.

Volúmenes nombrados: `metalmecanica-dev_db-data`, `metalmecanica-dev_sites`, `metalmecanica-dev_redis-queue-data`, `metalmecanica-dev_logs`. Redis cache usa tmpfs. No se montan código ni datos del proyecto de OneDrive.

Solo frontend publica `127.0.0.1:8080`. No se publican puertos de MariaDB, Redis, backend ni websocket.

## Cambios propios

Editar `apps/metalmecanica/`. Para cambios Python:

```powershell
.\deployment\erp.ps1 -Action Reload
```

GET autenticado `/api/method/metalmecanica.api.get_build_info` devuelve versión y marcador de desarrollo. Un marcador distinto después de editar y reiniciar acredita que se está usando el código montado. Revertir el marcador tras la comprobación.

Los cambios de JS/CSS que afecten bundles requieren reconstruir assets/imagen. El modo Dev no afirma recarga automática de todos los lenguajes.

## Respaldo y restauración

```powershell
.\deployment\erp.ps1 -Action Backup
.\deployment\erp.ps1 -Action Restore -BackupBundle <carpeta-del-bundle> -RestoreSite <sitio-nuevo.localhost>
```

Los bundles se guardan en `deployment/backups/`, ignorados por Git. Contienen DB, archivos públicos/privados, configuración sensible y manifest de SHA256. Tener hashes verifica integridad, no cifra por sí mismo esos archivos. La copia fuera del equipo y la retención programada quedan pendientes de configurar antes de producción.

Restore exige un sitio nuevo y comprueba que la DB no coincide con ninguna existente. Nunca usar el nombre del sitio principal. El destino queda con scheduler pausado y correos silenciados; las credenciales de la DB origen no sustituyen las de destino. No copiar manualmente todo site_config.json de un sitio a otro.

`Stop` detiene procesos y conserva volúmenes. No hay comandos de eliminación de volúmenes ni reinstalación automática en el wrapper.

## Controles

- `deployment/locks/images.json`: bases por digest y snapshot Debian.
- `deployment/locks/tools.txt`: versiones y hashes de herramientas.
- `deployment/locks/runtime.txt`: transitivas Python fijadas y referencias Git exactas.
- Yarn usa los locks upstream congelados; ERPNext banking se instala explícitamente con su lock.
- Las herramientas Bench y las dependencias Frappe usan entornos Python separados.
- Los archivos de licencia upstream se incluyen en la imagen; no se han modificado los núcleos.
- El bootstrap rechaza sitios existentes sin su marcador de finalización, evitando reparación destructiva implícita.
- Los resultados de ejecución reales se registrarán en `phase-1-validation.md`.

## Límites de fase

No se han implementado reglas ETO ni el modelo de negocio. La configuración de empresa, usuarios ordinarios, roles concretos y requisitos contables se realizará con datos y criterios aprobados. No habilitar el servicio fuera de loopback como si ya tuviera una configuración de producción validada.
