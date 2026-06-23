import re

with open(r'd:\TAGw\src\pages\Analysis.js', 'r', encoding='utf-8') as f:
    content = f.read()

target = '''<div class="ana-content" style="padding: 20px 20px 100px;">
        <h2 class="ana-title">Ingredients Analysis 🔎</h2>'''

header_ui = '''<div class="ana-content" style="padding: 20px 20px 100px;">
        <!-- Product Info Header -->
        <div style="display:flex; gap:16px; margin-bottom:24px; align-items:flex-start;">
          <div style="width:80px; height:80px; background:#f3f4f6; border-radius:12px; overflow:hidden; flex-shrink:0; display:flex; align-items:center; justify-content:center; box-shadow:0 2px 8px rgba(0,0,0,0.05);">
            ${analysisData.gambar ? `<img src="${analysisData.gambar}" style="width:100%; height:100%; object-fit:cover;">` : `<span style="font-size:32px;">🧴</span>`}
          </div>
          <div style="flex:1;">
            <div style="font-size:0.8rem; color:var(--text-secondary); text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px; font-weight:700;">${analysisData.kategori || 'Analisis OCR'}</div>
            <h2 style="font-size:1.1rem; font-weight:800; color:var(--text-primary); margin-bottom:8px; line-height:1.3;">${analysisData.nama_produk || 'Hasil Scan'}</h2>
            <div style="display:inline-block; background:${analysisData.skor > 0 ? '#d1fae5' : '#fce8e8'}; color:${analysisData.skor > 0 ? '#059669' : '#dc2626'}; padding:4px 10px; border-radius:100px; font-weight:700; font-size:0.85rem;">
              Skor Kecocokan: ${analysisData.skor > 0 ? '+' : ''}${analysisData.skor}
            </div>
          </div>
        </div>
        <hr style="border:none; border-top:1px dashed var(--border-light); margin:0 0 24px 0;" />
        
        <h2 class="ana-title">Ingredients Analysis 🔎</h2>'''

if target in content:
    content = content.replace(target, header_ui)
    with open(r'd:\TAGw\src\pages\Analysis.js', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Updated Analysis.js header')
else:
    print('Target not found in Analysis.js')
