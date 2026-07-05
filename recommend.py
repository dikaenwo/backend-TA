"""
recommend.py – Product Recommendation Engine for B-Glow
Prefix: /api/recommend

Algoritma v3 (Constraint-Based Filtering + WSM):
  - Arsitektur berdasarkan Burke (2002): constraint vs preference/utility
  - Jenis Kulit  → Rule-Based Filtering (eligibility constraint)
    • Hard reject jika ingredient top-20% tidak cocok jenis kulit user
    • Ingredient di posisi >20% yang tidak cocok → warning saja
  - Masalah Kulit → WSM single-axis (ranking criterion)
    • Bobot posisi ingredient: atas 20% → 1.0, 20-50% → 0.5, sisanya → 0.2
    • Bobot spesifisitas ingredient: terapeutik (1.5x), moderat (1.0x), generik (0.7x)
    • Normalisasi ke [-100, 100] agar panjang ingredient list tidak bias
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

# ─── Ingredient Specificity Multipliers ──────────────────────────────────────
# Dihitung otomatis dari KB: berapa banyak masalah kulit yang di-tag "cocok"
# Therapeutic (≤2 masalah) → ingredient spesifik, bobot lebih tinggi
# Moderate (3-4 masalah)   → bobot standar
# Generic (≥5 masalah)     → ingredient generik/suportif, bobot lebih rendah
SPEC_THERAPEUTIC = 1.5   # e.g., Salicylic Acid (hanya Berjerawat, PIH)
SPEC_MODERATE    = 1.0   # e.g., Centella Asiatica (beberapa masalah)
SPEC_GENERIC     = 0.7   # e.g., Niacinamide (semua masalah)


def _get_data():
    """Load dataset sekali lalu cache. Raise RuntimeError jika gagal."""
    if _cache:
        return _cache
    try:
        rules_df = pd.read_csv(os.path.join(_DATASET, 'Dataset Terbaru.csv'))
        _cache['rules_df']  = rules_df
        _cache['produk_df'] = pd.read_excel(os.path.join(_DATASET, 'Dataset Produk.xlsx'))

        # Build specificity map once on first load
        _cache['specificity_map'] = _build_specificity_map(rules_df)

        print("[Recommend] Dataset Terbaru.csv & Dataset Produk.xlsx berhasil dimuat.")
        print(f"  Rules: {len(rules_df)} baris | Produk: {len(_cache['produk_df'])} baris")
        print(f"  Specificity map: {len(_cache['specificity_map'])} ingredients")
    except Exception as e:
        _cache.clear()
        raise RuntimeError(f"Gagal memuat dataset: {e}") from e
    return _cache


# ─── Specificity Map Builder ─────────────────────────────────────────────────

def _build_specificity_map(rules_df: pd.DataFrame) -> dict:
    """
    Hitung spesifisitas tiap ingredient terhadap masalah kulit.

    Spesifisitas ditentukan dari berapa banyak masalah kulit yang ingredient
    itu ditandai "cocok" di KB. Semakin sedikit masalah yang cocok, semakin
    spesifik/terapeutik ingredient tersebut.

    Returns: dict[ingredient_name_lower → multiplier (float)]
    """
    specificity = {}

    for _, row in rules_df.iterrows():
        name = str(row.get('Ingredient', '')).strip()
        if not name or name == 'nan':
            continue

        mk_cocok = str(row.get('Masalah Kulit Cocok', '') or '').strip()
        mk_list = [x.strip() for x in mk_cocok.split(',')
                    if x.strip() and x.strip() != '-']
        valid_count = len([m for m in mk_list if m in VALID_MASALAH_KULIT])

        # Assign multiplier based on specificity tier
        if valid_count >= 5:
            mult = SPEC_GENERIC
        elif valid_count >= 3:
            mult = SPEC_MODERATE
        else:
            # 0-2 masalah cocok → therapeutic (or no masalah data)
            # Ingredients with 0 masalah cocok still get therapeutic multiplier
            # because they have no generic-boosting effect
            mult = SPEC_THERAPEUTIC

        specificity[name.lower()] = mult

    return specificity


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


# ─── Product Analysis v3 (WSM Masalah Kulit Only + Specificity) ──────────────

def _analisis_produk_v3(
    produk: pd.Series,
    jk_cocok_map: dict, jk_tidak_map: dict,
    mk_cocok_map: dict, mk_tidak_map: dict,
    cache_jk_cocok: dict, cache_jk_tidak: dict,
    cache_mk_cocok: dict, cache_mk_tidak: dict,
    jk_cocok_keys: list, jk_tidak_keys: list,
    mk_cocok_keys: list, mk_tidak_keys: list,
    has_masalah: bool,
    specificity_map: dict,
) -> dict:
    """
    Analisis satu produk dengan arsitektur v3:
      Layer 1: Skin type constraint filter (hard/soft)
      Layer 2: WSM scoring hanya dari masalah kulit + spesifisitas ingredient

    Perubahan dari v2:
      - Jenis kulit TIDAK lagi masuk WSM scoring
      - Jenis kulit menjadi eligibility constraint (filter)
      - Masalah kulit adalah satu-satunya ranking criterion
      - Bobot posisi di-multiply dengan specificity multiplier
    """
    ingredients_list = _parse_ingredients(produk.get('Ingridients', ''))
    total = len(ingredients_list)
    if total == 0:
        return None

    # ── Layer 1: Skin Type Filter ──
    skin_type_info = _filter_skin_type(
        ingredients_list,
        jk_cocok_map, jk_tidak_map,
        cache_jk_cocok, cache_jk_tidak,
        jk_cocok_keys, jk_tidak_keys,
    )

    # ── Layer 2: WSM Masalah Kulit Only ──
    seen_mk_cocok = set()
    seen_mk_tidak = set()

    cocok_found = []    # bahan cocok masalah kulit
    tidak_found = []    # bahan tidak cocok masalah kulit

    ingredients_detail = []
    score_masalah = 0.0

    # Pre-compute max possible score for normalization
    max_possible = sum(
        _bobot_posisi(i, total)[0] * SPEC_THERAPEUTIC  # max possible with highest multiplier
        for i in range(total)
    )

    for idx, ingr in enumerate(ingredients_list):
        pos_w, neg_w = _bobot_posisi(idx, total)
        k_aliases = _get_aliases(ingr)

        # ── Masalah Kulit axis (WSM) ──
        mk_type = None
        if has_masalah:
            mk_type, mk_key = _match_ingredient(
                k_aliases, mk_cocok_map, mk_tidak_map,
                cache_mk_cocok, cache_mk_tidak, mk_cocok_keys, mk_tidak_keys
            )
            if mk_type == 'cocok':
                orig, manfaat = mk_cocok_map[mk_key]
                if orig not in seen_mk_cocok:
                    seen_mk_cocok.add(orig)
                    # Apply specificity multiplier
                    spec_mult = specificity_map.get(orig.lower(), SPEC_MODERATE)
                    effective_w = pos_w * spec_mult
                    score_masalah += effective_w
                    cocok_found.append({
                        'ingredient': orig,
                        'bobot': round(pos_w, 2),
                        'bobot_efektif': round(effective_w, 2),
                        'spesifisitas': _spec_label(spec_mult),
                        'manfaat': str(manfaat) if manfaat and str(manfaat) != '-' else '-',
                    })
            elif mk_type == 'tidak':
                orig, efek = mk_tidak_map[mk_key]
                if orig not in seen_mk_tidak:
                    seen_mk_tidak.add(orig)
                    spec_mult = specificity_map.get(orig.lower(), SPEC_MODERATE)
                    effective_w = neg_w * spec_mult
                    score_masalah -= effective_w
                    tidak_found.append({
                        'ingredient': orig,
                        'bobot': round(neg_w, 2),
                        'bobot_efektif': round(effective_w, 2),
                        'spesifisitas': _spec_label(spec_mult),
                        'efek_samping': str(efek) if efek and str(efek) != '-' else '-',
                    })

        # ── Determine ingredient status ──
        # Check jk status from skin_type_info
        jk_tidak_names = {e['ingredient'] for e in skin_type_info['jk_hard_reject']}
        jk_tidak_names |= {e['ingredient'] for e in skin_type_info['jk_warnings']}
        jk_cocok_names = {e['ingredient'] for e in skin_type_info['jk_cocok_list']}

        # Match this ingredient to its original name (if matched)
        ingr_lower_aliases = _get_aliases(ingr)
        ingr_status = 'netral'

        # Check mk status
        if mk_type == 'tidak':
            ingr_status = 'tidak_cocok'
        elif mk_type == 'cocok':
            ingr_status = 'cocok'

        # If jk says tidak, override to tidak
        for a in ingr_lower_aliases:
            if a in jk_tidak_map:
                orig_jk = jk_tidak_map[a][0]
                if orig_jk in jk_tidak_names:
                    ingr_status = 'tidak_cocok'
                    break

        ingredients_detail.append({'nama': ingr, 'status': ingr_status})

    # ── Normalize masalah kulit score to [-100, 100] ──
    if max_possible > 0:
        norm_masalah = max(-100.0, min(100.0, (score_masalah / max_possible) * 100))
    else:
        norm_masalah = 0.0

    # ── Final score = WSM masalah kulit only ──
    final_score = norm_masalah

    harga   = produk.get('Harga')
    gambar  = produk.get('Gambar')
    link    = produk.get('Link_Produk')
    tekstur = produk.get('Tekstur')

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
        'skor_masalah':       round(norm_masalah, 2),
        # Skin type info (constraint, bukan ranking)
        'skin_type_compatible': skin_type_info['compatible'],
        'skin_type_info':       skin_type_info,
        'rekomendasi':        'Direkomendasikan' if final_score > 0 else 'Tidak Direkomendasikan',
    }


def _spec_label(mult: float) -> str:
    """Convert specificity multiplier to human-readable label."""
    if mult >= SPEC_THERAPEUTIC:
        return 'Terapeutik'
    elif mult >= SPEC_MODERATE:
        return 'Moderat'
    return 'Generik'


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
        spec_map = data.get('specificity_map', {})
        # Count per tier
        tier_counts = {'Terapeutik': 0, 'Moderat': 0, 'Generik': 0}
        for mult in spec_map.values():
            tier_counts[_spec_label(mult)] += 1
        return jsonify({
            'status':       'ok',
            'produk':       len(data['produk_df']),
            'rules_rows':   len(rules_df),
            'columns':      rules_df.columns.tolist(),
            'dataset_path': _DATASET,
            'algorithm':    'v3: Constraint-Based Filtering + WSM Masalah Kulit',
            'specificity_tiers': tier_counts,
        }), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 503


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
      2. Ranking produk yang lolos filter berdasarkan WSM masalah kulit + spesifisitas
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
    specificity_map = data_set.get('specificity_map', {})

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
            has_masalah, specificity_map,
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
            'ranking_method': 'WSM_masalah_kulit_with_specificity',
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
    specificity_map = data_set.get('specificity_map', {})

    hasil = _analisis_produk_v3(
        dummy_produk, jk_cocok_map, jk_tidak_map, mk_cocok_map, mk_tidak_map,
        {}, {}, {}, {},
        list(jk_cocok_map.keys()), list(jk_tidak_map.keys()),
        list(mk_cocok_map.keys()), list(mk_tidak_map.keys()),
        has_masalah, specificity_map,
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
    specificity_map = data_set.get('specificity_map', {})

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
            has_masalah, specificity_map,
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
