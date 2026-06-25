# ==============================================================================
# app.py — INTERFAZ MAESTRA DE LOTEO DE TINTORERÍA (NV2 PRO)
# ==============================================================================
import io
import os
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px

# Importaciones seguras desde tus módulos locales originales
from loteo_engine import (
    load_data_sheet, run_loteo, format_workbook, 
    all_rule_order_options, prioridad_bloque
)
from reglas_operativas_parser import parse_reglas_operativas

# 1. Configuración de la página (Debe ser la primera instrucción de Streamlit)
st.set_page_config(page_title="Loteo de Tintorería NV2", layout="wide")

st.title("🧵 Loteo de Tintorería — NV2 PRO")

# ------------------------------------------------------------------------------
# Inicialización Segura del Session State
# ------------------------------------------------------------------------------
for key, default in [
    ("df_data", None), ("df_fam", None), ("reglas_raw", None),
    ("params", None), ("df_cap", None), ("resultado", None), 
    ("excel_bytes", None)
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ------------------------------------------------------------------------------
# Paso 1: Carga de Archivos e Inicialización
# ------------------------------------------------------------------------------
st.header("1. Entrada de Datos de Planta")
uploaded_file = st.file_uploader("Sube el archivo maestro de producción (.xlsx)", type=["xlsx"])

if uploaded_file is not None:
    # Usamos un botón para disparar la lectura y evitar sobrecargas continuas
    if st.button("🔄 Inicializar y Cargar Datos del Excel", type="primary"):
        with st.spinner("Leyendo estructura de pestañas y cargando parámetros de planta..."):
            try:
                # 1. Cargar las hojas de datos principales utilizando loteo_engine
                df_data, df_fam = load_data_sheet(uploaded_file)
                
                # 2. Parsear las reglas operativas utilizando reglas_operativas_parser
                reglas_raw, params_default, df_cap_default = parse_reglas_operativas(uploaded_file)
                
                # 3. Guardar todo limpiamente en el Session State
                st.session_state["df_data"] = df_data
                st.session_state["df_fam"] = df_fam
                st.session_state["reglas_raw"] = reglas_raw
                st.session_state["params"] = params_default
                st.session_state["df_cap"] = df_cap_default
                
                # Limpiar resultados anteriores para evitar inconsistencias
                st.session_state["resultado"] = None
                st.session_state["excel_bytes"] = None
                
                st.success("✅ ¡Hojas DATA, FAMILIA y REGLAS cargadas con éxito!")
            except Exception as e:
                st.error(f"❌ Error al procesar el archivo Excel: {str(e)}")

# ------------------------------------------------------------------------------
# Paso 2: Configuración Dinámica de Parámetros (Solo si hay datos cargados)
# ------------------------------------------------------------------------------
if st.session_state["df_data"] is not None:
    st.header("2. Configuración Dinámica de Parámetros")
    
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("⚙️ Tolerancias Técnicas")
        st.session_state["params"]["MIN_DIFF"] = st.number_input(
            "Diferencia Mínima de Ancho (pulgadas)", 
            value=float(st.session_state["params"].get("MIN_DIFF", 1.5))
        )
        st.session_state["params"]["MAX_DIFF"] = st.number_input(
            "Diferencia Máxima de Ancho (pulgadas)", 
            value=float(st.session_state["params"].get("MAX_DIFF", 4.0))
        )
        st.session_state["params"]["MAX_SKU"] = st.number_input(
            "Máximo de LNKs por Reactor", 
            value=int(st.session_state["params"].get("MAX_SKU", 5")), 
            step=1
        )
    
    with col2:
        st.subheader("⚖️ Pesos de Optimización (Scoring)")
        st.session_state["params"]["W_FILL"] = st.slider(
            "Importancia del Factor de Carga", 
            0.0, 20.0, float(st.session_state["params"].get("W_FILL", 5.0))
        )
        st.session_state["params"]["W_CAP_LOSS"] = st.slider(
            "Penalización de Espacio Vacío", 
            0.0, 20.0, float(st.session_state["params"].get("W_CAP_LOSS", 3.0))
        )
        
    # Selección del orden de ejecución de las reglas
    default_order_str = ">".join(st.session_state["params"].get("RULE_ORDER", []))
    if default_order_str not in all_rule_order_options:
        all_rule_order_options.append(default_order_str)
        
    selected_order = st.selectbox(
        "Estrategia de Secuenciación (Orden de Reglas):", 
        all_rule_order_options,
        index=all_rule_order_options.index(default_order_str) if default_order_str in all_rule_order_options else 0
    )
    st.session_state["params"]["RULE_ORDER"] = selected_order.split(">")

    # --------------------------------------------------------------------------
    # Paso 3: Ejecución del Algoritmo Combinatorio
    # --------------------------------------------------------------------------
    st.header("3. Ejecutar Algoritmo Combinatorio")
    if st.button("🚀 Iniciar Loteo de Producción", type="primary"):
        with st.spinner("Corriendo motor de optimización..."):
            try:
                # Ejecutar algoritmo nativo de loteo_engine
                res = run_loteo(
                    st.session_state["df_data"], 
                    st.session_state["df_cap"], 
                    st.session_state["params"], 
                    st.session_state["reglas_raw"]
                )
                st.session_state["resultado"] = res
                
                # Escritura segura del archivo de salida
                out_path = "RESULTADOS_LOTES_TEMP.xlsx"
                with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
                    for sheet_name, df in res.items():
                        df_safe = df.copy()
                        # Formatear columnas de tipo object para evitar conflictos en openpyxl
                        for col in df_safe.columns:
                            if df_safe[col].dtype == object:
                                df_safe[col] = df_safe[col].apply(lambda v: v if (v is None or isinstance(v, (str, int, float, bool))) else str(v))
                        df_safe.to_excel(writer, index=False, sheet_name=sheet_name[:31])
                
                # Aplicar tipografía Cambria 8 por defecto del sistema analítico
                format_workbook(out_path, font_name="Cambria", font_size=8)
                
                with open(out_path, "rb") as f:
                    st.session_state["excel_bytes"] = f.read()
                
                if os.path.exists(out_path):
                    os.remove(out_path)
                    
                st.success("🎉 ¡Loteo completado y optimizado con éxito!")
            except Exception as e:
                st.error(f"❌ Error durante el procesamiento del loteo: {str(e)}")

# ------------------------------------------------------------------------------
# Paso 4: Visualización de Tablas, KPIs y Descargas
# ------------------------------------------------------------------------------
if st.session_state["resultado"] is not None:
    st.header("4. Visualización de Indicadores y Descarga")
    df_maestro = st.session_state["resultado"].get("REPORTE_REGLAS_MIX", pd.DataFrame())
    
    if not df_maestro.empty:
        total_lbs = df_maestro["LBS_ASIGNADAS"].sum()
        total_lotes = df_maestro["LOTE_ID"].nunique()
        
        kpi1, kpi2 = st.columns(2)
        kpi1.metric("Volumen Total Loteado", f"{total_lbs:,.2f} Lbs")
        kpi2.metric("Reactores Ocupados", f"{total_lotes} Lotes")
        
        st.subheader("Esquema Maestro de Lotes Generados")
        st.dataframe(df_maestro, use_container_width=True)
        
        st.subheader("Eficiencia de Distribución de Carga")
        fig = px.bar(
            df_maestro.groupby(["APLICA_REGLA", "CATEGORIA"])["LBS_ASIGNADAS"].sum().reset_index(), 
            x="APLICA_REGLA", y="LBS_ASIGNADAS", color="CATEGORIA", 
            title="Libras Asignadas por Directiva Operativa"
        )
        st.plotly_chart(fig, use_container_width=True)
        
        st.download_button(
            label="⬇️ Descargar RESULTADOS_LOTES.xlsx (Formato Cambria 8)",
            data=st.session_state["excel_bytes"],
            file_name="RESULTADOS_LOTES.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="secondary"
        )
else:
    if st.session_state["df_data"] is None:
        st.info("💡 Por favor, sube un archivo Excel válido en el Paso 1 para comenzar a operar.")
