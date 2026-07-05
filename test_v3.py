"""Quick functional test for v3 recommendation engine"""
import sys
sys.path.insert(0, '.')
from recommend import _get_data, _build_rule_maps_v2, _analisis_produk_v3, _prepare_v3_caches

data = _get_data()
rules_df = data['rules_df']
produk_df = data['produk_df']
spec_map = data['specificity_map']

# Test: kulit Kering + Berjerawat
jk_c, jk_t, mk_c, mk_t = _build_rule_maps_v2(rules_df, 'Kering', 'Berjerawat')
caches = _prepare_v3_caches(jk_c, jk_t, mk_c, mk_t)

serum_df = produk_df[produk_df['Kategori'].str.lower() == 'serum']
lolos = []
filtered = []
for _, row in serum_df.iterrows():
    h = _analisis_produk_v3(
        row, jk_c, jk_t, mk_c, mk_t,
        *caches, True, spec_map,
    )
    if h:
        if h['skin_type_compatible']:
            lolos.append(h)
        else:
            filtered.append(h)

lolos.sort(key=lambda x: x['skor'], reverse=True)
print("Serum Kering+Berjerawat: {} lolos, {} filtered out".format(len(lolos), len(filtered)))
print()
for h in lolos[:3]:
    skor = h['skor']
    print("  [{:+.1f}] {}".format(skor, h['nama_produk']))
    # Show specificity of cocok ingredients
    for b in h['bahan_cocok'][:3]:
        print("       {} bobot={} efektif={} spec={}".format(
            b['ingredient'], b['bobot'], b['bobot_efektif'], b['spesifisitas']))

if filtered:
    print("\nFiltered out (top 3):")
    for h in filtered[:3]:
        rejects = h['skin_type_info']['jk_hard_reject']
        print("  [{}] {}".format(h['skor'], h['nama_produk']))
        print("       rejected by: {}".format([r['ingredient'] for r in rejects]))
