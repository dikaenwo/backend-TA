"""
recommend.py – Product Recommendation Engine for B-Glow
Prefix: /api/recommend

Algoritma v4 (Rule-Based + WSM Posisi, Tanpa Normalisasi):
  - Disamakan persis dengan algoritma v9 hasil uji coba di Google Colab.
  - Jenis Kulit DAN Masalah Kulit sama-sama dihitung sebagai WSM single-axis
    (bukan lagi filter/constraint) → tidak ada lagi hard-reject.
  - Skor akhir = penjumlahan (skor_wsm Jenis Kulit + skor_wsm Masalah Kulit)
  - Bobot posisi ingredient: atas 20% → +1.0/-2.0, 20-50% → +0.5/-1.0, sisanya → +0.2/-0.5
  - Skor menggunakan Laplace Smoothing WSM 3 Kriteria (C1: Jenis Kulit, C2: Masalah Kulit, C3: Posisi)
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

# rapidfuzz jauh lebih cepat dari difflib untuk fuzzy-matching massal (C-optimized).
# Fallback otomatis ke difflib kalau library belum terinstall, supaya kode tetap jalan.
try:
    from rapidfuzz import fuzz as _rf_fuzz, process as _rf_process
    _HAS_RAPIDFUZZ = True
except ImportError:
    _HAS_RAPIDFUZZ = False

recommend_bp = Blueprint('recommend', __name__, url_prefix='/api/recommend')

# ─── Path Dataset ─────────────────────────────────────────────────────────────
_BASE    = os.path.dirname(os.path.abspath(__file__))
_DATASET = os.path.join(_BASE, 'Dataset')

# ─── Lazy cache (dimuat saat request pertama) ─────────────────────────────────
_cache = {}

# Jenis Kulit & Masalah Kulit yang valid (sesuai dataset terbaru)
VALID_JENIS_KULIT   = {'Normal', 'Berminyak', 'Kering', 'Kombinasi'}
VALID_MASALAH_KULIT = {'Berjerawat', 'PIE', 'PIH', 'Aging', 'Kusam', 'Kemerahan'}


def _adapt_new_to_wide(rules_df_long: pd.DataFrame) -> pd.DataFrame:
    """
    Konversi dataset format BARU (long / one-row-per-rule) → format WIDE lama
    supaya sisa kode di recommend.py (yang mengasumsikan skema lama) tetap jalan.

    Format BARU (dataset TA terbaru):
      ingredient_name | domain (skin_type|concern) | target | polarity (positif|negatif)
      | badge_text | ...

    Format WIDE (lama) yang dihasilkan:
      Ingredient | Jenis Kulit Cocok | Jenis Kulit Tidak Cocok
                 | Masalah Kulit Cocok | Masalah Kulit Tidak Cocok
                 | Deskripsi_ID
    """
    df = rules_df_long.copy()
    df['ingredient_name'] = df['ingredient_name'].astype(str).str.strip()
    df['domain']   = df['domain'].astype(str).str.strip().str.lower()
    df['polarity'] = df['polarity'].astype(str).str.strip().str.lower()
    df['target']   = df['target'].astype(str).str.strip()

    # Buang baris kosong / nan
    df = df[(df['ingredient_name'] != '') & (df['ingredient_name'].str.lower() != 'nan')]
    df = df[(df['target'] != '') & (df['target'].str.lower() != 'nan')]

    def _agg(mask) -> pd.Series:
        return (
            df[mask]
            .groupby('ingredient_name')['target']
            .apply(lambda s: ', '.join(sorted({x for x in s if x and x.lower() != 'nan'})))
        )

    m_jk_cocok  = (df['domain'] == 'skin_type') & (df['polarity'] == 'positif')
    m_jk_tidak  = (df['domain'] == 'skin_type') & (df['polarity'] == 'negatif')
    m_mk_cocok  = (df['domain'] == 'concern')   & (df['polarity'] == 'positif')
    m_mk_tidak  = (df['domain'] == 'concern')   & (df['polarity'] == 'negatif')

    jk_cocok = _agg(m_jk_cocok).rename('Jenis Kulit Cocok')
    jk_tidak = _agg(m_jk_tidak).rename('Jenis Kulit Tidak Cocok')
    mk_cocok = _agg(m_mk_cocok).rename('Masalah Kulit Cocok')
    mk_tidak = _agg(m_mk_tidak).rename('Masalah Kulit Tidak Cocok')

    # Deskripsi = badge_text pertama yang tersedia per ingredient (opsional)
    if 'badge_text' in df.columns:
        desc = (
            df.assign(_bt=df['badge_text'].astype(str).str.strip())
              .query("_bt != '' and _bt != 'nan'")
              .groupby('ingredient_name')['_bt']
              .first()
              .rename('Deskripsi_ID')
        )
    else:
        desc = pd.Series(dtype=str, name='Deskripsi_ID')

    wide = pd.concat([jk_cocok, jk_tidak, mk_cocok, mk_tidak, desc], axis=1).fillna('')
    wide.index.name = 'Ingredient'
    wide = wide.reset_index()
    return wide


def _get_data():
    """Load dataset sekali lalu cache. Raise RuntimeError jika gagal."""
    if _cache:
        return _cache
    try:
        rules_raw = pd.read_csv(os.path.join(_DATASET, 'Dataset Terbaru.csv'), low_memory=False)

        # Deteksi format: BARU (long) vs LAMA (wide).
        if 'ingredient_name' in rules_raw.columns and 'domain' in rules_raw.columns:
            rules_df = _adapt_new_to_wide(rules_raw)
            _fmt = f"BARU (long → wide, {len(rules_raw)} rules → {len(rules_df)} ingredients)"
        else:
            rules_df = rules_raw
            _fmt = f"LAMA (wide, {len(rules_df)} ingredients)"

        rules_df['Ingredient'] = rules_df['Ingredient'].astype(str).str.strip()
        _cache['rules_df']  = rules_df
        _cache['produk_df'] = pd.read_excel(os.path.join(_DATASET, 'Dataset Produk.xlsx'))

        # Precompute rows sekali sebagai list of dict → jauh lebih cepat dipakai
        # berulang kali dibanding rules_df.iterrows() (overhead pandas per-baris).
        _cache['rules_records'] = rules_df.to_dict('records')
        _cache['dataset_format'] = _fmt

        # Cache hasil _build_rule_maps_v2 per kombinasi (jenis_kulit, masalah_kulit).
        # Kombinasinya terbatas (jenis kulit x masalah kulit), jadi tidak perlu
        # di-rebuild dari 10k+ baris setiap request — cukup sekali per kombinasi.
        _cache['rule_maps_cache'] = {}

        print("[Recommend] Dataset Terbaru.csv & Dataset Produk.xlsx berhasil dimuat.")
        print(f"  Rules: {len(rules_df)} baris | Produk: {len(_cache['produk_df'])} baris")
        print(f"  Fuzzy matcher: {'rapidfuzz' if _HAS_RAPIDFUZZ else 'difflib (fallback, lebih lambat)'}")
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

def _build_rule_maps_v2(rules_records: list, jenis_kulit: str, masalah_kulit: str):
    """
    Build 4 independent maps dari Dataset Terbaru.csv:
      jk_cocok_map : alias → (original_name, alasan)  — cocok for jenis_kulit
      mk_cocok_map : alias → (original_name, alasan)  — cocok for masalah_kulit
      jk_tidak_map : alias → (original_name, alasan)  — tidak cocok for jenis_kulit
      mk_cocok_map : alias → (original_name, alasan)  — cocok for masalah_kulit
      mk_tidak_map : alias → (original_name, alasan)  — tidak cocok for masalah_kulit

    Setiap axis independen satu sama lain (tidak ada short-circuit OR).

    rules_records: list of dict hasil rules_df.to_dict('records') — dipakai
    ketimbang rules_df.iterrows() karena jauh lebih cepat untuk iterasi 10k+ baris.
    """
    jk_cocok_map = {}
    jk_tidak_map = {}
    mk_cocok_map = {}
    mk_tidak_map = {}

    for row in rules_records:
        orig_name = str(row.get('Ingredient', '')).strip()
        if not orig_name or orig_name == 'nan':
            continue

        deskripsi = str(row.get('Deskripsi_ID', '') or '').strip()
        if deskripsi == 'nan':
            deskripsi = ''

        jk_cocok   = str(row.get('Jenis Kulit Cocok', '') or '').strip()
        alasan_jkc = deskripsi if deskripsi else str(row.get('Alasan Jenis Kulit Cocok', '') or '').strip()
        
        jk_tidak_raw = row.get('Jenis Kulit Tidak Cocok', None)
        if jk_tidak_raw is None:
            jk_tidak_raw = row.get('Jenis Kulit Hindari', '')
        jk_tidak   = str(jk_tidak_raw or '').strip()
        alasan_jkt = deskripsi if deskripsi else str(row.get('Alasan Jenis Kulit Tidak Cocok', '') or '').strip()

        mk_cocok   = str(row.get('Masalah Kulit Cocok', '') or '').strip()
        alasan_mkc = deskripsi if deskripsi else str(row.get('Alasan Masalah Kulit Cocok', '') or '').strip()
        
        mk_tidak_raw = row.get('Masalah Kulit Tidak Cocok', None)
        if mk_tidak_raw is None:
            mk_tidak_raw = row.get('Masalah Kulit Hindari', '')
        mk_tidak   = str(mk_tidak_raw or '').strip()
        alasan_mkt = deskripsi if deskripsi else str(row.get('Alasan Masalah Kulit Tidak Cocok', '') or '').strip()

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


def _get_rule_maps_cached(data_set: dict, jenis_kulit: str, masalah_kulit: str):
    """
    Ambil rule maps dari cache kalau kombinasi (jenis_kulit, masalah_kulit) ini
    sudah pernah dihitung sebelumnya; kalau belum, build sekali lalu simpan.

    Ini menghindari rebuild dari 10k+ baris rules setiap request — kombinasi
    jenis_kulit x masalah_kulit jumlahnya terbatas (maks ~24), jadi setelah
    beberapa request pertama, hampir semua kombinasi sudah di-cache.
    """
    key = (jenis_kulit, masalah_kulit)
    rule_maps_cache = data_set['rule_maps_cache']
    if key not in rule_maps_cache:
        rule_maps_cache[key] = _build_rule_maps_v2(
            data_set['rules_records'], jenis_kulit, masalah_kulit
        )
    return rule_maps_cache[key]


def _fuzzy_best_match(query: str, keys: list, cutoff: float = 0.85):
    """
    Cari kecocokan terbaik untuk `query` di antara `keys` (cutoff 0.0–1.0).
    Pakai rapidfuzz kalau tersedia (jauh lebih cepat, C-optimized untuk
    matching massal); fallback ke difflib kalau rapidfuzz belum terinstall.

    Catatan: rapidfuzz.fuzz.ratio dan difflib.SequenceMatcher.ratio memakai
    algoritma yang sedikit berbeda, jadi ada kemungkinan (jarang) hasil match
    di sekitar batas cutoff sedikit berbeda antara kedua mode ini.
    """
    if not keys:
        return None
    if _HAS_RAPIDFUZZ:
        result = _rf_process.extractOne(
            query, keys, scorer=_rf_fuzz.ratio, score_cutoff=cutoff * 100
        )
        return result[0] if result else None
    else:
        fuzz_result = get_close_matches(query, keys, n=1, cutoff=cutoff)
        return fuzz_result[0] if fuzz_result else None


def _match_ingredient(k_aliases, cocok_map, tidak_map,
                      cache_cocok, cache_tidak, cocok_keys, tidak_keys):
    """
    Cocokkan alias ingredient terhadap cocok/tidak map (exact → fuzzy).
    Returns ('cocok', matched_key) atau ('tidak', matched_key) atau (None, None).

    cache_cocok/cache_tidak SEHARUSNYA di-share lintas semua produk dalam satu
    request (bukan dibuat baru per produk) — supaya ingredient yang sama tidak
    di-fuzzy-match berulang kali. Ini adalah optimasi performa utama.
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
                found = _fuzzy_best_match(a, cocok_keys, cutoff=0.85)
                cache_cocok[a] = found
                if found:
                    match_cocok = found
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
                found = _fuzzy_best_match(a, tidak_keys, cutoff=0.85)
                cache_tidak[a] = found
                if found:
                    match_tidak = found
                    break

    if match_tidak:
        return 'tidak', match_tidak

    return None, None


