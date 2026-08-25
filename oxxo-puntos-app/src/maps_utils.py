"""Utilidades para interpretar y validar ubicaciones de mapas.

La aplicación recibe enlaces que pueden ser URL completas de Google Maps,
enlaces cortos maps.app.goo.gl, URLs de Bing/Waze o hipervínculos de Excel
cuyo texto visible no es la URL real. Este módulo extrae pares
latitud/longitud sin invertirlos, resuelve redirecciones cuando es
necesario y deja una fuente auditable para cada coordenada.
"""
from __future__ import annotations

import html as html_lib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, unquote, unquote_plus, urlparse

import requests
import streamlit as st

USER_AGENT = "oxxo-puntos-app/2.0 (contacto: equipo-expansion@oxxo.com)"
REQUEST_TIMEOUT = (4, 10)

# Las fichas de Google se consultan como respaldo de un identificador exacto;
# un fallo debe degradar rápidamente al diagnóstico, no bloquear la interfaz.
GOOGLE_PREVIEW_TIMEOUT = (3, 6)
MAX_LINK_WORKERS = 6
APP_ROOT = Path(__file__).resolve().parents[1]
COORDINATE_CACHE_PATH = APP_ROOT / "data" / "maps_coordinate_cache.json"

# Dominios de enlaces de ubicación que pueden requerir una redirección para
# revelar las coordenadas. Se comparan contra el hostname, no con subcadenas.
SHORT_LINK_DOMAINS = {"maps.app.goo.gl", "goo.gl"}
MAP_HOST_HINTS = ("google.", "goo.gl", "share.google", "bing.com", "waze.com")

# Llaves de query frecuentes para pares latitud,longitud. Incluye Google,
# Bing y enlaces compartidos de aplicaciones móviles.
COORD_QUERY_KEYS = (
    "q",
    "query",
    "ll",
    "destination",
    "origin",
    "center",
    "location",
    "coords",
    "coordinates",
    "latlng",
    "latlon",
)


def _clean_url(value: object) -> str:
    """Normaliza una URL preservando su contenido semántico.

    Algunas celdas llevan texto descriptivo seguido de una URL compartida. En
    ese caso se conserva únicamente la URL, que es la parte procesable y la
    que debe abrir el botón de detalle.
    """
    if not isinstance(value, str):
        return ""
    link = html_lib.unescape(value).replace("\u200b", "").strip()
    embedded_url = re.search(r"https?://[^\s<>\"']+", link, flags=re.IGNORECASE)
    if embedded_url:
        link = embedded_url.group(0).rstrip(".,;:)")
    if link.casefold().startswith("www."):
        link = f"https://{link}"

    # Outlook Safe Links encapsula la URL original como ?url=<URL codificada>.
    # Se extrae antes de realizar solicitudes para no perder el Maps real ni
    # depender de una página intermedia de protección.
    try:
        parsed = urlparse(link)
        if parsed.netloc.casefold().endswith("safelinks.protection.outlook.com"):
            original_urls = parse_qs(parsed.query).get("url", [])
            if original_urls:
                link = unquote(original_urls[0]).strip()
    except ValueError:
        pass
    return link


def _valid_coordinates(lat: object, lon: object) -> tuple[float, float] | None:
    """Convierte y valida una pareja geográfica en el orden latitud,longitud."""
    try:
        latitude, longitude = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if -90 <= latitude <= 90 and -180 <= longitude <= 180:
        return latitude, longitude
    return None


