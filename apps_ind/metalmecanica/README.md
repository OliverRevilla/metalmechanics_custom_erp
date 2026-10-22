# `metalmecanica_ind`

Esta aplicación es el carril experimental para construir capacidades propias de forma gradual. El servicio autónomo de esta variante no importa ni invoca Frappe; ERPNext solo permanece disponible en el stack para comparar la interfaz existente detrás de Nginx.

```text
apps_ind/metalmecanica/
├── README.md
├── pyproject.toml
└── metalmecanica_ind/
    ├── __init__.py               # Versión de la app experimental
    ├── public/                   # CSS y JavaScript propios del candidato
    ├── service/                  # API FastAPI, persistencia y login propios
    └── tests/                    # Pruebas del servicio independiente
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

La pantalla propia está en `http://127.0.0.1:8081/ind/`. El setup crea un
usuario inicial en la base independiente y `/ind-api/*` expone la API FastAPI.
Sus tablas usan el prefijo `ind_` y no dependen de tablas de Frappe.

No compartas bases de datos, volúmenes ni bundles de backup entre las dos variantes. La imagen independiente reutiliza el núcleo de la imagen estable como base técnica; toda la personalización del candidato —backend, frontend y assets— se copia desde `apps_ind/` y puede evolucionar sin modificar `apps/metalmecanica`.


