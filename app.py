# ==============================================================================
# loteo_engine_v2.py
# Motor de optimización y loteo industrial NV2 reestructurado.
# Con soporte de tipado estricto, desacoplamiento y optimización de estado.
# ==============================================================================

from dataclasses import dataclass, field
from typing import List, Dict, Set, Tuple, Optional, Any
import pandas as pd
import numpy as np
import re


# ==============================================================================
# 1. MODELOS DE DATOS E INMUTABILIDAD
# ==============================================================================

@dataclass(frozen=True)
def OptimizationParams:
    """Configuración global y parámetros de penalización matemática del motor."""
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
    w_tipo_tejido_fleece: float = 4.0
    beam_width: int = 3


@dataclass(frozen=True)
def CapRange:
    """Representación inmutable de una capacidad/máquina de la tintorería."""
    rango_id: str
    categoria: str
    minimo: float
    maximo: float
    capacidad: float
    mix: str


@dataclass
def SkuRow:
    """Estado mutable de un SKU/item individual durante el proceso de asignación."""
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
def BatchCandidate:
    """Estructura de control para evaluar un lote propuesto antes de su consolidación."""
    rango_id: str
    categoria: str
    mix: str
    minimo: float
    maximo: float
    total_lote: float
    rows_assigned: List[Tuple[int, float]]  # List of (idx, lbs_asignadas)
    anchos_unicos: List[float]
    pct_carga_usado: float
    score: float = -1e30


# ==============================================================================
# 2. CAPA DE ENTRADA Y TRANSFORMACIÓN DE DATOS
# ==============================================================================

class DataRepository:
    """Encargado exclusivo de la lectura, limpieza y tipado de los datos de entrada."""
    
    REQUIRED_DATA_COLS = ["LNK", "TELA.CUERPO", "COLOR", "PRIORIDAD", "ANCHO.F.C", "ANCHO.F.M", "TOTAL", "MIX", "CONSUMO_C"]

    @staticmethod
    def clean_string(val: Any) -> str:
        if pd.isna(val):
            return ""
        return str(val).strip().upper()

    @classmethod
    def load_sku_rows(cls, df_data: pd.DataFrame) -> List[SkuRow]:
        """Transforma un DataFrame crudo en estructuras orientadas a objetos optimizadas."""
        sku_list = []
        
        # Validar consistencia columnar básica
        for col in cls.REQUIRED_DATA_COLS:
            if col not in df_data.columns:
                raise KeyError(f"La columna requerida '{col}' no se encuentra en el set de datos.")

        for idx, row in df_data.iterrows():
            # Mapeo y normalización con defaults controlados
            mix_val = cls.clean_string(row["MIX"])
            bloque_val = cls.parse_bloque(cls.clean_string(row["PRIORIDAD"]))
            
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
                lnk=cls.clean_string(row["LNK"]),
                tela_cuerpo=cls.clean_string(row["TELA.CUERPO"]),
                color=cls.clean_string(row["COLOR"]),
                tono=cls.clean_string(row.get("TONO", "")),
                prioridad=cls.clean_string(row["PRIORIDAD"]),
                bloque=bloque_val,
                familia=cls.clean_string(row.get("FAMILIA", "")),
                color_r=cls.clean_string(row.get("COLOR_R", "")),
                style=cls.clean_string(row.get("STYLE", "")),
                tipo_tejido=cls.clean_string(row.get("TIPO_TEJIDO", "")),
                pct_carga=float(pct_carga),
                consumo_c=max(0.0, float(pd.to_numeric(row["CONSUMO_C"], errors="coerce") or 0.0)),
                anchos=anchos,
                lbs_iniciales=max(0.0, float(pd.to_numeric(row["TOTAL"], errors="coerce") or 0.0)),
                lbs_restantes=max(0.0, float(pd.to_numeric(row["TOTAL"], errors="coerce") or 0.0))
            )
            sku_list.append(sku)
        return sku_list

    @staticmethod
    def parse_bloque(prio_text: str) -> str:
        if any(token in prio_text for token in ["PAST DUE", "DUE", "VENC"]):
            return "VENCIDOS"
        if "AHEAD2" in prio_text:
            return "AHEAD2"
        if "AHEAD" in prio_text:
            return "AHEAD"
        return "OTROS"


# ==============================================================================
# 3. MOTOR DE OPTIMIZACIÓN MATEMÁTICA Y REGLAS (STRATEGY PATTERN)
# ==============================================================================