def _wsm_score_axis(ingredients_list: list, cocok_map: dict, tidak_map: dict,
                     cache_cocok: dict = None, cache_tidak: dict = None,
                     cocok_keys: list = None, tidak_keys: list = None) -> dict:
    """
    Hitung skor WSM murni berbasis posisi untuk SATU axis (jenis kulit ATAU
    masalah kulit). Tidak ada normalisasi, tidak ada evidence/contraindication
    multiplier — persis seperti wsm_score_axis() di Colab v9.

    cache_cocok/cache_tidak/cocok_keys/tidak_keys idealnya di-share antar
    produk dalam satu request (lihat _prepare_axis_caches) supaya fuzzy-match
    untuk ingredient yang sama tidak dihitung ulang tiap produk. Kalau tidak
    diberikan (mis. dipanggil sendiri untuk 1 produk saja), dibuat baru.
    """
    total = len(ingredients_list)
    if total == 0:
        return {'skor_raw': 0.0, 'skor_pos': 0.0, 'skor_neg': 0.0, 'cocok': [], 'tidak': [], 'detail': []}


    if cache_cocok is None:
        cache_cocok = {}
    if cache_tidak is None:
        cache_tidak = {}
    if cocok_keys is None:
        cocok_keys = list(cocok_map.keys())
    if tidak_keys is None:
        tidak_keys = list(tidak_map.keys())

    seen_cocok, seen_tidak = set(), set()
    cocok_found, tidak_found = [], []
    ingredients_detail = []
    score = 0.0
    skor_pos_acc = 0.0   # akumulasi bobot positif (untuk Laplace)
    skor_neg_acc = 0.0   # akumulasi bobot negatif (untuk Laplace)

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
                skor_pos_acc += pos_w
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
                skor_neg_acc += neg_w
                tidak_found.append({
                    'ingredient': orig,
                    'bobot': round(-neg_w, 6),
                    'alasan': alasan if alasan and alasan != '-' else '-',
                })
                status = 'tidak_cocok'

        ingredients_detail.append({'nama': ingr, 'posisi': idx + 1, 'status': status})

    return {
        'skor_raw': round(score, 6),
        'skor_pos': round(skor_pos_acc, 6),
        'skor_neg': round(skor_neg_acc, 6),
        'cocok': cocok_found,
        'tidak': tidak_found,
        'detail': ingredients_detail,
    }



