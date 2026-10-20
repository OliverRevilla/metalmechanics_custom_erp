# `metalmecanica_ind`

Esta aplicación es el carril experimental para internalizar capacidades de Frappe/ERPNext de forma gradual. No se instala en el sitio estable `metalmecanica.localhost`.

```text
apps_ind/metalmecanica/
├── README.md
├── pyproject.toml
└── metalmecanica_ind/
    ├── __init__.py               # Versión de la app experimental
    ├── api.py                    # Endpoint de identificación del candidato
    ├── hooks.py                  # Hooks aislados de la app experimental
    ├── modules.txt               # Módulos Frappe de esta app
    └── tests/
        ├── __init__.py
        └── test_api.py           # Pruebas del candidato
```

## Prueba local

El entorno estable conserva su flujo actual:

```powershell
.\deployment\erp.ps1 -Action Start
.\deployment\erp.ps1 -Action Verify
```

El entorno independiente usa otra imagen, otro proyecto Compose, otro sitio, otros volúmenes y el puerto `8081`:

```powershell
.\deployment\erp-independent.ps1 -Action Init
.\deployment\erp-independent.ps1 -Action Build
.\deployment\erp-independent.ps1 -Action Start
.\deployment\erp-independent.ps1 -Action Verify
```

URLs:

- Estable: `http://127.0.0.1:8080` (`metalmecanica.localhost`)
- Independiente: `http://127.0.0.1:8081` (`metalmecanica-ind.localhost`)

No compartas bases de datos, volúmenes ni bundles de backup entre las dos variantes. La imagen independiente reutiliza el núcleo de la imagen estable como base técnica; la app experimental se copia desde `apps_ind/` y puede evolucionar sin modificar `apps/metalmecanica`.


