# metalmechanics_custom_erp

ERP privado para una empresa metalmecánica peruana Engineer-to-Order (ETO), basado en Frappe y ERPNext, con una aplicación propia denominada `metalmecanica`.

## Estado

Fase 1 local completada el 20 de octubre de 2026: imagen propia construida, sitio `metalmecanica.localhost` instalado, servicios saludables y prueba de backup/restore validada en un sitio aislado. El alcance sigue siendo técnico; no se han creado datos de negocio.

Workspace autorizado: `C:\Users\olive\Proyectos\metalmechanics_custom_erp`. Se utiliza el repositorio existente en esta ruta. No se trasladó contenido desde OneDrive.

## Fuentes

| Componente | Referencia | Commit |
|---|---|---|
| Frappe | v16.52.0 | 4515014c87ece6cb20ee91d2cfb121c5cbd1ff4a |
| ERPNext | v16.51.1 | 5af6561f5aa48104559692fb4fd82f2580e8d338 |
| frappe_docker | snapshot del 2026-10-20 | df2a3b72910634cf0e878b59eccf68a93736bf9d |

Las fuentes del entorno estable siguen siendo submódulos en `vendor/`. La aplicación autónoma vive en `apps/metalmecanica/` y se ejecuta con su propio stack FastAPI/MySQL/Redis; no depende de la imagen estable ni de Frappe/ERPNext.

Al preparar otra copia autorizada del proyecto, usar `git submodule update --init --recursive` para recuperar los commits registrados. Evitar `git submodule update --remote` en construcciones. Las actualizaciones deben revisar los cambios y actualizar los punteros junto con el registro de fuentes.

Para iniciar el entorno estable, usa `.\deployment\erp.ps1 -Action Start`. La interfaz queda en [http://127.0.0.1:8080](http://127.0.0.1:8080). Para levantar el ERP autónomo, ejecuta `.\deployment\erp-independent.ps1 -Action Start`; queda en [http://127.0.0.1:8081](http://127.0.0.1:8081), con volúmenes propios y sin servicios Frappe. La guía está en [apps/metalmecanica/README.md](apps/metalmecanica/README.md).

## Documentación

- [Diseño del sistema](docs/architecture/system-overview.md)
- [Dependencias y compatibilidad](docs/architecture/dependencies.md)
- [Registro de fuentes](docs/architecture/sources.lock.json)
- [Servicios Docker propuestos](docs/architecture/docker-services.md)
- [Alcance del MVP](docs/business/mvp-scope.md)
- [Modelo de dominio](docs/business/domain-model.md)
- [Seguridad y QA](docs/architecture/security-and-qa.md)
- [Validación de fase 0](docs/architecture/phase-0-validation.md)
- [Plan y recursos de fase 1](docs/architecture/phase-1-plan.md)
- [Desarrollo y operación local](docs/architecture/development.md)
- [Validación de fase 1](docs/architecture/phase-1-validation.md)
- [Hallazgos de seguridad](docs/architecture/security-findings.md)
- [Bloqueo del entorno](docs/architecture/environment-lock.json)
- [Guía DevOps y flujo de ramas](DEVOPS.md)
- [Decisiones de arquitectura](docs/adr/)

La construcción está bloqueada para desarrollo local; la lista de pendientes de seguridad y las limitaciones de reproducibilidad están en la validación y en `security-findings.md`. No se considera aún lista para producción.

## Estructura completa del proyecto

El siguiente árbol enumera todos los archivos propios versionados. Los tres directorios de `vendor/` son gitlinks: sus miles de archivos pertenecen a repositorios upstream y se detallan por separado en `vendor/README.md`.

```text
metalmechanics_custom_erp/
├── .dockerignore
├── .gitattributes
├── .gitignore
├── .gitmodules
├── DEVOPS.md
├── README.md
├── .github/
│   └── workflows/
│       ├── dev.yml
│       └── main.yml
├── apps/
│   └── metalmecanica/
│       ├── DEVOPS.md
│       ├── README.md
│       ├── pyproject.toml
│       └── metalmecanica/
│           ├── __init__.py
│           ├── api.py
│           ├── hooks.py
│           ├── modules.txt
│           ├── patches.txt
│           ├── .frappe
│           ├── config/__init__.py
│           ├── metalmecanica/__init__.py
│           ├── patches/__init__.py
│           ├── public/.gitkeep
│           ├── public/css/.gitkeep
│           ├── public/js/.gitkeep
│           ├── templates/__init__.py
│           ├── templates/includes/.gitkeep
│           ├── templates/pages/__init__.py
│           ├── tests/__init__.py
│           ├── tests/test_api.py
│           └── www/.gitkeep
├── deployment/
│   ├── DEVOPS.md
│   ├── README.md
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── erp.ps1
│   ├── config/compose.dev.yml
│   ├── config/env.example
│   ├── lock-inputs/runtime.in
│   ├── lock-inputs/tools.in
│   ├── locks/images.json
│   ├── locks/runtime.txt
│   ├── locks/tools.txt
│   └── scripts/
│       ├── health.py
│       └── manage.py
├── docs/
│   ├── DEVOPS.md
│   ├── README.md
│   ├── adr/0001-modular-monolith.md
│   ├── adr/0002-source-provenance.md
│   ├── adr/0003-version-selection.md
│   ├── adr/0004-local-build-and-development.md
│   ├── architecture/dependencies.md
│   ├── architecture/development.md
│   ├── architecture/docker-services.md
│   ├── architecture/environment-lock.json
│   ├── architecture/phase-0-validation.md
│   ├── architecture/phase-1-plan.md
│   ├── architecture/phase-1-validation.md
│   ├── architecture/security-and-qa.md
│   ├── architecture/security-findings.md
│   ├── architecture/sources.lock.json
│   ├── architecture/system-overview.md
│   ├── business/domain-model.md
│   └── business/mvp-scope.md
├── tests/
│   ├── DEVOPS.md
│   ├── README.md
│   ├── phase1_http.py
│   ├── phase1_recovery.py
│   └── phase1_scheduler.py
└── vendor/
    ├── README.md
    ├── DEVOPS.md
    ├── frappe/              # gitlink al commit fijado
    ├── erpnext/             # gitlink al commit fijado
    └── frappe_docker/       # gitlink al commit fijado
```

Los directorios locales ignorados `deployment/.secrets/`, `deployment/backups/`, `deployment/reports/` y el archivo `deployment/.env` pueden aparecer durante la ejecución, pero no forman parte del repositorio versionado.

Cada área tiene su propio mapa detallado: [apps/metalmecanica/README.md](apps/metalmecanica/README.md), [deployment/README.md](deployment/README.md), [docs/README.md](docs/README.md), [tests/README.md](tests/README.md) y [vendor/README.md](vendor/README.md).


## Vía experimental independiente

La variante autónoma vive en `apps/metalmecanica/` y se construye con `deployment/Dockerfile.independent`. `deployment/config/compose.independent.yml` levanta únicamente `api`, `mysql` y `redis` en el proyecto `metalmecanica-independent`, con puerto `8081` y volúmenes separados. No comparte datos ni imagen con el entorno estable.

