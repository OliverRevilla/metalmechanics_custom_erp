# App `metalmecanica`

Aplicación propia de Frappe. Aquí vivirá la lógica y los DocTypes específicos de la empresa metalmecánica; ERPNext y Frappe permanecen en `vendor/` y no se editan desde esta app.

```text
apps/metalmecanica/
├── pyproject.toml                # Metadatos y dependencias de la app
└── metalmecanica/
    ├── __init__.py               # Versión de la app
    ├── api.py                    # Métodos HTTP propios, actualmente build info
    ├── hooks.py                  # Integración con el ciclo de Frappe
    ├── modules.txt               # Módulos Frappe instalables
    ├── patches.txt               # Patches versionados de la app
    ├── config/
    │   └── __init__.py           # Configuración de escritorio y módulos
    ├── metalmecanica/
    │   └── __init__.py           # Paquete del módulo funcional inicial
    ├── patches/
    │   └── __init__.py           # Namespace de patches
    ├── templates/
    │   ├── __init__.py
    │   └── pages/
    │       └── __init__.py       # Páginas web propias futuras
    └── tests/
        ├── __init__.py
        └── test_api.py           # Pruebas autenticadas y de permisos
```

El marcador de desarrollo actual es `metalmecanica-dev-001`. Los nuevos DocTypes deben organizarse dentro de `metalmecanica/` en la estructura estándar de Frappe y acompañarse de pruebas en `tests/`.
