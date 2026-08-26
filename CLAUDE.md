# VUELORADAR PERÚ — Contexto Maestro para Claude Code

> Este archivo es el contexto permanente del proyecto. Claude Code debe leerlo al inicio de cada sesión.

## 1. Qué es este proyecto

Sistema autónomo de monitoreo y búsqueda de vuelos domésticos en Perú. Encuentra el vuelo más barato para cualquier ruta nacional, acumula histórico de precios, detecta caídas de precio y envía alertas por Telegram. Incluye una capa de análisis con Claude API que interpreta el histórico y recomienda cuándo comprar.

**Usuario final:** viajeros peruanos vía bot de Telegram. Modelo futuro: freemium (búsqueda gratis, alertas y predicción premium).

## 2. Stack (NO cambiar sin justificación)

| Capa | Tecnología |
|---|---|
| Lenguaje | Python 3.11+ |
| Framework | Django 5.x (ORM + admin para inspeccionar datos) |
| Tareas async | Celery + Celery Beat |
| Broker/Cache | Redis (local en el servidor) |
| Base de datos | **Supabase** (PostgreSQL gestionado) — Django se conecta vía connection string del pooler (modo sesión, puerto 5432). NO usar Edge Functions ni Auth de Supabase por ahora; solo la DB |
| Bot | aiogram 3.x (asyncio) |
| Fuente de precios v1 | `fast-flights` **3.1.0** (Google Flights, sin API key). Se usa su query builder + fetcher; el parseo del payload es propio, ver `apps/scraping/providers/google_flights.py` |
| Fuente de precios v2 | Playwright (chromium headless) → `initial-sale.skyairline.com` y `booking.jetsmart.com`. Apagados por flag; NO entran al barrido masivo |
| IA | Router multi-proveedor con fallback (patrón JARVIS): Claude `claude-sonnet-4-6` → Groq → DeepSeek → Ollama (solo dev). Ver Fase 4 |
| IA (SDKs) | `anthropic` 1.x para Claude; `openai` 3.x con `base_url` custom para Groq, DeepSeek y Ollama (los tres son OpenAI-compatible) |
| Deploy | Docker Compose en VPS (`docker-compose.prod.yml`), 6 servicios. Ver `DEPLOY.md` |

## 3. Reglas de negocio del dominio

- **Mercado:** solo vuelos domésticos Perú. Aerolíneas relevantes: LATAM (LA), Sky Airline Perú (H2), JetSmart Perú (JZ), Star Perú (2I), ATSA.
- **Modelo hub-and-spoke:** casi todo pasa por Lima (LIM). Las rutas directas interprovinciales son excepciones (CUZ-AQP, CUZ-PEM, CUZ-JUL y pocas más). Si el usuario pide una ruta sin vuelo directo, el sistema debe proponer conexión vía LIM sumando ambos tramos.
- **Precios en PEN (S/).** Si la fuente devuelve USD, convertir con tipo de cambio del día (cachear 24h).
- **Precio total real:** siempre precio final con impuestos. Nunca tarifa base.
  **Verificado el 2026-08-23**: para JetSMART LIM-CUZ del 06/09, la web de la
  aerolínea muestra S/ 144,52 (tarifa base) y Google Flights S/ 201,00. La
  diferencia se descompone exacta: 144,52 x 1,18 (IGV) = 170,53, más S/ 30,47
  de TUUA nacional. **Google Flights ya incluye impuestos**, así que el precio
  que muestra el bot es el de venta. Los scrapers directos NO: Sky publica
  tarifa base y por eso su verificación viene apagada.
- **Aeropuertos monitoreados (IATA):** LIM, CUZ, AQP, PEM, IQT, TPP, PIU, TRU, CIX, JUL, AYP, PCL, CJA, TBP, HUU, JAU, ANS, CHM, TYL, RIM. Mantener en tabla `airports`, no hardcodear.
- **Rutas monitoreadas:** ~30 rutas activas definidas en tabla `routes` con flag `is_monitored`. Prioridad alta: LIM↔CUZ, LIM↔AQP, LIM↔PEM, LIM↔IQT, LIM↔TPP, LIM↔PIU, LIM↔TRU, LIM↔JUL, LIM↔JAU, CUZ↔PEM, CUZ↔AQP.

## 4. Arquitectura

