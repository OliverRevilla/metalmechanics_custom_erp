# DevOps de `docs`

1. Identificar el cambio técnico o funcional y enlazarlo con un ADR si altera una decisión.
2. Actualizar la guía correspondiente en `architecture/` o `business/` junto con el código.
3. Registrar versiones, commits, digests y resultados observados; separar planes de evidencia ejecutada.
4. Revisar que las instrucciones no expongan credenciales ni prometan producción antes de completar hardening.
5. Verificar enlaces y estructura del repositorio en la rama `feature/*`.
6. Fusionar a `dev` mediante PR y promover a `main` solo cuando la documentación describa el estado que realmente se validó.
