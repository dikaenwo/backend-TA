"""
recommend.py – Product Recommendation Engine for B-Glow
Prefix: /api/recommend

Algoritma v3 (Constraint-Based Filtering + WSM):
  - Arsitektur berdasarkan Burke (2002): constraint vs preference/utility
  - Jika user PUNYA masalah kulit:
    • Jenis Kulit  → Rule-Based Filtering (eligibility constraint)
      - Hard reject jika ingredient top-20% tidak cocok jenis kulit user
      - Ingredient di posisi >20% yang tidak cocok → warning saja
    • Masalah Kulit → WSM single-axis (ranking criterion)
  - Jika user TIDAK PUNYA masalah kulit (fallback mode):
    • Jenis Kulit  → WSM single-axis (ranking criterion, bukan filter)
    • Semua produk tetap mendapat skor berdasarkan kesesuaian jenis kulit
  - Bobot posisi ingredient: atas 20% → 1.0, 20-50% → 0.5, sisanya → 0.2
  - Normalisasi ke [-100, 100] agar panjang ingredient list tidak bias
  - Handle alias: nama/alternatif (/) dan nama (dalam kurung)
  - Produk lolos filter diurutkan dari skor tertinggi
"""

from __future__ import annotations

from flask import Blueprint, request, jsonify
import pandas as pd
import os
import re
from difflib import get_close_matches

recommend_bp = Blueprint('recommend', __name__, url_prefix='/api/recommend')

# ─── Path Dataset ─────────────────────────────────────────────────────────────
_BASE    = os.path.dirname(os.path.abspath(__file__))
_DATASET = os.path.join(_BASE, 'Dataset')

# ─── Lazy cache (dimuat saat request pertama) ─────────────────────────────────
_cache = {}

# Jenis Kulit & Masalah Kulit yang valid (sesuai dataset terbaru)
VALID_JENIS_KULIT   = {'Normal', 'Berminyak', 'Kering', 'Kombinasi'}
VALID_MASALAH_KULIT = {'Berjerawat', 'PIE', 'PIH', 'Aging', 'Kusam', 'Kemerahan'}

# ─── Evidence Strength Multipliers (for masalah kulit cocok) ─────────────────
# Koreksi kecil berdasarkan kekuatan bukti dermatologi.
# Diterapkan SETELAH bobot posisi → tidak menggantikan posisi.
EVIDENCE_PRIMARY     = 1.15   # Strong dermatological evidence
EVIDENCE_SUPPORTIVE  = 1.0    # Supportive / anecdotal evidence
EVIDENCE_DEFAULT     = 1.0    # No evidence data available

# ─── Contraindication Strength Multipliers (for masalah kulit tidak cocok) ────
# Memperbesar penalti untuk ingredient dengan bukti kontraindikasi kuat.
CONTRA_HIGH     = 1.4    # Strong contraindication evidence
CONTRA_MODERATE = 1.2    # Moderate contraindication evidence
CONTRA_LOW      = 1.0    # Low / weak contraindication evidence
CONTRA_DEFAULT  = 1.0    # No contraindication data available


def _get_data():
    """Load dataset sekali lalu cache. Raise RuntimeError jika gagal."""
    if _cache:
        return _cache
    try:
        rules_df = pd.read_csv(os.path.join(_DATASET, 'Dataset Terbaru.csv'))
        _cache['rules_df']  = rules_df
        _cache['produk_df'] = pd.read_excel(os.path.join(_DATASET, 'Dataset Produk.xlsx'))

        # Build evidence & contraindication maps once on first load
        _cache['evidence_maps'] = _build_evidence_maps(rules_df)

        print("[Recommend] Dataset Terbaru.csv & Dataset Produk.xlsx berhasil dimuat.")
        print(f"  Rules: {len(rules_df)} baris | Produk: {len(_cache['produk_df'])} baris")
        ev_maps = _cache['evidence_maps']
        print(f"  Evidence map: {len(ev_maps.get('evidence', {}))} ingredients")
        print(f"  Contraindication map: {len(ev_maps.get('contraindication', {}))} ingredients")
    except Exception as e:
        _cache.clear()
        raise RuntimeError(f"Gagal memuat dataset: {e}") from e
    return _cache


# ─── Evidence & Contraindication Map Builders ────────────────────────────────

def _build_evidence_maps(rules_df: pd.DataFrame) -> dict:
    """
    Parse 'Masalah Kulit Evidence Strength' dan 'Masalah Kulit Contraindication Strength'
    menjadi nested dict: { ingredient_lower: { masalah_kulit: level } }

    Returns dict with keys 'evidence' and 'contraindication'.
    """
    evidence_map = {}           # ingredient_lower -> {masalah: 'Primary'|'Supportive'}
    contraindication_map = {}   # ingredient_lower -> {masalah: 'High'|'Moderate'|'Low'}

    for _, row in rules_df.iterrows():
        name = str(row.get('Ingredient', '')).strip()
        if not name or name == 'nan':
            continue
        key = name.lower()

        # Parse Evidence Strength
        ev_raw = str(row.get('Masalah Kulit Evidence Strength', '') or '').strip()
        if ev_raw and ev_raw != '-' and ev_raw != 'nan':
            evidence_map[key] = _parse_strength_pairs(ev_raw)

        # Parse Contraindication Strength
        ct_raw = str(row.get('Masalah Kulit Contraindication Strength', '') or '').strip()
        if ct_raw and ct_raw != '-' and ct_raw != 'nan':
            contraindication_map[key] = _parse_strength_pairs(ct_raw)

    return {'evidence': evidence_map, 'contraindication': contraindication_map}


