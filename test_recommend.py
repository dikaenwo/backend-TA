import sys
sys.path.insert(0, '.')
from recommend import _load, _analisis_produk
import pandas as pd

jenis_cocok, jenis_tidak, masalah_cocok, masalah_tidak, produk_df = _load()

jenis_kulit   = 'Kering'
masalah_kulit = 'Hiperpigmentasi'
kategori      = 'Serum'

rules_cocok = pd.concat([
    jenis_cocok[jenis_cocok['Jenis_Kulit'].str.strip() == jenis_kulit][['Ingredient', 'Manfaat']],
    masalah_cocok[masalah_cocok['Masalah_Kulit'].str.strip() == masalah_kulit][['Ingredient', 'Manfaat']]
], ignore_index=True).drop_duplicates(subset='Ingredient')

rules_tidak = pd.concat([
    jenis_tidak[jenis_tidak['Jenis_Kulit'].str.strip() == jenis_kulit].rename(columns={'Efek Samping': 'Efek_Samping'})[['Ingredient', 'Efek_Samping']],
    masalah_tidak[masalah_tidak['Masalah_Kulit'].str.strip() == masalah_kulit].rename(columns={'Efek Samping': 'Efek_Samping'})[['Ingredient', 'Efek_Samping']]
], ignore_index=True).drop_duplicates(subset='Ingredient')

produk_filter = produk_df[produk_df['Kategori'].str.lower() == kategori.lower()]
hasil_semua = []
for _, row in produk_filter.iterrows():
    h = _analisis_produk(row, rules_cocok, rules_tidak)
    if h:
        hasil_semua.append(h)

hasil_sorted = sorted(hasil_semua, key=lambda x: x['skor'], reverse=True)
direk = [h for h in hasil_sorted if h['skor'] > 0]

print("Total produk dianalisis:", len(hasil_sorted))
print("Direkomendasikan:", len(direk))
print()
for h in hasil_sorted[:3]:
    print("  [{}] {}".format(h['skor'], h['nama_produk']))
    print("  Bahan cocok: {}, Tidak cocok: {}".format(len(h['bahan_cocok']), len(h['bahan_tidak_cocok'])))
    print()
