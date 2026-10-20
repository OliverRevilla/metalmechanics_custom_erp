# DevOps de `tests`

1. Elegir el nivel de prueba: contrato HTTP, recuperación, scheduler o prueba de app.
2. Mantener pruebas deterministas y sin secretos en el repositorio; leer credenciales desde archivos efímeros del entorno.
3. Ejecutar las pruebas técnicas contra un sitio local aislado y guardar solo evidencia sanitizada en `deployment/reports/`.
4. Para cambios de app ejecutar además `bench --site <sitio> run-tests --app metalmecanica`.
5. Ejecutar `Verify` antes de abrir el PR a `dev`; el workflow de `main` debe repetir las comprobaciones de empaquetado.
6. Si una prueba falla por un cambio intencional, actualizarla junto con la documentación y el PR, nunca ocultar el fallo.
