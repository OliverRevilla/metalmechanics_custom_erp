# DevOps de `metalmecanica`

1. Crear la rama desde `dev`: `git switch -c feature/<nombre>`.
2. Cambiar solo `apps/metalmecanica/`; no editar `vendor/`.
3. Para cada DocType, endpoint o tarea, añadir su prueba en `metalmecanica/tests/` y revisar permisos.
4. Ejecutar compilación sintáctica y las pruebas Frappe: `bench --site <sitio> run-tests --app metalmecanica`.
5. Ejecutar `.\deployment\erp.ps1 -Action Build` si cambia empaquetado, assets o dependencias; para Python puro reiniciar servicios en modo Dev.
6. Ejecutar `.\deployment\erp.ps1 -Action Verify` y documentar migraciones necesarias.
7. Abrir PR a `dev`; corregir el workflow antes de fusionar. Promover a `main` únicamente desde `dev`.

El marcador de build y la versión de `pyproject.toml` se cambian junto con una nota de release, no como sustituto de pruebas.
