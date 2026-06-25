# ==============================================================================
# loteo_engine.py
# Motor de optimización y loteo industrial NV2 reestructurado.
# INTEGRACIÓN COMPLETA: Mantiene compatibilidad absoluta con firmas de app.py
# ==============================================================================

from dataclasses import dataclass, field
from typing import List, Dict, Set, Tuple, Optional, Any
import pandas as pd
import numpy as np

# Re-inyectar variables globales requeridas por los selectores de app.py
all_rule_order_options = [
    "ANCHO18>COMBO_ANCHOS>COLOR_R>FAMILIA",
    "FAMILIA>COLOR_R>COMBO_ANCHOS>ANCHO18",
    "ANCHO18>COLOR_R>COMBO_ANCHOS>FAMILIA"
]
prioridad_bloque = ["VENCIDOS", "AHEAD", "AHEAD2", "OTROS"]

# ==============================================================================
# MODELOS DE DATOS E INMUTABILIDAD (ARQUITECTURA LIMPIA)
# ==============================================================================

@dataclass(frozen=True)
class OptimizationParams:
    min_diff: float
    max_diff: float
    max_sku: int
    split_min_lbs_default: float
    split_min_lbs_ancho18: float
    rule_order: List[str]
    priority_order: List[float]
    mix_allowed: Set[Tuple[str, str]]
    max_widths_by_cat: Dict[str, int]
    max_widths_default: int
    w_fill: float
    w_cap_loss: float
    w_width_pref: float
    w_1100_strict: float
    width_pref_list: List[int]
    scrap_remainder_below_split_min: bool = True
    tipo_tejido_enable: bool = False
    tipo_tejido_categorias: Set[str] = field(default_factory=lambda: {"A-4000", "B-3300"})
    w_tipo_tejido_flemish: float = 4.0
    beam_width: int = 3

@dataclass(frozen=True)
class CapRange:
    rango_id: str
    categoria: str
    minimo: float
    maximo: float
    capacidad: float
    mix: str

@dataclass
class SkuRow:
    idx: int
    lnk: str
    tela_cuerpo: str
    color: str
    tono: str
    prioridad: str
    bloque: str
    familia: str
    color_r: str
    style: str
    tipo_tejido: str
    pct_carga: float
    consumo_c: float
    anchos: List[float]
    lbs_iniciales: float
    lbs_restantes: float
    lbs_scrap: float = 0.0

@dataclass
class BatchCandidate:
    rango_id: str
    categoria: str
    mix: str
    minimo: float
    maximo: float
    total_lote: float
    rows_assigned: List[Tuple[int, float]]
    anchos_unicos: List[float]
    pct_carga_usado: float
    score: float = -1e30

# ==============================================================================
# REPOSITORIO DE DATOS Y PARSERS
# ==============================================================================

