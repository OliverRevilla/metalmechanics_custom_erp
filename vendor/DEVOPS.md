# DevOps de `vendor`

`vendor/` contiene submódulos upstream y se trata como solo lectura durante el desarrollo normal.

1. Registrar la versión deseada y el motivo en `docs/architecture/sources.lock.json` y, si aplica, en un ADR.
2. Obtener la referencia upstream en una rama de trabajo separada y verificar licencia, compatibilidad y cambios.
3. Actualizar el gitlink desde la raíz con `git add vendor/<submódulo>`; no crear archivos dentro del checkout del submódulo.
4. Reconstruir la imagen, ejecutar pruebas y revisar migraciones antes de fusionar a `dev`.
5. Promover a `main` solo cuando los locks, documentación y evidencia estén alineados.

Los submódulos `frappe`, `erpnext` y `frappe_docker` no reciben `DEVOPS.md` internos porque sus árboles pertenecen a repositorios externos.