def _parse_strength_pairs(raw: str) -> dict:
    """
    Parse 'Berjerawat: Primary; PIH: Supportive'
    → {'Berjerawat': 'Primary', 'PIH': 'Supportive'}
    """
    result = {}
    pairs = raw.split(';')
    for pair in pairs:
        pair = pair.strip()
        if ':' in pair:
            masalah, level = pair.rsplit(':', 1)
            result[masalah.strip()] = level.strip()
    return result


def _get_evidence_multiplier(ingredient_lower: str, masalah_kulit: str,
                              evidence_map: dict) -> float:
    """Lookup evidence multiplier for specific ingredient + masalah kulit combination."""
    mk_map = evidence_map.get(ingredient_lower, {})
    level = mk_map.get(masalah_kulit, '')
    if level == 'Primary':
        return EVIDENCE_PRIMARY
    return EVIDENCE_DEFAULT


def _get_contraindication_multiplier(ingredient_lower: str, masalah_kulit: str,
                                      contraindication_map: dict) -> float:
    """Lookup contraindication multiplier for specific ingredient + masalah kulit combination."""
    mk_map = contraindication_map.get(ingredient_lower, {})
    level = mk_map.get(masalah_kulit, '')
    if level == 'High':
        return CONTRA_HIGH
    elif level == 'Moderate':
        return CONTRA_MODERATE
    return CONTRA_DEFAULT


def _evidence_label(level: str) -> str:
    """Convert evidence level string to display label."""
    if level == 'Primary':
        return 'Primary'
    elif level == 'Supportive':
        return 'Supportive'
    return '-'


def _contra_label(level: str) -> str:
    """Convert contraindication level string to display label."""
    if level == 'High':
        return 'High'
    elif level == 'Moderate':
        return 'Moderate'
    elif level == 'Low':
        return 'Low'
    return '-'


# ─── Dataset Audit (Revisi 3) ─────────────────────────────────────────────────
# Audit ringan, read-only, TIDAK menyentuh pipeline scoring/filtering.
# Tidak ada nama ingredient yang di-hardcode — semua statistik murni
# hasil agregasi terhadap isi dataset saat ini, jadi tetap valid kalau
# datasetnya diperbarui.

def _audit_evidence_dataset(rules_df: pd.DataFrame, evidence_maps: dict) -> dict:
    """
    Audit kelengkapan & konsistensi kolom Evidence Strength dan
    Contraindication Strength pada Dataset Terbaru.csv.

    Tidak melakukan penilaian benar/salah terhadap suatu ingredient —
    hanya melaporkan apa yang ADA dan apa yang KOSONG di dataset,
    plus baris duplikat yang berpotensi menimbulkan entri kontradiktif.
    """
    names = rules_df.get('Ingredient', pd.Series(dtype=str)).dropna().astype(str).str.strip()
    names = names[(names != '') & (names.str.lower() != 'nan')]
    total_unique = names.str.lower().nunique()

    evidence_map = (evidence_maps or {}).get('evidence', {})
    contra_map   = (evidence_maps or {}).get('contraindication', {})

    with_evidence = len(evidence_map)
    with_contra   = len(contra_map)

    # Baris dengan nama ingredient yang sama (potensi entri konflik/duplikat)
    lower_names = names.str.lower()
    dup_series  = lower_names[lower_names.duplicated(keep=False)]
    duplicate_ingredients = sorted(dup_series.value_counts().to_dict().items())

    # Distribusi level evidence & contraindication APA ADANYA di dataset
    # (bukan daftar tetap — hanya merefleksikan nilai unik yang muncul)
    evidence_level_counts = {}
    for mk_map in evidence_map.values():
        for level in mk_map.values():
            evidence_level_counts[level] = evidence_level_counts.get(level, 0) + 1

    contra_level_counts = {}
    for mk_map in contra_map.values():
        for level in mk_map.values():
            contra_level_counts[level] = contra_level_counts.get(level, 0) + 1

    return {
        'total_unique_ingredients': int(total_unique),
        'ingredients_with_evidence_data': with_evidence,
        'ingredients_with_contraindication_data': with_contra,
        'evidence_coverage_pct': round(with_evidence / total_unique * 100, 1) if total_unique else 0.0,
        'contraindication_coverage_pct': round(with_contra / total_unique * 100, 1) if total_unique else 0.0,
        'evidence_level_distribution': evidence_level_counts,
        'contraindication_level_distribution': contra_level_counts,
        'duplicate_ingredient_rows': [
            {'ingredient': name, 'jumlah_baris': int(count)}
            for name, count in duplicate_ingredients
        ],
        'catatan': (
            'Audit ini hanya melaporkan kelengkapan & duplikasi data apa adanya. '
            'Tidak ada asumsi default (mis. "selalu High") untuk ingredient mana pun; '
            'level yang tidak tercatat di dataset akan tetap diperlakukan sebagai '
            'CONTRA_DEFAULT/EVIDENCE_DEFAULT oleh mesin scoring.'
        ),
    }


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _bobot_posisi(index: int, total: int) -> tuple[float, float]:
    """
    Bobot berdasarkan posisi ingredient dalam formula produk:
      Top 20%    (Utama)   → +1.0 / -2.0
      20–50%     (Menengah)→ +0.5 / -1.0
      >50%       (Minor)   → +0.2 / -0.5
    """
    persen = (index + 1) / total
    if persen <= 0.2:
        return 1.0, 2.0
    elif persen <= 0.5:
        return 0.5, 1.0
    return 0.2, 0.5


def _parse_ingredients(raw: str) -> list:
    if not isinstance(raw, str):
        return []
    raw = raw.strip().strip('"').strip("'")
    return [i.strip() for i in raw.split(',') if i.strip()]


