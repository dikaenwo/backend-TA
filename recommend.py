"""
recommend.py – Product Recommendation Engine for B-Glow
Prefix: /api/recommend

Algoritma (sesuai notebook):
  - Load rules dari 4 file Excel (jenis kulit cocok/tidak, masalah kulit cocok/tidak)
  - Hitung bobot posisi ingredient: atas 20% → 1.0, 20-50% → 0.5, sisanya → 0.2
  - +score untuk bahan cocok, -2x score untuk bahan tidak cocok
  - Urutkan dari skor tertinggi
"""

from flask import Blueprint, request, jsonify
import pandas as pd
import os

recommend_bp = Blueprint('recommend', __name__, url_prefix='/api/recommend')

# ─── Path Dataset ─────────────────────────────────────────────────────────────
_BASE    = os.path.dirname(os.path.abspath(__file__))
_DATASET = os.path.join(_BASE, 'Dataset')

# ─── Lazy cache (dimuat saat request pertama) ─────────────────────────────────
_cache = {}


def _get_data():
    """Load dataset sekali lalu cache. Raise RuntimeError jika gagal."""
    if _cache:
        return _cache
    try:
        _cache['jenis_cocok']   = pd.read_excel(os.path.join(_DATASET, 'Jenis Kulit Cocok.xlsx'))
        _cache['jenis_tidak']   = pd.read_excel(os.path.join(_DATASET, 'Jenis Kulit Tidak Cocok.xlsx'))
        _cache['masalah_cocok'] = pd.read_excel(os.path.join(_DATASET, 'Masalah Kulit Cocok.xlsx'))
        _cache['masalah_tidak'] = pd.read_excel(os.path.join(_DATASET, 'Masalah Kulit Tidak Cocok.xlsx'))
        _cache['produk_df']     = pd.read_excel(os.path.join(_DATASET, 'Dataset Produk.xlsx'))
        print("[Recommend] Dataset berhasil dimuat.")
    except Exception as e:
        _cache.clear()
        raise RuntimeError(f"Gagal memuat dataset: {e}") from e
    return _cache


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _bobot_posisi(index: int, total: int) -> float:
    persen = (index + 1) / total
    if persen <= 0.2:
        return 1.0
    elif persen <= 0.5:
        return 0.5
    return 0.2


def _parse_ingredients(raw: str) -> list:
    if not isinstance(raw, str):
        return []
    raw = raw.strip().strip('"').strip("'")
    return [i.strip() for i in raw.split(',') if i.strip()]


import re
from difflib import get_close_matches

def _get_aliases(ingr_name: str) -> set:
    ingr_name = str(ingr_name).lower().strip()
    aliases = {ingr_name}
    
    if '/' in ingr_name:
        for part in ingr_name.split('/'):
            aliases.add(part.strip())
            
    match = re.search(r'^(.*?)\s*\((.*?)\)(.*?)$', ingr_name)
    if match:
        prefix = match.group(1).strip()
        inside = match.group(2).strip()
        suffix = match.group(3).strip()
        
        opt1 = f'{prefix} {suffix}'.strip()
        if opt1: aliases.add(opt1)
            
        for part in inside.split(','):
            part = part.strip()
            if part:
                aliases.add(part)
                opt2 = f'{part} {suffix}'.strip()
                if opt2: aliases.add(opt2)
                opt3 = f'{prefix} {part}'.strip()
                if opt3: aliases.add(opt3)
            
    return {a for a in aliases if len(a) > 1}

def _build_rule_dict(rules_df, val_col):
    rule_map = {}
    for _, row in rules_df.iterrows():
        orig_name = str(row['Ingredient']).strip()
        val = str(row[val_col]) if val_col in row and pd.notna(row[val_col]) else ""
        for alias in _get_aliases(orig_name):
            if alias not in rule_map:
                rule_map[alias] = (orig_name, val)
    return rule_map

