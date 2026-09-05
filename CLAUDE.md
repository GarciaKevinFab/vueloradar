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
- **Precios en PEN (S/).** Si la fuente devuelve USD, convertir con tipo de cambio **en vivo**, cacheado 1 hora (`apps/scraping/fx.py`). **No hay tasa fija de respaldo**: la vieja `FX_FALLBACK_USD_PEN=3.80` estaba 13% desviada de la tasa real (3.35 al 2026-08-27) y se usaba en silencio. El respaldo es la última tasa buena observada, con antigüedad máxima; si no hay ninguna, `convert_to_pen` devuelve `None`, la oferta se descarta y se avisa al admin. Perder una oferta es recuperable; contaminar el histórico no.
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
│   ├── web/                  # capa pública (solo lee, nunca scrapea)
│   │   ├── verdict.py        # veredicto por fecha y de tendencia (lógica pura)
│   │   ├── queries.py        # lecturas del histórico para las páginas
│   │   ├── chart.py          # SVG del histórico, generado en el servidor
│   │   ├── cloudflare.py     # purga del caché del borde tras cada barrido
│   │   ├── sitemaps.py       # sitemap.xml
│   │   └── templates/web/    # base, home, route
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
| 6 | Capa web pública: fichas por ruta, veredicto, SEO y Cloudflare | 2026-08-26 |

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
| Tests | 358, en verde, sin tocar red ni Supabase |
| Web pública | **En línea en https://vueloradar.com**: 40 rutas, 18 páginas por ciudad, términos y privacidad, veredicto por fecha, CTA al bot, OG, sitemap y robots |
| VPS | **Desplegado y sano.** Hostinger `srv1933835` (2.24.115.75), repo en `/opt/vueloradar`, los 6 contenedores en `healthy`, `cloudflared` activo. Se despliega con `git pull` + `docker compose -f docker-compose.prod.yml build web && up -d` |

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

- **Los correos ya salen (2026-08-27).** Resend por SMTP (`smtp.resend.com:587`,
  usuario literal `resend`, la API key como contraseña). Verificado con un envío
  real por el camino de producción (`mailer._enviar`). Django habla SMTP de
  fábrica: no hace falta el SDK de Resend.
  **Trampa que costó una API key:** cargar las variables con
  `Get-Content | ssh '... printf "a\nb" ...'` desde PowerShell **no funciona**.
  PowerShell no preserva las barras invertidas del argumento, así que los `\n`
  llegan como la letra `n` y las cinco variables se escriben pegadas en una sola
  línea. Peor: al volcar el `.env` para diagnosticarlo, la key salió por
  pantalla y hubo que rotarla. Para secretos, `nano` directo en el servidor, y
  **nunca volcar el `.env`** — verificar con booleanos (`bool(...)`, `len(...)`,
  `startswith(...)`), jamás con el valor.
- **AdSense RECHAZÓ el sitio el 2026-09-05: «contenido de bajo valor».**
  `ADSENSE_CLIENT` quedó **vacía a propósito** en el `.env` del VPS; el ID bueno
  es `ca-pub-4805816769009138` (la nota lo tuvo mal hasta el 2026-08-29, con dos
  dígitos cambiados) y se restaura cuando haya algo nuevo que enseñar.

  **No es un problema técnico y no tiene arreglo técnico.** Medido sobre las 63
  páginas publicadas: las 40 fichas de ruta comparten el **79%** del vocabulario
  entre sí y los 18 hubs de ciudad el **95%** — son la misma plantilla con otro
  nombre de ciudad. Solo dos páginas tienen prosa escrita para ellas: la portada
  y `cuando-comprar`. Las fichas sí traen los tres bloques de análisis propio
  (40/40 «Cuándo comprar» y «Quién gana», 38/40 «Qué día volar»), así que no
  están vacías: el problema es que todo el texto es plantilla más números.

  Decisión del 2026-09-05: **posponer AdSense y perseguir tráfico primero.** El
  trabajo que pide AdSense es el mismo que pide el SEO, pero con cero posiciones
  el anuncio pagaría céntimos aunque aprobaran mañana. Con la cuenta rechazada,
  el script solo costaba latencia contra las Core Web Vitals — que sí son factor
  de ranking. Sin `ADSENSE_CLIENT` el sitio vuelve a **cero peticiones a
  terceros**: verificado que era la única, `t.me` y `sisac.pe` son enlaces y
  `schema.org`/`w3.org` son namespaces.

  **Antes de volver a «Solicitar revisión» hay que tener contenido original que
  no exista hoy.** Reintentar sin eso gasta el intento: el script tiene que
  estar puesto para que Google lo detecte, así que hay que volver a poner la
  variable, y cada ciclo de revisión son semanas.

