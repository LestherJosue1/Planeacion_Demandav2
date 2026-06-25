# ==============================================================================
# app.py — App Streamlit de Loteo de Tintorería (NV2 COMPLETAMENTE CORREGIDO)
# ==============================================================================
import io
import json
import os
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go

from loteo_engine import (
    load_data_sheet, build_cap_dataframe, run_loteo, build_reports,
    format_workbook, all_rule_order_options, prioridad_bloque
)
from reglas_operativas_parser import parse_reglas_operativas, rule_order_options

st.set_page_config(page_title="Loteo de Tintorería NV2", layout="wide")
st.title("🧵 Loteo de Tintorería — NV2")

# ---------------------------- Estado Inicial Seguro ----------------------------
if "df_data" not in st.session_state: st.session_state["df_data"] = None
if "df_fam" not in st.session_state: st.session_state["df_fam"] = None
if "reglas_raw" not in st.session_state: st.session_state["reglas_raw"] = None
if "params" not in st.session_state: st.session_state["params"] = None
if "df_cap" not in st.session_state: st.session_state["df_cap"] = None
if "resultado" not in st.session_state: st.session_state["resultado"] = None
if "excel_bytes" not in st.session_state: st.session_state["excel_bytes"] = None

def reset_params_from_excel(uploaded_file):
    """Parsea las reglas operativas directamente desde el archivo cargado en memoria"""
    reglas_raw, params_default, df_cap_default = parse_reglas_operativas(uploaded_file)
    st.session_state["reglas_raw"] = reglas_raw
    st.session_state["params"] = params_default
    st.session_state["df_cap"] = df_cap_default

# ---------------------------- 1. Carga de Archivos ----------------------------
st.header("1. Carga de Archivo Maestro")
uploaded_file = st.file_uploader("Sube el archivo Excel con DATA, FAMILIA y REGLAS_OPERATIVAS", type=["xlsx"])

if uploaded_file is not None:
    # Si el estado está vacío, cargamos los datos inmediatamente de forma segura
    if st.session_state["df_data"] is None:
        with st.spinner("Cargando datos iniciales del Excel..."):
            try:
                df_d, df_f = load_data_sheet(uploaded_file)
                st.session_state["df_data"] = df_d
                st.session_state["df_fam"] = df_f
                reset_params_from_excel(uploaded_file)
                st.success("✅ Archivo cargado e inicializado correctamente.")
            except Exception as e:
                st.error(f"Error al inicializar el archivo: {e}")

    # Botón de restauración original en caso de requerir un reinicio limpio
    if st.button("🔄 Reiniciar Parámetros desde Excel"):
        with st.spinner("Restableciendo configuración original..."):
            reset_params_from_excel(uploaded_file)
            st.success("✅ Parámetros restablecidos al estado del Excel.")

