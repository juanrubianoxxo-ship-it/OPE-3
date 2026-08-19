"""
Comparación por similitud de nombres entre los puntos evaluados
(Operaciones) y las tiendas ya ABIERTA / OBRA / FIRMADA (Book.xlsx).
"""
import re
import pandas as pd
from rapidfuzz import fuzz, process
from unidecode import unidecode


def normalize(text: str) -> str:
    if not isinstance(text, str):
        return ""
    text = unidecode(text).lower().strip()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def build_match_table(
    visitas: pd.DataFrame,
    tiendas: pd.DataFrame,
    threshold: int = 80,
    top_n: int = 3,
) -> pd.DataFrame:
    """
    Para cada punto evaluado, busca las tiendas vigentes con nombre más
    parecido (WRatio de rapidfuzz, 0-100) y arma una tabla resumen con la
    mejor coincidencia + una bandera de alerta si supera el umbral.
    """
    tiendas = tiendas.copy()
    tiendas["_norm"] = tiendas["NAME"].apply(normalize)
    choices = tiendas["_norm"].tolist()

    rows = []
    for _, visita in visitas.iterrows():
        nombre_punto = visita.get("Nombre del Punto", "")
        norm_punto = normalize(nombre_punto)

        matches = []
        if norm_punto and choices:
            results = process.extract(
                norm_punto, choices, scorer=fuzz.WRatio, limit=top_n
            )
            for _match_str, score, idx in results:
                tienda_row = tiendas.iloc[idx]
                matches.append(
                    {
                        "tienda_name": tienda_row["NAME"],
                        "estado": tienda_row["ESTADO"],
                        "plaza": tienda_row.get("PLAZA 2026", ""),
                        "municipio": tienda_row.get("MUNICIPIO", ""),
                        "score": round(score, 1),
                        "lat": tienda_row.get("lat"),
                        "lon": tienda_row.get("lon"),
                    }
                )

        best = matches[0] if matches else None
        rows.append(
            {
                "ID": visita.get("ID"),
                "Nombre del Punto": nombre_punto,
                "Jefe de zona": visita.get("Jefe de zona"),
                "Región": visita.get("Región"),
                "Plaza": visita.get("Plaza"),
                "Estado visita": visita.get("Estado"),
                "Mejor coincidencia": best["tienda_name"] if best else "",
                "Estado tienda": best["estado"] if best else "",
                "Score": best["score"] if best else 0.0,
                "Posible duplicado": bool(best and best["score"] >= threshold),
                "Todas las coincidencias": matches,
            }
        )

    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values("Score", ascending=False).reset_index(drop=True)
    return result
