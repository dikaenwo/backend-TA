"""
recommend.py – Product Recommendation Engine for B-Glow
Prefix: /api/recommend

Algoritma v4 (Rule-Based + WSM Posisi, Tanpa Normalisasi):
  - Disamakan persis dengan algoritma v9 hasil uji coba di Google Colab.
  - Jenis Kulit DAN Masalah Kulit sama-sama dihitung sebagai WSM single-axis
    (bukan lagi filter/constraint) → tidak ada lagi hard-reject.
  - Skor akhir = rata-rata (skor_wsm Jenis Kulit + skor_wsm Masalah Kulit) / 2
  - Bobot posisi ingredient: atas 20% → +1.0/-2.0, 20-50% → +0.5/-1.0, sisanya → +0.2/-0.5
  - Skor TIDAK dinormalisasi (raw score, sesuai Colab v9 "Tanpa Normalisasi")
  - Tidak ada lagi Evidence/Contraindication Strength multiplier
  - Alias ingredient = nama lengkap ingredient itu sendiri saja (tidak ada lagi
    pemecahan berdasarkan '/' atau '(...)', karena di dataset ini tanda '/'
    dipakai untuk daftar bahan baku dalam satu ingredient composite, bukan
    sinonim — lihat catatan di get_aliases() versi Colab)
  - Handle nama kolom alternatif: 'Jenis Kulit Hindari' / 'Masalah Kulit Hindari'
    sebagai fallback kalau 'Jenis/Masalah Kulit Tidak Cocok' tidak ada
  - Produk diurutkan dari skor tertinggi, semua produk tetap tampil (tidak ada
    yang difilter/ditolak total)
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


def _get_data():
    """Load dataset sekali lalu cache. Raise RuntimeError jika gagal."""
    if _cache:
        return _cache
    try:
        rules_df = pd.read_csv(os.path.join(_DATASET, 'Dataset Terbaru.csv'))
        rules_df['Ingredient'] = rules_df['Ingredient'].astype(str).str.strip()
        _cache['rules_df']  = rules_df
        _cache['produk_df'] = pd.read_excel(os.path.join(_DATASET, 'Dataset Produk.xlsx'))

        print("[Recommend] Dataset Terbaru.csv & Dataset Produk.xlsx berhasil dimuat.")
        print(f"  Rules: {len(rules_df)} baris | Produk: {len(_cache['produk_df'])} baris")
    except Exception as e:
        _cache.clear()
        raise RuntimeError(f"Gagal memuat dataset: {e}") from e
    return _cache


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
    Alias untuk sebuah nama ingredient.

    PENTING: di dataset ini tanda '/' TIDAK PERNAH dipakai sebagai sinonim
    (bukan seperti 'Aqua/Water/Eau'). Tanda '/' selalu dipakai untuk daftar
    bahan baku / bagian tanaman dalam SATU ingredient composite, contoh:
    'Nasturtium Officinale Flower/Leaf/Stem Extract' (satu ingredient, bukan 3)
    atau ferment filtrate raksasa berisi puluhan bahan baku dipisah '/'.

    Memecah nama berdasarkan '/' atau '(...)' menyebabkan fragmen salah
    dianggap alias dari ingredient lain, sehingga status cocok/tidak cocoknya
    ketuker. Karena itu alias sekarang HANYA nama lengkap ingredient itu
    sendiri (tidak ada pemecahan) — persis seperti versi Colab.
    """
    return {str(ingr_name).lower().strip()}


# ─── Core Scoring Functions (v4 — samakan dengan Colab v9) ──────────────────