- ~~Los correos de aviso nunca salieron.~~ (Resuelto arriba.) El DNS de Resend está puesto y
  propagado en Cloudflare (DKIM en `resend._domainkey.vueloradar.com`, SPF y MX
  de rebotes en `send.vueloradar.com`, o sea el dominio verificado es la raíz
  `vueloradar.com` y `send.` es solo el return-path), pero el `.env` del VPS no
  tiene ninguna variable `EMAIL_*`: Django cae al backend de consola y el
  mensaje de confirmación se imprime en el log del contenedor. Cero envíos en
  el log, así que nadie perdió un aviso todavía, pero el formulario está
  publicado. Falta solo `EMAIL_HOST_PASSWORD` con la API key de Resend.
- **Backup restaurado y verificado el 2026-08-27**: las cinco tablas coincidieron
  fila por fila con producción. **Requiere PostgreSQL 17+**: el formato del dump
  es 1.16 y con PG 16 falla con `unsupported version`. Procedimiento en
  `DEPLOY.md` §7.
- **JetSmart: verificado en vivo el 2026-08-27.** No apareció challenge alguno;
  el deep link sirvió la página completa. El bloqueo del 23-08 no se reprodujo,
  así que trátese como intermitente. Si vuelve, el provider devuelve `[]` y deja
  screenshot: **no se escribe código para evadir detección de bots.**
  Dos correcciones salieron de esa verificación:
  1. **El calendario es TARIFA BASE, no precio final** — al revés de lo que
     decía esta nota. Sin `publishes_base_fare = True` sus precios entraban ~30%
     bajos y habrían disparado alertas falsas.
  2. `price_for_day` tomaba la primera aparición de un número de día; con la
     grilla mostrando dos meses, el 6/10 devolvía el precio del 6/09 (175% de
     error). Ahora sigue el encabezado de mes y **falla cerrado** si no puede
     desambiguar. En el layout actual no hay días repetidos (30 días, 0
     colisiones), pero la grilla arrastra días del mes vecino, así que la
     protección importa.

### Deuda técnica conocida

- **Impuestos de Sky: resuelto.** `apps/scraping/taxes.py` convierte tarifa base
  a precio final (IGV sobre la tarifa, TUUA sumada después) y
  `DirectScraperProvider.search()` lo aplica a todo proveedor con
  `publishes_base_fare = True`. La fórmula reproduce al céntimo la observación
  del 2026-08-23. `VERIFY_DEALS_WITH_DIRECT_SCRAPER` sigue en False, pero ya
  **no por los impuestos**: falta revalidar los selectores en vivo.
- **4 rutas sin datos** (CHM y RIM en ambos sentidos): esos aeropuertos no
  tienen servicio comercial regular. Es dato correcto, no un bug.
- **El histórico todavía es corto para las alertas `deal_detected`**, que
  exigen 20 muestras por ruta. Con dos barridos diarios eso se cumple solo.
- **`evaluate_trend` necesita 14 días de serie y hay 7.** No es un bug: la web
  ahora dice cuántos días faltan (`Verdict.missing_days`) en vez de un "no sé"
  sin plazo. Los veredictos por fecha sí funcionan desde ya.

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

### Notas de la Fase 6

- **Dos veredictos, no uno.** `evaluate()` juzga el precio de **una fecha**
  contra la distribución de la ruta (válido: ese precio es una muestra de esa
  distribución). `evaluate_trend()` juzga el **mínimo de hoy** contra la serie
  de mínimos diarios. Mezclarlos fue un bug real: comparar el mínimo entre 46
  fechas contra la distribución de todas las fechas da "chollo" siempre, por
  construcción. Si se toca esto, mantener la separación.
- **Serie plana.** Cuando el precio no se mueve, `p25 == mediana` y el precio
  de siempre se leía como oferta. Por eso `BUENO` exige además `price < median`.
- **`evaluate_trend` calla con menos de 14 días** de serie. Hoy hay 7, así que
  la tendencia dice "sin histórico suficiente" y se resuelve sola en una semana.
  Los veredictos por fecha sí funcionan desde ya (350+ muestras por ruta).
- **Los umbrales salen de los mismos settings que las alertas**
  (`DEAL_P25_FACTOR`, `VERDICT_MIN_SAMPLES`): la web y el bot no pueden decir
  cosas distintas del mismo precio. Hay un test que lo fija.