def _get_aliases(ingr_name: str) -> set:
    """
    Hasilkan semua alias untuk suatu nama ingredient.
    Tangani format:
      - 'Alcohol/Ethanol'   → {'alcohol', 'ethanol'}
      - 'Retinol (Vitamin A)' → {'retinol', 'vitamin a', 'retinol vitamin a'}
    """
    ingr_name = str(ingr_name).lower().strip()
    aliases = {ingr_name}

    # Handle slash separator  e.g. "Alcohol/Ethanol"
    if '/' in ingr_name:
        for part in ingr_name.split('/'):
            aliases.add(part.strip())

    # Handle parentheses  e.g. "Retinol (Vitamin A)"
    match = re.search(r'^(.*?)\s*\((.*?)\)(.*?)$', ingr_name)
    if match:
        prefix = match.group(1).strip()
        inside = match.group(2).strip()
        suffix = match.group(3).strip()

        opt1 = f'{prefix} {suffix}'.strip()
        if opt1:
            aliases.add(opt1)

        for part in inside.split(','):
            part = part.strip()
            if part:
                aliases.add(part)
                opt2 = f'{part} {suffix}'.strip()
                if opt2:
                    aliases.add(opt2)
                opt3 = f'{prefix} {part}'.strip()
                if opt3:
                    aliases.add(opt3)

    return {a for a in aliases if len(a) > 1}


# ─── Core Scoring Functions (v3) ─────────────────────────────────────────────

def _build_rule_maps_v2(rules_df: pd.DataFrame, jenis_kulit: str, masalah_kulit: str):
    """
    Build 4 independent maps from Dataset Terbaru.csv:
      jk_cocok_map : alias → (original_name, alasan)  — cocok for jenis_kulit
      jk_tidak_map : alias → (original_name, alasan)  — tidak cocok for jenis_kulit
      mk_cocok_map : alias → (original_name, alasan)  — cocok for masalah_kulit
      mk_tidak_map : alias → (original_name, alasan)  — tidak cocok for masalah_kulit

    Each axis is completely independent — eliminates the OR bug where
    jenis_kulit match would short-circuit masalah_kulit evaluation.
    """
    jk_cocok_map = {}
    jk_tidak_map = {}
    mk_cocok_map = {}
    mk_tidak_map = {}

    for _, row in rules_df.iterrows():
        orig_name = str(row.get('Ingredient', '')).strip()
        if not orig_name or orig_name == 'nan':
            continue

        jk_cocok   = str(row.get('Jenis Kulit Cocok', '') or '').strip()
        alasan_jkc = str(row.get('Alasan Jenis Kulit Cocok', '') or '').strip()
        jk_tidak   = str(row.get('Jenis Kulit Tidak Cocok', '') or '').strip()
        alasan_jkt = str(row.get('Alasan Jenis Kulit Tidak Cocok', '') or '').strip()
        mk_cocok   = str(row.get('Masalah Kulit Cocok', '') or '').strip()
        alasan_mkc = str(row.get('Alasan Masalah Kulit Cocok', '') or '').strip()
        mk_tidak   = str(row.get('Masalah Kulit Tidak Cocok', '') or '').strip()
        alasan_mkt = str(row.get('Alasan Masalah Kulit Tidak Cocok', '') or '').strip()

        # Parse comma-separated values
        jk_cocok_list = [x.strip() for x in jk_cocok.split(',') if x.strip() and x.strip() != '-']
        jk_tidak_list = [x.strip() for x in jk_tidak.split(',') if x.strip() and x.strip() != '-']
        mk_cocok_list = [x.strip() for x in mk_cocok.split(',') if x.strip() and x.strip() != '-']
        mk_tidak_list = [x.strip() for x in mk_tidak.split(',') if x.strip() and x.strip() != '-']

        aliases = _get_aliases(orig_name)

        # ── Jenis Kulit axis (independent) ──
        if jenis_kulit:
            is_jk_tidak = jenis_kulit in jk_tidak_list
            is_jk_cocok = jenis_kulit in jk_cocok_list
            alasan_jkc_clean = alasan_jkc if alasan_jkc and alasan_jkc != 'nan' else '-'
            alasan_jkt_clean = alasan_jkt if alasan_jkt and alasan_jkt != 'nan' else '-'

            # Conservative: tidak cocok takes priority over cocok
            if is_jk_tidak:
                for alias in aliases:
                    if alias not in jk_tidak_map:
                        jk_tidak_map[alias] = (orig_name, alasan_jkt_clean)
            elif is_jk_cocok:
                for alias in aliases:
                    if alias not in jk_cocok_map:
                        jk_cocok_map[alias] = (orig_name, alasan_jkc_clean)

        # ── Masalah Kulit axis (independent) ──
        if masalah_kulit:
            is_mk_tidak = masalah_kulit in mk_tidak_list
            is_mk_cocok = masalah_kulit in mk_cocok_list
            alasan_mkc_clean = alasan_mkc if alasan_mkc and alasan_mkc != 'nan' else '-'
            alasan_mkt_clean = alasan_mkt if alasan_mkt and alasan_mkt != 'nan' else '-'
#upd
            if is_mk_tidak:
                for alias in aliases:
                    if alias not in mk_tidak_map:
                        mk_tidak_map[alias] = (orig_name, alasan_mkt_clean)
            elif is_mk_cocok:
                for alias in aliases:
                    if alias not in mk_cocok_map:
                        mk_cocok_map[alias] = (orig_name, alasan_mkc_clean)

    return jk_cocok_map, jk_tidak_map, mk_cocok_map, mk_tidak_map