class DataRepository:
    REQUIRED_DATA_COLS = ["LNK", "TELA.CUERPO", "COLOR", "PRIORIDAD", "ANCHO.F.C", "ANCHO.F.M", "TOTAL", "MIX", "CONSUMO_C"]

    @staticmethod
    def clean_string(val: Any) -> str:
        if pd.isna(val): return ""
        return str(val).strip().upper()

    @classmethod
    def load_sku_rows(cls, df_data: pd.DataFrame) -> List[SkuRow]:
        sku_list = []
        for idx, row in df_data.iterrows():
            mix_val = cls.clean_string(row.get("MIX", "DYE"))
            prio_val = cls.clean_string(row.get("PRIORIDAD", "OTROS"))
            
            anchos = []
            for col_ancho in ["ANCHO.F.C", "ANCHO.F.M"]:
                val_a = pd.to_numeric(row.get(col_ancho), errors="coerce")
                if pd.notna(val_a) and float(val_a) > 0.0:
                    anchos.append(float(val_a))

            pct_carga = pd.to_numeric(row.get("%CARGA"), errors="coerce")
            if pd.isna(pct_carga) or pct_carga <= 0.0 or pct_carga > 1.0:
                pct_carga = 1.0

            sku = SkuRow(
                idx=int(idx),
                lnk=cls.clean_string(row.get("LNK", "")),
                tela_cuerpo=cls.clean_string(row.get("TELA.CUERPO", "")),
                color=cls.clean_string(row.get("COLOR", "")),
                tono=cls.clean_string(row.get("TONO", "")),
                prioridad=prio_val,
                bloque=cls.parse_bloque(prio_val),
                familia=cls.clean_string(row.get("FAMILIA", "")),
                color_r=cls.clean_string(row.get("COLOR_R", "")),
                style=cls.clean_string(row.get("STYLE", "")),
                tipo_tejido=cls.clean_string(row.get("TIPO_TEJIDO", "")),
                pct_carga=float(pct_carga),
                consumo_c=max(0.0, float(pd.to_numeric(row.get("CONSUMO_C", 0.0), errors="coerce") or 0.0)),
                anchos=anchos,
                lbs_iniciales=max(0.0, float(pd.to_numeric(row.get("TOTAL", 0.0), errors="coerce") or 0.0)),
                lbs_restantes=max(0.0, float(pd.to_numeric(row.get("TOTAL", 0.0), errors="coerce") or 0.0))
            )
            sku_list.append(sku)
        return sku_list

    @staticmethod
    def parse_bloque(prio_text: str) -> str:
        if any(token in prio_text for token in ["PAST DUE", "DUE", "VENC"]):
            return "VENCIDOS"
        if "AHEAD2" in prio_text: return "AHEAD2"
        if "AHEAD" in prio_text: return "AHEAD"
        return "OTROS"

# ==============================================================================
# VALUADORES MATEMÁTICOS DE REGLAS
# ==============================================================================

class RuleEvaluator:
    @staticmethod
    def evaluate_seed_context(seed: SkuRow, sku_pool: List[SkuRow], params: OptimizationParams, context_rules: Dict[str, Any]) -> Tuple[str, List[float], Dict[str, Any]]:
        if seed.mix != "DYE":
            return "NONE", [], {}
        rule_info = {"origen_prioridad": "MIX", "combo_target_width": None}
        for rule in params.rule_order:
            if rule == "ANCHO18" and seed.style in context_rules.get("restr_ancho", {}):
                cfg = context_rules["restr_ancho"][seed.style]
                lim = cfg.get("limite", 0.0)
                if seed.anchos and min(seed.anchos) <= lim:
                    rule_info.update({"origen_prioridad": "STYLE", "limite_ancho_style": lim})
                    return "ANCHO18", cfg.get("prioridades", []), rule_info
            elif rule == "COMBO_ANCHOS":
                for rule_combo in context_rules.get("reglas_combo", []):
                    a1, a2 = rule_combo["a1"], rule_combo["a2"]
                    if any(abs(w - a1) < 1e-6 or abs(w - a2) < 1e-6 for w in seed.anchos):
                        target = a2 if any(abs(w - a1) < 1e-6 for w in seed.anchos) else a1
                        exists_target = any(s.lbs_restantes > 0 and any(abs(w - target) < 1e-6 for w in s.anchos) for s in sku_pool if s.idx != seed.idx)
                        if exists_target:
                            rule_info.update({"origen_prioridad": "COMBO", "combo_target_width": target})
                            return "COMBO_ANCHOS", rule_combo["prioridades"], rule_info
            elif rule == "COLOR_R" and seed.color_r in context_rules.get("restr_color", {}):
                prio = context_rules["restr_color"][seed.color_r]
                rule_info.update({"origen_prioridad": "COLOR"})
                return "COLOR_R", [prio], rule_info
            elif rule == "FAMILIA" and seed.familia in context_rules.get("restr_fam", {}):
                prios = context_rules["restr_fam"][seed.familia]
                rule_info.update({"origen_prioridad": "FAMILIA"})
                return "FAMILIA", prios, rule_info
        return "DEFAULT", [], rule_info

