# ADR 0003: Línea v16 y compatibilidad por metadatos

Estado: selección técnica de fase 0; pendiente validar ejecución.
Fecha: 2026-10-20.

## Contexto

Las ramas principales no garantizan compatibilidad entre proyectos.

## Decisión

Frappe v16.52.0 y ERPNext v16.51.1, en los SHA registrados. ERPNext exige Frappe >=16.50.0,<17.0.0; la selección lo satisface. Python 3.14 y Node 24 concuerdan con requisitos y builder oficial revisado. Fijar frappe_docker al snapshot registrado.

## Consecuencias

No asumir igualdad de números de versión. MariaDB 11.8 y Redis 8.6 son líneas propuestas sustentadas por referencias oficiales; falta probarlas juntas. No llamar al entorno completamente reproducible hasta cerrar dependencias, herramientas y digests. La instalación, migración y salud se validan en fase 1.
