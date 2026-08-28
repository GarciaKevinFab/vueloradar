"""Arranque del bot de Telegram.

Dos modos, elegidos con `BOT_MODE`:

- **polling** (default): el bot pregunta a Telegram. Simple, sin dominio ni
  certificado. Es lo que conviene en desarrollo.
- **webhook**: Telegram empuja los updates a un endpoint propio. Necesita
  dominio con HTTPS y un reverse proxy delante (ver DEPLOY.md). Escala mejor y
  responde más rápido, pero hay más piezas que pueden romperse.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from . import heartbeat

logger = logging.getLogger(__name__)


def build_dispatcher() -> Dispatcher:
    """Arma el dispatcher con sus middlewares y routers, en orden.

    El router de texto libre va último: recibe lo que no matcheó ningún comando.
    """
    from .handlers import admin, alerts, commands, natural, premium
    from .middlewares.antiflood import SingleFlightMiddleware
    from .middlewares.logging import LoggingMiddleware

    dispatcher = Dispatcher()

    dispatcher.message.middleware(LoggingMiddleware())
    dispatcher.message.middleware(SingleFlightMiddleware())

    dispatcher.include_router(admin.router)
    dispatcher.include_router(commands.router)
    # Antes que `natural`: ese captura texto suelto y se tragaría el mensaje de
    # servicio del pago si llegara primero.
    dispatcher.include_router(premium.router)
    dispatcher.include_router(alerts.router)
    dispatcher.include_router(natural.router)

    return dispatcher


def build_bot() -> Bot:
    token = settings.TELEGRAM_TOKEN
    if not token:
        raise ImproperlyConfigured(
            "Falta TELEGRAM_TOKEN en el .env. Pedile uno a @BotFather en Telegram."
        )
    return Bot(
        token=token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
            # Sin esto Telegram pega una tarjeta de Google debajo de cada
            # resultado y tapa los precios, que es lo que el usuario vino a ver.
            link_preview_is_disabled=True,
        ),
    )


#: El menú que aparece al tocar "/" en Telegram.
#:
#: Se publica desde acá y no a mano en BotFather porque si no, el menú y el
#: código se desincronizan sin que nadie lo note: el bot llevaba TODOS los
#: comandos sin registrar, así que quien abría el menú no veía ninguno y daba
#: por hecho que el bot no hacía nada. Un comando que existe pero no se anuncia
#: no existe para el usuario.
COMANDOS = [
    ("vuelo", "Buscar un vuelo: /vuelo LIM CUZ 15/10"),
    ("rutas", "Rutas monitoreadas y su precio mínimo"),
    ("alerta", "Avisarme cuando baje: /alerta LIM CUZ"),
    ("misalertas", "Ver y desactivar mis alertas"),
    ("premium", "Quitar los límites con estrellas de Telegram"),
    ("ayuda", "Cómo funciona y qué incluye el precio"),
]


async def publicar_comandos(bot: Bot) -> None:
    """Sincroniza el menú de Telegram con lo que el bot realmente entiende.

    Nunca lanza: quedarse sin menú es molesto, pero no arrancar el bot por eso
    sería peor.
    """
    from aiogram.types import BotCommand

    try:
        await bot.set_my_commands(
            [BotCommand(command=c, description=d) for c, d in COMANDOS]
        )
        logger.info("bot: menu de comandos publicado (%s)", len(COMANDOS))
    except Exception:  # noqa: BLE001
        logger.exception("bot: no se pudo publicar el menu de comandos")


async def run_polling() -> None:
    bot = build_bot()
    dispatcher = build_dispatcher()

    await publicar_comandos(bot)
    me = await bot.get_me()
    logger.info("bot: conectado como @%s (%s) en modo polling", me.username, me.id)

    latido = asyncio.create_task(heartbeat.run_forever())
    try:
        # Descarta los updates acumulados mientras el bot estuvo caído: son
        # búsquedas viejas que ya no le sirven a nadie.
        await bot.delete_webhook(drop_pending_updates=True)
        await dispatcher.start_polling(bot)
    finally:
        latido.cancel()
        await bot.session.close()


async def run_webhook() -> None:
    """Levanta un servidor aiohttp y registra el webhook en Telegram."""
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
    from aiohttp import web

    if not settings.BOT_WEBHOOK_URL:
        raise ImproperlyConfigured("BOT_MODE=webhook requiere BOT_WEBHOOK_URL")

    bot = build_bot()
    dispatcher = build_dispatcher()

    me = await bot.get_me()
    logger.info("bot: conectado como @%s (%s) en modo webhook", me.username, me.id)

    await bot.set_webhook(
        url=settings.BOT_WEBHOOK_URL,
        secret_token=settings.BOT_WEBHOOK_SECRET or None,
        drop_pending_updates=True,
    )
    logger.info("bot: webhook registrado en %s", settings.BOT_WEBHOOK_URL)

    app = web.Application()
    SimpleRequestHandler(
        dispatcher=dispatcher, bot=bot, secret_token=settings.BOT_WEBHOOK_SECRET or None
    ).register(app, path=settings.BOT_WEBHOOK_PATH)
    setup_application(app, dispatcher, bot=bot)

    app.router.add_get("/healthz", _webhook_health)

    latido = asyncio.create_task(heartbeat.run_forever())
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, settings.BOT_WEBHOOK_HOST, settings.BOT_WEBHOOK_PORT)
    await site.start()
    logger.info(
        "bot: escuchando en %s:%s%s",
        settings.BOT_WEBHOOK_HOST, settings.BOT_WEBHOOK_PORT, settings.BOT_WEBHOOK_PATH,
    )

    try:
        await asyncio.Event().wait()
    finally:
        latido.cancel()
        await runner.cleanup()
        await bot.session.close()


async def _webhook_health(request):
    from aiohttp import web

    return web.json_response({"status": "ok"})


async def run() -> None:
    if settings.BOT_MODE == "webhook":
        await run_webhook()
    else:
        await run_polling()


def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        logger.info("bot: detenido")