```
      usuario                                            admin
         │                                                 │
         ▼                                                 ▼
  ┌─────────────┐                                  ┌──────────────┐
  │ Bot Telegram│                                  │ Django admin │
  │  (aiogram)  │                                  │  + /healthz  │
  └──────┬──────┘                                  └──────┬───────┘
         │                                                 │
         └──────────────┬──────────────────────────────────┘
                        ▼
              ┌───────────────────┐        ┌──────────────────┐
              │  Django (core)    │◀───────│   Celery Beat    │
              │  ORM + servicios  │        │ 06:00 / 18:00    │
              └─────────┬─────────┘        │ backup, salud    │
                        │                  └────────┬─────────┘
        ┌───────────────┼───────────────┐           │
        ▼               ▼               ▼           ▼
┌───────────────┐ ┌───────────┐ ┌─────────────┐ ┌──────────────────┐
│ Supabase (PG) │ │  Redis    │ │ Router IA   │ │ Celery Workers   │
│ precios       │ │ broker    │ │ Claude →    │ │ cola scraping    │
│ rutas         │ │ cache     │ │ Groq →      │ │ cola default     │
│ usuarios      │ │ locks     │ │ DeepSeek →  │ └────────┬─────────┘
│ alertas       │ │ semáforo  │ │ Ollama      │          │
└───────────────┘ └───────────┘ └─────────────┘          ▼
                                              ┌────────────────────┐
                                              │ Providers          │
                                              │ 1. fast-flights    │
                                              │    (Google, masivo)│
                                              │ 2. Playwright      │
                                              │    (Sky/JetSmart,  │
                                              │     por flag)      │
                                              └────────────────────┘
```

**Tres principios que sostienen el diseño:**

1. **Los scrapers son plugins intercambiables** detrás de `FlightProvider`
   (`search(origin, dest, date) -> list[RawFlightOffer]`). Si Google Flights
   cambia, solo se toca `apps/scraping/providers/google_flights.py`.
2. **La IA nunca bloquea.** Si toda la cadena de proveedores falla, el veredicto
   simplemente no aparece: la alerta se manda igual y la búsqueda responde
   igual. Ninguna función de negocio depende de que un LLM conteste.
3. **Nada se pierde en silencio.** Las tasks usan `acks_late`, los scrapers
   dejan screenshot al fallar, y un healthcheck cada 30 min avisa al admin si
   el histórico se congeló o una fuente quedó pausada.

## 5. Estructura del proyecto

```
vueloradar/
├── CLAUDE.md                 # este archivo
├── README.md                 # uso local
├── DEPLOY.md                 # despliegue en VPS + runbook de incidentes
├── Dockerfile                # multi-stage, usuario no-root, chromium
├── docker-compose.yml        # dev: solo redis
├── docker-compose.prod.yml   # prod: redis, web, 2 workers, beat, bot
├── manage.py
├── config/
│   ├── settings.py           # todo lee del .env
│   ├── settings_test.py      # sqlite en memoria + locmem, sin red
│   ├── celery.py             # broker redis, beat estático en settings
│   ├── health.py             # /healthz (SELECT 1 contra Supabase)
│   └── urls.py               # admin detrás de DJANGO_ADMIN_PATH
├── apps/
│   ├── flights/              # Airport, Route, FlightOffer, PriceSnapshot, RouteStats
│   │   ├── stats.py          # percentiles, promedio (lógica pura)
│   │   └── management/commands/  # load_airports, load_routes, search_flights, route_report
│   ├── scraping/
│   │   ├── providers/        # base, google_flights, playwright_base, sky, jetsmart, registry
│   │   ├── services.py       # search_and_store: dedupe, persistencia, conexión vía LIM
│   │   ├── tasks.py          # scan_route_date, scan_all_monitored, compute_route_stats, purge
│   │   ├── ratelimit.py      # lock por fuente, contador de fallos, pausa
│   │   ├── schedule.py       # granularidad del barrido (14 diarios + salteados a 60)
│   │   ├── verification.py   # contraste de precio contra la aerolínea
│   │   ├── maintenance.py    # backup_database, system_healthcheck
│   │   ├── fx.py             # USD→PEN cacheado 24h
│   │   └── notify.py         # avisos al admin por Telegram
│   ├── alerts/               # Alert, AlertTrigger + engine, services, notifications, digest
│   ├── users/                # TelegramUser + services (cupo diario)
│   └── ai_analyst/
│       ├── llm_router.py     # cadena con fallback + circuit breaker
│       ├── analyst.py        # veredicto comprar/esperar
│       ├── nl_parser.py      # lenguaje natural → intención de vuelo
│       ├── prompts.py        # prompts en español
│       ├── models.py         # AIUsageLog
│       └── usage.py          # contabilidad de consumo
├── bot/                      # aiogram
│   ├── main.py               # polling | webhook según BOT_MODE
│   ├── db.py                 # puente sync_to_async con el ORM
│   ├── search_flow.py        # flujo de búsqueda compartido
│   ├── formatting.py         # todos los mensajes al usuario
│   ├── throttle.py           # semáforo global de búsquedas
│   ├── heartbeat.py          # latido para el healthcheck de Docker
│   ├── handlers/             # commands, alerts, natural, admin
│   └── middlewares/          # logging, antiflood
├── scripts/                  # seeds: load_airports.py, load_routes.py
└── tests/                    # 227 tests, sin red ni Supabase
```

