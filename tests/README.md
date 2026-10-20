# Pruebas técnicas

```text
tests/
├── phase1_http.py                 # Login, permisos, CSRF, assets y Engine.IO
├── phase1_recovery.py              # Fixtures público/privado y password cifrada
└── phase1_scheduler.py             # Dos ciclos reales del scheduler y logs
```

Son pruebas de infraestructura y del contrato mínimo de la app. Las pruebas unitarias Frappe viven junto a la app en `apps/metalmecanica/metalmecanica/tests/`. Las pruebas de negocio y aceptación se añadirán cuando exista el modelo funcional.