def _prepare_axis_caches(jk_cocok_map: dict, jk_tidak_map: dict,
                          mk_cocok_map: dict, mk_tidak_map: dict) -> dict:
    """
    Siapkan cache fuzzy-match + key list SEKALI untuk dipakai bersama oleh
    semua produk dalam satu request. Ini optimasi performa utama: tanpa ini,
    setiap produk akan fuzzy-match ulang dari nol meski hasilnya pasti sama
    untuk ingredient yang sama (mis. "Aqua", "Glycerin" muncul di ratusan produk).
    """
    return {
        'jk': {
            'cache_cocok': {}, 'cache_tidak': {},
            'cocok_keys': list(jk_cocok_map.keys()),
            'tidak_keys': list(jk_tidak_map.keys()),
        },
        'mk': {
            'cache_cocok': {}, 'cache_tidak': {},
            'cocok_keys': list(mk_cocok_map.keys()),
            'tidak_keys': list(mk_tidak_map.keys()),
        },
    }


def _analisis_produk_v4(
    produk: pd.Series,
    jk_cocok_map: dict, jk_tidak_map: dict,
    mk_cocok_map: dict, mk_tidak_map: dict,
    axis_caches: dict = None,
) -> dict:
    """
    Analisis satu produk dengan WSM 3 Kriteria + Laplace Smoothing

    axis_caches: hasil _prepare_axis_caches(), di-share antar produk untuk
    performa. Kalau None, cache dibuat baru khusus untuk 1 produk ini saja
    (dipakai di endpoint /analyze yang memang cuma 1 produk).
    """
    ingredients_list = _parse_ingredients(produk.get('Ingridients', ''))
    if len(ingredients_list) == 0:
        return None

    if axis_caches is None:
        axis_caches = _prepare_axis_caches(jk_cocok_map, jk_tidak_map, mk_cocok_map, mk_tidak_map)

    jk_c = axis_caches['jk']
    mk_c = axis_caches['mk']

    hasil_jk = _wsm_score_axis(
        ingredients_list, jk_cocok_map, jk_tidak_map,
        jk_c['cache_cocok'], jk_c['cache_tidak'], jk_c['cocok_keys'], jk_c['tidak_keys'],
    )
    hasil_mk = _wsm_score_axis(
        ingredients_list, mk_cocok_map, mk_tidak_map,
        mk_c['cache_cocok'], mk_c['cache_tidak'], mk_c['cocok_keys'], mk_c['tidak_keys'],
    )

    # ── WSM 3 Kriteria (C1, C2, C3) ──────────────────────────────────────────
    # C1: Jenis Kulit, C2: Masalah Kulit, C3: Posisi (combined)
    # Disini kita adaptasi logika ke Laplace Smoothing:
    # Skor = 0.35 * C1 + 0.40 * C2 + 0.25 * C3
    # (Menggunakan hasil_jk dan hasil_mk)

    def _laplace(b, p):
        return (b + 1.0) / (b + p + 2.0)

    c1 = _laplace(hasil_jk['skor_pos'], hasil_jk['skor_neg'])
    c2 = _laplace(hasil_mk['skor_pos'], hasil_mk['skor_neg'])
    # C3 = simplified based on relevance
    c3 = _laplace(hasil_jk['skor_pos'] + hasil_mk['skor_pos'], hasil_jk['skor_neg'] + hasil_mk['skor_neg'])

    skor_total = 0.35 * c1 + 0.40 * c2 + 0.25 * c3
    rekomendasi_text = 'Sangat Direkomendasikan' if skor_total >= 0.65 else ('Cukup Direkomendasikan' if skor_total >= 0.52 else 'Tidak Direkomendasikan')

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
        'skor_total':         round(skor_total, 4),
        'skor':               round(skor_total, 4),
        'match_pct':          int(skor_total * 100),
        'rekomendasi':        rekomendasi_text,
        'wsm_detail':         {'C1': round(c1, 4), 'C2': round(c2, 4), 'C3': round(c3, 4)},
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
      2. Skor akhir = penjumlahan kedua axis (tanpa normalisasi, tanpa filter)
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

    jk_cocok_map, jk_tidak_map, mk_cocok_map, mk_tidak_map = _get_rule_maps_cached(
        data_set, jenis_kulit, masalah_kulit
    )

    produk_df = data_set['produk_df']
    produk_filter = (
        produk_df[produk_df['Kategori'].str.lower() == kategori.lower()]
        if kategori else produk_df.copy()
    )

    if produk_filter.empty:
        return jsonify({'results': [], 'total': 0, 'kategori': kategori}), 200

    # Cache fuzzy-match & key list dibuat SEKALI, dipakai bersama semua produk.
    axis_caches = _prepare_axis_caches(jk_cocok_map, jk_tidak_map, mk_cocok_map, mk_tidak_map)

    hasil_semua = []
    for _, row in produk_filter.iterrows():
        h = _analisis_produk_v4(row, jk_cocok_map, jk_tidak_map, mk_cocok_map, mk_tidak_map, axis_caches)
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
            'ranking_method': 'skor_jenis_kulit + skor_masalah_kulit',
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

    jk_cocok_map, jk_tidak_map, mk_cocok_map, mk_tidak_map = _get_rule_maps_cached(
        data_set, jenis_kulit, masalah_kulit
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

    jk_cocok_map, jk_tidak_map, mk_cocok_map, mk_tidak_map = _get_rule_maps_cached(
        data_set, jenis_kulit, masalah_kulit
    )

    produk_df = data_set['produk_df']
    axis_caches = _prepare_axis_caches(jk_cocok_map, jk_tidak_map, mk_cocok_map, mk_tidak_map)

    # Build results: match by normalized ingredients string so frontend can lookup by ingredients
    results = []
    for _, row in produk_df.iterrows():
        h = _analisis_produk_v4(row, jk_cocok_map, jk_tidak_map, mk_cocok_map, mk_tidak_map, axis_caches)
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