# ---------------------------- 2. Configuración Dinámica ----------------------------
# Bloque de seguridad: Solo renderizar la interfaz si los parámetros ya fueron parseados del Excel
if st.session_state["params"] is not None and st.session_state["df_cap"] is not None:
    st.header("2. Configuración de Reglas Operativas")
    
    # Pestañas de control idénticas a tu diseño nativo
    tab_gen, tab_cap, tab_rules, tab_adv = st.tabs([
        "⚙️ Parámetros Generales", 
        "🛢️ Capacidad de Reactores", 
        "📜 Toggles de Reglas", 
        "🧠 Pesos de Scoring (Avanzado)"
    ])
    
    with tab_gen:
        col1, col2 = st.columns(2)
        with col1:
            st.session_state["params"]["MIN_DIFF"] = st.number_input(
                "Diferencia Mínima de Ancho (MIN_DIFF)", 
                value=float(st.session_state["params"]["MIN_DIFF"])
            )
            st.session_state["params"]["MAX_DIFF"] = st.number_input(
                "Diferencia Máxima de Ancho (MAX_DIFF)", 
                value=float(st.session_state["params"]["MAX_DIFF"])
            )
            st.session_state["params"]["MAX_SKU"] = st.number_input(
                "Máximo de LNKs por Reactor (MAX_SKU)", 
                value=int(st.session_state["params"]["MAX_SKU"]), step=1
            )
        with col2:
            st.session_state["params"]["SPLIT_MIN_LBS_DEFAULT"] = st.number_input(
                "Libras Mínimas sobrantes (Split Default)", 
                value=float(st.session_state["params"]["SPLIT_MIN_LBS_DEFAULT"])
            )
            st.session_state["params"]["SPLIT_MIN_LBS_ANCHO18"] = st.number_input(
                "Libras Mínimas sobrantes (Split Ancho <= 18)", 
                value=float(st.session_state["params"]["SPLIT_MIN_LBS_ANCHO18"])
            )
            
            # Selector de orden de reglas
            curr_order = ">".join(st.session_state["params"]["RULE_ORDER"])
            if curr_order not in all_rule_order_options:
                all_rule_order_options.append(curr_order)
            sel_order = st.selectbox(
                "Orden Estratégico de Reglas (RULE_ORDER):", 
                all_rule_order_options, 
                index=all_rule_order_options.index(curr_order)
            )
            st.session_state["params"]["RULE_ORDER"] = sel_order.split(">")

    with tab_cap:
        st.subheader("Capacidades y Restricciones por Categoría")
        # Permitir edición interactiva del DataFrame de capacidades extraído del Excel
        st.session_state["df_cap"] = st.data_editor(
            st.session_state["df_cap"], 
            use_container_width=True, 
            num_rows="dynamic"
        )

    with tab_rules:
        st.subheader("Activación / Desactivación de Reglas Complejas")
        toggles = st.session_state["params"]["RULE_TOGGLES"]
        for k_tg in list(toggles.keys()):
            toggles[k_tg] = st.checkbox(f"Activar {k_tg}", value=bool(toggles[k_tg]))

    with tab_adv:
        st.subheader("Pesos Matemáticos del Algoritmo de Selección")
        col3, col4 = st.columns(2)
        with col3:
            st.session_state["params"]["W_FILL"] = st.slider("Peso Factor de Carga (W_FILL)", 0.0, 20.0, float(st.session_state["params"]["W_FILL"]))
            st.session_state["params"]["W_CAP_LOSS"] = st.slider("Penalización Espacio Vacío (W_CAP_LOSS)", 0.0, 20.0, float(st.session_state["params"]["W_CAP_LOSS"]))
        with col4:
            st.session_state["params"]["W_WIDTH_PREF"] = st.slider("Preferencia Cantidad de Anchos (W_WIDTH_PREF)", 0.0, 20.0, float(st.session_state["params"]["W_WIDTH_PREF"]))
            st.session_state["params"]["W_1100_WIDTHS_STRICT"] = st.slider("Rigidez Anchos en 1100 (W_1100_WIDTHS_STRICT)", 0.0, 50.0, float(st.session_state["params"]["W_1100_WIDTHS_STRICT"]))

    # ---------------------------- 3. Ejecución del Loteo ----------------------------
    st.header("3. Procesar Combinatoria")
    if st.button("🚀 Ejecutar Loteo Automático", type="primary"):
        with st.spinner("Procesando asignaciones en reactores óptimos..."):
            try:
                # Ejecución directa pasando los estados mutados de la UI
                res = run_loteo(
                    st.session_state["df_data"], 
                    st.session_state["df_cap"], 
                    st.session_state["params"], 
                    st.session_state["reglas_raw"]
                )
                st.session_state["resultado"] = res
                
                # Generación del archivo en memoria (BytesIO) para evitar bloqueos de disco
                out_io = io.BytesIO()
                with pd.ExcelWriter(out_io, engine="openpyxl") as writer:
                    used_names = set()
                    for sheet_name, df in res.items():
                        base = sheet_name[:31]
                        sheet_name = base
                        i = 1
                        while sheet_name in used_names:
                            suffix = f"_{i}"
                            sheet_name = base[:31 - len(suffix)] + suffix
                            i += 1
                        used_names.add(sheet_name)
                        
                        df_safe = df.copy()
                        for col in df_safe.columns:
                            if df_safe[col].dtype == object:
                                df_safe[col] = df_safe[col].apply(lambda v: v if (v is None or isinstance(v, (str, int, float, bool))) else str(v))
                        df_safe.to_excel(writer, index=False, sheet_name=sheet_name)
                
                # Guardar temporalmente para dar formato estricto Cambria 8
                temp_file = "RESULTADOS_LOTES_TEMP.xlsx"
                with open(temp_file, "wb") as f:
                    f.write(out_io.getvalue())
                format_workbook(temp_file, font_name="Cambria", font_size=8)
                
                with open(temp_file, "rb") as f:
                    st.session_state["excel_bytes"] = f.read()
                
                if os.path.exists(temp_file):
                    os.remove(temp_file)
                    
                st.success("🎉 ¡Loteo completado con éxito! Reportes listos para descarga.")
            except Exception as e:
                st.session_state["excel_bytes"] = None
                st.error(f"❌ Error en la simulación: {e}")

# ---------------------------- 4. Despliegue de Resultados ----------------------------
if st.session_state["resultado"] is not None:
    st.header("4. Diagnóstico de Loteo y Descarga")
    
    df_m = st.session_state["resultado"].get("REPORTE_REGLAS_MIX", pd.DataFrame())
    if not df_m.empty:
        c1, c2 = st.columns(2)
        c1.metric("Volumen Total Asignado", f"{df_m['LBS_ASIGNADAS'].sum():,.2f} Lbs")
        c2.metric("Reactores Utilizados", f"{df_m['LOTE_ID'].nunique()} Lotes")
        
        st.subheader("Esquema General Generado")
        st.dataframe(df_m, use_container_width=True)
        
        if st.session_state["excel_bytes"] is not None:
            st.download_button(
                label="⬇️ Descargar RESULTADOS_LOTES.xlsx (Cambria 8)",
                data=st.session_state["excel_bytes"],
                file_name="RESULTADOS_LOTES.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
else:
    if uploaded_file is None:
        st.info("💡 Sube tu archivo maestro de producción en el Paso 1 para activar los paneles operativos.")
