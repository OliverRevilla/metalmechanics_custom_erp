# Fuentes upstream vendorizadas

`vendor/` contiene submódulos Git externos. Cada uno conserva su propio árbol y licencia; no se deben crear archivos locales dentro de ellos porque cualquier cambio quedaría fuera del commit del repositorio principal.

```text
vendor/
├── frappe/                        # Framework Frappe, commit fijado
├── erpnext/                       # ERP base, commit fijado
└── frappe_docker/                 # Referencia upstream de imágenes y despliegue
```

Los commits exactos están en `.gitmodules`, en los gitlinks y en [docs/architecture/sources.lock.json](../docs/architecture/sources.lock.json). Para reconstruirlos usa `git submodule update --init --recursive`; no uses `--remote` durante una construcción bloqueada.