def _analisis_produk(produk: pd.Series, cocok_map: dict, tidak_map: dict, cache_cocok: dict, cache_tidak: dict, cocok_keys: list, tidak_keys: list) -> dict:
    ingredients_list = _parse_ingredients(produk.get('Ingridients', ''))
    total = len(ingredients_list)
    if total == 0:
        return None

    cocok_found, tidak_found = [], []
    ingredients_detail = []
    score = 0.0

    for idx, ingr in enumerate(ingredients_list):
        w = _bobot_posisi(idx, total)
        k_aliases = _get_aliases(ingr)
        
        # 1. Cek Exact Match di Cocok Map
        match_cocok = None
        for a in k_aliases:
            if a in cocok_map:
                match_cocok = a
                break
                
        if not match_cocok:
            # 2. Cek Fuzzy Match
            for a in k_aliases:
                if a in cache_cocok:
                    match_cocok = cache_cocok[a]
                    if match_cocok: break
                else:
                    fuzz = get_close_matches(a, cocok_keys, n=1, cutoff=0.85)
                    cache_cocok[a] = fuzz[0] if fuzz else None
                    if fuzz: 
                        match_cocok = fuzz[0]
                        break
            
        if match_cocok:
            orig_name, manfaat = cocok_map[match_cocok]
            cocok_found.append({'ingredient': orig_name, 'bobot': round(w, 2), 'manfaat': str(manfaat)})
            score += w
            ingredients_detail.append({'nama': ingr, 'status': 'cocok'})
            continue
            
        # 1. Cek Exact Match di Tidak Cocok Map
        match_tidak = None
        for a in k_aliases:
            if a in tidak_map:
                match_tidak = a
                break
                
        if not match_tidak:
            # 2. Cek Fuzzy Match
            for a in k_aliases:
                if a in cache_tidak:
                    match_tidak = cache_tidak[a]
                    if match_tidak: break
                else:
                    fuzz = get_close_matches(a, tidak_keys, n=1, cutoff=0.85)
                    cache_tidak[a] = fuzz[0] if fuzz else None
                    if fuzz: 
                        match_tidak = fuzz[0]
                        break
            
        if match_tidak:
            orig_name, efek = tidak_map[match_tidak]
            tidak_found.append({'ingredient': orig_name, 'bobot': round(2*w, 2), 'efek_samping': str(efek)})
            score -= 2 * w
            ingredients_detail.append({'nama': ingr, 'status': 'tidak_cocok'})
            continue
            
        ingredients_detail.append({'nama': ingr, 'status': 'netral'})

    harga = produk.get('Harga')
    gambar = produk.get('Gambar')
    link   = produk.get('Link_Produk')
    tekstur = produk.get('Tekstur')

    return {
        'nama_produk':       str(produk.get('Nama Produk', '')),
        'kategori':          str(produk.get('Kategori', '')),
        'harga':             int(harga) if pd.notna(harga) else 0,
        'gambar':            str(gambar) if pd.notna(gambar) else '',
        'link':              str(link)   if pd.notna(link)   else '',
        'tekstur':           str(tekstur) if pd.notna(tekstur) else '',
        'bahan_cocok':       cocok_found,
        'bahan_tidak_cocok': tidak_found,
        'ingredients_detail': ingredients_detail,
        'skor':              round(score, 2),
        'rekomendasi':       'Direkomendasikan' if score > 0 else 'Tidak Direkomendasikan',
    }


# ─── GET /api/recommend/debug ─────────────────────────────────────────────────

