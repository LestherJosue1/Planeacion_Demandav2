# ==============================================================================
# app.py — App Streamlit de Loteo de Tintorería (NV2)
# ==============================================================================

import streamlit as st
import pandas as pd
import os
from loteo_engine import run_loteo, build_reports, format_workbook

st.set_page_config(page_title="Loteo de Tintorería NV2", layout="wide")
st.title("🧵 Sistema de Loteo de Tintorería — NV2 PRO")

if "excel_bytes" not in st.session_state:
    st.session_state["excel_bytes"] = None

uploaded_file = st.file_uploader("Sube el archivo maestro de producción (.xlsx)", type=["xlsx"])

if uploaded_file is not None:
    # Guardar temporalmente para procesos de openpyxl
    temp_path = "temp_maestro.xlsx"
    with open(temp_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
        
    try:
        df_data = pd.read_excel(temp_path, sheet_name="DATA")
        st.success("📊 Hoja 'DATA' cargada correctamente.")
        
        # Simulación de parámetros UI configurables por planta
        ui_params = {
            "MIN_DIFF": 1.5, "MAX_DIFF": 4.0, "MAX_SKU": 5,
            "SPLIT_MIN_LBS_DEFAULT": 500.0, "SPLIT_MIN_LBS_ANCHO18": 500.0,
            "W_FILL": 5.0, "W_CAP_LOSS": 3.0, "W_WIDTH_PREF": 2.0,
            "W_1100_WIDTHS_STRICT": 10.0, "TIPO_TEJIDO_ENABLE": True,
            "W_TIPO_TEJIDO_FLEECE": 4.0, "RULE_ORDER": ["ANCHO18", "COMBO_ANCHOS", "COLOR_R", "FAMILIA"]
        }
        
        # DataFrame de capacidades simulado (se puede alimentar del Excel)
        df_cap_ui = pd.DataFrame([
            {"CATEGORIA": "A-4000", "MINIMO": 3200.0, "MAXIMO": 4000.0, "CAPACIDAD_TOTAL": 40000.0, "MIX": "DYE", "MAX_WIDTHS": 4},
            {"CATEGORIA": "B-3300", "MINIMO": 2600.0, "MAXIMO": 3300.0, "CAPACIDAD_TOTAL": 33000.0, "MIX": "DYE", "MAX_WIDTHS": 4},
            {"CATEGORIA": "E-1100", "MINIMO": 900.0, "MAXIMO": 1100.0, "CAPACIDAD_TOTAL": 11000.0, "MIX": "DYE", "MAX_WIDTHS": 2}
        ])
        
        context_rules = {"restr_ancho": {}, "reglas_combo": [], "restr_color": {}, "restr_fam": {}}

        if st.button("🚀 Ejecutar Loteo de Planta"):
            res_dict = run_loteo(df_data, df_cap_ui, ui_params, context_rules)
            reports = build_reports(res_dict)
            
            st.subheader("📈 Resumen de Lotes Generados")
            st.dataframe(reports["RESUMEN_LOTEO"])
            
            # Exportar y formatear con Cambria 8
            out_path = "RESULTADOS_LOTES.xlsx"
            with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
                reports["RESUMEN_LOTEO"].to_excel(writer, sheet_name="RESUMEN", index=False)
                reports["DETALLE_LOTEO"].to_excel(writer, sheet_name="DETALLE", index=False)
            
            format_workbook(out_path)
            with open(out_path, "rb") as f:
                st.session_state["excel_bytes"] = f.read()
            st.success("✅ Excel optimizado listo para descarga.")
            
    except Exception as e:
        st.error(f"Error procesando el archivo: {e}")
        
if st.session_state["excel_bytes"] is not None:
    st.download_button(
        label="⬇️ Descargar RESULTADOS_LOTES.xlsx",
        data=st.session_state["excel_bytes"],
        file_name="RESULTADOS_LOTES.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
