# Despliegue de VueloRadar Perú

Guía para dejar el sistema corriendo 24/7 en un VPS. Asume que no conocés el
proyecto: seguí los pasos en orden.

## 1. Qué necesitás antes de empezar

**VPS:** 2 vCPU, 4 GB RAM, 20 GB de disco, Ubuntu 24.04. Los 4 GB no son
capricho: Chromium (los scrapers directos) se come entre 500 MB y 1 GB por
instancia. Con 2 GB el worker muere por OOM en el primer scraping directo.

**Cuentas y credenciales:**

| Qué | Dónde se saca | Cuándo hace falta |
|---|---|---|
| Connection string de Supabase | Dashboard → Connect → **Session pooler** (puerto 5432) | siempre |
| Token del bot | [@BotFather](https://t.me/BotFather) → `/newbot` | siempre |
| Tu chat ID de Telegram | [@userinfobot](https://t.me/userinfobot) | siempre |
| API key de Anthropic | console.anthropic.com | opcional (sin ella no hay veredicto) |
| API key de Groq | console.groq.com | opcional (respaldo de la IA) |

## 2. Preparar el servidor

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER
```

Cerrá sesión y volvé a entrar para que el grupo `docker` tome efecto.

## 3. Clonar y configurar

```bash
git clone <tu-repo> vueloradar && cd vueloradar
cp .env.example .env
nano .env
```

Lo mínimo que hay que llenar:

```
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=<50 caracteres aleatorios, SIN $ ni %>
DJANGO_ALLOWED_HOSTS=tu-dominio.com,127.0.0.1
DJANGO_ADMIN_PATH=<algo-no-obvio-que-no-sea-admin>
DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
TELEGRAM_TOKEN=<de BotFather>
TELEGRAM_ADMIN_CHAT_ID=<tu chat id>
ANTHROPIC_API_KEY=<opcional>
LOG_FORMAT=json
```

**Tres trampas que cuestan horas:**

1. **El `SECRET_KEY` no puede tener `$` ni `%`.** Docker Compose interpola las
   variables del `.env`, así que un `$` mutila la clave dentro del contenedor
   sin avisar: sesiones y tokens dejan de validar y el error no dice por qué.
   Generá una segura así:

   ```bash
   python3 -c "import secrets,string; a=string.ascii_letters+string.digits+'!@#^&*(-_=+)'; print(''.join(secrets.choice(a) for _ in range(50)))"
   ```

2. **El puerto de Supabase es 5432, nunca 6543.** El dashboard muestra primero
   el Transaction Pooler (6543), que no mantiene la sesión entre queries y
   rompe las migrations de Django con errores de "prepared statement".

3. **Codificá TODOS los símbolos del password de la base**, sobre todo el `@`:
   `#` es `%23`, `@` es `%40`, `:` es `%3A`, `/` es `%2F`, `?` es `%3F`,
   `%` es `%25`.

   Un `@` sin codificar es especialmente traicionero porque **Django funciona
   igual**: su parser corta en el último `@` de la URL. Pero `libpq` corta en el
   primero, así que `pg_dump` falla con
   `could not translate host name "...@aws-0-..."`. Resultado: la app anda
   perfecto y el backup diario falla en silencio. Verificalo así:

   ```bash
   docker run --rm postgres:17-alpine psql "$DATABASE_URL" -c "select 1"
   ```

   Si eso responde, Django y pg_dump están de acuerdo.

## 4. Levantar

```bash
docker compose -f docker-compose.prod.yml build
```

```bash
docker compose -f docker-compose.prod.yml up -d
```

La primera build tarda 10-15 minutos porque descarga Chromium. Si no vas a usar
los scrapers directos, comentá la línea `RUN playwright install` del
`Dockerfile` y la imagen baja unos 400 MB.

Migrations y datos iniciales:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py migrate
```

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py load_airports
```

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py load_routes
```

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser
```

Verificar que todo esté sano:

```bash
docker compose -f docker-compose.prod.yml ps
```

```bash
curl -fsS http://127.0.0.1:8000/healthz
```

Los seis servicios deben decir `healthy` o `running`, y el `curl` devolver
`{"status": "ok", "database": "ok"}`.

## 5. Primer barrido

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py shell -c "from apps.scraping.tasks import scan_all_monitored; print(scan_all_monitored.delay())"
```

Son unas 1.300 consultas, alrededor de 2 horas. Seguilo con:

```bash
docker compose -f docker-compose.prod.yml logs -f worker-scraping
```

De ahí en más, beat lo dispara solo a las 06:00 y 18:00 (hora Perú).

## 6. Exponer el sitio: Cloudflare Tunnel

**No hace falta nginx ni certbot.** El compose publica el puerto solo en
`127.0.0.1:8000`, y `cloudflared` abre una conexión **saliente** hacia
Cloudflare: el VPS no recibe conexiones entrantes y no hay puerto 80/443
abierto en el firewall. Tampoco hay certificados que renovar — el TLS termina
en Cloudflare.

El paso a paso completo (DNS, reglas de caché, WAF, token de purga) está en
**[DEPLOY-WEB.md](DEPLOY-WEB.md)**. Lo mínimo:

```bash
cloudflared tunnel login
cloudflared tunnel create vueloradar
cloudflared tunnel route dns vueloradar vueloradar.com
```

`/etc/cloudflared/config.yml`:

```yaml
tunnel: vueloradar
credentials-file: /etc/cloudflared/vueloradar.json
ingress:
  - hostname: vueloradar.com
    service: http://localhost:8000
  - service: http_status:404
```

```bash
cloudflared service install
systemctl enable --now cloudflared
```

**Un solo túnel sirve varios proyectos**: se agregan más `hostname` al
`ingress`, cada uno apuntando al puerto local de su compose.

### Bot por webhook

En modo polling el bot no necesita nada de esto. Si lo pasás a webhook,
agregá su hostname al mismo `ingress`:

```yaml
  - hostname: bot.vueloradar.com
    service: http://localhost:8080
```

y publicá el puerto 8080 del servicio `bot` en el compose, con:

```bash
BOT_MODE=webhook
BOT_WEBHOOK_URL=https://bot.vueloradar.com/telegram/webhook
BOT_WEBHOOK_SECRET=<cadena aleatoria>
```

## 7. Backups

Un `pg_dump` corre solo todos los días a las 04:00 hacia el volumen `backups`,
con retención de 14 días. **Es la única copia propia del dato**: el free tier de
Supabase no incluye backups restaurables a demanda.

### Copia fuera del servidor (R2)

Un backup que vive en el mismo disco que la aplicación no protege del caso más
probable: perder el servidor. Si configurás estas variables, cada dump se sube
a R2 de Cloudflare apenas se genera:

```bash
R2_ACCOUNT_ID=<id de cuenta de Cloudflare>
R2_ACCESS_KEY_ID=<token de R2>
R2_SECRET_ACCESS_KEY=<secreto de R2>
R2_BUCKET=vueloradar-backups
R2_PREFIX=backups/
```

Sin ellas la subida no ocurre y el backup local sigue funcionando igual. **Con
ellas configuradas, un fallo de subida te llega por Telegram**: en un
filesystem efímero eso significaría que no quedó ninguna copia.

Ver qué hay:

```bash
docker compose -f docker-compose.prod.yml exec worker-default ls -lh /backups
```

Forzar uno ahora:

```bash
docker compose -f docker-compose.prod.yml exec worker-default python manage.py shell -c "from apps.scraping.maintenance import backup_database; print(backup_database.delay().get(timeout=1800))"
```

### Restaurar

**Verificado el 2026-08-27.** Se restauró un dump completo en un PostgreSQL
limpio y las cinco tablas coincidieron fila por fila con producción
(14.435 snapshots, 84.573 ofertas, 44 rutas, 20 aeropuertos, 40 estadísticas).

> **El dump necesita PostgreSQL 17 o superior para restaurarse.** Supabase corre
> 17.6 y el `pg_dump` de la imagen es 17.11, así que el formato del archivo es
> la versión 1.16. Intentar restaurarlo con PostgreSQL 16 falla con
> `unsupported version (1.16) in file header`. Esto solo aparece al probar una
> restauración de verdad: el backup se genera sin quejarse igual.

Procedimiento verificado, sin tocar producción:

```bash
DUMP=$(docker compose -f docker-compose.prod.yml exec -T worker-default ls /backups | tr -d '' | tail -1)
docker compose -f docker-compose.prod.yml cp worker-default:/backups/$DUMP /tmp/$DUMP

docker run -d --name pg-restore-test -e POSTGRES_PASSWORD=prueba   -e POSTGRES_DB=verificacion postgres:17-alpine
docker cp /tmp/$DUMP pg-restore-test:/tmp/dump
docker exec pg-restore-test pg_restore --no-owner --no-privileges   -U postgres -d verificacion /tmp/dump

docker exec pg-restore-test psql -U postgres -d verificacion -c   "SELECT count(*) FROM flights_pricesnapshot;"

docker rm -f pg-restore-test && rm -f /tmp/$DUMP
```

`pg_restore` reporta **3 errores ignorados** sobre `vault.secrets`: es un esquema
interno de Supabase (bóveda de secretos cifrados) que no nos pertenece y no se
puede restaurar. No afecta a ninguna tabla del proyecto.

```bash
docker compose -f docker-compose.prod.yml cp worker-default:/backups/vueloradar-20260823-040000.dump ./restore.dump
```

```bash
pg_restore --no-owner --no-privileges --clean --if-exists -d "postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres" restore.dump
```

El `--clean --if-exists` borra los objetos antes de recrearlos. **Nunca lo
corras contra la base de producción sin haberlo probado antes en otra**, porque
elimina las tablas actuales.

**Errores esperables al restaurar en un Postgres que no es Supabase:** vas a ver
2 o 3 errores sobre `supabase_vault` y `vault.secrets`. Son objetos de la
plataforma Supabase, no datos del proyecto — el dump los incluye pero un
Postgres vanilla no los tiene. `pg_restore` los ignora y sigue. Lo que importa
es que las tablas del proyecto queden completas:

```bash
psql -d restauracion -c "select count(*) from flights_pricesnapshot"
```

Verificado el 2026-08-23 contra un `postgres:17-alpine` limpio: los 8.289
snapshots, 47.395 ofertas, 44 rutas y 20 aeropuertos restauraron intactos.

## 8. Runbook de incidentes

### "El bot dice que no hay vuelos en una ruta que sí existe"

Primero descartá lo obvio: **¿faltan migraciones?** Es la causa más común y la
más silenciosa. El código pide una columna que la base todavía no tiene, la
consulta revienta y el usuario ve "no encontré vuelos".

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py showmigrations | grep "\[ \]"
```

Si sale algo, aplicalas:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py migrate
```

**Después de cada `git pull` corré `migrate`.** El `build` no lo hace solo.

Desde la corrección del 2026-08-23 el bot distingue los dos casos: si la falla
es del sistema responde "se me rompió algo de mi lado", no "no hay vuelos". Si
ves ese mensaje, mirá los logs del bot.

### "El bot no responde"

```bash
docker compose -f docker-compose.prod.yml logs --tail=100 bot
```

- **Estado `unhealthy`**: el heartbeat quedó viejo, el polling se colgó.
  Reiniciá el servicio `bot`.
- **`TelegramConflictError`**: hay dos instancias del bot con el mismo token
  (lo típico: quedó uno corriendo en tu máquina). Matá el otro.
- **`Unauthorized`**: el token es inválido o lo revocaste en BotFather.
- **Responde pero no busca**: fijate si Redis está sano, porque el semáforo
  global vive ahí (`redis-cli ping` dentro del contenedor `redis`).

### "Google Flights cambió y fast-flights devuelve vacío"

Síntoma: `google_flights: 0 ofertas` en todas las rutas, y a las pocas horas
llega la alerta del healthcheck.

Primero confirmá que no sea un problema de red:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py search_flights LIM CUZ 2026-12-01
```

Si devuelve vacío para una ruta que seguro tiene vuelos, Google cambió el
formato. **Solo hay un archivo que tocar**:
`apps/scraping/providers/google_flights.py`. Los índices del payload JS están
documentados arriba del archivo. Para ver la estructura nueva:

```python
from fast_flights import FlightQuery, Passengers, create_query, fetch_flights_html
from selectolax.lexbor import LexborHTMLParser
import json
q = create_query(flights=[FlightQuery(date="2026-12-01", from_airport="LIM", to_airport="CUZ")],
                 trip="one-way", seat="economy", passengers=Passengers(adults=1),
                 currency="PEN", language="es")
html = fetch_flights_html(q)
js = LexborHTMLParser(html).css_first(r"script.ds\:1").text()
payload = json.loads(js.split("data:", 1)[1].rsplit(",", 1)[0])
print(json.dumps(payload[3][0][0], indent=1)[:2000])
```

Mientras lo arreglás, activá los scrapers directos como paliativo:
`ENABLE_SKY_SCRAPER=True` y marcá las rutas prioritarias con
`use_direct_scrapers=True` desde el admin.

### "Sky o JetSmart bloquearon el scraper"

Síntoma: `sin resultados visibles` o `posible challenge anti-bot` en los logs.

Mirá el screenshot, que existe exactamente para esto:

```bash
docker compose -f docker-compose.prod.yml exec worker-default ls -lt /tmp/scraper_fails
```

```bash
docker compose -f docker-compose.prod.yml cp worker-default:/tmp/scraper_fails/ARCHIVO.png .
```

- **Página de error de la aerolínea**: cambió la URL o sus parámetros. Están en
  las constantes al inicio de `sky.py` / `jetsmart.py`.
- **Captcha o "Client Challenge"**: te detectaron. Bajá la frecuencia, o
  desactivá el flag y viví con Google Flights. Pelear contra esto no rinde.
- **Página correcta pero sin tarjetas**: cambiaron el DOM. Actualizá
  `FLIGHT_CARD` en el archivo del provider.

### "La fuente quedó pausada"

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py shell -c "from apps.scraping import ratelimit; print('pausada:', ratelimit.is_paused('google_flights'), 'fallos:', ratelimit.failure_count('google_flights'))"
```

Destrabar a mano:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py shell -c "from apps.scraping import ratelimit; ratelimit.resume('google_flights')"
```

### "Se acabaron las conexiones a Supabase"

Síntoma: `FATAL: too many connections` o timeouts al conectar.

El free tier del pooler da poco margen. El presupuesto está calculado para
quedar por debajo de unas 15 conexiones: `web` 2 workers, `worker-scraping` 1,
`worker-default` 2, `beat` 1, `bot` 1, con `CONN_MAX_AGE=60`. Si subiste alguna
concurrencia, bajala. Si el proyecto creció de verdad, las salidas son el plan
Pro de Supabase o mover la base al VPS.

### "El sistema dejó de acumular histórico"

El healthcheck avisa solo, cada 30 minutos, si el último snapshot tiene más de
8 horas. Diagnóstico rápido:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py shell -c "from apps.flights.models import PriceSnapshot; s=PriceSnapshot.objects.order_by('-snapshot_at').first(); print(s.snapshot_at if s else 'ninguno')"
```

```bash
docker compose -f docker-compose.prod.yml exec redis redis-cli LLEN scraping
```

Cola en cero y sin snapshots nuevos significa que beat no está encolando;
revisá los logs de `beat`. Cola llena y sin snapshots significa que el worker
está trabado; reinicialo.

## 9. Operación diaria

```bash
docker compose -f docker-compose.prod.yml ps
```

```bash
docker compose -f docker-compose.prod.yml logs -f --tail=100 worker-scraping
```

Métricas desde Telegram: mandale `/stats` al bot (solo responde a tu chat ID).

Actualizar el código:

```bash
git pull && docker compose -f docker-compose.prod.yml build && docker compose -f docker-compose.prod.yml up -d && docker compose -f docker-compose.prod.yml exec web python manage.py migrate
```

Matar cualquier contenedor es seguro: todos tienen `restart: unless-stopped` y
las tasks usan `acks_late`, así que una task a medio hacer se re-encola en vez
de perderse.

## 10. Checklist pre-lanzamiento

- [ ] `.env` sin ningún valor de ejemplo, y sin `$` ni `%` en el `SECRET_KEY`
- [ ] `DJANGO_DEBUG=False`, `ALLOWED_HOSTS` correcto, `SECRET_KEY` nueva
- [ ] `DJANGO_ADMIN_PATH` cambiado y contraseña de superusuario fuerte
- [ ] `manage.py check --deploy` sin issues
- [ ] `python scripts/check_production.py` en verde — renderiza las páginas
      con los ajustes reales y resuelve el manifiesto de estáticos, que
      `check --deploy` no toca (ver `DEPLOY-WEB.md`)
- [ ] `/healthz` devuelve `database: ok`
- [ ] **Un backup restaurado con éxito al menos una vez** (paso 7)
- [ ] Barrido completo corriendo 48h sin intervención
- [ ] `/stats` responde en Telegram con números coherentes
- [ ] Costo de IA del mes revisado en el admin (`AIUsageLog`) contra tu presupuesto
