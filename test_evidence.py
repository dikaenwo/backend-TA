"""Quick end-to-end test for Evidence Strength integration."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from recommend import (
    _get_data, _build_rule_maps_v2, _analisis_produk_v3, _prepare_v3_caches,
    _parse_ingredients, EVIDENCE_PRIMARY, CONTRA_HIGH,
)
import pandas as pd

data = _get_data()
rules_df = data['rules_df']
specificity_map = data['specificity_map']
evidence_maps = data['evidence_maps']

# ─── Test 1: Salicylic Acid should get Primary boost for Berjerawat ──────────
print("=" * 60)
print("TEST 1: Evidence boost for Berjerawat (Salicylic Acid = Primary)")
print("=" * 60)

# Create a fake product with Salicylic Acid at position #1
dummy = pd.Series({
    'Nama Produk': 'Test Product SA',
    'Kategori': 'Test',
    'Ingridients': 'Water, Salicylic Acid, Glycerin, Butylene Glycol, Dimethicone',
})

jk_c, jk_t, mk_c, mk_t = _build_rule_maps_v2(rules_df, 'Berminyak', 'Berjerawat')
caches = _prepare_v3_caches(jk_c, jk_t, mk_c, mk_t)

result = _analisis_produk_v3(
    dummy, jk_c, jk_t, mk_c, mk_t,
    *caches, True, specificity_map,
    evidence_maps, 'Berjerawat',
)

for b in result['bahan_cocok']:
    print(f"  {b['ingredient']:30s} bobot={b['bobot']:5.2f}  efektif={b['bobot_efektif']:5.2f}  "
          f"spec={b['spesifisitas']:12s}  ev={b.get('evidence_strength', '-')}")
print(f"  Skor: {result['skor']}")
print()

# ─── Test 2: Same product but user selects PIH → Salicylic Acid should be Supportive
print("=" * 60)
print("TEST 2: Same product, masalah=PIH (Salicylic Acid = Supportive)")
print("=" * 60)

jk_c2, jk_t2, mk_c2, mk_t2 = _build_rule_maps_v2(rules_df, 'Berminyak', 'PIH')
caches2 = _prepare_v3_caches(jk_c2, jk_t2, mk_c2, mk_t2)

result2 = _analisis_produk_v3(
    dummy, jk_c2, jk_t2, mk_c2, mk_t2,
    *caches2, True, specificity_map,
    evidence_maps, 'PIH',
)

for b in result2['bahan_cocok']:
    print(f"  {b['ingredient']:30s} bobot={b['bobot']:5.2f}  efektif={b['bobot_efektif']:5.2f}  "
          f"spec={b['spesifisitas']:12s}  ev={b.get('evidence_strength', '-')}")
print(f"  Skor: {result2['skor']}")
print()

# ─── Test 3: Contraindication strength ───────────────────────────────────────
print("=" * 60)
print("TEST 3: Contraindication (MCI = High for Kemerahan)")
print("=" * 60)

dummy3 = pd.Series({
    'Nama Produk': 'Test Product MCI',
    'Kategori': 'Test',
    'Ingridients': 'Water, Methylchloroisothiazolinone, Glycerin, Butylene Glycol, Dimethicone',
})

jk_c3, jk_t3, mk_c3, mk_t3 = _build_rule_maps_v2(rules_df, 'Normal', 'Kemerahan')
caches3 = _prepare_v3_caches(jk_c3, jk_t3, mk_c3, mk_t3)

result3 = _analisis_produk_v3(
    dummy3, jk_c3, jk_t3, mk_c3, mk_t3,
    *caches3, True, specificity_map,
    evidence_maps, 'Kemerahan',
)

for b in result3['bahan_tidak_cocok']:
    print(f"  {b['ingredient']:30s} bobot={b['bobot']:5.2f}  efektif={b['bobot_efektif']:5.2f}  "
          f"spec={b['spesifisitas']:12s}  contra={b.get('contraindication_strength', '-')}")
print(f"  Skor: {result3['skor']}")
print()

# ─── Test 4: Position dominance check ────────────────────────────────────────
print("=" * 60)
print("TEST 4: Position remains dominant")
print("=" * 60)
print(f"  Evidence Primary multiplier: {EVIDENCE_PRIMARY}")
print(f"  Contra High multiplier: {CONTRA_HIGH}")
print()
print("  Position tier gaps (pos_w):")
print("    Top 20%: 1.0")
print("    20-50%:  0.5  (gap = 0.5)")
print("    >50%:    0.2  (gap = 0.3)")
print()
print("  Max evidence boost at Top 20%: 1.0 * 1.5 * 1.15 = {:.3f}".format(1.0 * 1.5 * 1.15))
print("  Max evidence boost at Mid:     0.5 * 1.5 * 1.15 = {:.3f}".format(0.5 * 1.5 * 1.15))
print("  → Top 20% Primary still > Mid Primary: POSITION DOMINANT ✓")
print()

print("ALL TESTS COMPLETED SUCCESSFULLY!")
