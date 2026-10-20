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

Las fuentes externas son submódulos en `vendor/`. La aplicación propia se creará en `apps/metalmecanica/` durante la fase 1 autorizada. El Dockerfile y Compose propios también corresponden a esa fase.

Al preparar otra copia autorizada del proyecto, usar `git submodule update --init --recursive` para recuperar los commits registrados. Evitar `git submodule update --remote` en construcciones. Las actualizaciones deben revisar los cambios y actualizar los punteros junto con el registro de fuentes.

Para iniciar el entorno local, usa `.\deployment\erp.ps1 -Action Start`. La interfaz queda en [http://127.0.0.1:8080](http://127.0.0.1:8080). La contraseña inicial de `Administrator` se conserva únicamente en `deployment/.secrets/admin_password`.

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

```text
metalmechanics_custom_erp/
├── apps/                         # Aplicaciones propias del ERP
│   └── metalmecanica/            # App Frappe de negocio y pruebas
├── deployment/                   # Imagen, Compose, locks y operaciones locales
│   ├── config/                   # Overrides y variables de entorno de ejemplo
│   ├── lock-inputs/              # Entradas declarativas de resolución
│   ├── locks/                    # Dependencias e imágenes fijadas
│   └── scripts/                  # Bootstrap, backup, restore y health checks
├── docs/                         # Arquitectura, negocio, ADR y validaciones
│   ├── adr/
│   ├── architecture/
│   └── business/
├── tests/                        # Pruebas HTTP, scheduler y recuperación
├── vendor/                       # Submódulos upstream fijados por commit
│   ├── frappe/
│   ├── erpnext/
│   └── frappe_docker/
├── .dockerignore                 # Exclusiones de la construcción
├── .gitattributes                # Normalización de texto
├── .gitignore                    # Secretos, reportes y datos locales
├── .gitmodules                   # Definición de submódulos Git
└── README.md                     # Mapa y guía del repositorio
```

Cada área tiene su propio mapa detallado: [apps/metalmecanica/README.md](apps/metalmecanica/README.md), [deployment/README.md](deployment/README.md), [docs/README.md](docs/README.md), [tests/README.md](tests/README.md) y [vendor/README.md](vendor/README.md).
