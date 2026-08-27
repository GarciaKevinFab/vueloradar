"""Modelos del dominio de vuelos domésticos en Perú."""

from django.db import models


class Airport(models.Model):
    """Aeropuerto monitoreado, identificado por su código IATA."""

    iata_code = models.CharField("código IATA", max_length=3, primary_key=True)
    name = models.CharField("nombre", max_length=200)
    city = models.CharField("ciudad", max_length=100)
    region = models.CharField("región", max_length=100, blank=True)
    is_active = models.BooleanField("activo", default=True)

    class Meta:
        verbose_name = "aeropuerto"
        verbose_name_plural = "aeropuertos"
        ordering = ["iata_code"]

    def __str__(self) -> str:
        return f"{self.iata_code} — {self.city}"

    @property
    def slug(self) -> str:
        """Ciudad en forma de URL, para las páginas por origen.

        Se deriva en vez de guardarse: son 20 aeropuertos y una columna seria
        el mismo dato en dos lugares, con el riesgo de que se desincronicen.
        """
        from django.utils.text import slugify

        return slugify(self.city)


class Route(models.Model):
    """Ruta dirigida origen→destino. LIM→CUZ y CUZ→LIM son rutas distintas."""

    PRIORITY_HIGH = 1
    PRIORITY_MEDIUM = 2
    PRIORITY_LOW = 3
    PRIORITY_CHOICES = [
        (PRIORITY_HIGH, "Alta"),
        (PRIORITY_MEDIUM, "Media"),
        (PRIORITY_LOW, "Baja"),
    ]

    origin = models.ForeignKey(
        Airport, on_delete=models.CASCADE, related_name="routes_from", verbose_name="origen"
    )
    destination = models.ForeignKey(
        Airport, on_delete=models.CASCADE, related_name="routes_to", verbose_name="destino"
    )
    is_monitored = models.BooleanField("monitoreada", default=False)
    has_direct_flights = models.BooleanField("tiene vuelo directo", default=True)
    priority = models.PositiveSmallIntegerField(
        "prioridad", choices=PRIORITY_CHOICES, default=PRIORITY_LOW
    )
    #: Los scrapers directos son pesados; solo las rutas marcadas los pagan.
    use_direct_scrapers = models.BooleanField("usar scrapers directos", default=False)

    class Meta:
        verbose_name = "ruta"
        verbose_name_plural = "rutas"
        ordering = ["priority", "origin_id", "destination_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["origin", "destination"], name="uniq_route_origin_destination"
            ),
            models.CheckConstraint(
                condition=~models.Q(origin=models.F("destination")),
                name="route_origin_ne_destination",
            ),
        ]
        indexes = [
            models.Index(fields=["is_monitored", "priority"], name="idx_route_monitored_prio"),
        ]

    def __str__(self) -> str:
        return f"{self.origin_id}→{self.destination_id}"

    @property
    def code(self) -> str:
        return f"{self.origin_id}-{self.destination_id}"