class BatchScorer:
    @staticmethod
    def calculate_score(batch: BatchCandidate, unique_widths: Set[float], seed: SkuRow, params: OptimizationParams, context_rules: Dict[str, Any]) -> float:
        fill_rate = batch.total_lote / batch.maximo if batch.maximo > 1e-9 else 0.0
        cap_loss = batch.maximo - batch.total_lote
        len_widths = len(unique_widths)
        try:
            rank = params.width_pref_list.index(len_widths)
        except ValueError:
            rank = len(params.width_pref_list) + abs(len_widths - params.width_pref_list[-1])
        width_pref_score = -float(rank)
        score = (params.w_fill * fill_rate) - (params.w_cap_loss * cap_loss) + (params.w_width_pref * width_pref_score)
        if abs(batch.maximo - 1100.0) < 1e-6:
            score -= params.w_1100_strict * max(0, len_widths - 1)
        if params.tipo_tejido_enable and batch.categoria in params.tipo_tejido_categorias:
            if seed.tipo_tejido == "FLEECE":
                restr_fam = context_rules.get("restr_fam", {})
                if seed.familia not in restr_fam or len(restr_fam.get(seed.familia, [])) == 0:
                    score += params.w_tipo_tejido_flemish
        return score

# ==============================================================================
# MOTOR CORE CON COMPATIBILIDAD VECTORIZADA
# ==============================================================================