@lru_cache(maxsize=1)
def _verified_coordinate_cache() -> dict[str, dict[str, object]]:
    """Carga coordenadas auditadas del libro actual, si el archivo existe."""
    try:
        payload = json.loads(COORDINATE_CACHE_PATH.read_text(encoding="utf-8"))
        entries = payload.get("entries", {})
        return entries if isinstance(entries, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _cached_coordinates(url: str) -> tuple[float, float, str] | None:
    """Devuelve una coordenada auditada para el mismo enlace normalizado."""
    entry = _verified_coordinate_cache().get(_clean_url(url))
    if not isinstance(entry, dict):
        return None
    coordinates = _valid_coordinates(entry.get("lat"), entry.get("lon"))
    if not coordinates:
        return None
    source = str(entry.get("source") or "Coordenada auditada del enlace")
    return coordinates[0], coordinates[1], source


def _parse_coordinate_pair(value: object) -> tuple[float, float] | None:
    """Extrae un par latitud,longitud de una cadena de consulta."""
    if not isinstance(value, str):
        return None
    text = unquote(value).strip()
    # Soporta valores como geo:4.71,-74.07 o loc:4.71,-74.07.
    match = re.search(
        r"(?:geo:|loc:)?\s*(-?\d{1,2}(?:\.\d+)?)\s*[,~;]\s*"
        r"(-?\d{1,3}(?:\.\d+)?)",
        text,
    )
    if not match:
        return None
    return _valid_coordinates(match.group(1), match.group(2))


def _parse_coords_from_text(value: object) -> tuple[float, float] | None:
    """Obtiene coordenadas explícitas de formatos habituales de URL de mapas."""
    link = _clean_url(value)
    if not link:
        return None

    # Se decodifica de forma repetida porque algunos enlaces contienen una URL
    # anidada o parámetros que llegan doblemente codificados desde Excel.
    decoded = link
    for _ in range(2):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value

    # Vista/resultado de Google Maps: .../@4.7101,-74.0721,15z
    patterns = (
        r"@\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)",
        # Pin exacto de Google Maps: !3d<lat>!4d<lon>
        r"!3d(-?\d{1,2}(?:\.\d+)?)!4d(-?\d{1,3}(?:\.\d+)?)",
        # Formatos observados en exportaciones y redirecciones heredadas.
        r"!1d(-?\d{1,2}(?:\.\d+)?)!2d(-?\d{1,3}(?:\.\d+)?)",
        r"!2d(-?\d{1,2}(?:\.\d+)?)!3d(-?\d{1,3}(?:\.\d+)?)",
        # Bing Maps: ppois=<lat>_<lon>_... y cp=<lat>~<lon>
        r"(?:[?&]|^)ppois=(-?\d{1,2}(?:\.\d+)?)(?:_|%5[fF])(-?\d{1,3}(?:\.\d+)?)",
        r"(?:[?&]|^)cp=(-?\d{1,2}(?:\.\d+)?)(?:~|%7[eE])(-?\d{1,3}(?:\.\d+)?)",
        # URL de Google Maps donde la coordenada aparece como parte de /place/.
        r"/place/\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)",
    )
    for pattern in patterns:
        match = re.search(pattern, decoded, flags=re.IGNORECASE)
        if match:
            coordinates = _valid_coordinates(match.group(1), match.group(2))
            if coordinates:
                return coordinates

    # Consulta estándar y URLs con query anidada. ``parse_qs`` preserva el
    # signo negativo y evita errores con parámetros adicionales de Google.
    parsed = urlparse(decoded)
    query = parse_qs(parsed.query, keep_blank_values=False)
    for key in COORD_QUERY_KEYS:
        for candidate in query.get(key, []):
            coordinates = _parse_coordinate_pair(candidate)
            if coordinates:
                return coordinates

    # Como último intento, busca pares sólo cuando están precedidos por una
    # llave conocida. Esto evita interpretar números arbitrarios del enlace.
    key_pattern = "|".join(re.escape(key) for key in COORD_QUERY_KEYS)
    match = re.search(
        rf"(?:[?&]|\b)(?:{key_pattern})=([^&#]+)", decoded, flags=re.IGNORECASE
    )
    if match:
        return _parse_coordinate_pair(match.group(1))
    return None


def _hostname(url: str) -> str:
    try:
        return urlparse(url).netloc.casefold().split(":", 1)[0]
    except ValueError:
        return ""


def _is_short_link(url: str) -> bool:
    host = _hostname(url)
    return host in SHORT_LINK_DOMAINS or host.endswith(".goo.gl")


def _should_follow_redirects(url: str) -> bool:
    host = _hostname(url)
    return bool(host) and any(hint in host for hint in MAP_HOST_HINTS)


def _map_query_as_address(url: str) -> str:
    """Extrae una búsqueda o dirección legible de una URL final de mapas.

    Un enlace compartido puede terminar en ``q=<dirección>&ftid=...``. Esa URL
    sí identifica el lugar, aunque no tenga un par latitud/longitud; se usa
    como respaldo antes de recurrir a la dirección libre de la hoja Excel.
    """
    link = _clean_url(url)
    if not link:
        return ""
    decoded = link
    for _ in range(2):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    parsed = urlparse(decoded)
    query = parse_qs(parsed.query, keep_blank_values=False)
    for key in ("q", "query", "destination", "origin", "location", "search"):
        for candidate in query.get(key, []):
            candidate = candidate.strip()
            if not candidate or _parse_coordinate_pair(candidate):
                continue
            # Los IDs técnicos no se pueden geocodificar de forma fiable.
            if candidate.casefold().startswith(("place_id:", "cid:")):
                continue
            return candidate

    # Los enlaces ``/maps/place/<nombre>/data=...`` pueden no incluir q=, pero
    # el nombre de la ficha sigue siendo un respaldo útil para geocodificación.
    match = re.search(r"/(?:maps/)?place/([^/]+)", parsed.path, flags=re.IGNORECASE)
    if match:
        candidate = unquote_plus(match.group(1)).strip()
        if candidate and not _parse_coordinate_pair(candidate):
            return candidate
    return ""


def _google_place_id(url: str) -> str:
    """Extrae el identificador 0x...:0x... de una ficha de Google Maps."""
    link = _clean_url(url)
    if not link:
        return ""
    decoded = unquote(link)
    parsed = urlparse(decoded)
    for candidate in parse_qs(parsed.query).get("ftid", []):
        if re.fullmatch(r"0x[0-9a-f]+:0x[0-9a-f]+", candidate, flags=re.IGNORECASE):
            return candidate
    match = re.search(r"(?:!1s|ftid=)(0x[0-9a-f]+:0x[0-9a-f]+)", decoded, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def _google_preview_payload(place_id: str, label: str) -> str:
    """Construye el parámetro de vista previa usado por una ficha pública.

    El identificador del lugar es el dato decisivo. El centro inicial se fija
    sobre Colombia sólo para completar el formato de la solicitud; la respuesta
    se acepta únicamente si devuelve el mismo identificador de lugar.
    """
    return (
        f"!1m15!1s{place_id}!2s{label[:500]}!3m12!1m3!1d100000!2d-74.0!3d4.6"
        "!2m3!1f0.0!2f0.0!3f0.0!3m2!1i1024!2i768!4f13.1"
    )


@lru_cache(maxsize=2_000)
def google_place_coordinates(url: str) -> tuple[float, float] | None:
    """Obtiene el pin principal de una ficha pública de Google Maps.

    Esta ruta sólo se ejecuta cuando el enlace ya identificó un lugar con
    ``ftid``/``!1s`` pero no expuso latitud y longitud en su URL. La respuesta
    se valida contra ese mismo ID antes de aceptar la pareja de coordenadas,
    evitando confundirla con el centro de la vista o con lugares cercanos.
    """
    link = _clean_url(url)
    place_id = _google_place_id(link)
    if not link or not place_id or "google" not in _hostname(link):
        return None
    label = _map_query_as_address(link) or place_id
    response = None
    try:
        response = requests.get(
            "https://www.google.com/maps/preview/place",
            params={
                "authuser": "0",
                "hl": "es",
                "gl": "co",
                "q": label,
                "ftid": place_id,
                "pb": _google_preview_payload(place_id, label),
            },
            headers={"User-Agent": USER_AGENT},
            timeout=GOOGLE_PREVIEW_TIMEOUT,
        )
        response.raise_for_status()
        body = response.text
    except requests.RequestException:
        return None
    finally:
        if response is not None:
            response.close()

    # El bloque [null, null, lat, lon], inmediatamente seguido por el mismo
    # ID de lugar, describe el pin principal de la ficha solicitada.
    pattern = re.compile(
        r"\[\s*null\s*,\s*null\s*,\s*"
        r"(-?\d{1,2}(?:\.\d+)?)\s*,\s*"
        r"(-?\d{1,3}(?:\.\d+)?)\s*\]\s*,\s*\""
        + re.escape(place_id)
        + r"\"",
        flags=re.IGNORECASE,
    )
    match = pattern.search(body)
    if not match:
        return None
    return _valid_coordinates(match.group(1), match.group(2))


@lru_cache(maxsize=2_000)
def resolve_map_link(url: str) -> str:
    """Sigue redirecciones sin descargar el cuerpo de la página de mapas.

    ``stream=True`` es importante: Google puede conservar la conexión abierta
    para cargar la interfaz completa, mientras que las cabeceras ya contienen
    la URL final necesaria para analizar las coordenadas.
    """
    link = _clean_url(url)
    if not link or not _should_follow_redirects(link):
        return link
    response = None
    try:
        response = requests.get(
            link,
            headers={"User-Agent": USER_AGENT},
            allow_redirects=True,
            stream=True,
            timeout=REQUEST_TIMEOUT,
        )
        return response.url or link
    except requests.RequestException:
        return link
    finally:
        if response is not None:
            response.close()


@lru_cache(maxsize=4_000)
def geocode_address(address: str, region_hint: str = "Colombia") -> tuple[float, float] | None:
    """Geocodifica una dirección sólo si el enlace no expone coordenadas."""
    normalized = (address or "").strip()
    if not normalized:
        return None
    try:
        response = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": f"{normalized}, {region_hint}", "format": "json", "limit": 1},
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


def get_coordinates(maps_link: str, address: str = "") -> tuple[float | None, float | None, str]:
    """Devuelve (latitud, longitud, fuente) de un enlace o una dirección.

    Primero se intenta analizar el enlace original, después su redirección. La
    geocodificación por dirección queda como respaldo explícitamente etiquetado
    para que no se confunda con una coordenada obtenida del enlace.
    """
    link = _clean_url(maps_link)

    direct = _parse_coords_from_text(link)
    if direct:
        return direct[0], direct[1], "Enlace de mapa: coordenadas explícitas"

    cached = _cached_coordinates(link)
    if cached:
        return cached

    final_link = link
    if link and _should_follow_redirects(link):
        final_link = resolve_map_link(link)
        redirected = _parse_coords_from_text(final_link)
        if redirected:
            return redirected[0], redirected[1], "Enlace de mapa: redirección resuelta"

    # Las fichas de Google pueden incluir sólo ftid/!1s. Se consulta la ficha
    # pública y se acepta el resultado únicamente si coincide con ese ID.
    google_place = google_place_coordinates(final_link)
    if google_place:
        return google_place[0], google_place[1], "Enlace de mapa: ficha de Google validada"

    # Google suele entregar q=<dirección>&ftid=<identificador> en vez de
    # coordenadas. La búsqueda viene del propio enlace y es más específica que
    # una dirección opcional de la hoja, por lo que se prueba primero.
    link_address = _map_query_as_address(final_link)
    if link_address:
        coordinates = geocode_address(link_address)
        if coordinates:
            return coordinates[0], coordinates[1], "Dirección del enlace geocodificada"

    if address and str(address).strip():
        coordinates = geocode_address(str(address))
        if coordinates:
            return coordinates[0], coordinates[1], "Dirección geocodificada (respaldo)"

    if not link:
        return None, None, "Sin enlace ni dirección utilizable"
    return None, None, "No se obtuvieron coordenadas válidas del enlace"


def get_coordinates_batch(
    records: Iterable[tuple[object, str, str]], max_workers: int = MAX_LINK_WORKERS
) -> dict[object, tuple[float | None, float | None, str]]:
    """Resuelve varios registros de forma acotada sin bloquear la interfaz.

    Cada registro tiene la forma ``(clave, enlace, dirección)``. El número de
    trabajadores se limita para no realizar una ráfaga de solicitudes a los
    servicios de mapas ni hacer esperar al usuario por enlaces cortos uno a uno.
    """
    items = list(records)
    if not items:
        return {}

    workers = max(1, min(int(max_workers), len(items), MAX_LINK_WORKERS))
    results: dict[object, tuple[float | None, float | None, str]] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(get_coordinates, str(link or ""), str(address or "")): key
            for key, link, address in items
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as exc:  # Evita que un enlace defectuoso detenga la carga completa.
                results[key] = (None, None, f"Error al interpretar enlace: {type(exc).__name__}")
    return results