- **Caché en el borde**: las vistas declaran `s-maxage=1800` y
  `compute_route_stats` purga la zona al terminar. Sin
  `CLOUDFLARE_API_TOKEN` la purga no corre y solo lo loguea. Ver `DEPLOY-WEB.md`.
  **ROTO desde algún momento entre el 2026-08-29 y el 2026-09-05: el token da
  `HTTP 401 Unauthorized`.** La variable está puesta y `CLOUDFLARE_ZONE_ID`
  también, así que el chequeo de «configurada» pasa: falla la llamada, no la
  configuración, y `purge_everything()` devuelve `False` sin que nadie mire.
  El daño es acotado —con `s-maxage=1800` el borde revalida solo cada 30 min,
  así que la purga acelera pero no es la única vía— pero hoy cada barrido
  termina creyendo que refrescó el sitio y no lo hizo. Se arregla emitiendo un
  token nuevo en Cloudflare con permiso *Zone → Cache Purge*. **Comprobarlo con
  el resultado, no con `bool(...)`**: un token revocado sigue pareciendo
  configurado.
- **La portada usa consultas agregadas** (`bulk_upcoming_prices`,
  `bulk_price_history`). Pedir el histórico ruta por ruta costaba 121 consultas
  y 22 s con 40 rutas, y ese costo lo paga entero el primer visitante tras cada
  purga del borde. Hay un test que fija el máximo de consultas.
- **Solo se publican rutas con histórico.** `route_detail` filtra por
  `published_routes()`, igual que el sitemap: CHM y RIM no tienen servicio
  comercial y devolvían páginas vacías con 200 (pasivo SEO). Ahora son 404.
- **Estáticos con WhiteNoise** (`STATIC_ROOT`, `CompressedManifestStaticFilesStorage`).
  Los tests usan el storage plano en `settings_test.py` porque no corren
  `collectstatic`; sin ese override, `{% static %}` falla por manifiesto ausente.
- **`500.html` es autocontenida.** Django la renderiza con contexto vacío y sin
  context processors: no puede heredar de `base.html`. Y ojo, el motor procesa
  las etiquetas **incluso dentro de comentarios HTML** — un `{% url %}` dentro
  de un `<!-- -->` rompe la plantilla.
- **Django fuerza `DEBUG=False` durante los tests**, así que la suite ya
  ejercita las plantillas de error de producción. Lo que NO cubre es el storage
  con manifiesto (los tests usan el plano): para eso está
  `scripts/check_production.py`, que renderiza con `config.settings` reales.
  Correrlo antes de desplegar; `check --deploy` no resuelve un solo estático.
- **La web convierte a Telegram con enlace profundo**: el botón de cada ficha
  apunta a `t.me/<bot>?start=ORI-DES` y `cmd_start_deep_link` resuelve el
  payload a una ruta. Si el payload no valida, cae en la bienvenida normal.
  Perder ese contexto desperdicia la única conversión del embudo.
- **Los activos de marca se derivan de `brand/logo-source.png`** con
  `scripts/build_brand_assets.py` (iconos con esquinas transparentes y
  `og.png` de 1200x630). Se corre a mano y los resultados se versionan; por eso
  Pillow no está en `requirements.txt`. El radio de las esquinas se detecta del
  propio logo, no está hardcodeado.
- **`og:image` tiene que ser URL absoluta** o WhatsApp la descarta, y WhatsApp
  es el canal real de difusión en Perú (94% de penetración).
- **La web no publica tarifas de Google en crudo**, solo estadísticas propias
  derivadas del histórico. Es lo que nos hace defendibles y además lo que baja
  el riesgo de exponer públicamente la dependencia de `fast-flights`.

### Hallazgos del histórico y 3D por CSS (2026-08-27)

- **El precio de venta se eliminó entero.** Ya no hay `apps/flights/pricing.py`,
  ni `SALE_*` en settings, ni desglose de margen en el bot. El producto informa;
  no revende.
- **`PriceSnapshot.days_ahead` es una columna, no una resta.** «¿Cuántos días
  antes conviene comprar?» se responde agrupando por anticipación, y calcular
  `flight_date - snapshot_at` en cada consulta obliga a recorrer el histórico
  entero. Se llena al insertar (contra `localdate()`, porque `snapshot_at` es
  `auto_now_add` y todavía no existe) y hay migración de backfill (`0005`)
  para las 15.568 filas previas — corrida el 2026-08-27, cero pendientes.
