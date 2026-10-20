# DevOps de `deployment`

1. Revisar `Dockerfile`, Compose, locks y ADR antes de cambiar infraestructura.
2. Si cambia una entrada de dependencia, editar `lock-inputs/*.in` y ejecutar `.\deployment\erp.ps1 -Action Resolve`.
3. Revisar los archivos generados en `locks/`; conservar digests y commits, sin secretos.
4. Construir con `.\deployment\erp.ps1 -Action Build` y comprobar `uv pip check`.
5. Arrancar con `Start`, validar con `Status` y `Verify`, y revisar colas y scheduler.
6. Ejecutar backup y una restauración a un sitio nuevo cuando cambien persistencia, configuración o scripts de recuperación.
7. Abrir PR a `dev`; el workflow debe validar Compose e imagen. Promover a `main` después de revisión.

No se publican imágenes ni se modifican volúmenes en los workflows actuales. Los archivos `.env`, `.secrets`, `backups/` y `reports/` son locales.
