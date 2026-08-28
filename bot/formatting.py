"""Armado de los mensajes que ve el usuario en Telegram (parse_mode HTML)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.utils import timezone

MEDALLAS = ["🥇", "🥈", "🥉"]

MESES = [
    "ene", "feb", "mar", "abr", "may", "jun",
    "jul", "ago", "set", "oct", "nov", "dic",
]
DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def escape(text: str) -> str:
    """Escapa lo que Telegram interpreta como HTML."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def format_date(value: date) -> str:
    return f"{DIAS[value.weekday()]} {value.day} {MESES[value.month - 1]}"


def searching_message(origin: str, dest: str, flight_date: date) -> str:
    return (
        f"🔎 Buscando vuelos <b>{escape(origin)} → {escape(dest)}</b> "
        f"para el {format_date(flight_date)}…"
    )


def format_results(
    *,
    origin: str,
    dest: str,
    flight_date: date,
    offers: list,
    stats=None,
    limit: int = 5,
) -> str:
    """Mensaje con las N ofertas más baratas y el contexto de precio."""
    if not offers:
        return no_results_message(origin, dest, flight_date)

    lineas = [
        f"✈️ <b>{escape(origin)} → {escape(dest)}</b> · {format_date(flight_date)}",
        "<i>precios finales, con impuestos incluidos</i>",
        "",
    ]

    for i, offer in enumerate(offers[:limit]):
        prefijo = MEDALLAS[i] if i < len(MEDALLAS) else f"{i + 1}."
        lineas.append(f"{prefijo} {_offer_line(offer)}")

    contexto = _price_context(offers[0].price_pen, stats)
    if contexto:
        lineas += ["", contexto]

    enlace = buy_link([(origin, dest, flight_date)])
    if enlace:
        lineas += ["", enlace]

    lineas += ["", f"🔔 /alerta {escape(origin)} {escape(dest)} para avisarte si baja más."]
    return "\n".join(lineas)


def no_results_message(origin: str, dest: str, flight_date: date) -> str:
    return (
        f"😕 No encontré vuelos <b>{escape(origin)} → {escape(dest)}</b> "
        f"para el {format_date(flight_date)}.\n\n"
        f"Probá otra fecha, o revisá que la ruta exista: no todas las ciudades "
        f"tienen vuelo entre sí."
    )


def format_flexible_results(
    *, origin: str, dest: str, best_by_day: dict, target: date
) -> str:
    """Vista compacta: el mejor precio de cada día del rango."""
    if not best_by_day:
        return no_results_message(origin, dest, target)

    fechas = sorted(best_by_day)
    mejor_dia = min(best_by_day, key=lambda d: best_by_day[d])

    lineas = [
        f"📅 <b>{escape(origin)} → {escape(dest)}</b>, "
        f"del {fechas[0].day} al {format_date(fechas[-1])} (mejor por día):",
        "",
    ]

    for fecha in fechas:
        estrella = " ⭐" if fecha == mejor_dia else ""
        lineas.append(f"<b>{fecha.day:02d}</b>: S/ {_money(best_by_day[fecha])}{estrella}")

    lineas += [
        "",
        f"El {format_date(mejor_dia)} es el más barato: "
        f"<b>S/ {_money(best_by_day[mejor_dia])}</b>.",
        f"Pedime <code>/vuelo {escape(origin)} {escape(dest)} {mejor_dia.isoformat()}</code> "
        f"para ver los vuelos de ese día.",
    ]
    return "\n".join(lineas)


def format_routes(rows: list) -> str:
    """Listado de rutas monitoreadas con su mínimo actual."""
    if not rows:
        return "Todavía no hay rutas monitoreadas con histórico."

    lineas = ["🗺 <b>Rutas monitoreadas</b>", ""]
    for row in rows:
        precio = f"S/ {_money(row['min_price'])}" if row["min_price"] is not None else "sin datos"
        lineas.append(
            f"<code>{escape(row['origin'])}→{escape(row['dest'])}</code>  "
            f"{escape(row['origin_city'])} a {escape(row['dest_city'])} · {precio}"
        )
    lineas += ["", "Usá <code>/vuelo LIM CUZ 2026-09-15</code> para buscar en cualquiera."]
    return "\n".join(lineas)