@recommend_bp.route('/debug', methods=['GET'])
def debug():
    """Cek apakah dataset berhasil dimuat."""
    try:
        data = _get_data()
        return jsonify({
            'status': 'ok',
            'produk': len(data['produk_df']),
            'jenis_cocok': len(data['jenis_cocok']),
            'masalah_cocok': len(data['masalah_cocok']),
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
        "jenis_kulit":   "Normal",
        "masalah_kulit": "Jerawat",
        "kategori":      "Moisturizer",
        "ingredients":   [...]   // opsional, dari scan OCR
    }
    """
    try:
        data_set = _get_data()
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 503

    data = request.get_json(silent=True) or {}
    jenis_kulit   = (data.get('jenis_kulit')   or '').strip()
    masalah_kulit = (data.get('masalah_kulit') or '').strip()
    kategori      = (data.get('kategori')      or '').strip()

    if not jenis_kulit:
        return jsonify({'error': 'jenis_kulit wajib diisi.'}), 400

    jc = data_set['jenis_cocok']
    jt = data_set['jenis_tidak']
    mc = data_set['masalah_cocok']
    mt = data_set['masalah_tidak']
    produk_df = data_set['produk_df']

    rules_cocok = pd.concat([
        jc[jc['Jenis_Kulit'].str.strip() == jenis_kulit][['Ingredient', 'Manfaat']],
        mc[mc['Masalah_Kulit'].str.strip() == masalah_kulit][['Ingredient', 'Manfaat']],
    ], ignore_index=True).drop_duplicates(subset='Ingredient')

    rules_tidak = pd.concat([
        jt[jt['Jenis_Kulit'].str.strip() == jenis_kulit].rename(columns={'Efek Samping': 'Efek_Samping'})[['Ingredient', 'Efek_Samping']],
        mt[mt['Masalah_Kulit'].str.strip() == masalah_kulit].rename(columns={'Efek Samping': 'Efek_Samping'})[['Ingredient', 'Efek_Samping']],
    ], ignore_index=True).drop_duplicates(subset='Ingredient')

    produk_filter = (
        produk_df[produk_df['Kategori'].str.lower() == kategori.lower()]
        if kategori else produk_df.copy()
    )

    if produk_filter.empty:
        return jsonify({'results': [], 'total': 0, 'kategori': kategori}), 200

    cocok_map = _build_rule_dict(rules_cocok, 'Manfaat' if 'Manfaat' in rules_cocok.columns else 'None')
    tidak_map = _build_rule_dict(rules_tidak, 'Efek_Samping' if 'Efek_Samping' in rules_tidak.columns else 'None')
    
    cache_cocok = {}
    cache_tidak = {}
    cocok_keys = list(cocok_map.keys())
    tidak_keys = list(tidak_map.keys())

    hasil = []
    for _, row in produk_filter.iterrows():
        h = _analisis_produk(row, cocok_map, tidak_map, cache_cocok, cache_tidak, cocok_keys, tidak_keys)
        if h:
            hasil.append(h)

    hasil.sort(key=lambda x: x['skor'], reverse=True)
    direk  = [h for h in hasil if h['skor'] > 0]
    lainnya = [h for h in hasil if h['skor'] <= 0]

    return jsonify({
        'jenis_kulit':   jenis_kulit,
        'masalah_kulit': masalah_kulit,
        'kategori':      kategori,
        'total':         len(direk),
        'results':       direk[:20],
        'tidak_cocok':   lainnya[:5],
    }), 200
# ─── POST /api/analyze ────────────────────────────────────────────────────────

@recommend_bp.route('/analyze', methods=['POST'])
def analyze_ingredients():
    """
    Analisis satu set ingredients hasil scan terhadap profil pengguna.
    Body JSON:
    {
        "jenis_kulit":   "Normal",
        "masalah_kulit": "Jerawat",
        "ingredients":   ["Ceramide", "Niacinamide", "Centella Asiatica"]
    }
    """
    try:
        data_set = _get_data()
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 503

    data = request.get_json(silent=True) or {}
    jenis_kulit   = (data.get('jenis_kulit')   or '').strip()
    masalah_kulit = (data.get('masalah_kulit') or '').strip()
    ingredients   = data.get('ingredients') or []

    if not jenis_kulit or not ingredients:
        return jsonify({'error': 'jenis_kulit dan ingredients wajib diisi.'}), 400

    jc = data_set['jenis_cocok']
    jt = data_set['jenis_tidak']
    mc = data_set['masalah_cocok']
    mt = data_set['masalah_tidak']

    rules_cocok = pd.concat([
        jc[jc['Jenis_Kulit'].str.strip() == jenis_kulit][['Ingredient', 'Manfaat']],
        mc[mc['Masalah_Kulit'].str.strip() == masalah_kulit][['Ingredient', 'Manfaat']],
    ], ignore_index=True).drop_duplicates(subset='Ingredient')

    rules_tidak = pd.concat([
        jt[jt['Jenis_Kulit'].str.strip() == jenis_kulit].rename(columns={'Efek Samping': 'Efek_Samping'})[['Ingredient', 'Efek_Samping']],
        mt[mt['Masalah_Kulit'].str.strip() == masalah_kulit].rename(columns={'Efek Samping': 'Efek_Samping'})[['Ingredient', 'Efek_Samping']],
    ], ignore_index=True).drop_duplicates(subset='Ingredient')

    # ─── Cocokkan dengan Dataset (Match Product) ───
    scan_set = set([i.strip().lower() for i in ingredients])
    best_match = None
    best_score = 0.0

    for _, row in data_set['produk_df'].iterrows():
        prod_ingr = _parse_ingredients(row.get('Ingridients', ''))
        prod_set = set([i.strip().lower() for i in prod_ingr])
        if not prod_set:
            continue
        
        # Jaccard Similarity
        intersection = len(scan_set.intersection(prod_set))
        union = len(scan_set.union(prod_set))
        score = intersection / union
        
        if score > best_score:
            best_score = score
            best_match = row

    if best_score >= 0.65:
        # Jika ketemu match (>65% mirip), gunakan data asli produk tersebut
        dummy_produk = best_match.copy()
        # Kita bisa tetap menggunakan ingredients hasil OCR atau yang dari database.
        # Lebih aman pakai dari database (krn OCR bisa typo), 
        # tapi user minta dicocokkan, jd kita pakai data database agar analisis 100% akurat.
    else:
        # Buat dummy product dari ingredients jika tidak ada match
        dummy_produk = pd.Series({
            'Nama Produk': 'Hasil Scan (Produk Tidak Dikenali)',
            'Kategori':    'Umum',
            'Ingridients': ','.join(ingredients),
        })

    cocok_map = _build_rule_dict(rules_cocok, 'Manfaat' if 'Manfaat' in rules_cocok.columns else 'None')
    tidak_map = _build_rule_dict(rules_tidak, 'Efek_Samping' if 'Efek_Samping' in rules_tidak.columns else 'None')
    
    hasil = _analisis_produk(dummy_produk, cocok_map, tidak_map, {}, {}, list(cocok_map.keys()), list(tidak_map.keys()))
    
    return jsonify({
        'jenis_kulit':   jenis_kulit,
        'masalah_kulit': masalah_kulit,
        'hasil':         hasil
    }), 200


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

    df = data['produk_df']
    mask = df['Nama Produk'].astype(str).str.lower().str.contains(query, na=False)
    filtered = df[mask].head(10)

    results = []
    for _, row in filtered.iterrows():
        ingr_list = _parse_ingredients(row.get('Ingridients', ''))
        results.append({
            'nama_produk': str(row.get('Nama Produk', '')),
            'kategori': str(row.get('Kategori', '')),
            'gambar': str(row.get('Gambar', '')) if pd.notna(row.get('Gambar')) else '',
            'ingredients': ingr_list
        })

    return jsonify(results), 200

# ─── GET /api/recommend/meta ──────────────────────────────────────────────────

# ─── GET /api/recommend/search ────────────────────────────────────────────────

# ─── GET /api/recommend/meta ──────────────────────────────────────────────────

@recommend_bp.route('/meta', methods=['GET'])
def get_meta():
    try:
        data_set = _get_data()
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 503

    return jsonify({
        'jenis_kulit':   sorted(data_set['jenis_cocok']['Jenis_Kulit'].dropna().unique().tolist()),
        'masalah_kulit': sorted(data_set['masalah_cocok']['Masalah_Kulit'].dropna().unique().tolist()),
        'kategori':      sorted(data_set['produk_df']['Kategori'].dropna().unique().tolist()),
    }), 200
