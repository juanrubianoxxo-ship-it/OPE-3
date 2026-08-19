"""
Utilidades de geolocalización compartidas.

- haversine_m: distancia en metros entre dos coordenadas.
- buscar_cercanos: dado un punto (lat, lon) y un DataFrame con columnas de
  lat/lon, devuelve las filas dentro de un radio, ordenadas por distancia.
"""
from __future__ import annotations

from math import radians, sin, cos, sqrt, atan2

import pandas as pd

RADIO_TIERRA_M = 6_371_000  # metros


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia en metros entre dos puntos (lat/lon en grados decimales)."""
    if any(pd.isna(v) for v in (lat1, lon1, lat2, lon2)):
        return float("inf")

    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)

    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return RADIO_TIERRA_M * c


def buscar_cercanos(
    lat: float,
    lon: float,
    df: pd.DataFrame,
    lat_col: str = "lat",
    lon_col: str = "lon",
    radio_m: float = 300,
) -> pd.DataFrame:
    """
    Devuelve las filas de `df` cuya coordenada (lat_col, lon_col) está a
    `radio_m` metros o menos del punto (lat, lon), con una columna extra
    'distancia_m', ordenadas de más cerca a más lejos.

    Filas sin coordenadas válidas se ignoran.
    """
    if lat is None or lon is None or pd.isna(lat) or pd.isna(lon):
        return df.iloc[0:0].copy()

    if lat_col not in df.columns or lon_col not in df.columns:
        return df.iloc[0:0].copy()

    trabajo = df.copy()
    trabajo["distancia_m"] = trabajo.apply(
        lambda row: haversine_m(lat, lon, row.get(lat_col), row.get(lon_col)),
        axis=1,
    )
    cercanos = trabajo[trabajo["distancia_m"] <= radio_m].sort_values("distancia_m")
    return cercanos
