# Aplicaciones independientes

`apps/metalmecanica/` contiene la aplicación autónoma del ERP metalmecánico. Su servicio FastAPI se ejecuta en un stack propio y no depende de Frappe ni ERPNext.

```text
apps/
├── README.md
└── metalmecanica/
    ├── README.md
    ├── pyproject.toml
    └── metalmecanica_ind/
        ├── __init__.py
        ├── api.py
        ├── hooks.py
        ├── modules.txt
        └── tests/
            ├── __init__.py
            └── test_api.py
```

La app usa el nombre técnico `metalmecanica_ind` para mantener separado su paquete autónomo de cualquier código Frappe histórico.
