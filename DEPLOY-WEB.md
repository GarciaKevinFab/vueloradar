# Capa web pública sobre Cloudflare

Complementa `DEPLOY.md` (que cubre el stack de Docker en el VPS). Acá va solo
lo que agrega el sitio público: dominio, borde, caché y protección.

## Arquitectura

```
        visitante / Googlebot
                 │
                 ▼
     ┌───────────────────────────┐
     │        CLOUDFLARE         │
     │  Registrar (vueloradar.com)│
     │  DNS + CDN + caché         │
     │  WAF + rate limiting       │
     │  SSL (Full strict)         │
     └─────────────┬─────────────┘
                   │  Tunnel saliente (cloudflared)
                   ▼            sin puertos abiertos, sin IP expuesta
     ┌───────────────────────────┐
     │           VPS             │
     │  web (Django/gunicorn)    │
     │  workers · beat · bot     │
     │  redis                    │
     └─────────────┬─────────────┘
                   ▼
              Supabase (PG)
```

**Por qué Tunnel y no un A record:** el VPS no abre el 80/443 a internet, no
hay IP de origen que descubrir y no hay certificados de origen que renovar.
`cloudflared` abre la conexión hacia afuera.

**Por qué el caché importa acá:** las páginas declaran `s-maxage=1800`. Entre
barridos las sirve el borde entero y el VPS no recibe tráfico. Al terminar
cada `compute_route_stats` purgamos la zona (`apps/web/cloudflare.py`), así
que los datos nuevos aparecen al instante sin esperar el TTL.

## 1. Dominio

`vueloradar.com` — mantiene la marca del bot ([@Vuelosradar_bot](https://t.me/Vuelosradar_bot)).
Cloudflare Registrar vende `.com` a precio de costo y con WHOIS privado
incluido, así que registro y gestión quedan en el mismo panel.

> `.pe` **no** está en el registrar de Cloudflare. Si además querés
> `vueloradar.pe`, hay que registrarlo en un registrador `.pe` (punto.pe) y
> delegar los nameservers a Cloudflare: se gestiona igual, solo cambia dónde
> se paga la renovación. Verificá disponibilidad ahí, no por DNS.

## 2. DNS

Con Tunnel no se crea el registro a mano: `cloudflared` publica un CNAME
proxied (nube naranja) apuntando al túnel.

| Nombre | Tipo | Destino | Proxy |
|---|---|---|---|
| `vueloradar.com` | CNAME | `<id>.cfargotunnel.com` | sí |
| `www` | CNAME | `vueloradar.com` | sí |

Redirigí `www` al apex con una Redirect Rule (301) para no partir la señal SEO.

## 3. Túnel al VPS

En el VPS, junto al resto del stack:

```bash
cloudflared tunnel login
cloudflared tunnel create vueloradar
cloudflared tunnel route dns vueloradar vueloradar.com
```

`~/.cloudflared/config.yml`:

```yaml
tunnel: vueloradar
credentials-file: /etc/cloudflared/vueloradar.json
ingress:
  - hostname: vueloradar.com
    service: http://web:8000
  - service: http_status:404
```

## 4. SSL/TLS

- Modo **Full (strict)**.
- **Always Use HTTPS**: on.
- **Automatic HTTPS Rewrites**: on.
- HSTS: activar recién cuando el sitio esté estable en HTTPS.

Django ya trae `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")`,
que es lo que hace falta detrás del proxy.

## 5. Caché

Cloudflare **no cachea HTML por defecto**. Sin esto, nada de lo anterior sirve.

Cache Rule — *Cachear páginas públicas*:

- **Si**: `(http.request.uri.path eq "/") or (starts_with(http.request.uri.path, "/vuelos/")) or (http.request.uri.path in {"/sitemap.xml" "/robots.txt"})`
- **Entonces**: Eligible for cache → **Respect origin TTL**

Respetar el TTL de origen es deliberado: el `s-maxage=1800` de las vistas es la
única fuente de verdad del TTL, y así no hay dos números que mantener.

Y una regla de **bypass** para lo que nunca debe cachearse:

- **Si**: `starts_with(http.request.uri.path, "/<DJANGO_ADMIN_PATH>/") or (http.request.uri.path eq "/healthz")`
- **Entonces**: Bypass cache

## 6. Protección — con cuidado

El sitio **vive de que lo rastreen**. Configuración agresiva de bots = suicidio SEO.

- **NO** actives *Under Attack Mode* ni challenges en `/` o `/vuelos/*`.
- **Verified bots**: permitidos siempre (Googlebot, Bingbot).
- WAF Rule — *proteger el admin*: si `starts_with(http.request.uri.path, "/<DJANGO_ADMIN_PATH>/")`
  y `ip.src` no está en tu lista, → **Block**.
- Rate limiting (1 regla gratis): `/vuelos/*` a 60 req/min por IP → Managed Challenge.
  Frena raspado del histórico sin tocar a Google.

## 7. Token para la purga

Cloudflare → *My Profile* → *API Tokens* → **Create Token** → *Custom*:

- Permisos: **Zone → Cache Purge → Purge**
- Recursos: solo la zona `vueloradar.com`

Al `.env` del VPS:

```bash
SITE_NAME=VueloRadar
DJANGO_ALLOWED_HOSTS=vueloradar.com,www.vueloradar.com
CSRF_TRUSTED_ORIGINS=https://vueloradar.com,https://www.vueloradar.com
CLOUDFLARE_API_TOKEN=<token>
CLOUDFLARE_ZONE_ID=<zone id, en el Overview de la zona>
```

Sin estas dos últimas la purga no corre y solo lo anota en el log: el barrido
nunca se rompe por Cloudflare.

## 8. Comprobar que quedó bien

```bash
curl -sI https://vueloradar.com/vuelos/LIM-CUZ/ | grep -iE "cf-cache-status|cache-control"
```

- Primera vez: `cf-cache-status: MISS` · segunda: `HIT`.
- Después de un barrido, vuelve a `MISS` (la purga funcionó).
- `cache-control` debe traer `s-maxage=1800`.

Y en Google Search Console: agregar la propiedad, enviar
`https://vueloradar.com/sitemap.xml` y verificar por DNS (el registro TXT se
crea en el mismo panel de Cloudflare).
