# VueloRadar Perú

Monitoreo autónomo de vuelos domésticos en Perú: busca el vuelo más barato de
cualquier ruta nacional, acumula histórico de precios y (desde la Fase 4)
alerta cuando detecta una oferta real.

El contexto completo del proyecto vive en [CLAUDE.md](CLAUDE.md).

## Requisitos

- Python 3.11+
- Docker Desktop (solo para Redis)
- Un proyecto en Supabase con la connection string del **Session Pooler** (puerto 5432)

## Puesta en marcha

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Linux/macOS: .venv/bin/python
cp .env.example .env                                          # y completá DATABASE_URL
docker compose up -d
.venv/Scripts/python.exe manage.py migrate
.venv/Scripts/python.exe manage.py load_airports
.venv/Scripts/python.exe manage.py load_routes
```

En PowerShell 5.1 no existe `&&`: encadená con `;` y `if ($?) { ... }`.

## Uso

### Búsqueda puntual

```bash
python manage.py search_flights LIM CUZ 2026-09-15
```

Imprime los vuelos del día ordenados por precio en soles y los guarda. Si la
ruta no tiene vuelo directo y Google tampoco devuelve itinerarios con escala,
arma la conexión vía LIM sumando ambos tramos.

### Estado histórico de una ruta

```bash
python manage.py route_report LIM CUZ
```

Muestra las estadísticas de 30 días, los últimos snapshots y el mínimo histórico.

## Monitor automático (Celery)

Se necesitan **dos procesos** además de Redis: un worker que ejecuta y un beat
que agenda.

```bash
celery -A config worker -Q scraping,default -c 2
```

```bash
celery -A config beat
```

**En Windows** el pool por defecto (prefork) no funciona; agregá `--pool=solo`
al worker:

```bash
celery -A config worker -Q scraping,default -c 2 --pool=solo
```

### Qué corre solo

| Task | Cuándo | Qué hace |
|---|---|---|
| `scan_all_monitored` | 06:00 y 18:00 (hora Perú) | Encola el barrido de todas las rutas monitoreadas |
| `compute_route_stats` | al terminar cada barrido | Recalcula promedio, mediana, p25 y mínimo de 30 días |
| `purge_old_offers` | domingos 03:00 | Borra ofertas crudas de más de 90 días. **Los snapshots no se purgan nunca** |

### Lanzar un barrido a mano

```bash
python manage.py shell -c "from apps.scraping.tasks import scan_all_monitored; print(scan_all_monitored.delay())"
```

Solo las rutas prioritarias (`priority=1`):

```bash
python manage.py shell -c "from apps.scraping.tasks import scan_all_monitored; print(scan_all_monitored.delay(priority_max=1))"
```

Un barrido completo son ~44 rutas x 30 fechas = ~1.300 consultas. A 3-8s cada
una son varias horas con un worker. **Eso es intencional**: el espaciado es
anti-bloqueo, no una limitación de performance. Subir la concurrencia hace que
Google bloquee la IP.

### Cuando una fuente falla

A los 3 fallos consecutivos, `scan_route_date` dispara `pause_source`: la
fuente queda castigada 30 minutos y se manda un aviso al admin por Telegram (si
`TELEGRAM_TOKEN` y `TELEGRAM_ADMIN_CHAT_ID` están configurados). Un éxito
resetea el contador. Mientras dura la pausa el barrido saltea las consultas en
vez de insistir.

Para destrabar a mano:

```bash
python manage.py shell -c "from apps.scraping import ratelimit; ratelimit.resume('google_flights')"
```

## Bot de Telegram

```bash
python manage.py runbot
```

Levanta el bot con polling (webhook llega en Fase 5). Necesita `TELEGRAM_TOKEN`
en el `.env` — pedíselo a [@BotFather](https://t.me/BotFather) con `/newbot`.

### Qué entiende

**Lenguaje natural.** Cualquier mensaje que no sea un comando pasa por Claude,
que extrae origen, destino y fecha:

```
vuelo de lima a puerto maldonado el 20 de setiembre  ->  LIM PEM 2026-09-20
quiero ir de huancayo a lima el viernes              ->  JAU LIM 2026-08-28
de cusco a arequipa la próxima semana                ->  CUZ AQP, flexible 3 días
```

Las ciudades sin aeropuerto propio se mapean al más cercano (Huancayo a Jauja,
Machu Picchu a Cusco). La tabla ciudad→IATA sale de la base, no está hardcodeada:
si agregás un aeropuerto, el parser lo entiende sin tocar el prompt.

**Comandos.**

| Comando | Qué hace |
|---|---|
| `/start` | Registro y bienvenida |
| `/vuelo LIM CUZ 2026-09-15` | Búsqueda directa, sin pasar por la IA |
| `/rutas` | Rutas monitoreadas con su mínimo de 30 días |
| `/alerta LIM CUZ` | Placeholder — el motor llega en Fase 4 |
| `/ayuda` | Ayuda completa |

### Ida y vuelta

El parser reconoce el viaje completo escrito como lo dice la gente:

```
"Pasaje para el 16 de octubre iba d dpto a Lima y de lima puerto el 18"
   -> PEM → LIM el 16/10, vuelta el 18/10
