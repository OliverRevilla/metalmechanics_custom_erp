# ADR 0001: Monolito modular sobre Frappe y ERPNext

Estado: requisito inicial confirmado; detalle de implementación propuesto.
Fecha: 2026-10-20.

## Contexto

Empresa ETO con menos de diez usuarios internos, procesos comerciales, operativos y financieros relacionados.

## Decisión

Un sitio Frappe con ERPNext y una aplicación propia metalmecanica. MariaDB y Redis; varios procesos en contenedores y un Compose principal. Sin microservicios, Kubernetes, Azure ni IA en el MVP.

## Consecuencias

Reutilizar maestros y documentos nativos. Aislar reglas propias en la app y usar hooks/extensiones. No editar upstream sin justificación y aprobación. Las actualizaciones requieren pruebas integradas de toda la aplicación.
