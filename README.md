# VueloRadar Perú

Monitoreo autónomo de vuelos domésticos en Perú. Busca el vuelo más barato de
cualquier ruta nacional, acumula histórico de precios y avisa cuando detecta una
oferta real.

Son dos caras del mismo motor:

- **Sitio público** — una ficha por ruta que responde lo que ni Google Flights
  ni los metabuscadores responden en una página indexable: *si el precio de una
  fecha es bueno para esa ruta*, comparado contra su propio histórico.
- **Bot de Telegram** — búsqueda a pedido en lenguaje natural y alertas de caída
  de precio.

El diferencial es el mismo en los dos: no vendemos pasajes ni cobramos comisión,
así que podemos decir **"esperá"**. Un metabuscador nunca lo dirá.

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

## Sitio público

```bash
python manage.py runserver
```

| Ruta | Qué es |
|---|---|
| `/` | Todas las rutas publicadas, ordenadas por dónde hay más oportunidad |
| `/vuelos/LIM-CUZ/` | Ficha: precio desde, veredicto por fecha, gráfico e histórico |
| `/sitemap.xml`, `/robots.txt` | Indexación |

Solo lee de `PriceSnapshot` y `RouteStats`. **No scrapea ni escribe nada.**

### Dos veredictos, y no se pueden mezclar

- **Por fecha** (`evaluate`) — el precio de un día contra la distribución de la
  ruta. Es válido porque ese precio es una muestra de esa misma distribución.
  Funciona desde el primer mes de histórico y es lo que llena el calendario.
- **De tendencia** (`evaluate_trend`) — el mínimo de hoy contra la serie de
  mínimos diarios. Necesita 14 días; hasta entonces la página dice cuántos
  faltan en vez de inventar.

Usar el primero para juzgar el mínimo entre 46 fechas daba **"chollo" en el
100% de las rutas, por construcción**: se comparaba el más barato de todas las
fechas contra la distribución de todas las fechas. Si tocás esto, mantené la
separación.

### Caché en el borde

Las páginas declaran `s-maxage=1800` y `compute_route_stats` purga la zona de
Cloudflare al terminar cada barrido. Entre barridos sirve el borde y el origen
no recibe tráfico; al entrar datos nuevos, aparecen al instante.

### Activos de marca

```bash
python scripts/build_brand_assets.py   # brand/logo-source.png -> iconos + og.png
python manage.py collectstatic
```

El radio de las esquinas se detecta del propio logo, así que cambiar la marca no
requiere tocar plantillas.

## Bot de Telegram

```bash
python manage.py runbot
```

Levanta el bot con polling; en producción se puede usar webhook según
`BOT_MODE`. Necesita `TELEGRAM_TOKEN`
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
| `/alerta LIM CUZ` | Aviso cuando aparezca una oferta real (ver *Alertas*) |
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

### Link de compra

Cada resultado trae el link a Google Flights. En ida y vuelta es **uno solo y
apunta a la búsqueda de paquete**, no dos links de solo ida — ahí es donde
está el precio combinado, que suele ser más bajo que sumar dos pasajes.

La vista previa de enlaces va desactivada (`link_preview_is_disabled`): sin
eso Telegram pega una tarjeta de Google debajo de cada resultado y tapa
justamente los precios que el usuario vino a ver.

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
red, Supabase ni Redis**: los providers y el reloj se mockean. Django fuerza
`DEBUG=False` durante los tests, así que las plantillas de error de producción
también quedan cubiertas.

## Producción

Corre en **Railway** (un proyecto, cinco servicios desde el mismo `Dockerfile`)
con **Cloudflare** adelante y **Supabase** como base. El despliegue completo,
los backups y el runbook de incidentes están en **[DEPLOY.md](DEPLOY.md)**; la
capa de dominio, caché y WAF en **[DEPLOY-WEB.md](DEPLOY-WEB.md)**.

```
Cloudflare  dominio · DNS · CDN · caché · WAF
     |
Railway     web · worker-scraping · worker-default · beat · bot · Redis
     |
Supabase    Postgres
```

`docker-compose.prod.yml` sigue en el repo y sirve para levantar el stack en un
VPS si algún día hace falta, pero **no es el despliegue de referencia**.

Antes de abrir tráfico:

```bash
python scripts/check_production.py
```

Renderiza las páginas con los ajustes reales y resuelve el manifiesto de
estáticos — lo que `manage.py check --deploy` no toca.

### Scrapers directos de aerolínea

`apps/scraping/providers/sky.py` y `jetsmart.py` usan Playwright contra el
motor de venta de cada aerolínea. **No entran al barrido masivo**: un Chromium
por consulta multiplicado por 1.300 consultas sería inviable. Se activan por
flag y solo corren en rutas marcadas con `use_direct_scrapers`.

Estado real de cada uno:

| Provider | Estado | Nota |
|---|---|---|
| Sky | ✅ verificado en vivo (2026-08-23) | Publica **tarifa base**; se normaliza a precio final |
| JetSmart | ✅ verificado en vivo (2026-08-27) | Publica **tarifa base**; devuelve precio del día, sin horarios |

**Los dos publican tarifa base y los dos se normalizan solos.**
`apps/scraping/taxes.py` aplica IGV sobre la tarifa y suma la TUUA después —en
ese orden, que es lo que hace cuadrar el número al céntimo— y
`DirectScraperProvider.search()` lo aplica a cualquier provider marcado con
`publishes_base_fare`. Sin eso parecían 25-30% más baratos que Google y
corrompían las alertas.

JetSMART aterriza en un **calendario de precios**, no en la lista de vuelos: da
el precio del día sin horarios ni número de vuelo. Alcanza para verificar un
precio, no para mostrarle vuelos a alguien. Si vuelve a aparecer el challenge
anti-bot que se vio el 23-08, el provider devuelve `[]` y deja screenshot: **no
hay código para evadir detección de bots.**

Ante un fallo cada scraper deja un screenshot en
`DIRECT_SCRAPER_SCREENSHOT_DIR`: abrirlo suele bastar para distinguir "cambió
el DOM" de "no hay vuelos ese día".

## Estructura

```
config/          settings, celery, healthz
apps/flights/    modelos, admin, comandos CLI, estadísticas
apps/scraping/   providers, búsqueda, tasks, rate limit, FX, impuestos
apps/web/        sitio público: veredicto, consultas, gráfico SVG, sitemap
apps/users/      usuarios de Telegram y cupo diario
apps/alerts/     motor de alertas y notificaciones
apps/ai_analyst/ router de IA con fallback y veredicto de compra
bot/             handlers de aiogram, formato de mensajes
templates/       404 y 500 (del proyecto, no de una app)
brand/           logo fuente y su especificación
scripts/         semillas, activos de marca, chequeo de producción
```