- **`apps/web/insights.py` responde tres preguntas que exigen histórico**: qué
  día volar, cuándo comprar y qué aerolínea gana en precio. Con datos reales al
  2026-08-27: comprar 1–2 semanas antes sale S/ 308 contra S/ 387 a última hora
  (20%); volar miércoles S/ 313 contra domingo S/ 365 (14%); LATAM es la más
  barata el 73% de las veces a nivel nacional — pero en LIM–CUZ gana JetSMART
  con 80%. **El análisis por ruta no es el nacional repetido**, y ese contraste
  es justamente el valor.
- **Nada opina sin muestras.** Misma regla que el veredicto: mínimo 3 grupos,
  20 muestras por grupo, 300 en total, y una diferencia bajo el 5% no es
  titular sino ruido. Si no alcanza, el bloque no se renderiza.
- **El alto de las barras se calcula sobre el RANGO, no sobre el precio.** Entre
  S/ 313 y S/ 365 hay un 14%: barras al 86% y al 100% no dejan ver nada. Hay un
  piso del 22% para que la más barata siga siendo visible.
- **Las etiquetas cortas (`Barra.corto`) no son un capricho.** En 375 px caben
  siete columnas de 36 px; «menos de una semana» se truncaba con puntos
  suspensivos y borraba justo el dato que la barra viene a mostrar. Verificado
  midiendo `scrollWidth > clientWidth` en el navegador.
- **El 3D es por CSS, no por WebGL, y es una decisión de negocio.** La
  adquisición es 100% SEO, el tráfico es móvil peruano y las Core Web Vitals
  son factor de ranking: three.js serían cientos de KB contra lo único que trae
  usuarios. `perspective`, `rotateX/Y`, `translateZ` y `animation-timeline:view()`
  los compone la GPU y cuestan cero JS. El horizonte, el grano
  (`feTurbulence` en línea, ~200 bytes), la inclinación de las tarjetas y el
  relieve del precio salen todos de ahí.
- **`perspective` va en el padre (`.tilt`), no en el hijo.** Puesto en el hijo,
  cada tarjeta tendría su propio punto de fuga y una fila no compartiría escena.
- **El servidor de desarrollo con `--noreload` sirve código rancio.** Perdí un
  rato creyendo que un bug de plantilla era real: el proceso viejo seguía vivo
  en el puerto y el nuevo no llegaba a levantar. Ante un cambio en Python que
  «no se aplica», levantar en un puerto nuevo antes de diagnosticar.

### Contacto, analítica e imágenes (2026-08-28)

- **`docker compose up -d` NO recreó el contenedor al cambiar solo el `.env`.**
  Gunicorn siguió con el entorno viejo mientras `exec … python` —proceso
  nuevo— ya veía la variable nueva, así que el sitio y la consola decían cosas
  distintas. Ante un cambio de variable que «no se aplica»:
  `up -d --force-recreate web`.
- **Cloudflare reescribe los `mailto:`.** Con Scrape Shield → *Email Address
  Obfuscation* (activo por defecto) el HTML servido trae
  `/cdn-cgi/l/email-protection#…` y un `[email protected]`, y un script propio
  lo descifra en el navegador. **`curl` nunca va a ver la dirección**: buscarla
  con grep da cero y parece un bug que no existe. Verificado en navegador real:
  el enlace resuelve a `mailto:contacto@vueloradar.com` y el texto se ve.
- **`CONTACT_EMAIL` vacío por defecto**: publicar una dirección que rebota es
  peor que no publicar ninguna. Recibe por Cloudflare Email Routing, que
  reenvía a un Gmail. Ojo con la trampa del panel: *Destination Address* es el
  buzón externo que **recibe**, no la dirección que se quiere crear — ponerla
  ahí deja un `Pending` que nadie puede verificar nunca.
- **La analítica ya estaba activa por «automatic setup» de Cloudflare**, que
  inyecta el beacon en el borde sin que Django lo sepa. `curl` no lo ve; un
  navegador real sí. Por eso `ANALYTICS_ENABLED` (lo que declara la privacidad)
  está separado de `CLOUDFLARE_ANALYTICS_TOKEN` (lo que inyectamos nosotros):
  **poner los dos duplicaría el conteo.**
- **Tres veces se cayó en el mismo error**: la privacidad afirmando lo
  contrario de lo que la página hace (AdSense, analítica propia, analítica del
  borde). La regla que quedó: toda afirmación sobre terceros va condicionada a
  la variable que gobierna ese tercero, y con test.
