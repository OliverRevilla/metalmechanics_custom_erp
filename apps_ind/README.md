# Aplicaciones independientes

`apps_ind/` contiene implementaciones experimentales que se comparan con la aplicación estable de `apps/` sin modificar su arranque ni sus volúmenes.

```text
apps_ind/
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

La app independiente usa el nombre técnico `metalmecanica_ind` para evitar colisiones con `metalmecanica`.
