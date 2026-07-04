"""
recommend.py – Product Recommendation Engine for B-Glow
Prefix: /api/recommend

Algoritma:
  - Load rules dari Dataset Terbaru.csv (1 file, semua kolom jenis & masalah kulit)
  - Hitung bobot posisi ingredient: atas 20% → 1.0, 20-50% → 0.5, sisanya → 0.2
  - Bahan cocok  → +score (bobot posisi)
  - Bahan tidak cocok → -2x score (bobot posisi)
  - Handle alias: nama/alternatif (/) dan nama (dalam kurung)
  - Urutkan dari skor tertinggi
"""

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


def _build_rule_maps_from_csv(rules_df: pd.DataFrame, jenis_kulit: str, masalah_kulit: str):
    """
    Buat dua dict dari Dataset Terbaru.csv:
      cocok_map : alias → (original_name, alasan)
      tidak_map : alias → (original_name, alasan)

    Cara kerja:
      Untuk tiap baris, cek apakah jenis_kulit ada di kolom 'Jenis Kulit Cocok'
      dan/atau masalah_kulit ada di kolom 'Masalah Kulit Cocok'.
      Demikian pula untuk kolom Tidak Cocok.
    """
    cocok_map = {}
    tidak_map = {}

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

        # Parsing nilai multi (dipisah koma)
        jk_cocok_list = [x.strip() for x in jk_cocok.split(',') if x.strip() and x.strip() != '-']
        jk_tidak_list = [x.strip() for x in jk_tidak.split(',') if x.strip() and x.strip() != '-']
        mk_cocok_list = [x.strip() for x in mk_cocok.split(',') if x.strip() and x.strip() != '-']
        mk_tidak_list = [x.strip() for x in mk_tidak.split(',') if x.strip() and x.strip() != '-']

        is_cocok = (
            (jenis_kulit and jenis_kulit in jk_cocok_list) or
            (masalah_kulit and masalah_kulit in mk_cocok_list)
        )
        is_tidak = (
            (jenis_kulit and jenis_kulit in jk_tidak_list) or
            (masalah_kulit and masalah_kulit in mk_tidak_list)
        )

        # Gabungkan alasan yang relevan
        alasan_cocok_parts = []
        if jenis_kulit and jenis_kulit in jk_cocok_list and alasan_jkc and alasan_jkc != 'nan':
            alasan_cocok_parts.append(alasan_jkc)
        if masalah_kulit and masalah_kulit in mk_cocok_list and alasan_mkc and alasan_mkc != 'nan':
            alasan_cocok_parts.append(alasan_mkc)
        alasan_cocok = ' | '.join(alasan_cocok_parts) if alasan_cocok_parts else '-'

        alasan_tidak_parts = []
        if jenis_kulit and jenis_kulit in jk_tidak_list and alasan_jkt and alasan_jkt != 'nan':
            alasan_tidak_parts.append(alasan_jkt)
        if masalah_kulit and masalah_kulit in mk_tidak_list and alasan_mkt and alasan_mkt != 'nan':
            alasan_tidak_parts.append(alasan_mkt)
        alasan_tidak = ' | '.join(alasan_tidak_parts) if alasan_tidak_parts else '-'

        aliases = _get_aliases(orig_name)

        # Prioritas: jika ingredient cocok DAN tidak cocok sekaligus,
        # masukkan ke tidak_cocok (lebih konservatif)
        if is_tidak:
            for alias in aliases:
                if alias not in tidak_map:
                    tidak_map[alias] = (orig_name, alasan_tidak)
        elif is_cocok:
            for alias in aliases:
                if alias not in cocok_map:
                    cocok_map[alias] = (orig_name, alasan_cocok)

    return cocok_map, tidak_map