- **Las imágenes bajaron de 442 KB a 43 KB.** `og.png` de 111 a 14 KB —es la
  que descarga WhatsApp en cada enlace compartido—, `icon-512` de 235 a 9 KB.
  La optimización vive en `scripts/build_brand_assets.py` (`_guardar`), no en
  los archivos, así que regenerar el logo no la deshace. Son marca plana, así
  que una paleta de 256 colores las reproduce sin diferencia visible; se
  comparan ambas versiones y se guarda la más chica, porque con degradados la
  paleta puede pesar más.

### Notas del pie, la publicidad y el botón al bot (2026-08-27)

- **El pie eran cuatro párrafos apilados** y la promesa que sostiene la marca
  ("no vendemos pasajes ni cobramos comisión") quedaba enterrada entre el aviso
  legal y el disclaimer. Ahora son tres columnas y el crédito de
  **Star Insights IT by SISAC** va en la barra de abajo, por `BUILDER_NAME` /
  `BUILDER_URL`: sin `BUILDER_NAME` no se dibuja crédito vacío.
- **El botón flotante es un enlace, no un widget de chat.** Sin JS, sin iframe
  y sin script de terceros leyendo la sesión. El chat de verdad ya existe y
  vive en Telegram; un widget propio sería construir el bot dos veces. En las
  fichas lleva el enlace profundo (`?start=LIM-CUZ`) vía el bloque `bot_start`,
  porque abrir el bot en blanco desperdicia la única conversión del embudo.
- **La publicidad está maquetada pero apagada.** Sin `ADSENSE_CLIENT` no se
  renderiza ni el hueco ni el script: AdSense exige aprobación previa y un
  script que carga sin cuenta aprobada paga la latencia sin mostrar un anuncio.
  Sin `slot` tampoco se dibuja el contenedor — un hueco vacío desplaza el
  contenido para nada. Un slot por ubicación (`ADSENSE_SLOT_HOME`,
  `ADSENSE_SLOT_ROUTE`): Google reporta por slot y un ID único para todo el
  sitio hace imposible saber qué espacio rinde.
- **El anuncio va rotulado y después del dato**, no entre la pregunta y la
  respuesta. Las políticas de AdSense exigen distinguirlo del contenido, y acá
  confundirlo con el veredicto costaría más de lo que paga.
- **No hay CSP.** El comentario de `base.html` decía que "la CSP no permite JS",
  pero el borde no emite la cabecera: verificado el 2026-08-27 contra
  `https://vueloradar.com`. La ausencia de JS es una decisión de diseño, no una
  restricción impuesta — conviene no confundirlas al tocar el `<head>`.
- **`foot-by` a secas matchea la regla CSS además del marcado.** Un test que
  afirme "el crédito no aparece" tiene que buscar `class="foot-by"`.
- **`.wrap` le ganaba en especificidad a `footer`.** `<footer class="wrap">` y
  `.wrap{margin:0 auto;padding:0 1.5rem}`: una clase (0,1,0) vence a un
  elemento (0,0,1), así que el margen y el padding superiores del pie se
  anulaban y quedaba pegado a la última tarjeta. Medido en vivo: `marginTop`
  0 px. Venía así desde antes del rediseño. El selector es `footer.wrap` y
  repite el `auto` horizontal — sin él el pie se descentra. **Cualquier regla
  para `main`, `header` o `footer` tiene el mismo problema**: los tres llevan
  `.wrap`.
- **El logotipo del constructor (`BUILDER_LOGO`) sigue siendo condicional en la
  plantilla**, aunque hoy venga con valor por defecto: `{% static %}` con
  `CompressedManifestStaticFilesStorage` revienta en producción si el archivo no
  está en disco. Si se cambia la ruta, el archivo tiene que existir **y hay que
  correr `collectstatic`** — `scripts/check_production.py` lo detecta
  (`Missing staticfiles manifest entry`), `check --deploy` no.
- **El original del logo de SISAC vive en `brand/sisac-logo-source.png`.** Venía
  a 1134×1134 y 238 KB con un margen transparente enorme: a 24 px la marca
  habría quedado en unos 8 px. El publicado se recortó al `getbbox()` y se
  escaló a 96 px de alto (22 KB). Queda en **1,76:1**, así que el CSS fija el
  alto y deja el ancho en `auto`; forzarlo a cuadrado lo aplasta.