def quota_exceeded_message(limit: int) -> str:
    return (
        f"🚫 Llegaste a tus <b>{limit} búsquedas diarias</b> del plan gratuito.\n\n"
        f"El contador se reinicia mañana. Si necesitás búsquedas ilimitadas y "
        f"alertas de bajada de precio, el plan Premium está en camino."
    )


def welcome_message(first_name: str) -> str:
    saludo = f"¡Hola {escape(first_name)}!" if first_name else "¡Hola!"
    return (
        f"{saludo} Soy <b>VueloRadar</b> 🛫\n\n"
        f"Busco el vuelo más barato en cualquier ruta doméstica del Perú y "
        f"acumulo histórico de precios para saber si lo que ves hoy es una oferta "
        f"o un precio normal.\n\n"
        f"<b>Escribime como quieras:</b>\n"
        f"• <i>vuelo de Lima a Cusco el 15 de setiembre</i>\n"
        f"• <i>de Arequipa a Lima la próxima semana</i>\n"
        f"• <i>quiero ir a Puerto Maldonado el 20</i>\n\n"
        f"<b>O usá comandos:</b>\n"
        f"<code>/vuelo LIM CUZ 2026-09-15</code> — búsqueda directa\n"
        f"<code>/rutas</code> — rutas monitoreadas y su precio mínimo\n"
        f"<code>/alerta LIM CUZ</code> — avisos cuando aparezca una oferta real\n"
        f"<code>/ayuda</code> — más detalles"
    )


def welcome_from_route(first_name: str, route, alerta: dict | None = None) -> str:
    """Bienvenida para quien llega desde el botón «Notifícame» de la web.

    La persona ya dijo qué ruta le importa y ya pidió el aviso: el mensaje
    confirma lo que se hizo en vez de pedirle que repita la decisión
    escribiendo un comando. Ahí es donde se caía la conversión.
    """
    saludo = f"¡Hola {escape(first_name)}!" if first_name else "¡Hola!"
    origen = escape(route.origin.city)
    destino = escape(route.destination.city)
    estado = (alerta or {}).get("status")

    if estado == "ok":
        verbo = "Listo, te aviso" if alerta.get("created", True) else "Ya tenías ese aviso activo"
        cuerpo = (
            f"✅ {verbo} cuando <b>{origen} → {destino}</b> tenga una oferta real: "
            f"un precio en el 10% más barato del último mes para esa ruta.\n\n"
            f"Te quedan <b>{alerta.get('remaining', 0)}</b> alertas disponibles.\n"
            f"Con /misalertas las ves y podés desactivarlas."
        )
    elif estado == "limit_reached":
        cuerpo = (
            f"Quería avisarte de <b>{origen} → {destino}</b>, pero ya llegaste a tus "
            f"<b>{alerta.get('limit')} alertas activas</b>.\n\n"
            f"Desactivá alguna con /misalertas y volvé a tocar el botón."
        )
    else:
        cuerpo = (
            f"Veo que venís por <b>{origen} → {destino}</b>.\n\n"
            f"Para que te avise cuando baje: "
            f"<code>/alerta {route.origin_id} {route.destination_id}</code>"
        )

    return (
        f"{saludo} 🛫\n\n"
        f"{cuerpo}\n\n"
        f"También podés buscar una fecha concreta:\n"
        f"<code>/vuelo {route.origin_id} {route.destination_id} 2026-09-15</code>\n"
        f"<code>/ayuda</code> para ver todo lo que hago."
    )


