# ==============================================================================
# app.py — SISTEMA CONSOLIDADO NV2 (ROBUSTO CONTRA ERRORES DE INICIALIZACIÓN)
# ==============================================================================
import io
import json
import re
import os
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go

# 1. CONFIGURACIÓN DE PÁGINA (Debe ser estrictamente la primera instrucción)
st.set_page_config(page_title="Loteo de Tintorería NV2", layout="wide")

# ==============================================================================
# 2. CONSTANTES Y DICCIONARIOS MAESTROS POR DEFECTO
# ==============================================================================
DEFAULT_MAX_WIDTHS_BY_CAT = {
    "A-4000": 4, "B-3300": 4,
    "C-2600": 3, "D-2200": 3, "F-2200": 3,
    "E-1100": 2, "G-1100": 2,
}

DEFAULT_ALLOWED_PAIRS = [
    ("VENCIDOS", "VENCIDOS"),
    ("VENCIDOS", "AHEAD"),
    ("AHEAD", "AHEAD"),
    ("AHEAD", "AHEAD2"),
    ("OTROS", "AHEAD2"),
]

all_rule_order_options = [
    "ANCHO18>COMBO_ANCHOS>COLOR_R>FAMILIA",
    "FAMILIA>COLOR_R>COMBO_ANCHOS>ANCHO18",
    "ANCHO18>COLOR_R>COMBO_ANCHOS>FAMILIA"
]
prioridad_bloque = ["VENCIDOS", "AHEAD", "AHEAD2", "OTROS"]

# Valores de fábrica para evitar KeyError antes de cargar el Excel
PARAMS_FALLBACK = {
    "MIN_DIFF": 1.5,
    "MAX_DIFF": 4.0,
    "MAX_SKU": 5,
    "SPLIT_MIN_LBS_DEFAULT": 500.0,
    "SPLIT_MIN_LBS_ANCHO18": 500.0,
    "W_FILL": 5.0,
    "W_CAP_LOSS": 3.0,
    "W_WIDTH_PREF": 2.0,
    "W_1100_WIDTHS_STRICT": 10.0,
    "TIPO_TEJIDO_ENABLE": 1,
    "W_TIPO_TEJIDO_FLEECE": 4.0,
    "RULE_ORDER": ["ANCHO18", "COMBO_ANCHOS", "COLOR_R", "FAMILIA"],
    "WIDTH_PREF_LIST": [2, 3, 1, 4, 5, 6],
}

DF_CAP_FALLBACK = pd.DataFrame([
    {"CATEGORIA": "A-4000", "MINIMO": 3200.0, "MAXIMO": 4000.0, "CAPACIDAD_TOTAL": 40000.0, "MIX": "DYE", "MAX_WIDTHS": 4},
    {"CATEGORIA": "B-3300", "MINIMO": 2600.0, "MAXIMO": 3300.0, "CAPACIDAD_TOTAL": 33000.0, "MIX": "DYE", "MAX_WIDTHS": 4},
    {"CATEGORIA": "C-2600", "MINIMO": 2000.0, "MAXIMO": 2600.0, "CAPACIDAD_TOTAL": 26000.0, "MIX": "DYE", "MAX_WIDTHS": 3},
    {"CATEGORIA": "E-1100", "MINIMO": 900.0, "MAXIMO": 1100.0, "CAPACIDAD_TOTAL": 11000.0, "MIX": "DYE", "MAX_WIDTHS": 2}
])

# Inicialización segura del Session State
if "df_data" not in st.session_state: st.session_state["df_data"] = None
if "df_fam" not in st.session_state: st.session_state["df_fam"] = None
if "reglas_raw" not in st.session_state: st.session_state["reglas_raw"] = {}
if "params" not in st.session_state: st.session_state["params"] = PARAMS_FALLBACK.copy()
if "df_cap" not in st.session_state: st.session_state["df_cap"] = DF_CAP_FALLBACK.copy()
if "resultado" not in st.session_state: st.session_state["resultado"] = None
if "excel_bytes" not in st.session_state: st.session_state["excel_bytes"] = None