## 6. Convenciones de código

- Español para strings de usuario y docstrings de negocio; inglés para nombres de variables/funciones.
- Todo scraper devuelve `FlightOffer` (dataclass): `airline, flight_number, origin, destination, departure_dt, arrival_dt, stops, price_pen, price_currency_original, source, scraped_at, deep_link`.
- Manejo de errores en scrapers: nunca lanzar excepción al caller; devolver lista vacía + log estructurado + métrica de fallo en Redis.
- Tests con pytest para: normalización de ofertas, motor de alertas, cálculo de estadísticas de ruta.
- Migrations siempre versionadas; nunca `--fake`.
- Secrets solo en `.env` (TELEGRAM_TOKEN, ANTHROPIC_API_KEY, DATABASE_URL de Supabase).

## 6.1 Reglas Supabase

- Conexión Django: `DATABASE_URL` con el **Session Pooler** de Supabase (puerto 5432). NUNCA el Transaction Pooler (6543) — rompe prepared statements y migrations de Django.
- `CONN_MAX_AGE=60` en settings para reusar conexiones (el free tier de Supabase limita conexiones directas).
- Las migrations las maneja Django (`manage.py migrate`), NO el editor SQL de Supabase ni sus migrations propias. El dashboard de Supabase es solo para consultar/inspeccionar.
- RLS (Row Level Security): Supabase lo activa solo al crear tablas en `public`, **con cero políticas**. No hay que desactivarlo: Django conecta como `postgres` (dueño de las tablas) y `relforcerowsecurity=false`, así que el dueño lo atraviesa; para `anon`/`authenticated` el efecto es denegar todo, que es lo deseable hoy. Verificado en `pg_class` el 2026-08-20. La intención original era dejarlo deshabilitado en las tablas del proyecto — el único cliente es Django con la conexión directa. Si en el futuro se expone supabase-js a una web pública, ahí sí activar RLS con políticas de solo lectura sobre snapshots/rutas.
- En dev se puede usar Supabase directamente (mismo proyecto, schema `public`) o Postgres local en Docker — ambos válidos, la connection string decide.
- Si `DATABASE_URL` está vacío, settings cae a SQLite local (`db.sqlite3`). Es solo un andamio para trabajar sin conexión; el destino real es Supabase.
- La suite de tests usa `config/settings_test.py`: SQLite en memoria + cache local. Los tests nunca tocan Supabase, Redis ni la red.

## 7. Anti-bloqueo scraping (obligatorio)

| Regla | Dónde vive | Estado |
|---|---|---|
| Delays aleatorios 3-8s entre consultas de la misma fuente | `providers/google_flights.py` (`_SourceThrottle`) | implementado |
| 1 sola consulta concurrente por fuente | `scraping/ratelimit.py` (lock en Redis) | implementado |
| Headers realistas y anti-detección | `fast-flights` impersona Chrome; los directos usan viewport 1440x900, locale es-PE y `navigator.webdriver` oculto | implementado |
| Rotación de User-Agent | — | **NO implementado**: se usa un UA fijo pero realista. Si Google empieza a bloquear, es lo primero a agregar |
| 3 fallos consecutivos → pausa 30 min + aviso al admin | `scraping/tasks.py` + `ratelimit.py` | implementado |
| Reintentos con backoff exponencial | `TASK_KWARGS` en `scraping/tasks.py` | implementado |
| Barrido 2x/día, horizonte 60 días | `settings.CELERY_BEAT_SCHEDULE` + `scraping/schedule.py` | implementado |

