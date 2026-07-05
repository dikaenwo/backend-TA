import sys
sys.path.insert(0, '.')
from recommend import _get_data, _build_rule_maps_v2, _analisis_produk_v3, _prepare_v3_caches

data = _get_data()
rules_df, produk_df, spec_map = data['rules_df'], data['produk_df'], data['specificity_map']

jk_c, jk_t, mk_c, mk_t = _build_rule_maps_v2(rules_df, 'Kering', '')
caches = _prepare_v3_caches(jk_c, jk_t, mk_c, mk_t)

serum_df = produk_df[produk_df['Kategori'].str.lower() == 'serum'].head(5)
print('FALLBACK MODE TEST: KERING ONLY')
for _, row in serum_df.iterrows():
    h = _analisis_produk_v3(
        row, jk_c, jk_t, mk_c, mk_t,
        *caches, False, spec_map,
    )
    if h:
        print(f"- {h['nama_produk']}: skor={h['skor']}, mode={h['scoring_mode']}")
        for b in h['bahan_cocok']:
            print(f"   + {b['ingredient']} (spec={b['spesifisitas']})")
