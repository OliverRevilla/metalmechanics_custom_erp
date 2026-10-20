# Documentación

```text
docs/
├── DEVOPS.md                      # Procedimiento de cambios documentales
├── README.md                      # Este mapa
├── adr/                           # Decisiones de arquitectura numeradas
│   ├── 0001-modular-monolith.md
│   ├── 0002-source-provenance.md
│   ├── 0003-version-selection.md
│   └── 0004-local-build-and-development.md
├── architecture/                  # Diseño técnico y evidencia operativa
│   ├── system-overview.md
│   ├── dependencies.md
│   ├── docker-services.md
│   ├── development.md
│   ├── sources.lock.json
│   ├── environment-lock.json
│   ├── phase-0-validation.md
│   ├── phase-1-plan.md
│   ├── phase-1-validation.md
│   ├── security-and-qa.md
│   └── security-findings.md
└── business/                      # Alcance y modelo funcional
    ├── mvp-scope.md
    └── domain-model.md
```

Las validaciones registran hechos observados; los planes y ADR explican decisiones y límites. Los reportes generados de ejecución permanecen fuera de Git.