def _build_rule_maps_v2(rules_df: pd.DataFrame, jenis_kulit: str, masalah_kulit: str):
    """
    Build 4 independent maps dari Dataset Terbaru.csv:
      jk_cocok_map : alias → (original_name, alasan)  — cocok for jenis_kulit
      jk_tidak_map : alias → (original_name, alasan)  — tidak cocok for jenis_kulit
      mk_cocok_map : alias → (original_name, alasan)  — cocok for masalah_kulit
      mk_tidak_map : alias → (original_name, alasan)  — tidak cocok for masalah_kulit

    Setiap axis independen satu sama lain (tidak ada short-circuit OR).
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
        jk_tidak_raw = row.get('Jenis Kulit Tidak Cocok', None)
        if jk_tidak_raw is None:
            jk_tidak_raw = row.get('Jenis Kulit Hindari', '')
        jk_tidak   = str(jk_tidak_raw or '').strip()
        alasan_jkt = str(row.get('Alasan Jenis Kulit Tidak Cocok', '') or '').strip()

        mk_cocok   = str(row.get('Masalah Kulit Cocok', '') or '').strip()
        alasan_mkc = str(row.get('Alasan Masalah Kulit Cocok', '') or '').strip()
        mk_tidak_raw = row.get('Masalah Kulit Tidak Cocok', None)
        if mk_tidak_raw is None:
            mk_tidak_raw = row.get('Masalah Kulit Hindari', '')
        mk_tidak   = str(mk_tidak_raw or '').strip()
        alasan_mkt = str(row.get('Alasan Masalah Kulit Tidak Cocok', '') or '').strip()

        # Parse comma-separated values
        jk_cocok_list = [x.strip() for x in jk_cocok.split(',') if x.strip() and x.strip() != '-']
        jk_tidak_list = [x.strip() for x in jk_tidak.split(',') if x.strip() and x.strip() != '-']
        mk_cocok_list = [x.strip() for x in mk_cocok.split(',') if x.strip() and x.strip() != '-']
        mk_tidak_list = [x.strip() for x in mk_tidak.split(',') if x.strip() and x.strip() != '-']

        # Alias = nama lengkap ingredient itu sendiri saja (lihat _get_aliases()).
        aliases = _get_aliases(orig_name)

        # ── Jenis Kulit axis (independent) ──
        if jenis_kulit:
            is_jk_tidak = jenis_kulit in jk_tidak_list
            is_jk_cocok = jenis_kulit in jk_cocok_list
            alasan_jkc_clean = alasan_jkc if alasan_jkc and alasan_jkc != 'nan' else '-'
            alasan_jkt_clean = alasan_jkt if alasan_jkt and alasan_jkt != 'nan' else '-'

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
    Cocokkan alias ingredient terhadap cocok/tidak map (exact → fuzzy).
    Returns ('cocok', matched_key) atau ('tidak', matched_key) atau (None, None).
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


def _wsm_score_axis(ingredients_list: list, cocok_map: dict, tidak_map: dict) -> dict:
    """
    Hitung skor WSM murni berbasis posisi untuk SATU axis (jenis kulit ATAU
    masalah kulit). Tidak ada normalisasi, tidak ada evidence/contraindication
    multiplier — persis seperti wsm_score_axis() di Colab v9.
    """
    total = len(ingredients_list)
    if total == 0:
        return {'skor_raw': 0.0, 'cocok': [], 'tidak': [], 'detail': []}

    cache_cocok, cache_tidak = {}, {}
    cocok_keys, tidak_keys = list(cocok_map.keys()), list(tidak_map.keys())

    seen_cocok, seen_tidak = set(), set()
    cocok_found, tidak_found = [], []
    ingredients_detail = []
    score = 0.0

    for idx, ingr in enumerate(ingredients_list):
        pos_w, neg_w = _bobot_posisi(idx, total)
        k_aliases = _get_aliases(ingr)

        m_type, m_key = _match_ingredient(
            k_aliases, cocok_map, tidak_map, cache_cocok, cache_tidak, cocok_keys, tidak_keys
        )

        status = 'netral'
        if m_type == 'cocok':
            orig, alasan = cocok_map[m_key]
            if orig not in seen_cocok:
                seen_cocok.add(orig)
                score += pos_w
                cocok_found.append({
                    'ingredient': orig,
                    'bobot': round(pos_w, 6),
                    'alasan': alasan if alasan and alasan != '-' else '-',
                })
                status = 'cocok'
        elif m_type == 'tidak':
            orig, alasan = tidak_map[m_key]
            if orig not in seen_tidak:
                seen_tidak.add(orig)
                score -= neg_w
                tidak_found.append({
                    'ingredient': orig,
                    'bobot': round(-neg_w, 6),
                    'alasan': alasan if alasan and alasan != '-' else '-',
                })
                status = 'tidak_cocok'

        ingredients_detail.append({'nama': ingr, 'posisi': idx + 1, 'status': status})

    return {
        'skor_raw': round(score, 6),
        'cocok': cocok_found,
        'tidak': tidak_found,
        'detail': ingredients_detail,
    }


def _analisis_produk_v4(
    produk: pd.Series,
    jk_cocok_map: dict, jk_tidak_map: dict,
    mk_cocok_map: dict, mk_tidak_map: dict,
) -> dict:
    """
    Analisis satu produk dengan arsitektur v4 (= Colab v9):
      - Jenis Kulit  → WSM posisi (bukan filter/constraint lagi)
      - Masalah Kulit → WSM posisi
      - Skor akhir = rata-rata (skor_jk + skor_mk) / 2, TIDAK dinormalisasi.
      - Tidak ada hard-reject: semua produk tetap dikembalikan.
    """
    ingredients_list = _parse_ingredients(produk.get('Ingridients', ''))
    if len(ingredients_list) == 0:
        return None

    hasil_jk = _wsm_score_axis(ingredients_list, jk_cocok_map, jk_tidak_map)
    hasil_mk = _wsm_score_axis(ingredients_list, mk_cocok_map, mk_tidak_map)

    skor_total = round((hasil_jk['skor_raw'] + hasil_mk['skor_raw']) / 2, 6)
    rekomendasi_text = 'Direkomendasikan' if skor_total > 0 else 'Tidak Direkomendasikan'

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
        # Skor per-axis (samakan istilah dengan Colab)
        'skor_jenis_kulit':   hasil_jk['skor_raw'],
        'skor_masalah_kulit': hasil_mk['skor_raw'],
        'skor_total':         skor_total,
        'skor':                skor_total,  # alias, dipakai kode lama/frontend
        'rekomendasi':        rekomendasi_text,
        # Detail per-axis, sama struktur dengan detail_store Colab
        'jenis_kulit_detail':   hasil_jk,
        'masalah_kulit_detail': hasil_mk,
        # Ringkasan gabungan (kompatibilitas dengan field lama)
        'bahan_cocok':        hasil_mk['cocok'],
        'bahan_tidak_cocok':  hasil_mk['tidak'],
        'ingredients_detail': hasil_mk['detail'],
    }


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
            'algorithm':    'v4: Rule-Based + WSM Posisi (tanpa normalisasi, tanpa filter jenis kulit)',
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
    }

    Algoritma v4:
      1. Hitung skor WSM posisi untuk axis Jenis Kulit dan axis Masalah Kulit
      2. Skor akhir = rata-rata kedua axis (tanpa normalisasi, tanpa filter)
      3. Urutkan semua produk dari skor tertinggi
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

    produk_df = data_set['produk_df']
    produk_filter = (
        produk_df[produk_df['Kategori'].str.lower() == kategori.lower()]
        if kategori else produk_df.copy()
    )

    if produk_filter.empty:
        return jsonify({'results': [], 'total': 0, 'kategori': kategori}), 200

    hasil_semua = []
    for _, row in produk_filter.iterrows():
        h = _analisis_produk_v4(row, jk_cocok_map, jk_tidak_map, mk_cocok_map, mk_tidak_map)
        if h:
            hasil_semua.append(h)

    hasil_semua.sort(key=lambda x: x['skor_total'], reverse=True)
    direk   = [h for h in hasil_semua if h['skor_total'] > 0]
    lainnya = [h for h in hasil_semua if h['skor_total'] <= 0]

    return jsonify({
        'jenis_kulit':   jenis_kulit,
        'masalah_kulit': masalah_kulit,
        'kategori':      kategori,
        'total':         len(direk),
        'results':       direk[:20],
        'tidak_cocok':   lainnya[:5],
        'filter_info': {
            'method': 'wsm_posisi_dua_axis',
            'ranking_method': 'rata-rata (skor_jenis_kulit + skor_masalah_kulit) / 2',
            'normalisasi': 'tidak ada (raw score)',
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

    hasil = _analisis_produk_v4(dummy_produk, jk_cocok_map, jk_tidak_map, mk_cocok_map, mk_tidak_map)

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

    produk_df = data_set['produk_df']

    # Build results: match by normalized ingredients string so frontend can lookup by ingredients
    results = []
    for _, row in produk_df.iterrows():
        h = _analisis_produk_v4(row, jk_cocok_map, jk_tidak_map, mk_cocok_map, mk_tidak_map)
        if h:
            ingr_key = ','.join(sorted([i.strip().lower() for i in _parse_ingredients(row.get('Ingridients', ''))]))
            results.append({
                'nama': h['nama_produk'],
                'skor': h['skor_total'],
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