"""fix2.py – tulis ulang baris 314 yang corrupt di IngredientScanner.js"""
with open(r'd:\TAGw\src\pages\IngredientScanner.js', 'r', encoding='utf-8') as f:
    lines = f.readlines()

print("Line 313:", repr(lines[312]))
print("Line 314:", repr(lines[313]))
print("Line 315:", repr(lines[314]))

# Ganti baris 314 (index 313) dengan versi yang benar
lines[313] = '      ? displayText.split(",").map(s => s.trim()).filter(s => s.length > 2 && s.length < 60)\n'

with open(r'd:\TAGw\src\pages\IngredientScanner.js', 'w', encoding='utf-8') as f:
    f.writelines(lines)
print("Fixed line 314!")