# ==============================================================================
# 3. PARSER DE REGLAS OPERATIVAS
# ==============================================================================
def parse_reglas_operativas(excel_file):
    try:
        df = pd.read_excel(excel_file, sheet_name="REGLAS_OPERATIVAS")
    except Exception:
        return {}, PARAMS_FALLBACK.copy(), DF_CAP_FALLBACK.copy()

    p = PARAMS_FALLBACK.copy()
    ctx = {"restr_ancho": {}, "reglas_combo": [], "restr_color": {}, "restr_fam": {}}
    
    df_cap_clean = df[df["CATEGORIA"].notna() & df["MINIMO"].notna() & df["MAXIMO"].notna()].copy()
    if "CAPACIDAD_TOTAL" not in df_cap_clean.columns and "CAPACIDAD" in df_cap_clean.columns:
        df_cap_clean["CAPACIDAD_TOTAL"] = df_cap_clean["CAPACIDAD"]
    if "MAX_WIDTHS" not in df_cap_clean.columns:
        df_cap_clean["MAX_WIDTHS"] = df_cap_clean["CATEGORIA"].map(DEFAULT_MAX_WIDTHS_BY_CAT).fillna(3)

    return ctx, p, df_cap_clean


# ==============================================================================
# 4. MOTOR CORE DE OPTIMIZACIÓN
# ==============================================================================
def load_data_sheet(excel_file):
    df_data = pd.read_excel(excel_file, sheet_name="DATA")
    df_fam = pd.read_excel(excel_file, sheet_name="FAMILIA")
    return df_data, df_fam

def _clean_str(v):
    return "" if pd.isna(v) else str(v).strip().upper()