class RuleEvaluator:
    """Aplica las directrices de orden de reglas e infiere prioridades y reordenamientos."""
    
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
                        # Validar si existe la contraparte con libras en el pool disponible
                        exists_target = any(s.lbs_restantes > 0 and any(abs(w - target) < 1e-6 for w in s.anchos) for s in sku_pool if s.idx != seed.idx)
                        if exists_target:
                            rule_info.update({"origen_prioridad": "COMBO", "combo_target_width": target})
                            return "COMBO_ANCHOS", rule_combo["prioridades"], rule_info

            elif rule == "COLOR_R" and seed.color_r in context_rules.get("restr_color", {}):
                prio = context_rules["restr_color"][seed.color_r]
                rule_info.update({"origen_prioridad": "COLOR"})
                return "COLOR_R", [prio], rule_info

            elif rule == "FAMILIA" and seed.familia in context_rules.get("restr_fam", {}):
                prios = context_rules["restr_fam"][seed.family]
                rule_info.update({"origen_prioridad": "FAMILIA"})
                return "FAMILIA", prios, rule_info

        return "DEFAULT", [], rule_info


class BatchScorer:
    """Evaluador multidimensional de la calidad física y económica de un lote."""
    
    @staticmethod
    def calculate_score(batch: BatchCandidate, unique_widths: Set[float], seed: SkuRow, params: OptimizationParams, context_rules: Dict[str, Any]) -> float:
        fill_rate = batch.total_lote / batch.maximo if batch.maximo > 1e-9 else 0.0
        cap_loss = batch.maximo - batch.total_lote
        len_widths = len(unique_widths)

        # Penalización por preferencia de cantidad de anchos
        try:
            rank = params.width_pref_list.index(len_widths)
        except ValueError:
            rank = len(params.width_pref_list) + abs(len_widths - params.width_pref_list[-1])
        width_pref_score = -float(rank)

        # Core score
        score = (params.w_fill * fill_rate) - (params.w_cap_loss * cap_loss) + (params.w_width_pref * width_pref_score)

        # Restricción estricta de anchos sobre categoría 1100
        if abs(batch.maximo - 1100.0) < 1e-6:
            score -= params.w_1100_strict * max(0, len_widths - 1)

        # Bono estratégico por Tipo de Tejido (Fleece) en reactores grandes
        if params.tipo_tejido_enable and batch.categoria in params.tipo_tejido_categorias:
            if seed.tipo_tejido == "FLEECE":
                restr_fam = context_rules.get("restr_fam", {})
                if seed.familia not in restr_fam or len(restr_fam.get(seed.familia, [])) == 0:
                    score += params.w_tipo_tejido_fleece

        return score