def help_message(limit: int) -> str:
    return (
        f"<b>Cómo usarme</b>\n\n"
        f"<b>Lenguaje natural.</b> Escribime la ruta y la fecha como se te ocurra:\n"
        f"<i>vuelo de lima a piura el viernes</i>\n"
        f"<i>de cusco a arequipa la próxima semana</i>\n\n"
        f"Si decís algo vago como «esa semana» o «alrededor del 15», "
        f"te muestro el mejor precio de cada día del rango.\n\n"
        f"<b>Comandos</b>\n"
        f"<code>/vuelo ORIGEN DESTINO FECHA</code> — ej. <code>/vuelo LIM CUZ 2026-09-15</code>\n"
        f"<code>/rutas</code> — rutas monitoreadas con su mínimo actual\n"
        f"<code>/alerta LIM CUZ</code> — te aviso de cualquier oferta real\n"
        f"<code>/alerta LIM CUZ 180</code> — te aviso si baja de S/ 180\n"
        f"<code>/misalertas</code> — ver y desactivar tus alertas\n"
        f"<code>/premium</code> — quitar los límites, con estrellas de Telegram\n"
        f"<code>/ayuda</code> — este mensaje\n\n"
        f"<b>Tu plan</b>\n"
        f"Gratis: {limit} búsquedas por día y 2 alertas. Premium: sin límites.\n"
        f"El veredicto y los avisos funcionan igual en los dos.\n\n"
        f"<b>Sobre los precios</b>\n"
        f"Son <b>finales, con impuestos</b>: incluyen la tarifa base, el IGV del 18% "
        f"y la tasa de aeropuerto (TUUA). Es lo que pagás por el pasaje.\n\n"
        f"Lo que NO incluyen: equipaje en bodega, selección de asiento ni otros "
        f"extras que cada aerolínea cobra aparte. Una tarifa de S/ 200 en JetSmart "
        f"o Sky suele ser solo mochila; si viajás con maleta, sumale entre S/ 40 y "
        f"S/ 90 según la aerolínea.\n\n"
        f"El dato sale de Google Flights. A veces la app de la aerolínea tiene "
        f"promos que Google no ve, así que conviene comparar antes de pagar."
    )


def alert_placeholder_message(origin: str = "", dest: str = "") -> str:
    ruta = f" para <b>{escape(origin)} → {escape(dest)}</b>" if origin and dest else ""
    return (
        f"🔔 Las alertas{ruta} están en camino.\n\n"
        f"Todavía estoy acumulando histórico: con 2 o 3 semanas de datos puedo "
        f"distinguir una oferta real de un precio normal. Mientras tanto, "
        f"buscá cuando quieras y te digo cómo está el precio contra el promedio."
    )


def usage_hint_message() -> str:
    return (
        "🤔 No entendí qué vuelo buscás.\n\n"
        "Probá con algo como <i>«vuelo de Lima a Cusco el 15 de setiembre»</i>, "
        "o usá el formato exacto:\n"
        "<code>/vuelo LIM CUZ 2026-09-15</code>\n\n"
        "Con <code>/rutas</code> ves los códigos disponibles."
    )


def missing_fields_message(missing: list) -> str:
    faltan = " y ".join(missing) if len(missing) <= 2 else ", ".join(missing[:-1]) + f" y {missing[-1]}"
    return (
        f"Casi. Me falta {faltan}.\n\n"
        f"Decímelo completo, por ejemplo <i>«de Lima a Cusco el 15 de setiembre»</i>, "
        f"o usá <code>/vuelo LIM CUZ 2026-09-15</code>."
    )


# ------------------------------------------------------------------- internos
def _offer_line(offer) -> str:
    partes = [f"<b>S/ {_money(offer.price_pen)}</b>", escape(offer.airline or "aerolínea s/d")]
    horario = _schedule(offer)
    if horario:
        partes.append(horario)
    partes.append(_stops(offer))
    return " · ".join(partes)


def _schedule(offer) -> str:
    if offer.departure_dt is None or offer.arrival_dt is None:
        return ""
    salida = timezone.localtime(offer.departure_dt).strftime("%H:%M")
    llegada = timezone.localtime(offer.arrival_dt).strftime("%H:%M")
    return f"{salida}→{llegada}"


def _stops(offer) -> str:
    if offer.stops is None:
        return "escalas s/d"
    if offer.stops == 0:
        return "directo"
    if offer.pk is None:
        return "1 escala vía LIM"
    return "1 escala" if offer.stops == 1 else f"{offer.stops} escalas"


def _price_context(best_price, stats) -> str:
    """La línea 📊. Se omite entera si no hay histórico confiable."""
    if stats is None or not stats.avg_30d or not stats.has_enough_history:
        return ""

    avg = Decimal(stats.avg_30d)
    if avg <= 0:
        return ""

    diff = (avg - Decimal(best_price)) / avg * 100
    pct = abs(int(diff.quantize(Decimal("1"))))

    if diff >= 15:
        veredicto = f"el mejor precio está <b>{pct}% por debajo</b>. Buen momento."
    elif diff >= 5:
        veredicto = f"el mejor precio está {pct}% por debajo. Precio decente."
    elif diff > -5:
        veredicto = "el mejor precio está en el promedio. Nada especial."
    else:
        veredicto = f"el mejor precio está <b>{pct}% por encima</b>. Conviene esperar."

    return f"📊 Promedio 30d de la ruta: S/ {_money(avg)} — {veredicto}"