def _analisis_produk(
    produk: pd.Series,
    cocok_map: dict,
    tidak_map: dict,
    cache_cocok: dict,
    cache_tidak: dict,
    cocok_keys: list,
    tidak_keys: list,
) -> dict:
    ingredients_list = _parse_ingredients(produk.get('Ingridients', ''))
    total = len(ingredients_list)
    if total == 0:
        return None

    cocok_found, tidak_found = [], []
    ingredients_detail = []
    score = 0.0

    for idx, ingr in enumerate(ingredients_list):
        pos_w, neg_w = _bobot_posisi(idx, total)
        k_aliases = _get_aliases(ingr)

        # ── Cek Cocok (Exact → Fuzzy) ──────────────────────────────────────
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
            orig_name, manfaat = cocok_map[match_cocok]
            cocok_found.append({'ingredient': orig_name, 'bobot': round(pos_w, 2), 'manfaat': str(manfaat)})
            score += pos_w
            ingredients_detail.append({'nama': ingr, 'status': 'cocok'})
            continue

        # ── Cek Tidak Cocok (Exact → Fuzzy) ────────────────────────────────
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
            orig_name, efek = tidak_map[match_tidak]
            tidak_found.append({'ingredient': orig_name, 'bobot': round(neg_w, 2), 'efek_samping': str(efek)})
            score -= neg_w
            ingredients_detail.append({'nama': ingr, 'status': 'tidak_cocok'})
            continue

        ingredients_detail.append({'nama': ingr, 'status': 'netral'})

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
        'skor':               round(score, 2),
        'rekomendasi':        'Direkomendasikan' if score > 0 else 'Tidak Direkomendasikan',
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

    cocok_map, tidak_map = _build_rule_maps_from_csv(
        data_set['rules_df'], jenis_kulit, masalah_kulit
    )

    produk_df = data_set['produk_df']
    produk_filter = (
        produk_df[produk_df['Kategori'].str.lower() == kategori.lower()]
        if kategori else produk_df.copy()
    )

    if produk_filter.empty:
        return jsonify({'results': [], 'total': 0, 'kategori': kategori}), 200

    cache_cocok = {}
    cache_tidak = {}
    cocok_keys  = list(cocok_map.keys())
    tidak_keys  = list(tidak_map.keys())

    hasil = []
    for _, row in produk_filter.iterrows():
        h = _analisis_produk(row, cocok_map, tidak_map, cache_cocok, cache_tidak, cocok_keys, tidak_keys)
        if h:
            hasil.append(h)

    hasil.sort(key=lambda x: x['skor'], reverse=True)
    direk   = [h for h in hasil if h['skor'] > 0]
    lainnya = [h for h in hasil if h['skor'] <= 0]

    return jsonify({
        'jenis_kulit':   jenis_kulit,
        'masalah_kulit': masalah_kulit,
        'kategori':      kategori,
        'total':         len(direk),
        'results':       direk[:20],
        'tidak_cocok':   lainnya[:5],
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

    cocok_map, tidak_map = _build_rule_maps_from_csv(
        data_set['rules_df'], jenis_kulit, masalah_kulit
    )

    hasil = _analisis_produk(
        dummy_produk, cocok_map, tidak_map, {}, {},
        list(cocok_map.keys()), list(tidak_map.keys())
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

    cocok_map, tidak_map = _build_rule_maps_from_csv(
        data_set['rules_df'], jenis_kulit, masalah_kulit
    )

    produk_df = data_set['produk_df']
    cache_cocok = {}
    cache_tidak = {}
    cocok_keys  = list(cocok_map.keys())
    tidak_keys  = list(tidak_map.keys())

    # Build results: match by normalized ingredients string so frontend can lookup by ingredients
    results = []
    for _, row in produk_df.iterrows():
        h = _analisis_produk(row, cocok_map, tidak_map, cache_cocok, cache_tidak, cocok_keys, tidak_keys)
        if h:
            ingr_key = ','.join(sorted([i.strip().lower() for i in _parse_ingredients(row.get('Ingridients', ''))]))
            results.append({
                'nama': h['nama_produk'],
                'skor': h['skor'],
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
