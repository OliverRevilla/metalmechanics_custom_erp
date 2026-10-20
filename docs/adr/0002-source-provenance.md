# ADR 0002: Fuentes oficiales como submódulos fijados

Estado: estrategia aprobada por el propietario; aplicada en fase 0.
Fecha: 2026-10-20.

## Contexto

Se exige código completo controlado, procedencia verificable y evitar copias inconsistentes.

## Decisión

Submódulos en vendor/frappe, vendor/erpnext y vendor/frappe_docker, fijados a SHA. App propia dentro de apps/metalmecanica en el repositorio principal. Documentar upstream, tag, SHA y licencias en sources.lock.json.

## Alternativas evaluadas

Snapshots facilitan lectura sin submódulos, pero complican atribución y actualizaciones. Mirrors privados añaden mantenimiento y requieren autorización adicional del remoto; no se crearán ahora.

## Consecuencias

Los colaboradores deben inicializar submódulos. No seguir ramas remotas en builds. Copiar fuentes a la imagen no crea una segunda fuente canónica. Mantener licencias y atribuciones; revisar obligaciones antes de distribuir.
