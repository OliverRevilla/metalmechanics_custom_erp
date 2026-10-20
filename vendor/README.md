# Fuentes upstream vendorizadas

`vendor/` contiene submódulos Git externos. Cada uno conserva su propio árbol y licencia; no se deben crear archivos locales dentro de ellos porque cualquier cambio quedaría fuera del commit del repositorio principal.

```text
vendor/
├── DEVOPS.md                      # Reglas para actualizar submódulos
├── README.md                      # Este mapa
├── frappe/                        # Framework Frappe, commit fijado
├── erpnext/                       # ERP base, commit fijado
└── frappe_docker/                 # Referencia upstream de imágenes y despliegue
```

Los commits exactos están en `.gitmodules`, en los gitlinks y en [docs/architecture/sources.lock.json](../docs/architecture/sources.lock.json). Para reconstruirlos usa `git submodule update --init --recursive`; no uses `--remote` durante una construcción bloqueada.

## Estructura de cada repositorio upstream

Cada checkout mantiene su estructura completa propia; el repositorio principal solo versiona el gitlink. Estos son sus directorios de primer nivel en los commits actuales:

```text
vendor/frappe/
├── frappe/  ├── realtime/  ├── ui/  ├── cypress/  ├── esbuild/
├── playwright/  └── .github/

vendor/erpnext/
├── erpnext/  ├── banking/  ├── semgrep/  └── .github/

vendor/frappe_docker/
├── resources/  ├── images/  ├── overrides/  ├── development/
├── docs/  ├── tests/  ├── .github/  ├── compose.yaml  └── docker-bake.hcl
```

El detalle archivo por archivo de esos tres árboles pertenece a sus repositorios upstream y puede cambiar al actualizar sus commits; no se duplica dentro de este README.