- **`/ads.txt` se deriva de `ADSENSE_CLIENT`** (quitándole el prefijo `ca-`).
  AdSense no paga sin ese archivo: el inventario queda como no autorizado y los
  anunciantes no pujan. Sin ID devuelve 404, no un archivo con `pub-` vacío,
  que sería una declaración falsa sobre quién puede vender la publicidad.
- **Search Console: la verificación por DNS ya está puesta** (comprobado el
  2026-08-29: la zona sirve el TXT `google-site-verification=7ere6DRUZ7Mb…`).
  Es una **propiedad de dominio**, que cubre la raíz, `www`, `http` y `https`
  de una sola vez y no depende de que la web siga sirviendo una etiqueta.
  Por eso `GOOGLE_SITE_VERIFICATION` está y debe seguir **vacía**: la meta del
  `<head>` es la vía *alternativa* de verificación, no un complemento.
  Falta lo que solo se hace desde el panel: confirmar la propiedad y **enviar
  `sitemap.xml`** (63 URLs: portada, buscar, cuando-comprar, términos,
  privacidad, 18 hubs y 40 fichas). Sin eso la indexación sigue siendo pasiva.
- **Las fichas de ruta redirigen 301 a la forma canónica en MAYÚSCULAS.** La
  URL acepta cualquier caja a propósito (`[A-Za-z]{3}` en `urls.py`) para que
  un enlace tecleado a mano llegue, pero el `<link rel="canonical">` se arma
  con `{{ request.path }}`: sin el redirect, `/vuelos/lim-cuz/` y
  `/vuelos/LIM-CUZ/` devolvían 200 declarándose canónica cada una. Son 2^6
  combinaciones por ruta, 2.560 URLs con el mismo contenido. La canónica es
  mayúsculas porque es lo que ya emiten los `{% url %}`, el sitemap y los
  nombres de las imágenes OG. Los hubs y las estáticas nunca tuvieron el
  problema: `/vuelos/desde-Lima/` da 404.
- **`https://www.sisac.pe/` sirve un certificado autofirmado** y falla la
  verificación TLS; la raíz `https://sisac.pe/` responde bien. Es el sitio del
  constructor, no éste, pero conviene saberlo antes de enlazar el `www`.

### Notas del rediseño (2026-08-27)

- **Los comentarios `{# #}` de Django son de UNA línea.** Uno multilínea se
  renderiza como texto, y dentro de `<head>` eso cierra el head antes de tiempo
  y manda los `<meta>` de Open Graph al `<body>`, donde los crawlers los
  ignoran. Para varias líneas, `{% comment %}`.
- **`LANGUAGE_CODE="es"` localiza los decimales en las plantillas**: `712.0`
  sale como `712,0`. Una coma es inválida como coordenada SVG y como valor CSS,
  así que el punto del gráfico saltaba al origen y `stroke-dasharray` no se
  aplicaba. El bloque del SVG va dentro de `{% localize off %}`.
- **La URL del hub va antes que la de ruta** y la ficha exige tres letras por
  lado (`re_path`): sin esa restricción, `/vuelos/desde-lima/` entraba como
  origen "desde" y destino "lima".
- **Las fuentes están auto-alojadas** en `apps/web/static/web/fonts/` (49 KB
  las dos). Google Fonts serían peticiones a terceros y en móvil peruano eso se
  paga en latencia.
- **`Airport.slug` es una propiedad, no una columna.** Son 20 aeropuertos y el
  slug se deriva de la ciudad; una columna sería el mismo dato en dos lugares.
- **El largo del trazo del gráfico se calcula en Python** (`chart.length`).
  Medirlo en el navegador exigiría JS, y la CSP no lo permite.

### Notas de la Fase 5

- **`DJANGO_SECRET_KEY` no puede tener `$` ni `%`.** Docker Compose interpola los valores del `.env`, así que la clave llegaría mutilada al contenedor y distinta de la local: sesiones y firmas romperían en silencio. Detectado por el warning `"c" variable is not set` de `docker compose config`. La clave se regeneró con un charset seguro.
- **El precio de Sky en el listado es TARIFA BASE, sin impuestos** ("+ Tasas e impuestos" en la tarjeta). Ya no es un bloqueante: Sky se declara con `publishes_base_fare = True` y `DirectScraperProvider.search()` normaliza a precio final vía `apps/scraping/taxes.py`. `VERIFY_DEALS_WITH_DIRECT_SCRAPER` sigue en False solo por la fragilidad de los selectores, no por los impuestos.
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