def _match_ingredient(k_aliases, cocok_map, tidak_map,
                      cache_cocok, cache_tidak, cocok_keys, tidak_keys):
    """
    Try to match ingredient aliases against cocok/tidak maps (exact → fuzzy).
    Returns ('cocok', matched_key) or ('tidak', matched_key) or (None, None).
    """
    # ── Check Cocok (Exact → Fuzzy) ──
    match_cocok = None
    for a in k_aliases:
        if a in cocok_map:
            match_cocok = a
            break

    if not match_cocok:
        for a in k_aliases:
            if a in cache_cocok:
                match_cocok = cache_cocok[a]
                if match_cocok:
                    break
            else:
                fuzz = get_close_matches(a, cocok_keys, n=1, cutoff=0.85)
                cache_cocok[a] = fuzz[0] if fuzz else None
                if fuzz:
                    match_cocok = fuzz[0]
                    break

    if match_cocok:
        return 'cocok', match_cocok

    # ── Check Tidak Cocok (Exact → Fuzzy) ──
    match_tidak = None
    for a in k_aliases:
        if a in tidak_map:
            match_tidak = a
            break

    if not match_tidak:
        for a in k_aliases:
            if a in cache_tidak:
                match_tidak = cache_tidak[a]
                if match_tidak:
                    break
            else:
                fuzz = get_close_matches(a, tidak_keys, n=1, cutoff=0.85)
                cache_tidak[a] = fuzz[0] if fuzz else None
                if fuzz:
                    match_tidak = fuzz[0]
                    break

    if match_tidak:
        return 'tidak', match_tidak

    return None, None


# ─── Skin Type Filter (Constraint Layer) ─────────────────────────────────────

def _filter_skin_type(
    ingredients_list: list,
    jk_cocok_map: dict, jk_tidak_map: dict,
    cache_jk_cocok: dict, cache_jk_tidak: dict,
    jk_cocok_keys: list, jk_tidak_keys: list,
) -> dict:
    """
    Rule-Based Filtering Layer 1: Jenis Kulit sebagai eligibility constraint.

    Aturan:
      - Jika ada ingredient di TOP 20% posisi yang "tidak cocok" untuk jenis
        kulit user → produk DITOLAK (hard reject).
      - Ingredient di posisi >20% yang tidak cocok → warning saja (soft info).

    Returns dict:
      {
        'compatible': bool,           # True = lolos filter
        'jk_cocok_list': [...],       # ingredient cocok jenis kulit
        'jk_warnings': [...],         # ingredient tidak cocok (posisi >20%)
        'jk_hard_reject': [...],      # ingredient tidak cocok (posisi ≤20%)
        'jk_cocok_count': int,
        'jk_tidak_count': int,
      }
    """
    total = len(ingredients_list)
    if total == 0:
        return {
            'compatible': True,
            'jk_cocok_list': [], 'jk_warnings': [], 'jk_hard_reject': [],
            'jk_cocok_count': 0, 'jk_tidak_count': 0,
        }

    seen_cocok = set()
    seen_tidak = set()
    jk_cocok_list = []
    jk_warnings = []
    jk_hard_reject = []

    for idx, ingr in enumerate(ingredients_list):
        pos_w, neg_w = _bobot_posisi(idx, total)
        k_aliases = _get_aliases(ingr)
        persen = (idx + 1) / total
        is_top20 = persen <= 0.2

        jk_type, jk_key = _match_ingredient(
            k_aliases, jk_cocok_map, jk_tidak_map,
            cache_jk_cocok, cache_jk_tidak, jk_cocok_keys, jk_tidak_keys
        )

        if jk_type == 'cocok':
            orig, alasan = jk_cocok_map[jk_key]
            if orig not in seen_cocok:
                seen_cocok.add(orig)
                jk_cocok_list.append({
                    'ingredient': orig,
                    'alasan': str(alasan) if alasan and str(alasan) != '-' else '-',
                    'posisi': 'Utama' if is_top20 else ('Menengah' if persen <= 0.5 else 'Minor'),
                })
        elif jk_type == 'tidak':
            orig, alasan = jk_tidak_map[jk_key]
            if orig not in seen_tidak:
                seen_tidak.add(orig)
                entry = {
                    'ingredient': orig,
                    'alasan': str(alasan) if alasan and str(alasan) != '-' else '-',
                    'posisi': 'Utama' if is_top20 else ('Menengah' if persen <= 0.5 else 'Minor'),
                }
                if is_top20:
                    jk_hard_reject.append(entry)
                else:
                    jk_warnings.append(entry)

    compatible = len(jk_hard_reject) == 0

    return {
        'compatible': compatible,
        'jk_cocok_list': jk_cocok_list,
        'jk_warnings': jk_warnings,
        'jk_hard_reject': jk_hard_reject,
        'jk_cocok_count': len(jk_cocok_list),
        'jk_tidak_count': len(jk_warnings) + len(jk_hard_reject),
    }


# ─── Product Analysis v3 (WSM Masalah Kulit Only + Evidence) ───