Granularidad del barrido: todos los días para los próximos 14, cada 3 días
hasta el día 60. Son 30 fechas por ruta, 44 rutas = ~1.300 consultas por
corrida, unas 2 horas con un worker.

**No subas la concurrencia para "acelerar".** El límite es anti-bloqueo, no
performance: más paralelismo significa que Google bloquea la IP del VPS. Si
necesitás más frecuencia, barré las rutas de `priority=1` más seguido que el
resto (`scan_all_monitored(priority_max=1)`).

**Lección aprendida (2026-08-21):** el primer diseño contaba "cero ofertas" como
fallo de fuente. Las rutas chicas se barren juntas al final y encadenan vacíos
legítimos, así que pausaban la fuente y mataban medio barrido. Ver notas de la
Fase 2.

## 8. Fases del proyecto — TODAS COMPLETADAS

| Fase | Entregable | Cerrada |
|---|---|---|
| 1 | Django + modelos + scraper Google Flights por CLI | 2026-08-19 |
| 2 | Celery Beat + histórico + estadísticas por ruta | 2026-08-20 |
| 3 | Bot de Telegram con búsqueda on-demand y lenguaje natural | 2026-08-22 |
| 4 | Motor de alertas + veredicto de compra con router de IA | 2026-08-22 |
| 5 | Docker Compose, scrapers directos, observabilidad, DEPLOY.md | 2026-08-23 |

Los prompts originales de cada fase están en `~/Downloads/0X_FASEX_*.md`.

## 9. Estado actual

**El proyecto está funcionalmente completo.** Lo que falta no es código: es
correr el checklist pre-lanzamiento de `DEPLOY.md` en un VPS real.

### Qué corre hoy

