"""
patch_scanner.py – Ganti fungsi renderResults di IngredientScanner.js
"""
import re

INPUT  = r'd:\TAGw\src\pages\IngredientScanner.js'
OUTPUT = r'd:\TAGw\src\pages\IngredientScanner.js'

with open(INPUT, 'r', encoding='utf-8') as f:
    content = f.read()

# Fungsi baru yang akan menggantikan renderResults
NEW_FUNC = r"""  function renderResults(rawText, imageDataUrl = null, ocrWords = [], imgW = 100, imgH = 100) {
    let highlightsHtml = '';
    if (ocrWords.length > 0 && imgW > 0 && imgH > 0) {
      highlightsHtml = ocrWords.map(word => {
        const left   = (word.bbox.x0 / imgW) * 100;
        const top    = (word.bbox.y0 / imgH) * 100;
        const width  = ((word.bbox.x1 - word.bbox.x0) / imgW) * 100;
        const height = ((word.bbox.y1 - word.bbox.y0) / imgH) * 100;
        return `<div class="ocr-highlight match" style="top:${top}%; left:${left}%; width:${width}%; height:${height}%;"></div>`;
      }).join('');
    }

    const imagePreviewHtml = imageDataUrl ? `
      <div class="captured-image-wrapper">
        <img src="${imageDataUrl}" class="captured-image"/>
        ${highlightsHtml}
      </div>
    ` : '';

    let displayText = rawText;
    if (!displayText && ocrWords.length > 0) {
      displayText = ocrWords.map(w => w.text).join(' ');
    }

    // Parse ingredients dari teks OCR (split by koma / newline)
    const parsedIngredients = displayText
      ? displayText.split(/[,\n]+/).map(s => s.trim()).filter(s => s.length > 2 && s.length < 60)
      : [];
    const hasIngredients = parsedIngredients.length > 0;

    page.innerHTML = `
      <div class="scanner-header" style="background:var(--bg); color:var(--text-primary); position:sticky; top:0; z-index:10;">
        <button class="back-btn" id="sc-back-btn" style="color:var(--text-primary); background:var(--bg-soft);">${icons.chevronLeft}</button>
        <h2>Hasil Scan OCR</h2>
      </div>

      <div class="scan-results-page" style="padding: var(--space-lg); padding-bottom: 100px;">
        ${imagePreviewHtml}

        <!-- Teks hasil OCR -->
        <div style="background:var(--bg-card); border-radius:var(--radius-lg); padding:var(--space-lg); margin-bottom:var(--space-lg); border: 1px solid var(--border-light);">
          <h4 style="font-size:var(--font-sm); margin-bottom:12px; color:var(--text-secondary);">
            Teks yang terbaca dari gambar:
          </h4>
          <div style="font-size:13px; font-family:monospace; word-break:break-word; line-height:1.8; color:var(--text-primary); white-space:pre-wrap; background:var(--bg-soft); padding:16px; border-radius:var(--radius-md); max-height:200px; overflow-y:auto;">
            ${displayText || 'Tidak ada teks yang berhasil terbaca. Pastikan gambar jelas dan terang.'}
          </div>
        </div>

        <!-- Badge bahan terdeteksi -->
        ${hasIngredients ? `
        <div style="display:flex; align-items:center; gap:10px; background:var(--bg-overlay); border-radius:var(--radius-md); padding:12px var(--space-md); margin-bottom:var(--space-lg); border:1px solid var(--primary);">
          <span style="font-size:1.4rem;">🔬</span>
          <div>
            <div style="font-size:var(--font-sm); font-weight:700; color:var(--primary);">${parsedIngredients.length} Bahan Terdeteksi</div>
            <div style="font-size:var(--font-xs); color:var(--text-tertiary);">${parsedIngredients.slice(0,3).join(', ')}${parsedIngredients.length > 3 ? ' +' + (parsedIngredients.length - 3) + ' lainnya' : ''}</div>
          </div>
        </div>
        <button class="btn btn-primary btn-lg" id="get-reco-btn" style="width:100%; margin-bottom:12px; padding:16px; border-radius:100px; font-weight:700; font-size:var(--font-base); display:flex; align-items:center; justify-content:center; gap:10px;">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>
          Dapatkan Rekomendasi
        </button>
        ` : ''}

        <button class="btn btn-outline btn-lg" id="scan-again-btn" style="width:100%; border-radius:100px;">Scan Gambar Lain</button>
      </div>
    `;

    page.querySelector('#sc-back-btn').addEventListener('click', () => { window.location.hash = '#/'; });
    page.querySelector('#scan-again-btn').addEventListener('click', renderInitialView);

    // Tombol rekomendasi – simpan ingredients ke localStorage lalu navigasi
    if (hasIngredients) {
      page.querySelector('#get-reco-btn').addEventListener('click', () => {
        localStorage.setItem('bglow_scan_ingredients', JSON.stringify(parsedIngredients));
        window.location.hash = '#/recommendations';
      });
    }
  }
"""

# Ganti blok renderResults dengan regex
pattern = re.compile(
    r'  function renderResults\(rawText.*?^  \}',
    re.DOTALL | re.MULTILINE
)

new_content, count = pattern.subn(NEW_FUNC.strip(), content)

if count == 0:
    print("ERROR: Pattern tidak ditemukan! Tidak ada yang diganti.")
else:
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print(f"OK: renderResults berhasil diganti ({count}x).")