def _analisis_produk_v3(
    produk: pd.Series,
    jk_cocok_map: dict, jk_tidak_map: dict,
    mk_cocok_map: dict, mk_tidak_map: dict,
    cache_jk_cocok: dict, cache_jk_tidak: dict,
    cache_mk_cocok: dict, cache_mk_tidak: dict,
    jk_cocok_keys: list, jk_tidak_keys: list,
    mk_cocok_keys: list, mk_tidak_keys: list,
    has_masalah: bool,
    evidence_maps: dict = None,
    masalah_kulit: str = '',
) -> dict:
    """
    Analisis satu produk dengan arsitektur v3:

    # Mode 1 (has_masalah=True):
    #   Layer 1: Skin type constraint filter (hard/soft)
    #   Layer 2: WSM scoring dari masalah kulit

    Mode 2 — Fallback (has_masalah=False):
      Tidak ada filter. Jenis kulit menjadi kriteria WSM.
      Semua produk tetap mendapat skor berdasarkan kesesuaian jenis kulit.
    """
    ingredients_list = _parse_ingredients(produk.get('Ingridients', ''))
    total = len(ingredients_list)
    if total == 0:
        return None

    # ── Tentukan mode scoring ──
    # Jika ada masalah kulit: jenis kulit = filter, masalah kulit = WSM
    # Jika TIDAK ada masalah kulit: jenis kulit = WSM (fallback), tidak ada filter
    if has_masalah:
        # Mode 1: Skin type sebagai filter
        skin_type_info = _filter_skin_type(
            ingredients_list,
            jk_cocok_map, jk_tidak_map,
            cache_jk_cocok, cache_jk_tidak,
            jk_cocok_keys, jk_tidak_keys,
        )
        # WSM maps = masalah kulit
        wsm_cocok_map = mk_cocok_map
        wsm_tidak_map = mk_tidak_map
        wsm_cache_cocok = cache_mk_cocok
        wsm_cache_tidak = cache_mk_tidak
        wsm_cocok_keys = mk_cocok_keys
        wsm_tidak_keys = mk_tidak_keys
        scoring_mode = 'masalah_kulit'
    else:
        # Mode 2 — Fallback: Skin type sebagai WSM ranking, TIDAK ada filter
        skin_type_info = {
            'compatible': True,
            'jk_cocok_list': [], 'jk_warnings': [], 'jk_hard_reject': [],
            'jk_cocok_count': 0, 'jk_tidak_count': 0,
        }
        # WSM maps = jenis kulit (fallback)
        wsm_cocok_map = jk_cocok_map
        wsm_tidak_map = jk_tidak_map
        wsm_cache_cocok = cache_jk_cocok
        wsm_cache_tidak = cache_jk_tidak
        wsm_cocok_keys = jk_cocok_keys
        wsm_tidak_keys = jk_tidak_keys
        scoring_mode = 'jenis_kulit'

    # ── WSM Scoring ──
    seen_wsm_cocok = set()
    seen_wsm_tidak = set()

    cocok_found = []
    tidak_found = []

    ingredients_detail = []
    score_wsm = 0.0

    # ── Normalisasi denominator: "actual achievable maximum" ──
    # Revisi metodologi (lihat catatan review): denominator TIDAK lagi
    # dihitung dari seluruh posisi ingredient (theoretical maximum),
    # melainkan hanya dari posisi yang benar-benar "rule-relevant" —
    # yaitu ingredient yang cocok ATAU tidak cocok menurut dataset untuk
    # jenis_kulit/masalah_kulit user. Ingredient netral (tidak tercatat
    # di dataset sama sekali) tidak pernah bisa berkontribusi pada
    # score_wsm, sehingga tidak boleh memperbesar ceiling — kalau tetap
    # dihitung, produk dengan banyak ingredient netral/filler akan
    # dirugikan secara sistematis walau kandungan aktifnya identik
    # (length bias). Ini analog dengan normalisasi ideal-DCG pada IR:
    # ceiling dihitung atas himpunan item yang relevan/dinilai, bukan
    # atas seluruh koleksi. Struktur WSM, position weight, evidence,
    # dan contraindication multiplier TIDAK berubah — hanya cakupan
    # penjumlahan pada denominator.
    max_ev = EVIDENCE_PRIMARY if scoring_mode == 'masalah_kulit' else 1.0
    max_possible = 0.0

    # Resolve evidence maps for this analysis
    ev_map = (evidence_maps or {}).get('evidence', {})
    ct_map = (evidence_maps or {}).get('contraindication', {})

    for idx, ingr in enumerate(ingredients_list):
        pos_w, neg_w = _bobot_posisi(idx, total)
        k_aliases = _get_aliases(ingr)

        # ── WSM axis ──
        wsm_type = None
        wsm_type, wsm_key = _match_ingredient(
            k_aliases, wsm_cocok_map, wsm_tidak_map,
            wsm_cache_cocok, wsm_cache_tidak, wsm_cocok_keys, wsm_tidak_keys
        )

        # Ingredient ini rule-relevant (match cocok atau tidak cocok) →
        # ikut menyumbang ke ceiling normalisasi menggunakan bobot
        # positif posisinya (karena ceiling merepresentasikan skenario
        # terbaik: ingredient tersebut cocok dengan evidence maksimal).
        if wsm_type is not None:
            max_possible += pos_w * max_ev

        if wsm_type == 'cocok':
            orig, manfaat = wsm_cocok_map[wsm_key]
            if orig not in seen_wsm_cocok:
                seen_wsm_cocok.add(orig)
                if scoring_mode == 'masalah_kulit':
                    # Apply evidence strength multiplier (small correction)
                    ev_mult = _get_evidence_multiplier(orig.lower(), masalah_kulit, ev_map)
                else:
                    # Fallback mode: no evidence needed
                    ev_mult = 1.0
                effective_w = pos_w * ev_mult
                score_wsm += effective_w
                # Lookup evidence level label for response
                ev_level = ev_map.get(orig.lower(), {}).get(masalah_kulit, '') if masalah_kulit else ''
                cocok_found.append({
                    'ingredient': orig,
                    'bobot': round(pos_w, 2),
                    'bobot_efektif': round(effective_w, 2),
                    'evidence_strength': _evidence_label(ev_level) if scoring_mode == 'masalah_kulit' else '-',
                    'manfaat': str(manfaat) if manfaat and str(manfaat) != '-' else '-',
                })
        elif wsm_type == 'tidak':
            orig, efek = wsm_tidak_map[wsm_key]
            if orig not in seen_wsm_tidak:
                seen_wsm_tidak.add(orig)
                if scoring_mode == 'masalah_kulit':
                    # Apply contraindication strength multiplier (small correction)
                    contra_mult = _get_contraindication_multiplier(orig.lower(), masalah_kulit, ct_map)
                else:
                    contra_mult = 1.0
                effective_w = neg_w * contra_mult
                score_wsm -= effective_w
                # Lookup contraindication level label for response
                ct_level = ct_map.get(orig.lower(), {}).get(masalah_kulit, '') if masalah_kulit else ''
                tidak_found.append({
                    'ingredient': orig,
                    'bobot': round(neg_w, 2),
                    'bobot_efektif': round(effective_w, 2),
                    'contraindication_strength': _contra_label(ct_level) if scoring_mode == 'masalah_kulit' else '-',
                    'efek_samping': str(efek) if efek and str(efek) != '-' else '-',
                })

        # ── Determine ingredient status ──
        jk_tidak_names = {e['ingredient'] for e in skin_type_info['jk_hard_reject']}
        jk_tidak_names |= {e['ingredient'] for e in skin_type_info['jk_warnings']}

        ingr_lower_aliases = _get_aliases(ingr)
        ingr_status = 'netral'

        if wsm_type == 'tidak':
            ingr_status = 'tidak_cocok'
        elif wsm_type == 'cocok':
            ingr_status = 'cocok'

        # If in masalah kulit mode, also check jk filter status
        if has_masalah:
            for a in ingr_lower_aliases:
                if a in jk_tidak_map:
                    orig_jk = jk_tidak_map[a][0]
                    if orig_jk in jk_tidak_names:
                        ingr_status = 'tidak_cocok'
                        break

        ingredients_detail.append({'nama': ingr, 'status': ingr_status})

    # ── Normalize score to [-100, 100] ──
    if max_possible > 0:
        norm_score = max(-100.0, min(100.0, (score_wsm / max_possible) * 100))
    else:
        norm_score = 0.0

    final_score = norm_score

    harga   = produk.get('Harga')
    gambar  = produk.get('Gambar')
    link    = produk.get('Link_Produk')
    tekstur = produk.get('Tekstur')

    # ── Override final recommendation if it fails the hard filter ──
    if not skin_type_info['compatible']:
        rekomendasi_text = 'Tidak Direkomendasikan (Tidak Cocok Jenis Kulit)'
    else:
        rekomendasi_text = 'Direkomendasikan' if final_score > 0 else 'Tidak Direkomendasikan'

    return {
        'nama_produk':        str(produk.get('Nama Produk', '')),
        'kategori':           str(produk.get('Kategori', '')),
        'harga':              int(harga)   if pd.notna(harga)   else 0,
        'gambar':             str(gambar)  if pd.notna(gambar)  else '',
        'link':               str(link)    if pd.notna(link)    else '',
        'tekstur':            str(tekstur) if pd.notna(tekstur) else '',
        'bahan_cocok':        cocok_found,
        'bahan_tidak_cocok':  tidak_found,
        'ingredients_detail': ingredients_detail,
        'skor':               round(final_score, 2),
        'skor_masalah':       round(norm_score, 2),
        'skor_mentah':        round(score_wsm, 2),
        'skor_maksimal':      round(max_possible, 2),
        'normalisasi_metode': 'actual_achievable_max_rule_relevant',
        'scoring_mode':       scoring_mode,
        # Skin type info (constraint, bukan ranking)
        'skin_type_compatible': skin_type_info['compatible'],
        'skin_type_info':       skin_type_info,
        'rekomendasi':        rekomendasi_text,
    }