def _money(value) -> str:
    valor = Decimal(value)
    return f"{valor:,.0f}" if valor % 1 == 0 else f"{valor:,.2f}"


def alert_created_message(origin, dest, target_price, flight_date, *, created: bool, remaining: int) -> str:
    verbo = "Listo, alerta creada" if created else "Ya tenías esa alerta, la dejé activa"
    cuando = f" para el {flight_date.strftime('%d/%m/%Y')}" if flight_date else ""

    if target_price is not None:
        que = f"si <b>{escape(origin)} → {escape(dest)}</b> baja de <b>S/ {_money(target_price)}</b>"
    else:
        que = (
            f"cuando <b>{escape(origin)} → {escape(dest)}</b> tenga una oferta real "
            f"(precio en el 10% más barato del último mes)"
        )

    return (
        f"✅ {verbo}. Te aviso {que}{cuando}.\n\n"
        f"Te quedan <b>{remaining}</b> alertas disponibles.\n"
        f"Con /misalertas las ves y podés desactivarlas."
    )


def alert_limit_message(limit: int) -> str:
    return (
        f"🚫 Llegaste a tus <b>{limit} alertas activas</b>.\n\n"
        f"Desactivá alguna con /misalertas para hacer lugar, o pasate a Premium "
        f"cuando esté disponible."
    )


def alerts_list_message(alertas: list) -> str:
    lineas = ["🔔 <b>Tus alertas activas</b>", ""]
    for a in alertas:
        lineas.append(f"• {escape(a['describe'])}")
    lineas += ["", "Tocá una para desactivarla."]
    return "\n".join(lineas)


def no_alerts_message() -> str:
    return (
        "No tenés alertas activas.\n\n"
        "<code>/alerta LIM CUZ</code> — te aviso de cualquier oferta\n"
        "<code>/alerta LIM CUZ 180</code> — te aviso si baja de S/ 180"
    )


def verdict_line(verdict) -> str:
    """La línea 🤖 que se agrega a los resultados de búsqueda."""
    if verdict is None:
        return ""
    return f"🤖 <b>Veredicto: {verdict.label}.</b> {escape(verdict.reason)}"


def stats_message(m: dict) -> str:
    """Panel del admin. Números crudos, sin adornos."""
    lineas = [
        "📈 <b>VueloRadar — estado</b>",
        "",
        "<b>Usuarios</b>",
        f"  total: {m['usuarios_total']} · activos hoy: {m['usuarios_activos_hoy']} · premium: {m['premium']}",
        f"  búsquedas hoy: {m['busquedas_hoy']}",
        "",
        "<b>Histórico</b>",
        f"  snapshots hoy: {m['snapshots_hoy']} · total: {m['snapshots_total']:,}",
        f"  último: {_ago(m['ultimo_snapshot'])}",
        "",
        "<b>Alertas</b>",
        f"  activas: {m['alertas_activas']} · disparadas 24h: {m['alertas_disparadas_24h']}",
        "",
        "<b>Scraping</b>",
        f"  fuente pausada: {'SÍ' if m['fuente_pausada'] else 'no'} · "
        f"fallos consecutivos: {m['fallos_fuente']}",
    ]

    if m["ia_mes"]:
        lineas += ["", "<b>IA este mes</b>"]
        for fila in m["ia_mes"]:
            lineas.append(
                f"  {fila['provider']}: {fila['llamadas']} llamadas · "
                f"{(fila['entrada'] or 0):,} in / {(fila['salida'] or 0):,} out"
            )
    else:
        lineas += ["", "<b>IA este mes</b>", "  sin llamadas registradas"]

    return "\n".join(lineas)


def busy_message(minutes: int = 2) -> str:
    return (
        f"⏳ Hay mucha demanda ahora mismo. Tu búsqueda entra en cola, "
        f"probá de nuevo en ~{minutes} min."
    )


def _ago(cuando) -> str:
    if cuando is None:
        return "nunca"
    from django.utils import timezone

    delta = timezone.now() - cuando
    horas = delta.total_seconds() / 3600
    if horas < 1:
        return f"hace {int(delta.total_seconds() / 60)} min"
    if horas < 48:
        return f"hace {horas:.0f}h"
    return f"hace {horas / 24:.0f} días"


