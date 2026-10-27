# `metalmecanica_ind`

Esta aplicación contiene el ERP autónomo para la operación metalmecánica. El servicio no importa ni invoca Frappe o ERPNext; expone la interfaz web y la API directamente mediante FastAPI.

```text
apps/metalmecanica/
├── README.md
├── pyproject.toml
└── metalmecanica_ind/
    ├── __init__.py               # Versión de la app experimental
    ├── public/                   # CSS y JavaScript propios del candidato
    ├── service/                  # API FastAPI, persistencia y login propios
    └── tests/                    # Pruebas del servicio independiente
```

## Prueba local

El entorno autónomo usa una imagen, proyecto Compose y volúmenes propios, con el puerto `8081`:

```powershell
.\deployment\erp-independent.ps1 -Action Init
.\deployment\erp-independent.ps1 -Action Build
.\deployment\erp-independent.ps1 -Action Start
.\deployment\erp-independent.ps1 -Action Verify
```

URLs:

- ERP autónomo: `http://127.0.0.1:8081`

La pantalla web está en `http://127.0.0.1:8081/`. El setup crea un usuario
inicial en MySQL y la API usa las mismas rutas bajo el proceso FastAPI.
Sus tablas usan el prefijo `ind_`.

Los datos persistentes viven en volúmenes propios de MySQL, Redis y archivos de la aplicación. La imagen autónoma copia únicamente `apps/metalmecanica/` y sus dependencias FastAPI.