# ─── Helper: prepare caches & keys for v3 analysis ───────────────────────────

def _prepare_v3_caches(jk_cocok_map, jk_tidak_map, mk_cocok_map, mk_tidak_map):
    """Create fresh fuzzy-match caches and key lists for _analisis_produk_v3."""
    return (
        {}, {},  # cache_jk_cocok, cache_jk_tidak
        {}, {},  # cache_mk_cocok, cache_mk_tidak
        list(jk_cocok_map.keys()), list(jk_tidak_map.keys()),
        list(mk_cocok_map.keys()), list(mk_tidak_map.keys()),
    )


# ─── GET /api/recommend/debug ─────────────────────────────────────────────────

@recommend_bp.route('/debug', methods=['GET'])
def debug():
    """Cek apakah dataset berhasil dimuat."""
    try:
        data = _get_data()
        rules_df = data['rules_df']
        return jsonify({
            'status':       'ok',
            'produk':       len(data['produk_df']),
            'rules_rows':   len(rules_df),
            'columns':      rules_df.columns.tolist(),
            'dataset_path': _DATASET,
            'algorithm':    'v3: Constraint-Based Filtering + WSM Masalah Kulit',
        }), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 503


# ─── GET /api/recommend/audit ─────────────────────────────────────────────────

@recommend_bp.route('/audit', methods=['GET'])
def audit_dataset():
    """
    Audit ringan (read-only) terhadap kualitas data Evidence Strength &
    Contraindication Strength di Dataset Terbaru.csv. Tidak mempengaruhi
    endpoint scoring/filtering lain. Ditujukan untuk keperluan akademik
    (bab metodologi / validasi data skripsi).
    """
    try:
        data_set = _get_data()
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 503

    result = _audit_evidence_dataset(data_set['rules_df'], data_set.get('evidence_maps', {}))
    return jsonify(result), 200


# ─── POST /api/recommend ──────────────────────────────────────────────────────

