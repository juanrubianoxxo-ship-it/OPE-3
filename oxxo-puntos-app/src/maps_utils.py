"""Utilidades para extraer coordenadas precisas desde enlaces de mapas.

La fuente principal es siempre el enlace proporcionado en la base. Se soportan
URLs completas de Google Maps, enlaces cortos, URLs con parámetros codificados
y textos como ``Foto: https://...`` sin modificar el archivo Excel.
"""
from __future__ import annotations

import html
import re
from functools import lru_cache
from urllib.parse import parse_qs, unquote, urlparse

import requests
import streamlit as st

USER_AGENT = "oxxo-puntos-app/2.1"
REQUEST_TIMEOUT = (4, 10)
SHORT_LINK_HOSTS = {"maps.app.goo.gl", "goo.gl", "www.goo.gl"}


def _clean_url(value: object) -> str:
    """Extrae una URL real aunque la celda tenga una etiqueta delante."""
    if value is None:
        return ""
    text = html.unescape(str(value)).replace("\u200b", "").strip()
    match = re.search(r"https?://[^\s<>\"']+", text, flags=re.IGNORECASE)
    if match:
        text = match.group(0)
    elif text.casefold().startswith("www."):
        text = "https://" + text
    return text.rstrip(".,;:)")


def _valid_coordinates(lat: object, lon: object):
    try:
        latitude, longitude = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if -90 <= latitude <= 90 and -180 <= longitude <= 180:
        return latitude, longitude
    return None


def _pair(value: object):
    """Lee un par latitud,longitud, incluyendo separadores codificados."""
    if value is None:
        return None
    text = unquote(str(value)).strip()
    match = re.search(
        r"(-?\d{1,2}(?:\.\d+)?)\s*[,~;]\s*(-?\d{1,3}(?:\.\d+)?)",
        text,
    )
    return _valid_coordinates(match.group(1), match.group(2)) if match else None


def _parse_coords_from_text(value: object):
    """Extrae coordenadas explícitas sin confundir números del enlace."""
    link = _clean_url(value)
    if not link:
        return None

    decoded = link
    for _ in range(3):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value

    # Formato de vista de Google: /@lat,lon,zoom
    patterns = (
        (r"@\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)", False),
        # Pin de Google: !3d lat !4d lon
        (r"!3d(-?\d{1,2}(?:\.\d+)?)!4d(-?\d{1,3}(?:\.\d+)?)", False),
        # En estos formatos Google guarda primero longitud y luego latitud.
        (r"!1d(-?\d{1,3}(?:\.\d+)?)!2d(-?\d{1,2}(?:\.\d+)?)", True),
        (r"!2d(-?\d{1,3}(?:\.\d+)?)!3d(-?\d{1,2}(?:\.\d+)?)", True),
        # Pares que aparecen dentro de rutas /place/ o parámetros.
        (r"/place/\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)", False),
        (r"3d(-?\d{1,2}(?:\.\d+)?)!4d(-?\d{1,3}(?:\.\d+)?)", False),
    )
    for pattern, reverse in patterns:
        match = re.search(pattern, decoded, flags=re.IGNORECASE)
        if match:
            coordinates = (
                _valid_coordinates(match.group(2), match.group(1))
                if reverse
                else _valid_coordinates(match.group(1), match.group(2))
            )
            if coordinates:
                return coordinates

    parsed = urlparse(decoded)
    query = parse_qs(parsed.query, keep_blank_values=False)
    for key in ("q", "query", "ll", "destination", "origin", "center", "location", "coords", "coordinates", "latlng"):
        for candidate in query.get(key, []):
            coordinates = _pair(candidate)
            if coordinates:
                return coordinates

    return None


def _hostname(url: str) -> str:
    try:
        return urlparse(url).netloc.casefold().split(":", 1)[0]
    except ValueError:
        return ""


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def resolve_short_link(url: str) -> str:
    """Resuelve enlaces cortos sin descargar el contenido de la página."""
    link = _clean_url(url)
    if not link:
        return ""
    try:
        response = requests.get(
            link,
            headers={"User-Agent": USER_AGENT},
            allow_redirects=True,
            stream=True,
            timeout=REQUEST_TIMEOUT,
        )
        final_url = response.url or link
        response.close()
        return final_url
    except requests.RequestException:
        return link


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def geocode_address(address: str, region_hint: str = "Colombia"):
    """Respaldo únicamente para llamadas antiguas; no sustituye un link válido."""
    if not address or not address.strip():
        return None
    try:
        response = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": f"{address}, {region_hint}", "format": "json", "limit": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        if data:
            return _valid_coordinates(data[0].get("lat"), data[0].get("lon"))
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        return None
    return None


def get_coordinates(maps_link: str, address: str = ""):
    """Devuelve ``(latitud, longitud, fuente)`` usando primero el link de Maps."""
    link = _clean_url(maps_link)
    if not link:
        return None, None, "Sin enlace de Maps"

    coordinates = _parse_coords_from_text(link)
    if coordinates:
        return coordinates[0], coordinates[1], "Extraído directamente del enlace de Maps"

    # Los links compartidos o cortos suelen ocultar el pin en la URL final.
    final_url = resolve_short_link(link)
    if final_url and final_url != link:
        coordinates = _parse_coords_from_text(final_url)
        if coordinates:
            return coordinates[0], coordinates[1], "Extraído de la redirección del enlace de Maps"

    # No inventamos una posición por dirección: así el mapa solo muestra puntos
    # realmente derivados del enlace que viene en Operaciones.
    return None, None, "El enlace no contiene coordenadas explícitas"


@lru_cache(maxsize=4096)
def get_coordinates_cached(maps_link: str):
    """Versión cacheada para evitar repetir resolución de enlaces en Streamlit."""
    return get_coordinates(maps_link)
