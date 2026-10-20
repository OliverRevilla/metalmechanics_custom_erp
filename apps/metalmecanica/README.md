# App `metalmecanica`

Aplicación propia de Frappe. Aquí vivirá la lógica y los DocTypes específicos de la empresa metalmecánica; ERPNext y Frappe permanecen en `vendor/` y no se editan desde esta app.

```text
apps/metalmecanica/
├── DEVOPS.md                     # Procedimiento de cambios de la app
├── README.md                     # Este mapa
├── pyproject.toml                # Metadatos y dependencias de la app
└── metalmecanica/
    ├── __init__.py               # Versión de la app
    ├── api.py                    # Métodos HTTP propios
    ├── hooks.py                  # Integración con el ciclo de Frappe
    ├── modules.txt               # Módulos Frappe instalables
    ├── patches.txt               # Patches versionados de la app
    ├── .frappe                   # Marcador de app Frappe
    ├── config/
    │   └── __init__.py
    ├── metalmecanica/
    │   └── __init__.py           # Paquete del módulo funcional inicial
    ├── patches/
    │   └── __init__.py
    ├── public/
    │   ├── .gitkeep
    │   ├── css/.gitkeep
    │   └── js/.gitkeep
    ├── templates/
    │   ├── __init__.py
    │   ├── includes/.gitkeep
    │   └── pages/__init__.py
    ├── tests/
    │   ├── __init__.py
    │   └── test_api.py
    └── www/.gitkeep
```

El marcador de desarrollo actual es `metalmecanica-dev-001`. Los nuevos DocTypes deben organizarse dentro de `metalmecanica/` en la estructura estándar de Frappe y acompañarse de pruebas en `tests/`.