def system_error_message() -> str:
    """Cuando la falla es nuestra, no de la ruta.

    Decir "no encontré vuelos" ante un error de sistema es mentirle al usuario:
    descarta una ruta que sí existe y nadie se entera de que algo se rompió.
    """
    return (
        "⚠️ Se me rompió algo de mi lado buscando esa ruta — no es que no haya "
        "vuelos.\n\n"
        "Ya quedó registrado y lo estoy revisando. Probá de nuevo en unos "
        "minutos."
    )


def format_round_trip(
    *,
    origin: str,
    dest: str,
    outbound_date: date,
    return_date: date,
    outbound: list,
    inbound: list,
) -> str:
    """Cotización de ida y vuelta: los dos tramos y el total del viaje."""
    if not outbound or not inbound:
        return _incomplete_round_trip(
            origin, dest, outbound_date, return_date, outbound, inbound
        )

    ida = min(outbound, key=lambda o: o.price_pen)
    vuelta = min(inbound, key=lambda o: o.price_pen)
    ida_directo = _cheapest_nonstop(outbound)
    vuelta_directo = _cheapest_nonstop(inbound)

    lineas = [
        f"🔄 <b>{escape(origin)} ⇄ {escape(dest)}</b>",
        f"{format_date(outbound_date)} · vuelta {format_date(return_date)}",
        "<i>precios finales, con impuestos incluidos</i>",
        "",
        f"<b>Ida</b> · {format_date(outbound_date)}",
        f"  {_trip_line(ida)}",
    ]
    if ida_directo is not None and ida_directo is not ida:
        lineas.append(f"  {_trip_line(ida_directo)}")

    lineas += [
        "",
        f"<b>Vuelta</b> · {format_date(return_date)}",
        f"  {_trip_line(vuelta)}",
    ]
    if vuelta_directo is not None and vuelta_directo is not vuelta:
        lineas.append(f"  {_trip_line(vuelta_directo)}")

    total = Decimal(ida.price_pen) + Decimal(vuelta.price_pen)
    lineas += ["", f"💰 <b>Total del viaje: S/ {_money(total)}</b>"]

    if ida_directo is not None and vuelta_directo is not None:
        total_directo = Decimal(ida_directo.price_pen) + Decimal(vuelta_directo.price_pen)
        if total_directo != total:
            extra = total_directo - total
            lineas.append(
                f"   Todo directo: S/ {_money(total_directo)} "
                f"(S/ {_money(extra)} más, sin escalas)"
            )

    enlace = buy_link(
        [(origin, dest, outbound_date), (dest, origin, return_date)],
        etiqueta="Comprar el ida y vuelta",
    )
    if enlace:
        lineas += ["", enlace]

    lineas += [
        "",
        "<i>Ese link ya busca el paquete completo, que suele salir menos que "
        "sumar dos pasajes sueltos. Los S/ de arriba son tu techo.</i>",
    ]
    return "\n".join(lineas)


def _incomplete_round_trip(origin, dest, outbound_date, return_date, outbound, inbound) -> str:
    """Un tramo sin vuelos: decir cuál, no dar un total incompleto."""
    faltante = "la ida" if not outbound else "la vuelta"
    fecha = outbound_date if not outbound else return_date
    encontrado = inbound if not outbound else outbound
    otro = "vuelta" if not outbound else "ida"

    lineas = [
        f"🔄 <b>{escape(origin)} ⇄ {escape(dest)}</b>",
        "",
        f"😕 No encontré vuelos para <b>{faltante}</b> del {format_date(fecha)}.",
    ]
    if encontrado:
        mejor = min(encontrado, key=lambda o: o.price_pen)
        lineas += [
            "",
            f"La {otro} sí: desde <b>S/ {_money(mejor.price_pen)}</b> "
            f"con {escape(mejor.airline or 'aerolínea s/d')}.",
        ]
    lineas += ["", "Probá otra fecha para el tramo que falta."]
    return "\n".join(lineas)


def _trip_line(offer) -> str:
    partes = [f"<b>S/ {_money(offer.price_pen)}</b>", escape(offer.airline or "aerolínea s/d")]
    horario = _schedule(offer)
    if horario:
        partes.append(horario)
    partes.append(_stops(offer))
    return " · ".join(partes)