class LoteoEngine:
    """Motor central optimizado para el empaquetamiento eficiente de órdenes de producción."""
    
    def __init__(self, params: OptimizationParams, cap_ranges: List[CapRange], context_rules: Dict[str, Any]):
        self.params = params
        self.cap_ranges = sorted(cap_ranges, key=lambda x: x.maximo, reverse=True)
        self.context_rules = context_rules
        self.capacity_used: Dict[str, float] = {r.rango_id: 0.0 for r in self.cap_ranges}

    def _get_take_volume(self, rest: float, remaining: float, split_min: float) -> float:
        """Determina la partición exacta reduciendo variaciones de residuo."""
        if rest <= 0 or remaining <= 0:
            return 0.0
        if rest <= remaining + 1e-9:
            return rest
        if remaining + 1e-9 < split_min:
            return 0.0
        
        residue = rest - remaining
        if residue > 1e-9 and residue + 1e-9 < split_min:
            return remaining if self.params.scrap_remainder_below_split_min else 0.0
        return remaining

    def _is_width_group_valid(self, widths: List[float], max_widths: int) -> bool:
        """Vectorización conceptual de control de tolerancias de anchos cruzados."""
        unique_w = sorted(list(set(widths)))
        if len(unique_w) <= 1:
            return True
        if len(unique_w) > max_widths:
            return False
        
        # Matriz de diferencias absolutas acelerada por NumPy
        w_arr = np.array(unique_w)
        diffs = np.abs(w_arr[:, None] - w_arr)
        # Extraer triángulo superior excluyendo diagonal
        upper_idx = np.triu_indices(len(unique_w), k=1)
        active_diffs = diffs[upper_idx]
        
        return bool(np.all((active_diffs >= self.params.min_diff) & (active_diffs <= self.params.max_diff)))

    def _build_single_batch(self, seed: SkuRow, pool: List[SkuRow], range_target: CapRange, rule_info: Dict[str, Any], target_widths_count: Optional[int]) -> Optional[BatchCandidate]:
        rid = range_target.rango_id
        cap_left = max(0.0, range_target.capacidad - self.capacity_used[rid])
        max_effective = min(range_target.maximo * seed.pct_carga, cap_left)

        if seed.lbs_restantes <= 0 or max_effective <= 0:
            return None

        max_widths = self.params.max_widths_by_cat.get(range_target.categoria, self.params.max_widths_default)
        split_min = self.params.split_min_lbs_ancho18 if rule_info.get("regla_aplicada") == "ANCHO18" else self.params.split_min_lbs_default

        # Inicializar Candidato
        batch_lbs = 0.0
        assigned_rows = []
        batch_lnks = set()
        batch_blocks = []
        batch_widths = []

        def can_incorporate(sku: SkuRow, lbs_to_take: float) -> bool:
            if lbs_to_take <= 0:
                return False
            if seed.tono != sku.tono:
                return False
            if len(batch_lnks.union({sku.lnk})) > self.params.max_sku:
                return False
            if any((b, sku.bloque) not in self.params.mix_allowed for b in batch_blocks):
                return False
            
            candidate_widths = batch_widths + sku.anchos
            if not self._is_width_group_valid(candidate_widths, max_widths):
                return False
            
            if target_widths_count is not None:
                unique_c = set(candidate_widths)
                if len(unique_c) > target_widths_count:
                    return False
                    
            return (batch_lbs + lbs_to_take) <= (max_effective + 1e-9)

        # Intentar insertar elemento semilla
        take_seed = self._get_take_volume(seed.lbs_restantes, max_effective, split_min)
        if take_seed <= 0 or not can_incorporate(seed, take_seed):
            return None

        # Consolidar semilla en estructura temporal
        assigned_rows.append((seed.idx, take_seed))
        batch_lbs += take_seed
        batch_lnks.add(seed.lnk)
        batch_blocks.append(seed.bloque)
        batch_widths.extend(seed.anchos)

        # Bucle voraz de empaquetamiento sobre la cola del pool compatible
        for sku in pool:
            if sku.idx == seed.idx or sku.lbs_restantes <= 0:
                continue
            if batch_lbs >= max_effective - 1e-6:
                break

            remaining_space = max_effective - batch_lbs
            take_sku = self._get_take_volume(sku.lbs_restantes, remaining_space, split_min)
            
            if take_sku > 0 and can_incorporate(sku, take_sku):
                assigned_rows.append((sku.idx, take_sku))
                batch_lbs += take_sku
                batch_lnks.add(sku.lnk)
                batch_blocks.append(sku.bloque)
                batch_widths.extend(sku.anchos)

        # Validaciones de cierre de tolva industrial (Mínimo de Carga)
        if batch_lbs + 1e-9 < (range_target.minimo * seed.pct_carga):
            return None

        unique_batch_widths = set(batch_widths)
        if target_widths_count is not None and len(unique_batch_widths) < target_widths_count:
            return None

        # Si es regla COMBO_ANCHOS obligar a la mezcla efectiva de dos anchos
        if rule_info.get("regla_aplicada") == "COMBO_ANCHOS" and len(unique_batch_widths) < 2:
            return None

        candidate = BatchCandidate(
            rango_id=range_target.rango_id,
            categoria=range_target.categoria,
            mix=range_target.mix,
            minimo=range_target.minimo,
            maximo=range_target.maximo,
            total_lote=batch_lbs,
            rows_assigned=assigned_rows,
            anchos_unicos=sorted(list(unique_batch_widths)),
            pct_carga_usado=seed.pct_carga
        )
        candidate.score = BatchScorer.calculate_score(candidate, unique_batch_widths, seed, self.params, self.context_rules)
        return candidate

    def execute_optimization(self, sku_pool: List[SkuRow]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Orquestador principal del loteo por bloques prioritarios de planta."""
        detalle_salida = []
        resumen_salida = []
        lote_counter = 1

        # Agrupar por Tela, Tono/Color y Mix de proceso para evitar contaminación física en reactor
        df_view = pd.DataFrame([{
            "idx": s.idx, "tela": s.tela_cuerpo, "tono": s.tono, "color": s.color, "mix": s.mix, "bloque": s.bloque
        } for s in sku_pool])
        
        if df_view.empty:
            return [], []

        group_cols = ["tela", "tono", "mix"] if "tono" in df_view.columns else ["tela", "color", "mix"]
        grouped = df_view.groupby(group_cols)

        for keys, frame in grouped:
            sub_indices = frame["idx"].tolist()
            local_pool = [s for s in sku_pool if s.idx in sub_indices]

            block_sequence = ["VENCIDOS", "AHEAD", "AHEAD2", "OTROS"]
            
            while True:
                active_skus = [s for s in local_pool if s.lbs_restantes > 0]
                if not active_skus:
                    break
                
                batch_constructed = False

                for current_block in block_sequence:
                    block_skus = [s for s in active_skus if s.bloque == current_block]
                    if not block_skus:
                        continue

                    # Beam search conceptual ordenando por volumen remanente
                    block_skus.sort(key=lambda x: x.lbs_restantes, reverse=True)
                    seeds_to_test = block_skus[:self.params.beam_width]

                    best_candidate_pack: Optional[Tuple[BatchCandidate, Dict[str, Any], str]] = None

                    for seed in seeds_to_test:
                        rule_name, prio_list, rule_info = RuleEvaluator.evaluate_seed_context(
                            seed, local_pool, self.params, self.context_rules
                        )
                        rule_info["regla_aplicada"] = rule_name

                        # Filtrar reactores compatibles con el MIX del grupo de flujo
                        compatible_ranges = [r for r in self.cap_ranges if r.mix == seed.mix]

                        # Intentar empaquetamiento según el set-order de objetivos de anchos
                        for width_target in [2, 3, 4]:
                            for r_target in compatible_ranges:
                                if self.capacity_used[r_target.rango_id] >= r_target.capacidad - 1e-6:
                                    continue
                                
                                candidate = self._build_single_batch(seed, local_pool, r_target, rule_info, width_target)
                                if candidate and (best_candidate_pack is None or candidate.score > best_candidate_pack[0].score):
                                    best_candidate_pack = (candidate, rule_info, rule_name)

                        # Fallback holístico si no cumple la meta estricta de cantidad de anchos
                        if not best_candidate_pack:
                            for r_target in compatible_ranges:
                                if self.capacity_used[r_target.rango_id] >= r_target.capacidad - 1e-6:
                                    continue
                                candidate = self._build_single_batch(seed, local_pool, r_target, rule_info, None)
                                if candidate and (best_candidate_pack is None or candidate.score > best_candidate_pack[0].score):
                                    best_candidate_pack = (candidate, rule_info, rule_name)

                    if best_candidate_pack:
                        # Desempaquetar y consolidar el lote óptimo encontrado en el vecindario
                        final_batch, f_rule_info, f_rule_name = best_candidate_pack
                        lote_id_str = f"L{lote_counter:06d}"
                        lote_counter += 1

                        # Mutar el estado logístico del pool inyectando las asignaciones calculadas
                        for s_idx, lbs_asig in final_batch.rows_assigned:
                            target_sku = next(s for s in local_pool if s.idx == s_idx)
                            target_sku.lbs_restantes = max(0.0, target_sku.lbs_restantes - lbs_asig)
                            
                            # Manejo de Scrap Remanente bajo el límite mínimo de corte (Split Min)
                            split_limit = self.params.split_min_lbs_ancho18 if f_rule_name == "ANCHO18" else self.params.split_min_lbs_default
                            if self.params.scrap_remainder_below_split_min and 0.0 < target_sku.lbs_restantes < split_limit:
                                target_sku.lbs_scrap += target_sku.lbs_restantes
                                target_sku.lbs_restantes = 0.0

                            # Registrar fila de detalle consolidada
                            detalle_salida.append({
                                "LOTE_ID": lote_id_str,
                                "CATEGORIA": final_batch.categoria,
                                "MIX": final_batch.mix,
                                "LNK": target_sku.lnk,
                                "LBS_ASIGNADAS": lbs_asig,
                                "APLICA_REGLA": f_rule_name,
                                "DECISION_SCORE": final_batch.score
                            })

                        # Registrar fila resumen estructural
                        resumen_salida.append({
                            "LOTE_ID": lote_id_str,
                            "CATEGORIA": final_batch.categoria,
                            "MIX": final_batch.mix,
                            "LBS_TOTAL": final_batch.total_lote,
                            "ANCHOS_UNICOS": len(final_batch.anchos_unicos),
                            "REGLA_DOMINANTE": f_rule_name
                        })

                        self.capacity_used[final_batch.rango_id] += final_batch.total_lote
                        batch_constructed = True
                        break  # Romper secuenciación de bloques para re-evaluar la cola general

                if not batch_constructed:
                    # Si ningún bloque pudo generar un lote viable con las restricciones actuales, romper bucle de seguridad
                    break

        return detalle_salida, resumen_salida