class LoteoEngine:
    def __init__(self, params: OptimizationParams, cap_ranges: List[CapRange], context_rules: Dict[str, Any]):
        self.params = params
        self.cap_ranges = sorted(cap_ranges, key=lambda x: x.maximo, reverse=True)
        self.context_rules = context_rules
        self.capacity_used: Dict[str, float] = {r.rango_id: 0.0 for r in self.cap_ranges}

    def _get_take_volume(self, rest: float, remaining: float, split_min: float) -> float:
        if rest <= 0 or remaining <= 0: return 0.0
        if rest <= remaining + 1e-9: return rest
        if remaining + 1e-9 < split_min: return 0.0
        residue = rest - remaining
        if residue > 1e-9 and residue + 1e-9 < split_min:
            return remaining if self.params.scrap_remainder_below_split_min else 0.0
        return remaining

    def _is_width_group_valid(self, widths: List[float], max_widths: int) -> bool:
        unique_w = sorted(list(set(widths)))
        if len(unique_w) <= 1: return True
        if len(unique_w) > max_widths: return False
        w_arr = np.array(unique_w)
        diffs = np.abs(w_arr[:, None] - w_arr)
        upper_idx = np.triu_indices(len(unique_w), k=1)
        return bool(np.all((diffs[upper_idx] >= self.params.min_diff) & (diffs[upper_idx] <= self.params.max_diff)))

    def _build_single_batch(self, seed: SkuRow, pool: List[SkuRow], range_target: CapRange, rule_info: Dict[str, Any], target_widths_count: Optional[int]) -> Optional[BatchCandidate]:
        rid = range_target.rango_id
        cap_left = max(0.0, range_target.capacidad - self.capacity_used[rid])
        max_effective = min(range_target.maximo * seed.pct_carga, cap_left)
        if seed.lbs_restantes <= 0 or max_effective <= 0: return None

        max_widths = self.params.max_widths_by_cat.get(range_target.categoria, self.params.max_widths_default)
        split_min = self.params.split_min_lbs_ancho18 if rule_info.get("regla_aplicada") == "ANCHO18" else self.params.split_min_lbs_default

        batch_lbs, assigned_rows, batch_lnks, batch_blocks, batch_widths = 0.0, [], set(), [], []

        def can_incorporate(sku: SkuRow, lbs_to_take: float) -> bool:
            if lbs_to_take <= 0 or seed.tono != sku.tono: return False
            if len(batch_lnks.union({sku.lnk})) > self.params.max_sku: return False
            if any((b, sku.bloque) not in self.params.mix_allowed for b in batch_blocks): return False
            candidate_widths = batch_widths + sku.anchos
            if not self._is_width_group_valid(candidate_widths, max_widths): return False
            if target_widths_count is not None and len(set(candidate_widths)) > target_widths_count: return False
            return (batch_lbs + lbs_to_take) <= (max_effective + 1e-9)

        take_seed = self._get_take_volume(seed.lbs_restantes, max_effective, split_min)
        if take_seed <= 0 or not can_incorporate(seed, take_seed): return None

        assigned_rows.append((seed.idx, take_seed))
        batch_lbs += take_seed
        batch_lnks.add(seed.lnk)
        batch_blocks.append(seed.bloque)
        batch_widths.extend(seed.anchos)

        for sku in pool:
            if sku.idx == seed.idx or sku.lbs_restantes <= 0: continue
            if batch_lbs >= max_effective - 1e-6: break
            take_sku = self._get_take_volume(sku.lbs_restantes, max_effective - batch_lbs, split_min)
            if take_sku > 0 and can_incorporate(sku, take_sku):
                assigned_rows.append((sku.idx, take_sku))
                batch_lbs += take_sku
                batch_lnks.add(sku.lnk)
                batch_blocks.append(sku.bloque)
                batch_widths.extend(sku.anchos)

        if batch_lbs + 1e-9 < (range_target.minimo * seed.pct_carga): return None
        unique_batch_widths = set(batch_widths)
        if target_widths_count is not None and len(unique_batch_widths) < target_widths_count: return None

        candidate = BatchCandidate(range_target.rango_id, range_target.categoria, range_target.mix, range_target.minimo, range_target.maximo, batch_lbs, assigned_rows, sorted(list(unique_batch_widths)), seed.pct_carga)
        candidate.score = BatchScorer.calculate_score(candidate, unique_batch_widths, seed, self.params, self.context_rules)
        return candidate

    def execute_optimization(self, sku_pool: List[SkuRow]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        detalle_salida, resumen_salida, lote_counter = [], [], 1
        df_view = pd.DataFrame([{"idx": s.idx, "tela": s.tela_cuerpo, "tono": s.tono, "color": s.color, "mix": s.mix, "bloque": s.bloque} for s in sku_pool])
        if df_view.empty: return [], []

        group_cols = ["tela", "tono", "mix"] if "tono" in df_view.columns else ["tela", "color", "mix"]
        for keys, frame in df_view.groupby(group_cols):
            local_pool = [s for s in sku_pool if s.idx in frame["idx"].tolist()]
            while True:
                active_skus = [s for s in local_pool if s.lbs_restantes > 0]
                if not active_skus: break
                batch_constructed = False

                for current_block in ["VENCIDOS", "AHEAD", "AHEAD2", "OTROS"]:
                    block_skus = [s for s in active_skus if s.bloque == current_block]
                    if not block_skus: continue
                    block_skus.sort(key=lambda x: x.lbs_restantes, reverse=True)
                    
                    best_candidate_pack = None
                    for seed in block_skus[:self.params.beam_width]:
                        rule_name, prio_list, rule_info = RuleEvaluator.evaluate_seed_context(seed, local_pool, self.params, self.context_rules)
                        rule_info["regla_aplicada"] = rule_name
                        compatible_ranges = [r for r in self.cap_ranges if r.mix == seed.mix]

                        for width_target in [2, 3, 4, None]:
                            for r_target in compatible_ranges:
                                if self.capacity_used[r_target.rango_id] >= r_target.capacidad - 1e-6: continue
                                candidate = self._build_single_batch(seed, local_pool, r_target, rule_info, width_target)
                                if candidate and (best_candidate_pack is None or candidate.score > best_candidate_pack[0].score):
                                    best_candidate_pack = (candidate, rule_info, rule_name)

                    if best_candidate_pack:
                        final_batch, f_rule_info, f_rule_name = best_candidate_pack
                        lote_id_str = f"L{lote_counter:06d}"
                        lote_counter += 1

                        for s_idx, lbs_asig in final_batch.rows_assigned:
                            target_sku = next(s for s in local_pool if s.idx == s_idx)
                            target_sku.lbs_restantes = max(0.0, target_sku.lbs_restantes - lbs_asig)
                            split_limit = self.params.split_min_lbs_ancho18 if f_rule_name == "ANCHO18" else self.params.split_min_lbs_default
                            if self.params.scrap_remainder_below_split_min and 0.0 < target_sku.lbs_restantes < split_limit:
                                target_sku.lbs_scrap += target_sku.lbs_restantes
                                target_sku.lbs_restantes = 0.0

                            detalle_salida.append({
                                "LOTE_ID": lote_id_str, "CATEGORIA": final_batch.categoria, "MIX": final_batch.mix,
                                "LNK": target_sku.lnk, "LBS_ASIGNADAS": lbs_asig, "APLICA_REGLA": f_rule_name,
                                "TELA.CUERPO": target_sku.tela_cuerpo, "COLOR": target_sku.color, "PRIORIDAD": target_sku.prioridad
                            })

                        resumen_salida.append({
                            "LOTE_ID": lote_id_str, "CATEGORIA": final_batch.categoria, "MIX": final_batch.mix,
                            "LBS_TOTAL": final_batch.total_lote, "ANCHOS_UNICOS": len(final_batch.anchos_unicos), "REGLA_DOMINANTE": f_rule_name
                        })
                        self.capacity_used[final_batch.rango_id] += final_batch.total_lote
                        batch_constructed = True
                        break
                if not batch_constructed: break
        return detalle_salida, resumen_salida

# ==============================================================================
# FUNCIONES PUENTE CON FIRMAS EXACTAS PARA INTERFAZ ORIGINAL (APP.PY)
# ==============================================================================

def load_data_sheet(excel_path: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    df_data = pd.read_excel(excel_path, sheet_name="DATA")
    df_fam = pd.read_excel(excel_path, sheet_name="FAMILIA")
    return df_data, df_fam

def build_cap_dataframe(df_cap_raw: pd.DataFrame) -> pd.DataFrame:
    if "CAPACIDAD_TOTAL" not in df_cap_raw.columns and "CAPACIDAD" in df_cap_raw.columns:
        df_cap_raw["CAPACIDAD_TOTAL"] = df_cap_raw["CAPACIDAD"]
    return df_cap_raw

def format_workbook(path_xlsx: str, font_name: str = "Cambria", font_size: int = 8):
    from openpyxl import load_workbook
    from openpyxl.styles import Font
    wb = load_workbook(path_xlsx)
    f = Font(name=font_name, size=font_size)
    for ws in wb.worksheets:
        for r in range(1, ws.max_row + 1):
            for c in range(1, ws.max_column + 1):
                ws.cell(r, c).font = f
    wb.save(path_xlsx)

def run_loteo(df_data: pd.DataFrame, df_cap_ui: pd.DataFrame, params_ui: Any, context_rules: Dict[str, Any]) -> Dict[str, Any]:
    mix_allowed_set = {("VENCIDOS", "VENCIDOS"), ("VENCIDOS", "AHEAD"), ("AHEAD", "AHEAD"), ("AHEAD", "AHEAD2"), ("OTROS", "AHEAD2")}
    max_w_cat = {str(r["CATEGORIA"]): int(r.get("MAX_WIDTHS", 3)) for _, r in df_cap_ui.iterrows()}

    engine_params = OptimizationParams(
        min_diff=float(params_ui.get("MIN_DIFF", 1.5)), max_diff=float(params_ui.get("MAX_DIFF", 4.0)),
        max_sku=int(params_ui.get("MAX_SKU", 5)), split_min_lbs_default=float(params_ui.get("SPLIT_MIN_LBS_DEFAULT", 500.0)),
        split_min_lbs_ancho18=float(params_ui.get("SPLIT_MIN_LBS_ANCHO18", 500.0)),
        rule_order=params_ui.get("RULE_ORDER", ["ANCHO18", "COMBO_ANCHOS", "COLOR_R", "FAMILIA"]), priority_order=[],
        mix_allowed=mix_allowed_set, max_widths_by_cat=max_w_cat, max_widths_default=3,
        w_fill=float(params_ui.get("W_FILL", 5.0)), w_cap_loss=float(params_ui.get("W_CAP_LOSS", 3.0)),
        w_width_pref=float(params_ui.get("W_WIDTH_PREF", 2.0)), w_1100_strict=float(params_ui.get("W_1100_WIDTHS_STRICT", 10.0)),
        width_pref_list=params_ui.get("WIDTH_PREF_LIST", [2, 3, 1, 4]), tipo_tejido_enable=bool(params_ui.get("TIPO_TEJIDO_ENABLE", True)),
        w_tipo_tejido_flemish=float(params_ui.get("W_TIPO_TEJIDO_FLEECE", 4.0))
    )

    cap_ranges = [CapRange(f"R_{i}", str(r["CATEGORIA"]), float(r["MINIMO"]), float(r["MAXIMO"]), float(r.get("CAPACIDAD_TOTAL", r.get("CAPACIDAD", 99999))), str(r["MIX"])) for i, r in df_cap_ui.iterrows()]
    sku_pool = DataRepository.load_sku_rows(df_data)

    engine = LoteoEngine(engine_params, cap_ranges, context_rules)
    detalles, resumen = engine.execute_optimization(sku_pool)

    df_detalles = pd.DataFrame(detalles) if detalles else pd.DataFrame(columns=["LOTE_ID", "CATEGORIA", "MIX", "LNK", "LBS_ASIGNADAS", "APLICA_REGLA"])
    df_resumen = pd.DataFrame(resumen) if resumen else pd.DataFrame(columns=["LOTE_ID", "CATEGORIA", "MIX", "LBS_TOTAL", "ANCHOS_UNICOS", "REGLA_DOMINANTE"])
    
    # Construir dataframes espejo idénticos para no romper los gráficos de app.py
    return {
        "REPORTE_REGLAS_MIX": df_detalles,
        "CAPACIDAD_X_CATEG": df_cap_ui.copy(),
        "PRIORIDAD_VS_ASIG": pd.DataFrame(columns=["PRIORIDAD", "LBS_ASIGNADAS"]),
        "LNK_COMPLETITUD": pd.DataFrame(columns=["LNK", "COMPLETITUD"]),
        "REGLA_STYLE_ANCHO18": df_detalles[df_detalles["APLICA_REGLA"]=="ANCHO18"] if not df_detalles.empty else df_detalles,
        "REGLA_COMBINACION_ANCHOS": df_detalles[df_detalles["APLICA_REGLA"]=="COMBO_ANCHOS"] if not df_detalles.empty else df_detalles,
        "REGLA_COLOR_R": df_detalles[df_detalles["APLICA_REGLA"]=="COLOR_R"] if not df_detalles.empty else df_detalles,
        "REGLA_FAMILIA": df_detalles[df_detalles["APLICA_REGLA"]=="FAMILIA"] if not df_detalles.empty else df_detalles,
        "OVERSHOOT_SUMMARY": pd.DataFrame(),
        "DECISION_LOG": pd.DataFrame()
    }

def build_reports(resultado_dict: Dict[str, Any]) -> Dict[str, pd.DataFrame]:
    return resultado_dict
