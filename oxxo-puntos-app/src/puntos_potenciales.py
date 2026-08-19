"""Carga de puntos potenciales desde la hoja ``ISO``.

El archivo se espera en ``data/Puntos_Potenciales.xlsx``. Se normalizan los
nombres y coordenadas para que el mapa y la búsqueda de cercanía puedan usar
un esquema consistente, sin depender de la antigua hoja ``MS26``.
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st

from src.geo_utils import buscar_cercanos

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUNTOS_POTENCIALES_PATH = os.path.join(BASE_DIR, "data", "Puntos_Potenciales.xlsx")
HOJA_ISO = "ISO"

COLUMNAS_ISO = [
    "ISO",
    "FECHA RECEPCIÓN",
    "FECHA DE ENVÍO ISO",
    "DÍAS DE ENVÍO",
    "PRACTICANTE",
    "MEDIO (TEAMS/ CORREO)",
    "MS (SI O NO)",
    "ESTADO",
    "NOMBRE",
    "CIUDAD",
    "ESPECIALISTA",
    "LONGITUD",
    "LATITUD",
    "COMENTARIOS",
]


def _empty_dataframe() -> pd.DataFrame:
    return pd.DataFrame(
        columns=COLUMNAS_ISO
        + ["Nombre PP", "Estado", "Ciudad", "Region", "lat", "lon"]
    )


def _to_coordinate(series: pd.Series) -> pd.Series:
    cleaned = series.astype(str).str.strip().str.replace(",", ".", regex=False)
    return pd.to_numeric(cleaned, errors="coerce")


@st.cache_data(show_spinner=False)
def load_puntos_potenciales(path: str | Path = PUNTOS_POTENCIALES_PATH) -> pd.DataFrame:
    """Lee la hoja ``ISO`` y devuelve coordenadas válidas en ``lat``/``lon``."""
    path = Path(path)
    if not path.exists():
        return _empty_dataframe()

    try:
        df = pd.read_excel(path, sheet_name=HOJA_ISO)
    except ValueError:
        return _empty_dataframe()

    df.columns = [str(column).strip() for column in df.columns]
    present = [column for column in COLUMNAS_ISO if column in df.columns]
    df = df[present].copy()

    # Alias homogéneos para el resto de la aplicación.
    df["Nombre PP"] = df.get("NOMBRE", pd.Series("", index=df.index)).fillna("").astype(str).str.strip()
    df["Estado"] = df.get("ESTADO", pd.Series("", index=df.index)).fillna("").astype(str).str.strip()
    df["Ciudad"] = df.get("CIUDAD", pd.Series("", index=df.index)).fillna("").astype(str).str.strip()
    # Alias de compatibilidad para versiones anteriores de la interfaz.
    df["Region"] = df["Ciudad"]

    df["lat"] = _to_coordinate(df.get("LATITUD", pd.Series(index=df.index, dtype=object)))
    df["lon"] = _to_coordinate(df.get("LONGITUD", pd.Series(index=df.index, dtype=object)))
    valid = df["lat"].between(-90, 90) & df["lon"].between(-180, 180)
    df = df[valid].copy()
    return df.reset_index(drop=True)


def buscar_puntos_potenciales_cercanos(
    lat: float,
    lon: float,
    radio_m: float = 300,
    df_pp: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Busca puntos ISO dentro del radio solicitado y los ordena por distancia."""
    if df_pp is None:
        df_pp = load_puntos_potenciales()
    if df_pp.empty:
        return df_pp
    return buscar_cercanos(lat, lon, df_pp, lat_col="lat", lon_col="lon", radio_m=radio_m)
