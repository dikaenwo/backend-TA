"""fix_scanner_regex.py – fix regex inside template literal"""
with open(r'd:\TAGw\src\pages\IngredientScanner.js', 'r', encoding='utf-8') as f:
    content = f.read()

# The problematic line contains a literal newline inside a regex inside a template literal.
# Find the exact bytes around parsedIngredients
idx = content.find('parsedIngredients = displayText')
print("Snippet:", repr(content[idx:idx+200]))

# Replace the split regex with a safe alternative (no literal newline)
old_snippet = 'displayText.split(/[,'
if old_snippet in content:
    # Find start and end of this expression
    start = content.find(old_snippet)
    end = content.find(')', start) + 1
    old_expr = content[start:end]
    print("Old expr:", repr(old_expr))
    
    new_expr = 'displayText.split(",").map(s => s.trim()).filter(s => s.length > 2 && s.length < 60'
    content2 = content[:start] + new_expr + content[end:]
    with open(r'd:\TAGw\src\pages\IngredientScanner.js', 'w', encoding='utf-8') as f:
        f.write(content2)
    print('Fixed!')
else:
    print("Pattern not found")
