# iStore AI Agent (Novva)

Backend de un asistente de ventas por WhatsApp, multi-tenant: cada comercio
("tienda") tiene su propia instancia de WhatsApp, catálogo, configuración y
plan de suscripción. Un mismo servicio atiende a todos los comercios.

## Stack

- **FastAPI** — servidor HTTP (`src/server.py`), recibe los webhooks de WhatsApp
  y expone la API que usa el panel de administración (`istore-admin`).
- **Google Gemini** (`src/agent.py`) — agente conversacional con function
  calling: consulta stock, agenda turnos, deriva a un humano, etc. El prompt
  se arma dinámicamente por comercio según su configuración en `configuracion_comercios`.
- **Evolution API** — gateway self-hosted que conecta con WhatsApp (Baileys).
  El servidor le manda mensajes y recibe sus webhooks en `/webhook`.
- **Supabase (Postgres)** — base de datos: comercios, inventario, turnos,
  historial de chat, plantillas de post-venta, etc.
- **Redis (Upstash)** — buffer de mensajes entrantes (debounce), caché de
  comercio y barreras anti-duplicados (mensajes y pagos).
- **Upstash QStash** — agenda recordatorios de turnos y mensajes de
  post-venta para dispararse en el futuro exacto (llama de vuelta a
  `/api/webhooks/disparar-mensaje-programado`).
- **MercadoPago** — cobro de los planes de suscripción del SaaS.

## Setup local

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # completar con las credenciales reales (pedírselas a Facu)
uvicorn server:app --reload --app-dir src
```

Todas las variables de entorno que necesita el proyecto están documentadas
en `.env.example` y centralizadas en `src/config.py` — si falta alguna
obligatoria, el proceso corta al arrancar con un mensaje claro en vez de
fallar más adelante de forma confusa.

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

No necesitan `.env` ni credenciales reales — `tests/conftest.py` fuerza
variables dummy antes de importar cualquier módulo, y los tests reemplazan
`supabase` por un doble de prueba (`tests/fake_supabase.py`) que no pega a
la red. Cubren específicamente la lógica más delicada: la reserva atómica
de stock (`tests/test_stock.py`) y la verificación de que un comercio no
pueda tocar los datos de otro (`tests/test_auth.py`). No son exhaustivos —
si agregás lógica nueva de negocio o de permisos, sumale su test.

## Scripts sueltos (no forman parte del servidor)

- `src/conectar.py` / `src/conectar_qr.py`: crean una instancia nueva en
  Evolution API y la vinculan a un número de WhatsApp (por código o QR).
  Antes de correrlos, editar `INSTANCE_NAME` con el nombre de instancia que
  corresponda.
- `src/sincronizar.py`: sincroniza los contactos de una instancia de
  WhatsApp hacia la tabla `contactos` de Supabase.
- `src/cron_notificaciones.py`: se importa desde `server.py` para procesar
  la cola de mensajes de post-venta.
- `src/main.py`: CLI para probar el agente por consola sin pasar por WhatsApp.

## Seguridad

- Nunca hardcodear credenciales en el código — todo sale de `src/config.py`,
  que a su vez lee variables de entorno.
- `.env` y `evolution.env` están en `.gitignore` y no deben subirse nunca.
- Si alguna credencial llega a subirse al repo por error, rotarla de
  inmediato (no alcanza con borrarla del código, hay que invalidarla en el
  proveedor) y recién después limpiar el repo.