| Pieza | Estado |
|---|---|
| Base de datos | Supabase `htqyzxzqlzjqzhkzemgo` (us-west-2), migrada y poblada |
| Barrido automático | Beat a las 06:00 y 18:00 hora Perú, ~1.300 consultas por corrida |
| Bot | [@Vuelosradar_bot](https://t.me/Vuelosradar_bot) (id 8695027914), modo polling |
| Tests | 227, en verde, sin tocar red ni Supabase |

Datos acumulados al 2026-08-23: 20 aeropuertos, 44 rutas monitoreadas, 8.289
snapshots, 47.395 ofertas crudas, 40 rutas con estadísticas de 30 días.

### Comandos de uso diario

```bash
python manage.py search_flights LIM CUZ 2026-09-15   # búsqueda puntual
python manage.py route_report LIM CUZ                # histórico de una ruta
python manage.py runbot                              # bot en polling
celery -A config worker -Q scraping,default -c 2 --pool=solo   # worker (Windows)
celery -A config beat                                # scheduler
```

### Lo que NO está verificado

- **El stack de Docker nunca corrió.** `docker compose config` valida y
  `check --deploy` pasa limpio, pero nadie levantó los seis servicios en un
  VPS. El primer `build` tarda 10-15 min por Chromium.
- **Ningún backup fue restaurado.** El `pg_dump` diario está programado, pero
  un backup que nunca se restauró no es un backup (ver `DEPLOY.md` §7).
- **JetSmart está a medias.** URL verificada, extracción no. Ver notas de Fase 5.

### Deuda técnica conocida

- **Sky publica tarifa base, sin impuestos.** Por eso
  `VERIFY_DEALS_WITH_DIRECT_SCRAPER=False`. Resolver esto es lo que habilitaría
  la verificación de precios antes de alertar.
- **4 rutas sin datos** (CHM y RIM en ambos sentidos): esos aeropuertos no
  tienen servicio comercial regular. Es dato correcto, no un bug.
- **El histórico todavía es corto para las alertas `deal_detected`**, que
  exigen 20 muestras por ruta. Con dos barridos diarios eso se cumple solo.

### Próximos pasos sugeridos (fuera de las 5 fases)

1. Desplegar en el VPS y correr el checklist de `DEPLOY.md`.
2. Monetización: Yape/Plin manual → flag premium en el admin.
3. Rutas internacionales desde LIM (mismo motor, solo cambian los seeds).
4. Canal público de Telegram con las mejores ofertas del día.

### Alertas y granularidad del barrido (2026-08-26)

El barrido ralla el horizonte lejano de a 3 días, así que **una alerta sobre
una fecha fuera de esa grilla no se disparaba nunca**: nadie consultaba esa
fecha, así que no había snapshot que evaluar. Fallo silencioso, el peor tipo.

`scan_all_monitored` ahora suma al barrido las fechas de las alertas activas
(futuras y dentro del horizonte). Cuesta un puñado de consultas extra y hace
que la función sirva para lo que existe.

Caso real: alerta para LIM→PEM el 18/10 (día +53). La grilla pasaba por el 13,
16 y 19. Ahora el 18 entra explícitamente.

### Verificación del stack en Docker (2026-08-23)

Los seis servicios levantados y en `healthy`. Tres bugs que solo aparecieron
corriendo de verdad:

- **Healthcheck de los workers en forma `CMD` no expandía `$HOSTNAME`.** La
  forma exec no invoca shell, así que pingeaba a un nodo llamado literalmente
  `celery@$HOSTNAME` y siempre daba `unhealthy`. Corregido a `CMD-SHELL`.
- **El bot no podía escribir su latido**: el volumen nombrado se crea como root
  y el contenedor corre sin privilegios. El volumen además sobraba — el
  healthcheck corre *dentro* del contenedor. Eliminado.
- **El modelo de Groq del plan original (`llama-3.3-70b-versatile`) fue
  retirado.** Ahora `GROQ_MODEL` se configura por entorno; el default es
  `openai/gpt-oss-120b`. Verificado: con `ANTHROPIC_API_KEY` vacía, Groq
  responde el veredicto en español y `AIUsageLog` lo registra.

Recuperación ante caída, verificada: `docker kill` **no** dispara el reinicio
(Docker lo trata como parada manual) y `kill -9` a PID 1 desde adentro se
ignora (el kernel protege al PID 1 de su propio namespace). La prueba válida es
`kill -TERM 1`, que Celery sí maneja: el contenedor salió, se reinició solo
(`RestartCount: 1`) y volvió a `healthy`.

### Notas de la Fase 5

- **`DJANGO_SECRET_KEY` no puede tener `$` ni `%`.** Docker Compose interpola los valores del `.env`, así que la clave llegaría mutilada al contenedor y distinta de la local: sesiones y firmas romperían en silencio. Detectado por el warning `"c" variable is not set` de `docker compose config`. La clave se regeneró con un charset seguro.
- **El precio de Sky en el listado es TARIFA BASE, sin impuestos** ("+ Tasas e impuestos" en la tarjeta). Choca con la regla del dominio de usar siempre precio final, así que Sky **no sirve para comparar contra Google en términos absolutos**. Por eso `VERIFY_DEALS_WITH_DIRECT_SCRAPER` viene en False.
- **La URL de Sky que sirve es la del motor de venta**, `initial-sale.skyairline.com/es/peru?origin=..&destination=..&departureDate=..&flightType=OW&ADT=1`. El buscador público es una SPA sin URL de resultados. Verificado en vivo el 2026-08-23: 7 vuelos LIM-CUZ con horarios y precios en soles.
- **JetSmart está a medias.** La URL (`booking.jetsmart.com/Flight/InternalSelect`) está verificada, pero el motor está detrás de un challenge anti-bot y aterriza en un **calendario de precios**, no en la lista de vuelos: devuelve el precio del día sin horarios ni número de vuelo. A favor, sus precios **sí incluyen impuestos**. Necesita verificación en vivo antes de habilitarlo.
- **Los scrapers directos nunca entran al barrido masivo.** `get_active_providers()` devuelve solo Google Flights; los directos salen por `get_providers_for_route()` y únicamente en rutas con `use_direct_scrapers=True`. Un Chromium por consulta x 1.300 consultas sería inviable.
- **El healthcheck del bot mira el mtime de un archivo de latido, no el PID.** El proceso puede seguir vivo con el polling colgado; eso es justamente lo que hay que detectar.
- **`check --deploy` pasa limpio con `DEBUG=False`**: HSTS, cookies seguras, redirect a HTTPS y `X-Frame-Options` se activan solo en producción para no romper el desarrollo local sin certificado.
- **No verificado**: el `docker compose up` completo en un VPS. La imagen y el compose validan sintácticamente y `check --deploy` pasa, pero nadie corrió el stack de seis servicios end-to-end.

### Notas de la Fase 4

- **El router se arma en cada llamada** (`build_chain()`), no como lista de módulo. Una lista construida al importar congela las settings y los tests que cambian keys con `override_settings` no tendrían efecto.
- **El conteo de fallos del circuit breaker vive por proveedor**, con la misma mecánica que el lock de scraping: `cache.add` + `cache.incr` sobre Redis, locmem en tests.
- **Un JSON inutilizable cuenta como fallo del proveedor y cae al siguiente.** Los modelos de respaldo fallan más en formato que en contenido; `parse_json` intenta parsear crudo, sacar el contenido del fence y por último agarrar el primer bloque entre llaves.
- **El veredicto se pide DESPUÉS de mostrar los vuelos**, en una segunda edición del mensaje. Pedirlo antes agregaría varios segundos de espera por una línea que puede no llegar nunca.
- **`get_verdict` devuelve None con menos de `VERDICT_MIN_SAMPLES` muestras y ni siquiera llama a la IA.** Opinar sobre 5 snapshots sería inventar.
- **Un veredicto sin razón se descarta.** Un "COMPRA" pelado no le sirve a nadie y es la forma típica en que un modelo de respaldo devuelve algo sintácticamente válido pero vacío.
- **El veredicto se cachea por banda de S/ 10**: S/ 152 y S/ 158 comparten análisis. Sin eso, cada centavo de diferencia sería una llamada nueva.
- **`create_alert` reactiva en vez de duplicar.** Repetir el mismo comando no gasta cupo.
- **El `client.py` de la Fase 3 se eliminó**: el router lo reemplaza y `nl_parser` ya lo usa. El parser perdió los structured outputs de Anthropic (no todos los proveedores los soportan), por eso el formato JSON ahora se pide en el prompt y se valida con `parse_json`.
- **Verificado en vivo (2026-08-22)** contra datos reales de LIM-CUZ (151 muestras, promedio S/ 210,97, p25 S/ 201): S/ 180 devuelve COMPRA con confianza 82 y S/ 260 devuelve ESPERA con 62. Con todas las keys vacías el flujo sigue sin veredicto y sin excepción; con Anthropic caído responde el respaldo y `AIUsageLog` registra el desglose. Alerta `deal_detected` disparada de punta a punta con mensaje completo, y el segundo snapshot más barato frenado por el cooldown de 12h.

### Notas de la Fase 3

- **Bot en producción**: [@Vuelosradar_bot](https://t.me/Vuelosradar_bot) (id 8695027914). Se levanta con `manage.py runbot` (polling; webhook en Fase 5).
- **SDK de Anthropic subido de 0.45.0 a 1.0.0.** El 0.45 no tenía `output_config` ni `messages.parse`, así que no soportaba structured outputs. Con 1.0.0 el `nl_parser` pide un `json_schema` y la respuesta viene validada: no hay que limpiar backticks ni reintentar por JSON malformado. No hubo migración que hacer porque no había código Anthropic previo.
- **`thinking` apagado en el nl_parser.** Es una extracción mecánica y hay un usuario esperando en Telegram; cada token de razonamiento es latencia pura. `max_tokens=300`.
- **La tabla ciudad→IATA se arma desde la base en cada llamada**, no está en el prompt. Agregar un aeropuerto a `airports` basta para que el parser lo entienda.
- **El cache del parser lleva la fecha en la clave.** "mañana" no significa lo mismo hoy que ayer; sin eso, el cache de 1h devolvería fechas viejas al cruzar medianoche.
- **Todo acceso al ORM pasa por `bot/db.py`** envuelto en `sync_to_async`. El scraping además va a un `ThreadPoolExecutor` propio: bloquea segundos y no debe consumir los hilos que Django reserva.
- **El cupo se descuenta después de ejecutar la búsqueda**, no antes. Si el scraper falla o la ruta no existe, el usuario no pierde una de sus 10.
- **Middleware anti-flood por usuario**: una búsqueda concurrente por persona. Sin eso, cinco mensajes seguidos disparan cinco scrapings que hacen cola contra el lock de la fuente sin que el usuario gane nada.
- **La línea de contexto de precio (📊) se omite entera si hay menos de 10 muestras.** Un promedio con 3 snapshots no es histórico, es ruido, y decirle al usuario "está 27% bajo el promedio" con esa base sería mentirle.

### Notas de la Fase 2

- **Celery en Windows**: el pool prefork por defecto no funciona. El worker se lanza con `--pool=solo`. En el VPS Linux (Fase 5) esto no hace falta.
- **Beat schedule estático** en `settings.CELERY_BEAT_SCHEDULE`, no `django-celery-beat`. Una dependencia menos y el schedule queda versionado en el repo. Si algún día hace falta editarlo desde el admin, ahí sí conviene la tabla.
- **Volumen real del barrido**: 44 rutas monitoreadas x 30 fechas = 1.320 consultas por corrida, no las ~840 estimadas en el prompt de fase (la estimación asumía ~30 rutas). A 3-8s cada una son varias horas por barrido con un worker. Es aceptable con 2 corridas al día, pero si se agregan rutas conviene barrer las de `priority=1` más seguido que el resto en vez de subir la concurrencia.
- **Cero ofertas NO cuenta como fallo de fuente** (corregido 2026-08-21). El primer diseño sí lo contaba y se rompió en el primer barrido real: las rutas chicas (RIM, CHM, ANS, TYL, HUU) se barren juntas al final por orden de prioridad y encadenan vacíos legítimos, lo que pausaba la fuente y mataba la mitad del barrido. Ahora el contador de fallos vive en el **provider**, que es el único que distingue "Google falló" (excepción) de "no hay vuelos ese día" (lista vacía).
- **Con la fuente pausada, las tasks se reencolan, no se saltean** (corregido 2026-08-21). Saltear es instantáneo: la cola entera se vaciaba en segundos mientras la pausa duraba 30 min, así que una sola pausa destruía todo el barrido restante. Ahora `scan_route_date` hace `retry` con countdown mayor a la pausa; si se agotan los reintentos, abandona esa consulta con un log de error y sin traceback.
- **El lock y el contador de fallos usan el cache de Django**, no un cliente Redis directo. Así los tests corren con locmem sin fakeredis, y en producción son las mismas operaciones atómicas de Redis (`SET NX` vía `cache.add`, `INCR` vía `cache.incr`).
- **`purge_old_offers` solo borra `FlightOffer`.** Los `PriceSnapshot` no se purgan nunca: pesan poco y son el activo del negocio.
- **Primer barrido real (2026-08-20)**: 661 snapshots en 1h27, pero solo de las 22 rutas `priority=1`; las 22 de prioridad 2 y 3 quedaron en cero por el bug de la pausa descrito arriba. Verificado que esas rutas sí tienen vuelos (LIM-CIX 8 ofertas, LIM-PCL 3, LIM-ANS 1 para la misma fecha). Hay que rebarrerlas con el código corregido.
- **Verificado en vivo (2026-08-20)**: worker + beat levantados contra Redis, 3 tasks reales ejecutadas por el worker (LIM→CUZ 34 ofertas / S/ 202, LIM→AQP 15 / S/ 233, CUZ→LIM 33 / S/ 183), `compute_route_stats` corrida, y `flights_pricesnapshot` + `flights_routestats` confirmadas en Supabase. **No se corrió el barrido completo**: son horas de scraping.

### Notas de la Fase 1

- **`fast-flights` 2.x quedó obsoleto**: su parser lee clases CSS de Google que ya cambiaron y devuelve aerolínea/horarios/escalas vacíos. Se subió a **3.1.0**, que lee el payload JS embebido (`<script class="ds:1">`). Se usa su `create_query` + `fetch_flights_html`, pero el parseo es propio porque su `parse()` revienta con `IndexError` en tarifas sin precio y su modelo descarta el número de vuelo. Si Google cambia los índices del payload, el único archivo a tocar es `apps/scraping/providers/google_flights.py` (los índices están documentados ahí).
- **Google ya devuelve itinerarios con escala** para muchas rutas sin directo (p. ej. AQP→PEM), así que el combinador propio vía LIM actúa solo cuando Google no devuelve nada (verificado en vivo con HUU→PEM). Las ofertas sintéticas vuelven como instancias sin guardar (`offer.pk is None`); los tramos reales sí se persisten.
- **Supabase verificado (2026-08-20)**: `migrate` + seeds corridos contra el Session Pooler (`aws-0-us-west-2.pooler.supabase.com:5432`, usuario `postgres.htqyzxzqlzjqzhkzemgo`). Confirmado vía la API de Supabase: `flights_airport` 20 filas, `flights_route` 44, `flights_flightoffer` poblándose con búsquedas reales. El `db.sqlite3` local queda obsoleto.
- **Rate limit**: el espaciado 3–8s entre consultas está implementado en proceso (`_SourceThrottle`). El lock en Redis para 1 worker concurrente por fuente entra con Celery en Fase 2.