class FlightOffer(models.Model):
    """Precio observado para un vuelo concreto en una fecha concreta.

    Cada fila es una foto del precio en el momento del scraping: el histórico
    se construye acumulando filas, nunca actualizándolas.
    """

    SOURCE_GOOGLE_FLIGHTS = "google_flights"
    SOURCE_SKY = "sky"
    SOURCE_JETSMART = "jetsmart"
    SOURCE_CHOICES = [
        (SOURCE_GOOGLE_FLIGHTS, "Google Flights"),
        (SOURCE_SKY, "Sky Airline"),
        (SOURCE_JETSMART, "JetSmart"),
    ]

    route = models.ForeignKey(
        Route, on_delete=models.CASCADE, related_name="offers", verbose_name="ruta"
    )
    airline = models.CharField("aerolínea", max_length=100, blank=True)
    flight_number = models.CharField("número de vuelo", max_length=20, blank=True)
    departure_dt = models.DateTimeField("salida", null=True, blank=True)
    arrival_dt = models.DateTimeField("llegada", null=True, blank=True)
    stops = models.PositiveSmallIntegerField("escalas", null=True, blank=True)

    price_pen = models.DecimalField("precio S/", max_digits=10, decimal_places=2)
    original_price = models.DecimalField(
        "precio original", max_digits=10, decimal_places=2, null=True, blank=True
    )
    original_currency = models.CharField("moneda original", max_length=3, blank=True)

    source = models.CharField("fuente", max_length=20, choices=SOURCE_CHOICES)
    deep_link = models.URLField("enlace", max_length=1000, blank=True)

    scraped_at = models.DateTimeField("scrapeado en", auto_now_add=True, db_index=True)
    search_date = models.DateField("fecha del vuelo")

    class Meta:
        verbose_name = "oferta de vuelo"
        verbose_name_plural = "ofertas de vuelo"
        ordering = ["price_pen"]
        indexes = [
            models.Index(
                fields=["route", "search_date", "scraped_at"], name="idx_offer_route_date_scrape"
            ),
            models.Index(fields=["route", "price_pen"], name="idx_offer_route_price"),
        ]

    def __str__(self) -> str:
        return f"{self.route} {self.search_date} {self.airline} S/ {self.price_pen}"


class PriceSnapshot(models.Model):
    """Resumen de una búsqueda (ruta + fecha de vuelo) en un momento dado.

    Esta tabla es el corazón del histórico y la que más crece: una fila por
    ruta, por fecha de vuelo y por barrido. Nunca se actualiza ni se purga —
    el histórico acumulado es el activo del negocio.
    """

    route = models.ForeignKey(
        Route, on_delete=models.CASCADE, related_name="snapshots", verbose_name="ruta"
    )
    flight_date = models.DateField("fecha del vuelo")

    min_price_pen = models.DecimalField("precio mínimo S/", max_digits=10, decimal_places=2)
    avg_price_pen = models.DecimalField("precio promedio S/", max_digits=10, decimal_places=2)
    offers_count = models.PositiveIntegerField("ofertas encontradas", default=0)
    cheapest_airline = models.CharField("aerolínea más barata", max_length=100, blank=True)

    snapshot_at = models.DateTimeField("tomado en", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "snapshot de precio"
        verbose_name_plural = "snapshots de precio"
        ordering = ["-snapshot_at"]
        indexes = [
            models.Index(
                fields=["route", "flight_date", "snapshot_at"], name="idx_snap_route_date_taken"
            ),
            models.Index(fields=["route", "min_price_pen"], name="idx_snap_route_minprice"),
        ]

    def __str__(self) -> str:
        return f"{self.route} {self.flight_date} min S/ {self.min_price_pen}"


class RouteStats(models.Model):
    """Estadísticas de los últimos 30 días de una ruta.

    Se recalcula al terminar cada barrido. Es la referencia contra la que el
    motor de alertas (Fase 4) decide si un precio es realmente una oferta.
    """

    route = models.OneToOneField(
        Route, on_delete=models.CASCADE, related_name="stats", primary_key=True, verbose_name="ruta"
    )

    avg_30d = models.DecimalField("promedio 30d S/", max_digits=10, decimal_places=2, null=True)
    min_30d = models.DecimalField("mínimo 30d S/", max_digits=10, decimal_places=2, null=True)
    p25_30d = models.DecimalField("percentil 25 30d S/", max_digits=10, decimal_places=2, null=True)
    median_30d = models.DecimalField("mediana 30d S/", max_digits=10, decimal_places=2, null=True)

    samples_count = models.PositiveIntegerField("muestras", default=0)
    updated_at = models.DateTimeField("actualizado en", auto_now=True)

    class Meta:
        verbose_name = "estadística de ruta"
        verbose_name_plural = "estadísticas de ruta"
        ordering = ["route_id"]

    def __str__(self) -> str:
        return f"{self.route} avg30d S/ {self.avg_30d}"

    @property
    def has_enough_history(self) -> bool:
        """Con menos de 10 muestras el promedio no es confiable para alertar."""
        return self.samples_count >= 10