```

Busca los dos tramos en paralelo, muestra el más barato y el directo de cada
uno, y **suma el total del viaje**. Si un tramo no tiene vuelos, lo dice en vez
de inventar un total incompleto.

Aviso que sale siempre: comprar el ida y vuelta **como paquete** en la web de
la aerolínea suele salir menos que sumar dos pasajes sueltos. El total que da
el bot es un techo confiable, no la última palabra.

### Precio de venta

Si revendés los pasajes, el bot te muestra la cotización con tu margen:

```
🏷 Para cotizar al cliente
  Costo:    S/ 973
  Tu venta: S/ 1,075
  Ganancia: S/ 102 (10.5%)
```

El margen es el **mayor** entre `SALE_MARKUP_PCT` y `SALE_MARKUP_MIN_PEN`: un
10% sobre un pasaje de S/ 150 son S/ 15, que no paga el trabajo de gestionar la
compra. El precio final se redondea hacia arriba a múltiplos de
`SALE_ROUND_TO_PEN` — nadie cotiza S/ 1.070,30.

**Este desglose solo lo ve el admin.** Mandarle a un cliente su cotización con
tu ganancia al lado sería, como mínimo, incómodo. Se cambia con
`SHOW_SALE_PRICE_TO_ADMIN_ONLY=False`.

### Búsqueda flexible

Si el mensaje es vago ("esa semana", "alrededor del 15"), el bot barre la fecha
objetivo ±N días (máximo ±3) y muestra el mejor precio de cada día. Cada día
extra son 2 consultas más al scraper, por eso el techo es duro.

### Límites de uso

Plan gratis: 10 búsquedas por día (`FREE_DAILY_SEARCHES` en el `.env`).
Premium: ilimitado. El contador se resetea de forma perezosa — no hay task
nocturna, se compara la fecha en la primera búsqueda del día.

Para pasar a alguien a Premium: admin de Django, sección Usuarios de Telegram,
acción "Pasar a plan Premium".

### Alertas

| Comando | Qué hace |
|---|---|
| `/alerta LIM CUZ` | Aviso cuando el precio caiga en el 10% más barato del último mes |
| `/alerta LIM CUZ 180` | Aviso si baja de S/ 180 |
| `/alerta LIM CUZ 180 2026-10-14` | Solo esa fecha de vuelo |
| `/misalertas` | Listado con botones para desactivar |

La alerta sin precio objetivo (`deal_detected`) exige **20 muestras** de
histórico antes de opinar: un percentil calculado sobre 5 snapshots dispararía
con cualquier precio normal.

**Anti-spam de dos capas.** Ni más de un aviso cada 12 horas por alerta, ni
re-aviso si el precio no bajó al menos 5% desde el último. Sin la segunda capa,
un precio que oscila S/ 2 generaría un mensaje cada 12 horas para siempre.

Límites: 2 alertas activas en el plan gratis, 20 en Premium.

### Veredicto de compra

Cada alerta y cada búsqueda con histórico suficiente incluye una línea del
analista:

```
🤖 Veredicto: COMPRA. El precio actual de S/ 152 está muy por debajo del
percentil 25 (S/ 201) y del mínimo histórico observado, con solo 29 días
para el vuelo.
```

El contexto que ve el modelo sale entero de la base — stats de 30 días y la
evolución del precio para esa ruta y fecha. No inventa números, los interpreta.

En las búsquedas el veredicto llega en una **segunda edición** del mensaje:
primero los vuelos, después el análisis. Pedirlo antes agregaría segundos de
espera por una línea que puede no llegar.

### Router de IA con respaldo

Se intentan en orden hasta que uno responda:

| # | Proveedor | Modelo | Variable |
|---|---|---|---|
| 1 | Anthropic | `claude-sonnet-4-6` | `ANTHROPIC_API_KEY` |
| 2 | Groq | `llama-3.3-70b-versatile` | `GROQ_API_KEY` |
| 3 | DeepSeek | `deepseek-chat` | `DEEPSEEK_API_KEY` |
| 4 | Ollama | `OLLAMA_MODEL` | `OLLAMA_BASE_URL` (solo dev local) |

**Si todos fallan, el sistema sigue sin veredicto.** Una alerta de precio nunca
se bloquea porque la IA esté caída.

Cada proveedor tiene timeout de 20s y un solo intento. A los 3 fallos seguidos
se abre un circuit breaker que lo saltea 15 minutos — sin eso, con Anthropic
caído cada llamada quemaría 20 segundos antes de pasar al siguiente.

El consumo queda desglosado por proveedor en la tabla `AIUsageLog`, visible en
el admin de Django.

### Costo de la IA

Cada mensaje en lenguaje natural es una llamada a Claude con `max_tokens=300` y
thinking apagado. Las respuestas idénticas se cachean 1h en Redis, con el día en
la clave (porque "mañana" no significa lo mismo cada día). Los comandos `/vuelo`
no pasan por la IA.

## Tests

```bash
pytest
```

Corren contra SQLite en memoria (`config/settings_test.py`) y **nunca tocan la
red, Supabase ni Redis**: los providers y el reloj se mockean.

## Producción

El despliegue completo está en **[DEPLOY.md](DEPLOY.md)**: requisitos del VPS,
pasos exactos, backups y runbook de incidentes.

```bash
docker compose -f docker-compose.prod.yml up -d
```

Seis servicios: `redis`, `web` (admin y `/healthz`), `worker-scraping`,
`worker-default`, `beat` y `bot`. La base es Supabase, no hay contenedor de
Postgres.

### Scrapers directos de aerolínea

`apps/scraping/providers/sky.py` y `jetsmart.py` usan Playwright contra el
motor de venta de cada aerolínea. **No entran al barrido masivo**: un Chromium
por consulta multiplicado por 1.300 consultas sería inviable. Se activan por
flag y solo corren en rutas marcadas con `use_direct_scrapers`.

Estado real de cada uno:

| Provider | Estado | Caveat |
|---|---|---|
| Sky | ✅ verificado en vivo | El precio del listado es **tarifa base, sin impuestos** |
| JetSmart | ⚠️ URL verificada, extracción no | Challenge anti-bot; aterriza en calendario, sin horarios |

Ante un fallo cada scraper deja un screenshot en
`DIRECT_SCRAPER_SCREENSHOT_DIR`: abrirlo suele bastar para distinguir "cambió
el DOM" de "no hay vuelos ese día".

## Estructura

```
config/          settings, celery
apps/flights/    modelos, admin, comandos CLI, estadísticas
apps/scraping/   providers, servicio de búsqueda, tasks, rate limit, FX
apps/users/      esqueleto (Fase 3)
apps/alerts/     esqueleto (Fase 4)
apps/ai_analyst/ esqueleto (Fase 4)
scripts/         semillas de aeropuertos y rutas
```