@recommend_bp.route('', methods=['POST'])
def get_recommendations():
    """
    Body JSON:
    {
        "jenis_kulit":   "Normal",           // Normal|Berminyak|Kering|Kombinasi
        "masalah_kulit": "Berjerawat",       // Berjerawat|PIE|PIH|Aging|Kusam|Kemerahan
        "kategori":      "Moisturizer",      // opsional
        "ingredients":   [...]              // opsional, dari scan OCR
    }

    Algoritma v3:
      1. Filter produk berdasarkan jenis kulit (constraint)
      2. Ranking produk yang lolos filter berdasarkan WSM masalah kulit
    """
    try:
        data_set = _get_data()
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 503

    data          = request.get_json(silent=True) or {}
    jenis_kulit   = (data.get('jenis_kulit')   or '').strip()
    masalah_kulit = (data.get('masalah_kulit') or '').strip()
    kategori      = (data.get('kategori')      or '').strip()
    if masalah_kulit.lower() == 'null':
        masalah_kulit = ''

    if not jenis_kulit:
        return jsonify({'error': 'jenis_kulit wajib diisi.'}), 400

    jk_cocok_map, jk_tidak_map, mk_cocok_map, mk_tidak_map = _build_rule_maps_v2(
        data_set['rules_df'], jenis_kulit, masalah_kulit
    )
    has_masalah = bool(masalah_kulit)
    evidence_maps = data_set.get('evidence_maps', {})

    produk_df = data_set['produk_df']
    produk_filter = (
        produk_df[produk_df['Kategori'].str.lower() == kategori.lower()]
        if kategori else produk_df.copy()
    )

    if produk_filter.empty:
        return jsonify({'results': [], 'total': 0, 'kategori': kategori}), 200

    (cache_jk_cocok, cache_jk_tidak,
     cache_mk_cocok, cache_mk_tidak,
     jk_cocok_keys, jk_tidak_keys,
     mk_cocok_keys, mk_tidak_keys) = _prepare_v3_caches(
        jk_cocok_map, jk_tidak_map, mk_cocok_map, mk_tidak_map
    )

    hasil_lolos = []
    hasil_filtered_out = []

    for _, row in produk_filter.iterrows():
        h = _analisis_produk_v3(
            row, jk_cocok_map, jk_tidak_map, mk_cocok_map, mk_tidak_map,
            cache_jk_cocok, cache_jk_tidak, cache_mk_cocok, cache_mk_tidak,
            jk_cocok_keys, jk_tidak_keys, mk_cocok_keys, mk_tidak_keys,
            has_masalah,
            evidence_maps, masalah_kulit,
        )
        if h:
            if h['skin_type_compatible']:
                hasil_lolos.append(h)
            else:
                hasil_filtered_out.append(h)

    hasil_lolos.sort(key=lambda x: x['skor'], reverse=True)
    direk   = [h for h in hasil_lolos if h['skor'] > 0]
    lainnya = [h for h in hasil_lolos if h['skor'] <= 0]

    return jsonify({
        'jenis_kulit':   jenis_kulit,
        'masalah_kulit': masalah_kulit,
        'kategori':      kategori,
        'total':         len(direk),
        'results':       direk[:20],
        'tidak_cocok':   lainnya[:5],
        'filtered_out':  hasil_filtered_out[:5],
        'filter_info': {
            'method': 'constraint-based',
            'skin_type_filter': 'hard_reject_top20',
            'ranking_method': 'WSM_masalah_kulit',
            'filtered_out_count': len(hasil_filtered_out),
        },
    }), 200


# ─── POST /api/recommend/analyze ─────────────────────────────────────────────

@recommend_bp.route('/analyze', methods=['POST'])
def analyze_ingredients():
    """
    Analisis satu set ingredients hasil scan terhadap profil pengguna.
    Body JSON:
    {
        "jenis_kulit":   "Normal",
        "masalah_kulit": "Berjerawat",
        "ingredients":   ["Ceramide", "Niacinamide", "Centella Asiatica"]
    }
    """
    try:
        data_set = _get_data()
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 503

    data          = request.get_json(silent=True) or {}
    jenis_kulit   = (data.get('jenis_kulit')   or '').strip()
    masalah_kulit = (data.get('masalah_kulit') or '').strip()
    ingredients   = data.get('ingredients') or []
    if masalah_kulit.lower() == 'null':
        masalah_kulit = ''

    if not jenis_kulit or not ingredients:
        return jsonify({'error': 'jenis_kulit dan ingredients wajib diisi.'}), 400

    # ── Coba Cocokkan ke Produk di Database (Match by Ingredients) ──────────
    scan_set  = {i.strip().lower() for i in ingredients}
    best_match = None
    best_score = 0.0

    for _, row in data_set['produk_df'].iterrows():
        prod_ingr = _parse_ingredients(row.get('Ingridients', ''))
        prod_set  = {i.strip().lower() for i in prod_ingr}
        if not prod_set:
            continue

        # Jaccard Similarity
        intersection = len(scan_set & prod_set)
        union        = len(scan_set | prod_set)
        score        = intersection / union

        if score > best_score:
            best_score = score
            best_match = row

    if best_score >= 0.65:
        # Produk dikenal → gunakan data bersih dari database
        dummy_produk = best_match.copy()
    else:
        # Produk tidak dikenal → buat dummy dari hasil scan
        dummy_produk = pd.Series({
            'Nama Produk': 'Hasil Scan (Produk Tidak Dikenali)',
            'Kategori':    'Umum',
            'Ingridients': ','.join(ingredients),
        })

    jk_cocok_map, jk_tidak_map, mk_cocok_map, mk_tidak_map = _build_rule_maps_v2(
        data_set['rules_df'], jenis_kulit, masalah_kulit
    )
    has_masalah = bool(masalah_kulit)
    evidence_maps = data_set.get('evidence_maps', {})

    hasil = _analisis_produk_v3(
        dummy_produk, jk_cocok_map, jk_tidak_map, mk_cocok_map, mk_tidak_map,
        {}, {}, {}, {},
        list(jk_cocok_map.keys()), list(jk_tidak_map.keys()),
        list(mk_cocok_map.keys()), list(mk_tidak_map.keys()),
        has_masalah,
        evidence_maps, masalah_kulit,
    )

    return jsonify({
        'jenis_kulit':   jenis_kulit,
        'masalah_kulit': masalah_kulit,
        'hasil':         hasil,
        'product_matched': best_score >= 0.65,
        'match_score':   round(best_score, 3),
    }), 200


