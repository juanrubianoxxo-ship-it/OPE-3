"""Extracción de URLs de fotos del local revisado.

La columna 'Fotos del Local Revisado' puede venir como texto plano (con una o
varias líneas tipo 'Foto: https://...jpg') o como hipervínculos de Excel.
"""
import re

# Detectamos cualquier cosa que parezca una URL
URL_RE = re.compile(r"(?:https?://)?[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+\.(?:jpg|jpeg|png|gif|webp|bmp|svg)(?:\?[^\s]*)?")


def parse_photo_urls(raw_text) -> list[str]:
    # Si es un diccionario (como los que devuelve Pandas con los hipervínculos),
    # extraemos la URL directamente de ahí.
    if isinstance(raw_text, dict):
        if "url" in raw_text and raw_text["url"]:
            return [raw_text["url"]]
        if "text" in raw_text and raw_text["text"]:
            urls = URL_RE.findall(raw_text["text"])
            if urls:
                return [urls[0].rstrip(").,;")]
        return []
        
    if not isinstance(raw_text, str) or not raw_text.strip():
        return []
        
    urls = URL_RE.findall(raw_text)
    cleaned_urls = []
    for u in urls:
        # Limpiar posibles caracteres colgados al final (comas, puntos sueltos)
        u = u.rstrip(").,;")
        # Si no tiene protocolo, se lo añadimos
        if not u.startswith('http'):
            u = 'https://' + u
        cleaned_urls.append(u)
    return cleaned_urls
