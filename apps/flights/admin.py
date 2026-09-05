from django.contrib import admin

from .models import Airport, FlightOffer, PriceSnapshot, Route, RouteStats


@admin.register(Airport)
class AirportAdmin(admin.ModelAdmin):
    list_display = ("iata_code", "name", "city", "alias", "region", "is_active")
    list_filter = ("is_active", "region")
    search_fields = ("iata_code", "name", "city", "alias")


@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "origin",
        "destination",
        "is_monitored",
        "has_direct_flights",
        "priority",
    )
    list_filter = ("is_monitored", "has_direct_flights", "priority")
    search_fields = ("origin__iata_code", "destination__iata_code", "origin__city", "destination__city")
    list_select_related = ("origin", "destination")


@admin.register(FlightOffer)
class FlightOfferAdmin(admin.ModelAdmin):
    list_display = (
        "route",
        "search_date",
        "airline",
        "flight_number",
        "departure_dt",
        "stops",
        "price_pen",
        "source",
        "scraped_at",
    )
    list_filter = ("source", "airline", "search_date", "route")
    search_fields = ("airline", "flight_number", "route__origin__iata_code", "route__destination__iata_code")
    date_hierarchy = "scraped_at"
    list_select_related = ("route", "route__origin", "route__destination")
    readonly_fields = ("scraped_at",)


@admin.register(PriceSnapshot)
class PriceSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "route", "flight_date", "min_price_pen", "avg_price_pen",
        "offers_count", "cheapest_airline", "snapshot_at",
    )
    list_filter = ("route", "flight_date", "cheapest_airline")
    search_fields = ("route__origin__iata_code", "route__destination__iata_code", "cheapest_airline")
    date_hierarchy = "snapshot_at"
    list_select_related = ("route", "route__origin", "route__destination")
    readonly_fields = ("snapshot_at",)


@admin.register(RouteStats)
class RouteStatsAdmin(admin.ModelAdmin):
    list_display = (
        "route", "avg_30d", "median_30d", "p25_30d", "min_30d",
        "samples_count", "updated_at",
    )
    list_filter = ("route__priority", "route__is_monitored")
    search_fields = ("route__origin__iata_code", "route__destination__iata_code")
    list_select_related = ("route", "route__origin", "route__destination")
    readonly_fields = ("updated_at",)