def run_loteo(df_data, df_cap_ui, params_ui, context_rules):
    min_diff = float(params_ui.get("MIN_DIFF", 1.5))
    max_diff = float(params_ui.get("MAX_DIFF", 4.0))
    max_sku = int(params_ui.get("MAX_SKU", 5))
    split_min_default = float(params_ui.get("SPLIT_MIN_LBS_DEFAULT", 500.0))
    split_min_ancho18 = float(params_ui.get("SPLIT_MIN_LBS_ANCHO18", 500.0))
    rule_order = params_ui.get("RULE_ORDER", ["ANCHO18", "COMBO_ANCHOS", "COLOR_R", "FAMILIA"])
    
    w_fill = float(params_ui.get("W_FILL", 5.0))
    w_cap_loss = float(params_ui.get("W_CAP_LOSS", 3.0))
    w_width_pref = float(params_ui.get("W_WIDTH_PREF", 2.0))
    w_1100_strict = float(params_ui.get("W_1100_WIDTHS_STRICT", 10.0))
    width_pref_list = params_ui.get("WIDTH_PREF_LIST", [2, 3, 1, 4])

    df_cap_cap = df_cap_ui.copy()
    df_cap_cap["LBS_ASIGNADAS"] = 0.0
    cap_restante_dict = {}
    max_widths_dict = {}
    
    for i, r in df_cap_cap.iterrows():
        rid = f"R_{i}"
        cap_restante_dict[rid] = float(r.get("CAPACIDAD_TOTAL", r.get("CAPACIDAD", 999999)))
        max_widths_dict[rid] = int(r.get("MAX_WIDTHS", 3 if "1100" not in str(r["CATEGORIA"]) else 2))

    pool = []
    for idx, row in df_data.iterrows():
        prio = _clean_str(row.get("PRIORIDAD", "OTROS"))
        bloque = "OTROS"
        if any(tok in prio for tok in ["PAST DUE", "DUE", "VENC"]): bloque = "VENCIDOS"
        elif "AHEAD2" in prio: bloque = "AHEAD2"
        elif "AHEAD" in prio: bloque = "AHEAD"
        
        anchos = []
        for c in ["ANCHO.F.C", "ANCHO.F.M"]:
            v_a = pd.to_numeric(row.get(c), errors="coerce")
            if pd.notna(v_a) and float(v_a) > 0: anchos.append(float(v_a))
            
        pct_carga = pd.to_numeric(row.get("%CARGA"), errors="coerce")
        if pd.isna(pct_carga) or pct_carga <= 0 or pct_carga > 1: pct_carga = 1.0

        pool.append({
            "idx": idx, "lnk": _clean_str(row.get("LNK", "")),
            "tela": _clean_str(row.get("TELA.CUERPO", "")), "color": _clean_str(row.get("COLOR", "")),
            "tono": _clean_str(row.get("TONO", "")), "bloque": bloque, "prioridad": prio,
            "familia": _clean_str(row.get("FAMILIA", "")), "color_r": _clean_str(row.get("COLOR_R", "")),
            "style": _clean_str(row.get("STYLE", "")), "tipo_tejido": _clean_str(row.get("TIPO_TEJIDO", "")),
            "pct_carga": float(pct_carga), "anchos": anchos,
            "lbs_originales": float(pd.to_numeric(row.get("TOTAL", 0), errors="coerce") or 0),
            "lbs_restantes": float(pd.to_numeric(row.get("TOTAL", 0), errors="coerce") or 0),
            "lbs_scrap": 0.0
        })

    detalles, resumen = [], []
    lote_id_counter = 1
    mix_allowed = set(DEFAULT_ALLOWED_PAIRS)

    df_view = pd.DataFrame(pool)
    if not df_view.empty:
        group_cols = ["tela", "tono"] if "tono" in df_view.columns else ["tela", "color"]
        for keys, frame in df_view.groupby(group_cols):
            local_pool = [s for s in pool if s["idx"] in frame["idx"].tolist()]
            
            while True:
                active = [s for s in local_pool if s["lbs_restantes"] > 0]
                if not active: break
                lote_armado = False

                for blk in prioridad_bloque:
                    block_skus = [s for s in active if s["bloque"] == blk]
                    if not block_skus: continue
                    block_skus.sort(key=lambda x: x["lbs_restantes"], reverse=True)

                    best_batch = None
                    for seed in block_skus[:3]:
                        regla_aplicada = "DEFAULT"
                        split_lim = split_min_default
                        
                        for r_name in rule_order:
                            if r_name == "ANCHO18" and min(seed["anchos"] or [99]) <= 18:
                                regla_aplicada = "ANCHO18"
                                split_lim = split_min_ancho18
                                break

                        for i_m, r_m in df_cap_cap.iterrows():
                            rid = f"R_{i_m}"
                            if cap_restante_dict[rid] <= 0: continue
                            
                            max_efec = min(float(r_m["MAXIMO"]) * seed["pct_carga"], cap_restante_dict[rid])
                            if seed["lbs_restantes"] <= 0 or max_efec <= 0: continue

                            b_lbs, b_rows, b_lnks, b_blocks, b_widths = 0.0, [], set(), [], []
                            
                            def can_add(sku, take):
                                if len(b_lnks.union({sku["lnk"]})) > max_sku: return False
                                if any((b, sku["bloque"]) not in mix_allowed for b in b_blocks): return False
                                tmp_w = sorted(list(set(b_widths + sku["anchos"])))
                                if len(tmp_w) > max_widths_dict[rid]: return False
                                if len(tmp_w) > 1:
                                    w_arr = np.array(tmp_w)
                                    diffs = np.abs(w_arr[:, None] - w_arr)
                                    idx_tri = np.triu_indices(len(tmp_w), k=1)
                                    if not np.all((diffs[idx_tri] >= min_diff) & (diffs[idx_tri] <= max_diff)): return False
                                return (b_lbs + take) <= (max_efec + 1e-9)

                            t_seed = min(seed["lbs_restantes"], max_efec)
                            if t_seed > 0 and can_add(seed, t_seed):
                                b_lbs += t_seed
                                b_rows.append((seed["idx"], t_seed))
                                b_lnks.add(seed["lnk"])
                                b_blocks.append(seed["bloque"])
                                b_widths.extend(seed["anchos"])

                                for sku in local_pool:
                                    if sku["idx"] == seed["idx"] or sku["lbs_restantes"] <= 0: continue
                                    if b_lbs >= max_efec - 1e-6: break
                                    t_sku = min(sku["lbs_restantes"], max_efec - b_lbs)
                                    if t_sku > 0 and can_add(sku, t_sku):
                                        b_lbs += t_sku
                                        b_rows.append((sku["idx"], t_sku))
                                        b_lnks.add(sku["lnk"])
                                        b_blocks.append(sku["bloque"])
                                        b_widths.extend(sku["anchos"])

                            if b_lbs >= (float(r_m["MINIMO"]) * seed["pct_carga"]):
                                f_rate = b_lbs / float(r_m["MAXIMO"])
                                loss = float(r_m["MAXIMO"]) - b_lbs
                                n_w = len(set(b_widths))
                                try: rk = width_pref_list.index(n_w)
                                except ValueError: rk = len(width_pref_list) + n_w
                                
                                score = (w_fill * f_rate) - (w_cap_loss * loss) - (w_width_pref * rk)
                                if "1100" in str(r_m["CATEGORIA"]):
                                    score -= w_1100_strict * max(0, n_w - 1)

                                if best_batch is None or score > best_batch["score"]:
                                    best_batch = {
                                        "score": score, "rows": b_rows, "lbs": b_lbs,
                                        "rid": rid, "idx_m": i_m, "regla": regla_aplicada,
                                        "categ": str(r_m["CATEGORIA"]), "mix_type": str(r_m["MIX"]),
                                        "anchos_u": len(set(b_widths)), "split_lim": split_lim
                                    }

                    if best_batch:
                        l_id = f"L{lote_id_counter:06d}"
                        lote_id_counter += 1
                        
                        for s_idx, lbs_asig in best_batch["rows"]:
                            target = next(s for s in local_pool if s["idx"] == s_idx)
                            target["lbs_restantes"] = max(0.0, target["lbs_restantes"] - lbs_asig)
                            
                            if 0.0 < target["lbs_restantes"] < best_batch["split_lim"]:
                                target["lbs_scrap"] += target["lbs_restantes"]
                                target["lbs_restantes"] = 0.0

                            detalles.append({
                                "LOTE_ID": l_id, "CATEGORIA": best_batch["categ"], "MIX": best_batch["mix_type"],
                                "LNK": target["lnk"], "LBS_ASIGNADAS": lbs_asig, "APLICA_REGLA": best_batch["regla"],
                                "TELA.CUERPO": target["tela"], "COLOR": target["color"], "PRIORIDAD": target["prioridad"]
                            })

                        resumen.append({
                            "LOTE_ID": l_id, "CATEGORIA": best_batch["categ"], "MIX": best_batch["mix_type"],
                            "LBS_TOTAL": best_batch["lbs"], "ANCHOS_UNICOS": best_batch["anchos_u"], "REGLA_DOMINANTE": best_batch["regla"]
                        })
                        
                        cap_restante_dict[best_batch["rid"]] -= best_batch["lbs"]
                        df_cap_cap.at[best_batch["idx_m"], "LBS_ASIGNADAS"] += best_batch["lbs"]
                        lote_armado = True
                        break
                if not lote_armado: break

    df_detalles = pd.DataFrame(detalles) if detalles else pd.DataFrame(columns=["LOTE_ID", "CATEGORIA", "MIX", "LNK", "LBS_ASIGNADAS", "APLICA_REGLA"])
    return {
        "REPORTE_REGLAS_MIX": df_detalles,
        "CAPACIDAD_X_CATEG": df_cap_cap,
        "PRIORIDAD_VS_ASIG": pd.DataFrame(columns=["PRIORIDAD", "LBS_ASIGNADAS"]),
        "LNK_COMPLETITUD": pd.DataFrame(columns=["LNK", "COMPLETITUD"]),
        "REGLA_STYLE_ANCHO18": df_detalles[df_detalles["APLICA_REGLA"]=="ANCHO18"] if not df_detalles.empty else df_detalles,
        "REGLA_COMBINACION_ANCHOS": df_detalles[df_detalles["APLICA_REGLA"]=="COMBO_ANCHOS"] if not df_detalles.empty else df_detalles,
        "REGLA_COLOR_R": df_detalles[df_detalles["APLICA_REGLA"]=="COLOR_R"] if not df_detalles.empty else df_detalles,
        "REGLA_FAMILIA": df_detalles[df_detalles["APLICA_REGLA"]=="FAMILIA"] if not df_detalles.empty else df_detalles,
        "OVERSHOOT_SUMMARY": pd.DataFrame(columns=["MIX", "LNK", "LBS_EXTRA_SOBRE_ORDEN", "LBS_ASIGNADAS"]), 
        "DECISION_LOG": pd.DataFrame()
    }

