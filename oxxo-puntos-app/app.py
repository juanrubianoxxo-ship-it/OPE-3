import streamlit as st
import html

import pandas as pd
import folium
from rapidfuzz import process, fuzz
from streamlit_folium import st_folium

from src.data_loader import (
    load_tiendas,
    load_visitas,
    reload_all,
    DATE_COLUMN_STD,
    sync_operaciones_from_onedrive,
    get_onedrive_default_url,
)
from src.matching import build_match_table
from src.photos_utils import parse_photo_urls
from src.estado_subido import obtener_subidos, marcar_subido, desmarcar_subido
from src.geo_utils import buscar_cercanos
from src.puntos_potenciales import load_puntos_potenciales, buscar_puntos_potenciales_cercanos
from src.pdf_report import generar_informe_pdf

import os
from pathlib import Path


ASSETS_DIR = Path("oxxo-puntos-app/src")
LOGO_PATH = ASSETS_DIR / "logo_oxxo_simil.png"
ICONO_PATH = ASSETS_DIR / "icono_app.png"
FONDO_PATH = ASSETS_DIR / "fondo_login.png"


def _normalizar_nombre(valor):
    if pd.isna(valor):
        return ""
    return " ".join(str(valor).strip().casefold().split())


def _comparar_nombre_con_base(nombre, df, columna_nombre, etiqueta, threshold, id_actual=None):
    if df is None or df.empty or columna_nombre not in df.columns:
        return {"fuente": etiqueta, "nombre": "", "score": 0, "posible": False}
    nombre_normalizado = _normalizar_nombre(nombre)
    if not nombre_normalizado:
        return {"fuente": etiqueta, "nombre": "", "score": 0, "posible": False}
    candidatos = []
    for indice, fila in df.iterrows():
        if id_actual is not None and "ID" in df.columns and str(fila.get("ID", "")) == str(id_actual):
            continue
        nombre_candidato = _normalizar_nombre(fila.get(columna_nombre, ""))
        if nombre_candidato:
            candidatos.append((nombre_candidato, indice))
    if not candidatos:
        return {"fuente": etiqueta, "nombre": "", "score": 0, "posible": False}
    mejor = process.extractOne(nombre_normalizado, [x[0] for x in candidatos], scorer=fuzz.WRatio)
    if mejor is None:
        return {"fuente": etiqueta, "nombre": "", "score": 0, "posible": False}
    nombre_mejor, score, posicion = mejor
    fila_mejor = df.loc[candidatos[posicion][1]]
    return {"fuente": etiqueta, "nombre": str(fila_mejor.get(columna_nombre, nombre_mejor)), "score": int(round(score)), "posible": bool(score >= threshold)}


def agregar_alertas_duplicados(match_df, operaciones_df, puntos_potenciales_df, threshold):
    resultado = match_df.copy()
    ops, pps, resumenes = [], [], []
    for _, fila in resultado.iterrows():
        op = _comparar_nombre_con_base(fila.get("Nombre del Punto", ""), operaciones_df, "Nombre del Punto", "Operaciones_ult_semana", threshold, fila.get("ID", ""))
        pp = _comparar_nombre_con_base(fila.get("Nombre del Punto", ""), puntos_potenciales_df, "Nombre PP", "Puntos_Potenciales", threshold)
        ops.append(op); pps.append(pp)
        partes = []
        if op["posible"]: partes.append(f"Operaciones_ult_semana: {op['nombre']} ({op['score']}%)")
        if pp["posible"]: partes.append(f"Puntos_Potenciales: {pp['nombre']} ({pp['score']}%)")
        resumenes.append(" | ".join(partes))
    resultado["Duplicado Operaciones"] = [x["posible"] for x in ops]
    resultado["Coincidencia Operaciones"] = [x["nombre"] for x in ops]
    resultado["Score Operaciones"] = [x["score"] for x in ops]
    resultado["Duplicado Puntos Potenciales"] = [x["posible"] for x in pps]
    resultado["Coincidencia Puntos Potenciales"] = [x["nombre"] for x in pps]
    resultado["Score Puntos Potenciales"] = [x["score"] for x in pps]
    resultado["Tiene posibles duplicados"] = resultado["Posible duplicado"] | resultado["Duplicado Operaciones"] | resultado["Duplicado Puntos Potenciales"]
    resultado["Detalle posibles duplicados"] = resumenes
    return resultado


page_icon_src = None
if ICONO_PATH.exists():
    page_icon_src = str(ICONO_PATH)

st.set_page_config(
    page_title="Puntos evaluados vs. tiendas vigentes",
    page_icon=str(page_icon_src) if page_icon_src else "📍",
    layout="wide",
)

# ---------------------------------------------------------- Tema OXXO -----
OXXO_ROJO = "#E4032E"
OXXO_AMARILLO = "#FFD200"
OXXO_ROJO_OSCURO = "#B4022A"
OXXO_BLANCO = "#FFFFFF"
OXXO_GRIS = "#2B2B2B"

