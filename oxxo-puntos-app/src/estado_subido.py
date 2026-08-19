"""
Persistencia simple del estado "Subido" de cada punto evaluado.

Se guarda en data/estado_subido.json (dentro del repo, en el disco del
servidor). Persiste mientras la app no se reinicie/redeploye.
"""
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # oxxo-puntos-app/
ESTADO_PATH = os.path.join(BASE_DIR, "data", "estado_subido.json")


def _load_estado() -> set:
    if not os.path.exists(ESTADO_PATH):
        return set()
    try:
        with open(ESTADO_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(str(x) for x in data.get("subidos", []))
    except Exception:
        return set()


def _save_estado(ids: set) -> None:
    os.makedirs(os.path.dirname(ESTADO_PATH), exist_ok=True)
    with open(ESTADO_PATH, "w", encoding="utf-8") as f:
        json.dump({"subidos": sorted(ids)}, f, ensure_ascii=False, indent=2)


def obtener_subidos() -> set:
    return _load_estado()


def marcar_subido(id_punto) -> None:
    ids = _load_estado()
    ids.add(str(id_punto))
    _save_estado(ids)


def desmarcar_subido(id_punto) -> None:
    ids = _load_estado()
    ids.discard(str(id_punto))
    _save_estado(ids)