# ─── POST /api/recommend/batch ─────────────────────────────────────────────

@recommend_bp.route('/batch', methods=['POST'])
def batch_scores():
    """
    Hitung skor untuk semua produk sekaligus (digunakan untuk ProductList).
    Hanya mengembalikan nama_produk dan skor untuk menghemat bandwidth.
    Body JSON:
    {
        "jenis_kulit":   "Normal",
        "masalah_kulit": "Berjerawat"
    }
    """
    try:
        data_set = _get_data()
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 503

    data          = request.get_json(silent=True) or {}
    jenis_kulit   = (data.get('jenis_kulit')   or '').strip()
    masalah_kulit = (data.get('masalah_kulit') or '').strip()
    # 'Null' means user picked "no concern", treat as empty
    if masalah_kulit.lower() == 'null':
        masalah_kulit = ''

    if not jenis_kulit:
        return jsonify([])

    jk_cocok_map, jk_tidak_map, mk_cocok_map, mk_tidak_map = _build_rule_maps_v2(
        data_set['rules_df'], jenis_kulit, masalah_kulit
    )
    has_masalah = bool(masalah_kulit)
    evidence_maps = data_set.get('evidence_maps', {})

    produk_df = data_set['produk_df']

    (cache_jk_cocok, cache_jk_tidak,
     cache_mk_cocok, cache_mk_tidak,
     jk_cocok_keys, jk_tidak_keys,
     mk_cocok_keys, mk_tidak_keys) = _prepare_v3_caches(
        jk_cocok_map, jk_tidak_map, mk_cocok_map, mk_tidak_map
    )

    # Build results: match by normalized ingredients string so frontend can lookup by ingredients
    results = []
    for _, row in produk_df.iterrows():
        h = _analisis_produk_v3(
            row, jk_cocok_map, jk_tidak_map, mk_cocok_map, mk_tidak_map,
            cache_jk_cocok, cache_jk_tidak, cache_mk_cocok, cache_mk_tidak,
            jk_cocok_keys, jk_tidak_keys, mk_cocok_keys, mk_tidak_keys,
            has_masalah,
            evidence_maps, masalah_kulit,
        )
        if h:
            ingr_key = ','.join(sorted([i.strip().lower() for i in _parse_ingredients(row.get('Ingridients', ''))]))
            results.append({
                'nama': h['nama_produk'],
                'skor': h['skor'],
                'skin_type_compatible': h['skin_type_compatible'],
                'ingr_key': ingr_key,
            })

    return jsonify(results), 200


# ─── GET /api/recommend/search ────────────────────────────────────────────────

@recommend_bp.route('/search', methods=['GET'])
def search_products():
    """Pencarian produk berdasarkan nama (autocomplete)."""
    try:
        data = _get_data()
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 503

    query = request.args.get('q', '').strip().lower()
    if not query:
        return jsonify([])

    df   = data['produk_df']
    mask = df['Nama Produk'].astype(str).str.lower().str.contains(query, na=False)
    filtered = df[mask].head(10)

    results = []
    for _, row in filtered.iterrows():
        ingr_list = _parse_ingredients(row.get('Ingridients', ''))
        results.append({
            'nama_produk': str(row.get('Nama Produk', '')),
            'kategori':    str(row.get('Kategori', '')),
            'gambar':      str(row.get('Gambar', '')) if pd.notna(row.get('Gambar')) else '',
            'ingredients': ingr_list,
        })

    return jsonify(results), 200


# ─── GET /api/recommend/meta ──────────────────────────────────────────────────

@recommend_bp.route('/meta', methods=['GET'])
def get_meta():
    """
    Kembalikan daftar valid jenis kulit, masalah kulit, dan kategori produk.
    Dibaca langsung dari Dataset Terbaru.csv agar selalu sinkron.
    """
    try:
        data_set = _get_data()
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 503

    rules_df  = data_set['rules_df']
    produk_df = data_set['produk_df']

    # Kumpulkan nilai unik dari kolom multi-value
    jenis_set   = set()
    masalah_set = set()

    for col in ['Jenis Kulit Cocok', 'Jenis Kulit Tidak Cocok']:
        for val in rules_df[col].dropna():
            for x in str(val).split(','):
                x = x.strip()
                if x and x != '-':
                    jenis_set.add(x)

    for col in ['Masalah Kulit Cocok', 'Masalah Kulit Tidak Cocok']:
        for val in rules_df[col].dropna():
            for x in str(val).split(','):
                x = x.strip()
                if x and x != '-':
                    masalah_set.add(x)

    # Filter hanya nilai yang valid sesuai definisi dataset terbaru
    jenis_valid   = sorted(jenis_set   & VALID_JENIS_KULIT)
    masalah_valid = sorted(masalah_set & VALID_MASALAH_KULIT)

    return jsonify({
        'jenis_kulit':   jenis_valid,
        'masalah_kulit': masalah_valid,
        'kategori':      sorted(produk_df['Kategori'].dropna().unique().tolist()),
    }), 200