st.markdown(
    f"""
    <style>
    /* ---------- Fondo general ---------- */
    .stApp {{
        background: linear-gradient(180deg, #fffdf5 0%, #ffffff 35%);
    }}

    /* ---------- Sidebar ---------- */
    section[data-testid="stSidebar"] {{
        background: linear-gradient(180deg, {OXXO_ROJO} 0%, {OXXO_ROJO_OSCURO} 100%);
    }}
    section[data-testid="stSidebar"] * {{
        color: {OXXO_BLANCO} !important;
    }}
    section[data-testid="stSidebar"] .stCaption, 
    section[data-testid="stSidebar"] small {{
        color: #ffe9ec !important;
    }}
    section[data-testid="stSidebar"] hr {{
        border-color: rgba(255,255,255,0.35) !important;
    }}

    /* Sliders y radios dentro del sidebar */
    section[data-testid="stSidebar"] [data-baseweb="slider"] div[role="slider"] {{
        background-color: {OXXO_AMARILLO} !important;
        border: 2px solid {OXXO_BLANCO} !important;
    }}
    section[data-testid="stSidebar"] [data-testid="stTickBar"] {{
        display: none;
    }}

    /* ---------- Títulos ---------- */
    h1 {{
        color: {OXXO_ROJO_OSCURO} !important;
        font-weight: 800 !important;
        border-bottom: 4px solid {OXXO_AMARILLO};
        padding-bottom: 8px;
        display: inline-block;
    }}
    h2, h3 {{
        color: {OXXO_ROJO} !important;
        font-weight: 700 !important;
    }}

    /* ---------- Botones ---------- */
    .stButton > button, .stDownloadButton > button, .stLinkButton > a {{
        background: linear-gradient(135deg, {OXXO_AMARILLO} 0%, #ffc400 100%) !important;
        color: {OXXO_GRIS} !important;
        font-weight: 700 !important;
        border: 2px solid {OXXO_ROJO} !important;
        border-radius: 10px !important;
        transition: transform 0.15s ease, box-shadow 0.15s ease !important;
        box-shadow: 0 2px 6px rgba(228,3,46,0.25) !important;
    }}
    .stButton > button:hover, .stDownloadButton > button:hover, .stLinkButton > a:hover {{
        transform: translateY(-2px) scale(1.02);
        box-shadow: 0 6px 14px rgba(228,3,46,0.35) !important;
        background: linear-gradient(135deg, {OXXO_ROJO} 0%, {OXXO_ROJO_OSCURO} 100%) !important;
        color: {OXXO_BLANCO} !important;
        border: 2px solid {OXXO_AMARILLO} !important;
    }}

    /* ---------- Métricas (tarjetas) ---------- */
    div[data-testid="stMetric"] {{
        background: {OXXO_BLANCO};
        border: 2px solid {OXXO_AMARILLO};
        border-left: 8px solid {OXXO_ROJO};
        border-radius: 12px;
        padding: 14px 16px;
        box-shadow: 0 3px 10px rgba(0,0,0,0.08);
        transition: transform 0.15s ease;
    }}
    div[data-testid="stMetric"]:hover {{
        transform: translateY(-3px);
        box-shadow: 0 8px 18px rgba(228,3,46,0.18);
    }}
    div[data-testid="stMetricLabel"] {{
        color: {OXXO_ROJO_OSCURO} !important;
        font-weight: 700 !important;
    }}

    /* ---------- Radios (Vista) ---------- */
    section[data-testid="stSidebar"] div[role="radiogroup"] label {{
        background: rgba(255,255,255,0.12);
        border-radius: 8px;
        padding: 6px 10px;
        margin-bottom: 4px;
        transition: background 0.15s ease;
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"] label:hover {{
        background: rgba(255,210,0,0.3);
    }}

    /* ---------- Tablas / DataFrames ---------- */
    div[data-testid="stDataFrame"] {{
        border: 2px solid {OXXO_AMARILLO};
        border-radius: 10px;
        overflow: hidden;
    }}

    /* ---------- Alertas ---------- */
    div[data-testid="stAlert"] {{
        border-radius: 10px;
        border-left-width: 6px !important;
    }}

    /* ---------- Checkbox / inputs de texto ---------- */
    .stTextInput > div > div > input {{
        border: 2px solid {OXXO_AMARILLO} !important;
        border-radius: 8px !important;
    }}
    .stTextInput > div > div > input:focus {{
        border: 2px solid {OXXO_ROJO} !important;
        box-shadow: 0 0 0 2px rgba(228,3,46,0.15) !important;
    }}

    /* ---------- Selectbox ---------- */
    div[data-baseweb="select"] > div {{
        border: 2px solid {OXXO_AMARILLO} !important;
        border-radius: 8px !important;
    }}
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------- Sidebar --
with st.sidebar:
    if LOGO_PATH.exists():
        st.image(str(LOGO_PATH), use_container_width=True)
    
    st.title("📍 Panel de control")

    st.subheader("Fuente de operaciones")
    # El vínculo se configura fuera de la interfaz mediante Streamlit Secrets
    # o la variable ONEDRIVE_OPERACIONES_URL. Nunca se muestra al usuario.
    onedrive_url = get_onedrive_default_url().strip()
    sincronizar = st.button("☁️ Actualizar desde OneDrive", use_container_width=True)
    if sincronizar:
        try:
            st.session_state["sync_result"] = sync_operaciones_from_onedrive(onedrive_url, force=True)
            reload_all()
            load_puntos_potenciales.clear()
            st.rerun()
        except Exception as exc:
            st.session_state["sync_error"] = str(exc)
    if onedrive_url and "sync_result" not in st.session_state and "sync_error" not in st.session_state:
        try:
            st.session_state["sync_result"] = sync_operaciones_from_onedrive(onedrive_url)
            reload_all()
        except Exception as exc:
            st.session_state["sync_error"] = str(exc)
    if st.session_state.get("sync_result"):
        sync_result = st.session_state["sync_result"]
        st.success(f"{sync_result['message']}\n\nÚltima consulta: {sync_result.get('fetched_at', 'No disponible')}.")
        if sync_result.get("remote_modified") and sync_result.get("remote_modified") != "No informado por OneDrive":
            st.caption(f"Última modificación informada por OneDrive: {sync_result['remote_modified']}")
    elif st.session_state.get("sync_error"):
        st.warning(f"No se pudo sincronizar OneDrive; se conserva el archivo local. Detalle: {st.session_state['sync_error']}")
    else:
        st.info("La app está usando el archivo local incluido en `data/Operaciones_ult_semana.xlsm`.")

    if st.button("🔄 Recargar datos locales", use_container_width=True):
        reload_all()
        load_puntos_potenciales.clear()
        st.rerun()

    st.caption(
        "La fuente se configura de forma privada en los secretos de Streamlit. "
        "Si no existe un vínculo configurado, la app usa la copia local."
    )

    st.divider()
    threshold = st.slider(
        "Umbral de similitud para marcar posible duplicado",
        min_value=50, max_value=100, value=80, step=1,
    )
    top_n = st.slider("Coincidencias a mostrar por punto", 1, 5, 3)

    st.divider()
    radio_cercania_m = st.slider(
        "Radio de cercanía (metros) para tiendas abiertas",
        min_value=50, max_value=1000, value=300, step=25,
    )
    st.caption(
        "Este radio aplica a la tabla de cercanía y al Top 5 del informe. "
        "En el mapa se muestran todos los puntos ISO y los registros "
        "de Operaciones con Estado Growth = Subido que tengan coordenadas."
    )

    st.divider()
    page = st.radio(
        "Vista",
        ["📊 Estadísticas", "🔍 Comparación de nombres", "🗂️ Detalle por punto"],
        label_visibility="collapsed",
    )

# ------------------------------------------------------------------ Data --
try:
    tiendas = load_tiendas()
    visitas_full = load_visitas(include_coordinates=(page != "📊 Estadísticas"))
except FileNotFoundError as e:
    st.error(
        "No encuentro los archivos de datos. Verifica que "
        "`data/Book.xlsx` y `data/Operaciones_ult_semana.xlsm` "
        f"estén en el repo.\n\nDetalle: {e}"
    )
    st.stop()

puntos_potenciales = load_puntos_potenciales()

if visitas_full.empty:
    st.warning("La hoja 'Visitas_Operaciones' no tiene puntos para analizar.")
    st.stop()

subidos_ids = obtener_subidos()

# Capa independiente de la marcación manual: toma directamente el valor de
# la columna Estado Growth de la base de Operaciones y solo usa coordenadas
# explícitas presentes en esa misma hoja.
estado_growth = visitas_full.get(
    "Estado Growth", pd.Series("", index=visitas_full.index, dtype=object)
).fillna("").astype(str).str.strip().str.casefold()
mascara_growth_subido = estado_growth.eq("subido")
mascara_coords_operaciones = visitas_full["lat"].notna() & visitas_full["lon"].notna()
operaciones_growth_subidas = visitas_full[
    mascara_growth_subido & mascara_coords_operaciones
].copy()
n_growth_subidos_sin_coordenadas = int((mascara_growth_subido & ~mascara_coords_operaciones).sum())

# --------------------------------------------- Filtros (sidebar) ---
with st.sidebar:
    st.divider()
    mostrar_subidos = st.checkbox(
        "Mostrar también los puntos ya marcados como 'Subido'",
        value=False,
    )

    if puntos_potenciales.empty:
        st.caption(
            "⚠️ No encontré puntos con coordenadas válidas en "
            "`data/Puntos_Potenciales.xlsx`, hoja `ISO`."
        )

# ---------------------------------------------------- Aplicar filtros -----
visitas = visitas_full.copy()

n_subidos_ocultos = 0
if not mostrar_subidos:
    # Filtrar usando el estado Growth directamente de la base de Operaciones
    estado_growth = visitas.get(
        "Estado Growth", pd.Series("", index=visitas.index, dtype=object)
    ).fillna("").astype(str).str.strip().str.casefold()
    
    n_subidos_ocultos = int((estado_growth == "subido").sum())
    visitas = visitas[estado_growth != "subido"]

if visitas.empty:
    st.warning("No hay puntos evaluados que cumplan con los filtros seleccionados.")
    st.stop()

match_table = build_match_table(visitas, tiendas, threshold=threshold, top_n=top_n)
match_table["ID"] = match_table["ID"].astype(str)
match_table = agregar_alertas_duplicados(match_table, visitas_full, puntos_potenciales, threshold)
match_table["Subido"] = match_table["ID"].isin(subidos_ids)

# ======================================================== ESTADÍSTICAS ====
if page == "📊 Estadísticas":
    st.title("Estadísticas de Operaciones_ult_semana")
    st.caption("Indicadores para saber cuántos puntos están llegando por día y cómo avanza la operación.")

    stats_df = visitas_full.copy()
    stats_df["Fecha análisis"] = pd.to_datetime(stats_df[DATE_COLUMN_STD], errors="coerce")
    stats_df = stats_df[stats_df["Fecha análisis"].notna()].copy()
    if stats_df.empty:
        st.warning("No hay fechas válidas en la base de operaciones para construir estadísticas.")
        st.stop()

    min_date = stats_df["Fecha análisis"].min().date()
    max_date = stats_df["Fecha análisis"].max().date()
    with st.sidebar:
        st.divider()
        rango = st.date_input("Periodo de análisis", value=(min_date, max_date), min_value=min_date, max_value=max_date)
    if isinstance(rango, tuple) and len(rango) == 2:
        start_date, end_date = rango
    else:
        start_date = end_date = rango
    stats_df = stats_df[stats_df["Fecha análisis"].dt.date.between(start_date, end_date)].copy()

    daily = stats_df.groupby(stats_df["Fecha análisis"].dt.date).size().rename("Puntos recibidos").reindex(
        pd.date_range(start_date, end_date, freq="D").date, fill_value=0
    )
    daily_df = daily.rename_axis("Fecha").reset_index()
    daily_df["Acumulado"] = daily_df["Puntos recibidos"].cumsum()
    avg_daily = daily.mean() if len(daily) else 0
    max_day = int(daily.max()) if len(daily) else 0
    max_day_label = daily.idxmax().strftime("%d/%m/%Y") if len(daily) and max_day else "Sin datos"
    growth_subido = stats_df.get("Estado Growth", pd.Series("", index=stats_df.index)).fillna("").astype(str).str.strip().str.casefold().eq("subido").sum()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Puntos recibidos", f"{len(stats_df):,}")
    c2.metric("Promedio por día", f"{avg_daily:.1f}")
    c3.metric("Día con mayor llegada", f"{max_day:,}", max_day_label)
    c4.metric("Estado Growth: Subido", f"{int(growth_subido):,}")

    st.subheader("Llegada de puntos por día")
    st.bar_chart(daily_df.set_index("Fecha")["Puntos recibidos"], color="#E4032E")
    st.line_chart(daily_df.set_index("Fecha")["Acumulado"], color="#FFD200")
    st.dataframe(daily_df, use_container_width=True, hide_index=True)

    col_estado, col_jefe = st.columns(2)
    with col_estado:
        st.subheader("Distribución por estado")
        estado_series = stats_df.get("Estado Growth", pd.Series("Sin estado", index=stats_df.index)).fillna("Sin estado").replace("", "Sin estado").value_counts()
        st.bar_chart(estado_series, color="#FFD200")
        st.dataframe(estado_series.rename("Puntos").rename_axis("Estado").reset_index(), use_container_width=True, hide_index=True)
    with col_jefe:
        st.subheader("Puntos por jefe de zona")
        jefe_series = stats_df.get("Jefe de zona", pd.Series("Sin asignar", index=stats_df.index)).fillna("Sin asignar").replace("", "Sin asignar").value_counts().head(15)
        st.bar_chart(jefe_series, color="#E4032E")

    st.subheader("Distribución por región y plaza")
    group_cols = [column for column in ["Región", "Plaza"] if column in stats_df.columns]
    if group_cols:
        region_table = stats_df.groupby(group_cols, dropna=False).size().rename("Puntos").reset_index().sort_values("Puntos", ascending=False)
        st.dataframe(region_table, use_container_width=True, hide_index=True)
    else:
        st.info("La base no contiene columnas de Región o Plaza.")

    with st.expander("Ver datos usados en el periodo"):
        cols_stats = [column for column in ["ID", "Nombre del Punto", DATE_COLUMN_STD, "Jefe de zona", "Región", "Plaza", "Estado Growth"] if column in stats_df.columns]
        stats_table = (
            stats_df[cols_stats + ["Fecha análisis"]]
            .sort_values("Fecha análisis")
            [cols_stats]
        )
        st.dataframe(stats_table, use_container_width=True, hide_index=True)

# ============================================================== PAGE 1 ====
elif page == "🔍 Comparación de nombres":
    st.title("Comparación: puntos evaluados vs. tiendas vigentes")
    st.caption(
        f"{len(visitas)} puntos evaluados · {len(tiendas)} tiendas "
        "ABIERTA / OBRA / FIRMADA en la base."
    )
    if n_subidos_ocultos:
        st.caption(
            f"👁️ {n_subidos_ocultos} punto(s) ya marcados como 'Subido' "
            "están ocultos (activa la casilla en la barra lateral para verlos)."
        )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Puntos evaluados", len(match_table))
    c2.metric("Posibles duplicados", int(match_table["Tiene posibles duplicados"].sum()))
    c3.metric("Contra Operaciones", int(match_table["Duplicado Operaciones"].sum()))
    c4.metric("Contra Puntos Potenciales", int(match_table["Duplicado Puntos Potenciales"].sum()))

    solo_alertas = st.checkbox("Mostrar solo posibles duplicados", value=False)
    tabla = match_table[match_table["Tiene posibles duplicados"]] if solo_alertas else match_table

    def resaltar(row):
        color = "background-color: #ffe1e1" if row["Tiene posibles duplicados"] else ""
        return [color] * len(row)

    cols_mostrar = [
        "ID", "Nombre del Punto", "Jefe de zona", "Región", "Plaza",
        "Estado visita", "Mejor coincidencia", "Estado tienda", "Score",
        "Posible duplicado", "Coincidencia Operaciones", "Score Operaciones",
        "Coincidencia Puntos Potenciales", "Score Puntos Potenciales",
        "Tiene posibles duplicados", "Subido"
    ]
    st.dataframe(
        tabla[cols_mostrar].style.apply(resaltar, axis=1),
        use_container_width=True,
        hide_index=True,
        height=550,
    )

    st.caption(
        "El **Score** va de 0 a 100 (similitud de texto entre nombres, "
        "algoritmo WRatio). Ajusta el umbral en la barra lateral. Para ver "
        "el detalle completo de un punto (fotos, contacto, mapa, cercanía "
        "y descargar el informe en PDF) ve a la pestaña **Detalle por punto**."
    )

# ============================================================== PAGE 2 ====
else:
    st.title("Detalle del punto evaluado")

    # Construir opciones con ID como clave interna y ID + Nombre para mostrar
    if match_table.empty:
        st.info("No hay puntos que cumplan con los filtros seleccionados.")
        st.stop()

    # Mapeo de ID -> Nombre para el format_func del selectbox
    dict_nombres = match_table.set_index("ID")["Nombre del Punto"].to_dict()
    lista_ids = list(dict_nombres.keys())

    def format_func(id_val):
        nombre = dict_nombres.get(id_val, "Sin nombre")
        if id_val and str(id_val) != "nan":
            return f"{id_val} · {nombre}"
        return nombre

    id_seleccionado = st.selectbox(
        "Selecciona un punto evaluado",
        options=lista_ids,
        format_func=format_func
    )

    # Obtener la fila correspondiente por ID
    fila_match = match_table[match_table["ID"] == id_seleccionado].iloc[0]
    seleccion = fila_match["Nombre del Punto"]

    # Para el detalle completo, buscar en visitas_full por ID (asegurando comparación de strings)
    visitas_full["ID_str"] = visitas_full["ID"].astype(str)
    fila_visita_full = visitas_full[visitas_full["ID_str"] == str(id_seleccionado)]
    
    if fila_visita_full.empty:
        visitas["ID_str"] = visitas["ID"].astype(str)
        fila_visita = visitas[visitas["ID_str"] == str(id_seleccionado)].iloc[0]
    else:
        fila_visita = fila_visita_full.iloc[0]
    id_punto = str(fila_visita.get("ID", ""))
    ya_subido = id_punto in subidos_ids

    col_estado, col_boton = st.columns([3, 1])
    with col_estado:
        if ya_subido:
            st.success("✅ Este punto ya está marcado como **Subido**.")
        else:
            st.info("Este punto todavía no se ha marcado como Subido.")
    with col_boton:
        if ya_subido:
            if st.button("↩️ Desmarcar", use_container_width=True):
                desmarcar_subido(id_punto)
                st.rerun()
        else:
            if st.button("📤 Marcar como Subido", use_container_width=True):
                marcar_subido(id_punto)
                st.rerun()

    if fila_match["Posible duplicado"]:
        st.error(
            f"⚠️ Posible duplicado — coincide en un {fila_match['Score']}% "
            f"con **{fila_match['Mejor coincidencia']}** "
            f"({fila_match['Estado tienda']})."
        )

    if fila_match["Duplicado Operaciones"]:
        st.warning(
            f"⚠️ Posible duplicado de nombre en **Operaciones_ult_semana**: "
            f"coincide en un {fila_match['Score Operaciones']}% con "
            f"**{fila_match['Coincidencia Operaciones']}**."
        )
    if fila_match["Duplicado Puntos Potenciales"]:
        st.warning(
            f"⚠️ Posible duplicado de nombre en **Puntos_Potenciales**: "
            f"coincide en un {fila_match['Score Puntos Potenciales']}% con "
            f"**{fila_match['Coincidencia Puntos Potenciales']}**."
        )

    # -------------------------------------------- Nombre nuevo propuesto --
    st.subheader("✏️ Renombrar (opcional)")
    nuevo_nombre = st.text_input(
        "Si vas a subir este punto con otro nombre, escríbelo aquí. "
        "El informe en PDF imprimirá el nombre original y este nombre nuevo.",
        value="",
        placeholder=f"Nombre actual: {seleccion}",
        key=f"nuevo_nombre_{id_punto}",
    )

    col_info, col_foto = st.columns([1.1, 1])

    with col_info:
        st.subheader("Información principal")
        st.markdown(f"**Nombre del punto:** {fila_visita.get('Nombre del Punto', '')}")
        st.markdown(f"**Jefe de zona:** {fila_visita.get('Jefe de zona', '')}")
        st.markdown(f"**Región / Plaza:** {fila_visita.get('Región', '')} / {fila_visita.get('Plaza', '')}")
        st.markdown(f"**Dirección:** {fila_visita.get('Dirección', '')}")
        st.markdown(f"**Segmento aproximado:** {fila_visita.get('Segmento de tienda aproximado', '')}")
        st.markdown(f"**Tipo de local:** {fila_visita.get('Tienda de local', '')}")
        st.markdown(f"**Característica principal:** {fila_visita.get('Principal característica de la ubicación', '')}")
        st.markdown(f"**Contacto propietario (tel):** {fila_visita.get('Contacto del Propietario (Teléfono)', '')}")
        st.markdown(f"**Contacto propietario (correo):** {fila_visita.get('Contacto del Propietario (Correo Electronico)', '')}")
        st.markdown(f"**Estado visita:** {fila_visita.get('Estado', '')}")
        st.markdown(f"**Estado Growth:** {fila_visita.get('Estado Growth', '')}")
        fecha_val = fila_visita.get(DATE_COLUMN_STD)
        if pd.notna(fecha_val):
            st.markdown(f"**Fecha:** {fecha_val.strftime('%Y-%m-%d')}")
        if pd.notna(fila_visita.get("Comentarios")):
            st.markdown(f"**Comentarios:** {fila_visita.get('Comentarios', '')}")

        maps_link = fila_visita.get("Enlace de la ubicación en Google Maps", "")
        if isinstance(maps_link, str) and maps_link.strip():
            st.link_button("🗺️ Abrir en Google/Bing Maps", maps_link.strip())

    with col_foto:
        st.subheader("Fotos del local")
        fotos = parse_photo_urls(fila_visita.get("Fotos del Local Revisado", ""))
        if fotos:
            columnas_fotos = st.columns(2)
            for indice, url in enumerate(fotos[:6], start=1):
                with columnas_fotos[(indice - 1) % 2]:
                    st.image(url, caption=f"Foto {indice}", use_container_width=True)
            if len(fotos) > 6:
                st.caption(f"Mostrando las primeras 6 de {len(fotos)} fotos. (Optimizado para velocidad)")
        else:
            st.info("Este punto no tiene fotos cargadas.")

        st.divider()
    st.subheader("Ubicación en el mapa")
    if FONDO_PATH.exists():
        st.markdown(f'<div style="background-image: url("{str(FONDO_PATH)}"); background-size: cover; background-position: center; padding: 10px; border-radius: 10px;">', unsafe_allow_html=True)
    st.caption("Haz clic en cualquier punto del mapa para ver su información.")

    # --- Preparar datos ---
    # Coordenadas del punto seleccionado (extraídas del enlace de Maps)
    lat = fila_visita.get("lat")
    lon = fila_visita.get("lon")
    tiene_coordenadas_punto = (
        lat is not None and lon is not None and pd.notna(lat) and pd.notna(lon)
    )
    if tiene_coordenadas_punto:
        lat, lon = float(lat), float(lon)

    # Todos los puntos de Operaciones con coordenadas verificadas.
    mascara_coordenadas_validas = (
        visitas_full["lat"].notna() & visitas_full["lon"].notna()
        & visitas_full["lat"].between(-90, 90)
        & visitas_full["lon"].between(-180, 180)
    )
    puntos_operacion_mapeables = visitas_full[mascara_coordenadas_validas].copy()
    puntos_operacion_sin_ubicacion = visitas_full[~mascara_coordenadas_validas].copy()

    n_operaciones_mapeables = len(puntos_operacion_mapeables)
    n_operaciones_sin_ubicacion = len(puntos_operacion_sin_ubicacion)
    if n_operaciones_sin_ubicacion:
        st.warning(
            f"El mapa muestra {n_operaciones_mapeables} de {len(visitas_full)} "
            "operaciones con coordenadas verificadas. "
            f"{n_operaciones_sin_ubicacion} registro(s) se excluyen para evitar "
            "ubicarlos en un punto incorrecto."
        )
        with st.expander("Ver registros sin ubicación verificable"):
            columnas_diagnostico = [
                "ID", "Nombre del Punto", "Enlace de la ubicación en Google Maps",
                "Dirección", "diagnostico_ubicacion",
            ]
            columnas_diagnostico = [
                columna for columna in columnas_diagnostico
                if columna in puntos_operacion_sin_ubicacion.columns
            ]
            tabla_diagnostico = puntos_operacion_sin_ubicacion[columnas_diagnostico].copy()
            tabla_diagnostico = tabla_diagnostico.rename(columns={
                "diagnostico_ubicacion": "Diagnóstico de ubicación",
                "Enlace de la ubicación en Google Maps": "Enlace leído",
            })
            st.dataframe(tabla_diagnostico, use_container_width=True, hide_index=True)
    else:
        st.success(
            f"✅ Las {n_operaciones_mapeables} operaciones tienen coordenadas "
            "verificadas y se muestran en el mapa."
        )

    # Puntos con Estado Growth = Subido y coordenadas
    estado_growth_full = visitas_full.get(
        "Estado Growth", pd.Series("", index=visitas_full.index, dtype=object)
    ).fillna("").astype(str).str.strip().str.casefold()
    mascara_subido_full = estado_growth_full.eq("subido")
    operaciones_growth_subidas = visitas_full[
        mascara_subido_full & visitas_full["lat"].notna() & visitas_full["lon"].notna()
    ].copy()

    # Todas las tiendas abiertas (para el mapa y cercanía)
    tiendas_abiertas = tiendas[tiendas["ESTADO"] == "ABIERTA"]

    # Cercanía a 300m del punto seleccionado
    tiendas_cercanas = pd.DataFrame()
    puntos_potenciales_cercanos = pd.DataFrame()
    filas_cercania = []
    filas_tiendas_pdf = []

    if tiene_coordenadas_punto:
        tiendas_cercanas = buscar_cercanos(
            lat, lon, tiendas_abiertas,
            lat_col="lat", lon_col="lon", radio_m=radio_cercania_m,
        )
        puntos_potenciales_cercanos = buscar_puntos_potenciales_cercanos(
            lat, lon, radio_m=radio_cercania_m, df_pp=puntos_potenciales,
        )

    # --- Determinar centro del mapa ---
    if tiene_coordenadas_punto:
        centro_mapa = [lat, lon]
        zoom_inicial = 15
    elif not puntos_operacion_mapeables.empty:
        centro_mapa = [
            float(puntos_operacion_mapeables["lat"].mean()),
            float(puntos_operacion_mapeables["lon"].mean()),
        ]
        zoom_inicial = 8
    elif not puntos_potenciales.empty:
        centro_mapa = [
            float(puntos_potenciales["lat"].mean()),
            float(puntos_potenciales["lon"].mean()),
        ]
        zoom_inicial = 5
    elif not tiendas_abiertas.empty:
        centro_mapa = [
            float(tiendas_abiertas["lat"].mean()),
            float(tiendas_abiertas["lon"].mean()),
        ]
        zoom_inicial = 6
    else:
        centro_mapa = None
        zoom_inicial = 6

    if centro_mapa is not None:
        m = folium.Map(location=centro_mapa, zoom_start=zoom_inicial)
        capa_tiendas = folium.FeatureGroup(name="Tiendas abiertas")
        capa_iso = folium.FeatureGroup(name="Puntos potenciales ISO")
        capa_growth = folium.FeatureGroup(name="Operaciones Subidas")
        capa_ops = folium.FeatureGroup(name="Operaciones (todos)")

        # --- 1. Punto evaluado (selección actual) ---
        if tiene_coordenadas_punto:
            st.success(
                f"✅ Coordenadas extraídas del Maps · lat: {lat:.6f}, lon: {lon:.6f}."
            )
            popup_evaluado = folium.Popup(
                "<b>📍 " + html.escape(str(seleccion)) + "</b><br>"
                "Jefe de zona: " + html.escape(str(fila_visita.get("Jefe de zona", ""))) + "<br>"
                "Estado visita: " + html.escape(str(fila_visita.get("Estado", ""))) + "<br>"
                "Estado Growth: " + html.escape(str(fila_visita.get("Estado Growth", ""))) + "<br>"
                "Dirección: " + html.escape(str(fila_visita.get("Dirección", ""))),
                max_width=300,
            )
            if ICONO_PATH.exists():
                custom_icon = folium.CustomIcon(str(ICONO_PATH), icon_size=(32, 32))
            else:
                custom_icon = folium.Icon(color="blue", icon="star")
            folium.Marker(
                [lat, lon],
                popup=popup_evaluado,
                icon=custom_icon,
            ).add_to(m)

            # Tiendas cercanas a 300m del punto seleccionado
            for _, tienda in tiendas_cercanas.iterrows():
                popup_tienda = folium.Popup(
                    "<b>🟠 " + html.escape(str(tienda.get("NAME", ""))) + "</b><br>"
                    "Estado: " + html.escape(str(tienda.get("ESTADO", ""))) + "<br>"
                    f"Distancia: {round(tienda.get('distancia_m', 0))} m",
                    max_width=300,
                )
                folium.Marker(
                    [tienda["lat"], tienda["lon"]],
                    popup=popup_tienda,
                    icon=folium.Icon(color="orange", icon="shopping-cart"),
                ).add_to(capa_tiendas)

        # --- 2. Todos los puntos de Operaciones (excepto el seleccionado) ---
        for _, punto_op in puntos_operacion_mapeables.iterrows():
            # Saltar el punto seleccionado (ya está como estrella)
            if tiene_coordenadas_punto and str(punto_op.get("ID", "")) == id_punto:
                continue
            nombre_op = html.escape(str(punto_op.get("Nombre del Punto", "")))
            direccion_op = html.escape(str(punto_op.get("Dirección", "")))
            estado_growth_val = html.escape(str(punto_op.get("Estado Growth", "")))
            popup_op = folium.Popup(
                f"<b>⚫ {nombre_op}</b><br>Estado Growth: {estado_growth_val}<br>Dirección: {direccion_op}",
                max_width=300,
            )
            folium.CircleMarker(
                [float(punto_op["lat"]), float(punto_op["lon"])],
                radius=5,
                popup=popup_op,
                color="#555555",
                weight=1,
                fill=True,
                fill_color="#888888",
                fill_opacity=0.7,
            ).add_to(capa_ops)

        # --- 3. Puntos Subidos (destacados en rojo) ---
        for _, punto_subido in operaciones_growth_subidas.iterrows():
            # Si es el punto seleccionado, ya está como estrella
            if tiene_coordenadas_punto and str(punto_subido.get("ID", "")) == id_punto:
                continue
            nombre_subido = html.escape(str(punto_subido.get("Nombre del Punto", "")))
            direccion_subido = html.escape(str(punto_subido.get("Dirección", "")))
            popup_subido = folium.Popup(
                f"<b>🔴 {nombre_subido}</b><br>Estado Growth: Subido<br>Dirección: {direccion_subido}",
                max_width=300,
            )
            folium.CircleMarker(
                [float(punto_subido["lat"]), float(punto_subido["lon"])],
                radius=7,
                popup=popup_subido,
                color="#B4022A",
                weight=2,
                fill=True,
                fill_color="#E4032E",
                fill_opacity=0.9,
            ).add_to(capa_growth)

        # --- 4. Puntos potenciales ISO ---
        if not puntos_potenciales.empty:
            for _, punto_iso in puntos_potenciales.iterrows():
                popup_iso = folium.Popup(
                    "<b>🟣 " + html.escape(str(punto_iso.get("Nombre PP", ""))) + "</b><br>"
                    "ISO: " + html.escape(str(punto_iso.get("ISO", ""))) + "<br>"
                    "Estado: " + html.escape(str(punto_iso.get("Estado", ""))) + "<br>"
                    "Ciudad: " + html.escape(str(punto_iso.get("Ciudad", ""))) + "<br>"
                    "Especialista: " + html.escape(str(punto_iso.get("ESPECIALISTA", ""))),
                    max_width=300,
                )
                folium.CircleMarker(
                    [float(punto_iso["lat"]), float(punto_iso["lon"])],
                    radius=5,
                    popup=popup_iso,
                    color="#7B2D8E",
                    weight=1,
                    fill=True,
                    fill_color="#9B4DCA",
                    fill_opacity=0.7,
                ).add_to(capa_iso)

        capa_ops.add_to(m)
        capa_tiendas.add_to(m)
        capa_iso.add_to(m)
        capa_growth.add_to(m)
        folium.LayerControl(collapsed=True).add_to(m)

        n_ops_en_mapa = len(puntos_operacion_mapeables) - (1 if tiene_coordenadas_punto and id_punto in puntos_operacion_mapeables["ID"].astype(str).tolist() else 0)
        n_subidos_en_mapa = len(operaciones_growth_subidas) - (1 if tiene_coordenadas_punto and id_punto in operaciones_growth_subidas["ID"].astype(str).tolist() else 0)
        st.caption(
            f"🔵 Punto evaluado (selección) · ⚫ Operaciones ({n_ops_en_mapa} puntos) · "
            f"🔴 Operaciones Subidas ({n_subidos_en_mapa} en el mapa) · "
            f"🟠 Tiendas cercanas a {radio_cercania_m}m ({len(tiendas_cercanas)}) · "
            f"🟣 Puntos potenciales ISO. Usa el control superior derecho para ocultar capas."
        )
        st_folium(m, use_container_width=True, height=500, returned_objects=[])
        if FONDO_PATH.exists():
            st.markdown('</div>', unsafe_allow_html=True)
    else:
        st.warning(
            "No hay coordenadas disponibles para mostrar en el mapa. "
            "Verifica que los puntos tengan un enlace válido de Google Maps."
        )

    if n_growth_subidos_sin_coordenadas:
        st.info(
            f"{n_growth_subidos_sin_coordenadas} registro(s) con Estado Growth = "
            "Subido no se muestran en el mapa porque la base no contiene "
            "coordenadas explícitas válidas para ellos."
        )

    # ------------------------------------- Resultados de cercanía (texto) --
    st.divider()
    st.subheader(f"📡 Cercanía en un radio de {radio_cercania_m} m")
    st.caption(
        "Esto es solo por distancia (no por parecido de nombre): tiendas "
        "ABIERTAS y puntos potenciales de la hoja ISO. El PDF incluye solo "
        "las cinco tiendas abiertas más cercanas."
    )

    for _, tienda in tiendas_cercanas.iterrows():
        fila_tienda = {
            "Distancia (m)": round(tienda["distancia_m"]),
            "Tipo": "🟠 Tienda abierta",
            "Nombre": tienda.get("NAME", ""),
            "Detalle": f"{tienda.get('PLAZA 2026', '')} · {tienda.get('MUNICIPIO', '')}",
        }
        filas_cercania.append(fila_tienda)
        filas_tiendas_pdf.append(fila_tienda)
    for _, punto_iso in puntos_potenciales_cercanos.iterrows():
        filas_cercania.append({
            "Distancia (m)": round(punto_iso["distancia_m"]),
            "Tipo": "🟣 Punto potencial ISO",
            "Nombre": punto_iso.get("Nombre PP", ""),
            "Detalle": f"{punto_iso.get('Estado', '')} · {punto_iso.get('Ciudad', '')}",
        })

    if filas_cercania:
        tabla_cercania = pd.DataFrame(filas_cercania).sort_values("Distancia (m)").reset_index(drop=True)
        st.dataframe(tabla_cercania, hide_index=True, use_container_width=True)
    else:
        st.caption("No hay tiendas abiertas ni puntos potenciales dentro del radio.")

    st.divider()
    st.subheader("Coincidencias de nombre encontradas")
    st.caption("Esta comparación es solo por similitud de texto contra tiendas vigentes (no aplica a Puntos Potenciales).")
    if fila_match["Todas las coincidencias"]:
        st.dataframe(
            pd.DataFrame(fila_match["Todas las coincidencias"])[
                ["tienda_name", "estado", "plaza", "municipio", "score"]
            ].rename(columns={
                "tienda_name": "Tienda", "estado": "Estado",
                "plaza": "Plaza 2026", "municipio": "Municipio", "score": "Score",
            }),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No se encontraron tiendas con nombre parecido.")

    # -------------------------------------------------- Descargar informe --
    st.divider()
    st.subheader("📄 Informe del punto")

    pdf_bytes = generar_informe_pdf(
        datos=fila_visita.to_dict(),
        nombre_original=seleccion,
        nombre_nuevo=nuevo_nombre,
        fotos=fotos,
        lat=lat,
        lon=lon,
        tiendas_cercanas=filas_tiendas_pdf,
    )
    st.download_button(
        "⬇️ Descargar informe en PDF",
        data=pdf_bytes,
        file_name=f"informe_punto_{id_punto or seleccion}.pdf",
        mime="application/pdf",
        use_container_width=True,
    )