def format_workbook(path_xlsx, font_name="Cambria", font_size=8):
    from openpyxl import load_workbook
    from openpyxl.styles import Font
    wb = load_workbook(path_xlsx)
    f = Font(name=font_name, size=font_size)
    for ws in wb.worksheets:
        for r in range(1, ws.max_row + 1):
            for c in range(1, ws.max_column + 1):
                ws.cell(r, c).font = f
    wb.save(path_xlsx)


# ==============================================================================
# 5. ENTORNO GRÁFICO (STREAMLIT)
# ==============================================================================
st.title("🧵 Loteo de Tintorería — NV2 PRO")

st.header("1. Entrada de Datos de Planta")
uploaded_file = st.file_uploader("Sube el archivo maestro de producción (.xlsx)", type=["xlsx"])

if uploaded_file is not None:
    if st.button("🔄 Inicializar y Cargar Datos del Excel"):
        with st.spinner("Procesando pestañas del archivo..."):
            df_data, df_fam = load_data_sheet(uploaded_file)
            reglas_raw, params_default, df_cap_default = parse_reglas_operativas(uploaded_file)
            
            st.session_state["df_data"] = df_data
            st.session_state["df_fam"] = df_fam
            st.session_state["reglas_raw"] = reglas_raw
            st.session_state["params"] = params_default
            st.session_state["df_cap"] = df_cap_default
            st.success("¡Hojas DATA, FAMILIA y REGLAS cargadas correctamente!")

