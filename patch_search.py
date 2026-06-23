import os

filepath = r'd:\TAGw\backend\recommend.py'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

new_route = '''
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
'''.strip()

target = '# ─── GET /api/recommend/meta ──────────────────────────────────────────────────'

if target in content:
    content = content.replace(target, new_route)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print('Added /search endpoint successfully.')
else:
    print('Target not found in recommend.py')
