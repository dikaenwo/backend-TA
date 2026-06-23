import re

with open(r'd:\TAGw\src\pages\IngredientScanner.js', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('Dapatkan Rekomendasi', 'Analisis Produk')
content = content.replace("window.location.hash = '#/recommendations'", "window.location.hash = '#/analyze'")

with open(r'd:\TAGw\src\pages\IngredientScanner.js', 'w', encoding='utf-8') as f:
    f.write(content)
print('Updated IngredientScanner.js')