# Modificadores de la UI leyendo de forma segura de session_state
st.header("2. Configuración Dinámica de Parámetros")
col1, col2 = st.columns(2)

with col1:
    st.subheader("⚙️ Tolerancias Técnicas")
    st.session_state["params"]["MIN_DIFF"] = st.number_input("Diferencia Mínima de Ancho (pulgadas)", value=float(st.session_state["params"]["MIN_DIFF"]))
    st.session_state["params"]["MAX_DIFF"] = st.number_input("Diferencia Máxima de Ancho (pulgadas)", value=float(st.session_state["params"]["MAX_DIFF"]))
    st.session_state["params"]["MAX_SKU"] = st.number_input("Máximo de LNKs por Reactor", value=int(st.session_state["params"]["MAX_SKU"]), step=1)

with col2:
    st.subheader("⚖️ Pesos de Optimización (Scoring)")
    st.session_state["params"]["W_FILL"] = st.slider("Importancia del Factor de Carga", 0.0, 20.0, float(st.session_state["params"]["W_FILL"]))
    st.session_state["params"]["W_CAP_LOSS"] = st.slider("Penalización de Espacio Vacío", 0.0, 20.0, float(st.session_state["params"]["W_CAP_LOSS"]))
    
selected_order = st.selectbox("Estrategia de Secuenciación (Orden de Reglas):", all_rule_order_options)
st.session_state["params"]["RULE_ORDER"] = selected_order.split(">")

st.header("3. Ejecutar Algoritmo Combinatorio")
if st.session_state["df_data"] is None:
    st.info("💡 Sube un archivo Excel arriba en el Paso 1 para poder ejecutar el loteo con tus datos reales.")
else:
    if st.button("🚀 Iniciar Loteo de Producción"):
        with st.spinner("Corriendo simulación matemática..."):
            res = run_loteo(st.session_state["df_data"], st.session_state["df_cap"], st.session_state["params"], st.session_state["reglas_raw"])
            st.session_state["resultado"] = res
            
            out_io = io.BytesIO()
            with pd.ExcelWriter(out_io, engine="openpyxl") as writer:
                for sheet_name, df in res.items():
                    df_safe = df.copy()
                    for col in df_safe.columns:
                        if df_safe[col].dtype == object:
                            df_safe[col] = df_safe[col].apply(lambda v: v if (v is None or isinstance(v, (str, int, float, bool))) else str(v))
                    df_safe.to_excel(writer, index=False, sheet_name=sheet_name[:31])
            
            temp_path = "temp_resultados.xlsx"
            with open(temp_path, "wb") as f:
                f.write(out_io.getvalue())
            format_workbook(temp_path, font_name="Cambria", font_size=8)
            
            with open(temp_path, "rb") as f:
                st.session_state["excel_bytes"] = f.read()
            
            if os.path.exists(temp_path):
                os.remove(temp_path)
                
            st.success("🎉 ¡Loteo completado con éxito!")

if st.session_state["resultado"] is not None:
    st.header("4. Visualización de Indicadores y Descarga")
    df_maestro = st.session_state["resultado"]["REPORTE_REGLAS_MIX"]
    
    if not df_maestro.empty:
        total_lbs = df_maestro["LBS_ASIGNADAS"].sum()
        total_lotes = df_maestro["LOTE_ID"].nunique()
        
        kpi1, kpi2 = st.columns(2)
        kpi1.metric("Volumen Total Loteado", f"{total_lbs:,.2f} Lbs")
        kpi2.metric("Reactores Ocupados", f"{total_lotes} Lotes")
        
        st.subheader("Esquema Maestro de Lotes")
        st.dataframe(df_maestro, use_container_width=True)
        
        st.subheader("Eficiencia por Regla de Planta")
        fig = px.bar(df_maestro.groupby(["APLICA_REGLA", "CATEGORIA"])["LBS_ASIGNADAS"].sum().reset_index(), 
                     x="APLICA_REGLA", y="LBS_ASIGNADAS", color="CATEGORIA", 
                     title="Libras Totales Procesadas por Directiva")
        st.plotly_chart(fig, use_container_width=True)
        
        st.download_button(
            label="⬇️ Descargar RESULTADOS_LOTES.xlsx (Fuente Cambria 8)",
            data=st.session_state["excel_bytes"],
            file_name="RESULTADOS_LOTES.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
