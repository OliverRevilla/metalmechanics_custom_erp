# Despliegue local

Este módulo contiene la construcción reproducible de la imagen propia y la definición de servicios locales. No guarda credenciales: los secretos viven en `deployment/.secrets/`, ignorado por Git.

```text
deployment/
├── Dockerfile                    # Imagen multi-stage de Frappe/ERPNext/app propia
├── docker-compose.yml             # Stack principal y volúmenes nombrados
├── erp.ps1                        # Init, build, start, verify, backup y restore
├── config/
│   ├── compose.dev.yml            # Override de desarrollo con source mount
│   └── env.example                # Variables sin secretos
├── lock-inputs/
│   ├── runtime.in                 # Entradas de dependencias runtime
│   └── tools.in                   # Entradas de herramientas
├── locks/
│   ├── images.json                # Tags y digests de imágenes base/servicios
│   ├── runtime.txt                # Lock runtime compilado y refs Git
│   └── tools.txt                  # Lock de herramientas compilado
├── scripts/
│   ├── health.py                  # Probes TCP, HTTP, scheduler y workers
│   └── manage.py                  # Configure, bootstrap, backup y restore
├── .env                           # Configuración local ignorada
├── .secrets/                      # Passwords locales ignorados
├── backups/                       # Bundles locales ignorados
└── reports/                       # Evidencia local ignorada
```

Las operaciones normales se ejecutan desde la raíz con `.\deployment\erp.ps1 -Action <Init|Build|Start|Status|Verify|Backup|Restore>`.