def _cheapest_nonstop(offers: list):
    directos = [o for o in offers if o.stops == 0]
    return min(directos, key=lambda o: o.price_pen) if directos else None


def buy_link(legs: list, *, etiqueta: str = "Ver y comprar en Google Flights") -> str:
    """Link a Google Flights para los tramos dados.

    Con dos tramos invertidos arma la búsqueda de ida y vuelta, que es donde
    está el precio de paquete. Devuelve "" si no se pudo construir: un link
    roto no debe impedir que el usuario vea los precios.
    """
    from apps.scraping.providers.google_flights import build_search_url

    try:
        url = build_search_url(legs)
    except Exception:  # noqa: BLE001
        return ""
    if not url:
        return ""
    return f'🔗 <a href="{url}">{escape(etiqueta)}</a>'


# --- premium -----------------------------------------------------------------

def premium_offer(estado: dict) -> str:
    """La oferta de premium.

    Se listan primero los límites reales del plan gratis y después lo que
    cambia: vender "acceso premium" sin decir qué se está limitando hoy es
    justamente lo que hace que la gente desconfíe de un botón de pago.
    """
    from django.conf import settings

    from apps.users.payments import PLANES

    if estado.get("es_premium"):
        dias = estado.get("dias_restantes")
        cabecera = [
            "⭐ <b>Ya sos premium</b>",
            (f"Te quedan <b>{dias} días</b>." if dias is not None
             else "Tu acceso no tiene fecha de vencimiento."),
            "",
            "Si comprás de nuevo, los días se <b>suman</b> a los que ya tenés.",
            "",
        ]
    else:
        cabecera = [
            "⭐ <b>VueloRadar Premium</b>",
            "",
            f"<b>Gratis</b> tenés {settings.FREE_DAILY_SEARCHES} búsquedas por día "
            f"y {settings.FREE_MAX_ALERTS} alertas activas.",
            "",
            "<b>Con premium:</b>",
            "· Búsquedas sin límite",
            f"· Hasta {settings.PREMIUM_MAX_ALERTS} alertas a la vez",
            "",
            # Decir qué NO cambia vale más que inflar la lista. El veredicto y
            # los avisos ya funcionan gratis; venderlos como exclusivos sería
            # cobrar por algo que la persona ya tiene, y este producto vive de
            # que se le crea.
            "<i>El veredicto de compra, el histórico y los avisos funcionan "
            "igual en el plan gratis. Premium solo quita los límites.</i>",
            "",
        ]

    planes = [
        f"· <b>{p.titulo}</b> — {p.estrellas} ⭐  "
        f"({p.por_mes:g} ⭐ por mes)"
        for p in PLANES.values()
    ]

    return "\n".join([
        *cabecera,
        *planes,
        "",
        "<i>Se paga con estrellas de Telegram, desde la misma app. "
        "No pedimos tarjeta ni datos: nosotros nunca vemos tu medio de pago.</i>",
    ])


def premium_gracias(acreditacion) -> str:
    """Confirmación del pago."""
    if acreditacion.ya_estaba_acreditado:
        # El mismo pago llegó dos veces. Se le dice la verdad en vez de
        # simular que sumó días: si cree que pagó dos meses y tiene uno, el
        # reclamo llega igual, solo que más tarde y con menos confianza.
        return (
            "Ese pago ya estaba acreditado, así que no te cobramos de nuevo.\n"
            f"Tu premium sigue vigente hasta el <b>{format_date(acreditacion.hasta)}</b>."
        )

    return "\n".join([
        "⭐ <b>¡Listo! Ya sos premium.</b>",
        "",
        f"Sumaste <b>{acreditacion.dias} días</b>. "
        f"Vence el <b>{format_date(acreditacion.hasta)}</b>.",
        "",
        "Búsquedas sin límite y alertas ampliadas desde este momento. "
        "Probá con <code>/vuelo LIM CUZ 15/10</code> o creá una alerta.",
        "",
        "<i>Si algo no funciona como esperabas, escribinos y te devolvemos "
        "las estrellas.</i>",
    ])


def premium_error() -> str:
    """El pago entró pero no se pudo acreditar. Nunca callar esto."""
    return (
        "⚠️ Recibimos tu pago pero no pudimos activarte el premium "
        "automáticamente.\n\n"
        "Ya quedó registrado y lo vamos a resolver a mano. Escribinos y, si "
        "preferís, te devolvemos las estrellas."
    )
