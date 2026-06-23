import re

with open(r'd:\TAGw\src\pages\IngredientScanner.js', 'r', encoding='utf-8') as f:
    content = f.read()

old_initial = '''  function renderInitialView() {
    page.innerHTML = `
      <div class="scanner-header">
        <button class="back-btn" id="sc-back-btn">${icons.chevronLeft}</button>
        <h2>Scan Produk</h2>
      </div>

      <div class="scanner-instructions" style="margin-top: 10vh; display: flex; flex-direction: column; align-items: center; text-align: center; padding: 0 var(--space-lg);">
        <div style="background: var(--bg-soft); width: 120px; height: 120px; border-radius: 50%; display: flex; align-items: center; justify-content: center; margin-bottom: 24px;">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="var(--primary)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"></path><circle cx="12" cy="13" r="4"></circle></svg>
        </div>
        <h3 style="font-size: var(--font-lg); font-weight: 700; color: var(--text-primary); margin-bottom: 12px;">Ambil Foto Komposisi</h3>
        <p style="color: var(--text-secondary); line-height: 1.5; margin-bottom: 32px;">Gunakan kamera bawaan HP kamu untuk mengambil foto teks komposisi (ingredients) produk dengan jelas.</p>
        <button class="btn btn-primary btn-lg" id="open-camera-btn" style="width: 100%; max-width: 300px; padding: 16px; border-radius: 100px; font-weight: 600;">Buka Kamera</button>
      </div>
    `;'''

new_initial = '''  function renderInitialView() {
    page.innerHTML = `
      <div class="scanner-header" style="position: sticky; top: 0; background: var(--bg); z-index: 100;">
        <button class="back-btn" id="sc-back-btn">${icons.chevronLeft}</button>
        <h2>Scan & Cari Produk</h2>
      </div>

      <!-- Search Section -->
      <div style="padding: 24px 20px 10px;">
        <div style="position: relative;">
          <input type="text" id="product-search" placeholder="Cari nama produk di dataset..." style="width: 100%; padding: 14px 16px 14px 44px; border-radius: 100px; border: 1px solid var(--border-light); font-size: 0.95rem; background: var(--bg-card); outline: none;">
          <svg style="position: absolute; left: 16px; top: 50%; transform: translateY(-50%); color: var(--text-tertiary);" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
          
          <div id="search-dropdown" style="display: none; position: absolute; top: calc(100% + 8px); left: 0; right: 0; background: #fff; border: 1px solid var(--border-light); border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.1); z-index: 1000; max-height: 300px; overflow-y: auto;">
          </div>
        </div>
      </div>

      <div style="display: flex; align-items: center; margin: 16px 20px;">
        <div style="flex: 1; height: 1px; background: var(--border-light);"></div>
        <span style="padding: 0 16px; font-size: 0.8rem; color: var(--text-tertiary); font-weight: 600; letter-spacing: 0.5px;">ATAU</span>
        <div style="flex: 1; height: 1px; background: var(--border-light);"></div>
      </div>

      <div class="scanner-instructions" style="margin-top: 10px; display: flex; flex-direction: column; align-items: center; text-align: center; padding: 0 var(--space-lg);">
        <div style="background: var(--bg-soft); width: 100px; height: 100px; border-radius: 50%; display: flex; align-items: center; justify-content: center; margin-bottom: 20px;">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="var(--primary)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"></path><circle cx="12" cy="13" r="4"></circle></svg>
        </div>
        <h3 style="font-size: var(--font-lg); font-weight: 700; color: var(--text-primary); margin-bottom: 8px;">Ambil Foto Komposisi</h3>
        <p style="color: var(--text-secondary); line-height: 1.5; margin-bottom: 24px; font-size: 0.9rem;">Gunakan kamera HP kamu untuk scan teks komposisi (ingredients) produk secara langsung.</p>
        <button class="btn btn-primary btn-lg" id="open-camera-btn" style="width: 100%; max-width: 300px; padding: 16px; border-radius: 100px; font-weight: 600;">Buka Kamera</button>
      </div>
    `;'''

content = content.replace(old_initial, new_initial)

search_logic = '''      } catch (err) {
        console.log("Kamera dibatalkan atau error:", err);
      }
    });

    // --- Search Logic ---
    const searchInput = page.querySelector('#product-search');
    const dropdown = page.querySelector('#search-dropdown');
    let debounceTimer;

    searchInput.addEventListener('input', (e) => {
      clearTimeout(debounceTimer);
      const query = e.target.value.trim();
      if (!query) {
        dropdown.style.display = 'none';
        return;
      }
      
      debounceTimer = setTimeout(async () => {
        try {
          const res = await fetch(`${API_BASE}/api/recommend/search?q=${encodeURIComponent(query)}`);
          if (res.ok) {
            const results = await res.json();
            if (results.length > 0) {
              dropdown.innerHTML = results.map((item, index) => `
                <div class="search-item" data-idx="${index}" style="padding: 12px 16px; border-bottom: 1px solid var(--border-light); cursor: pointer; display: flex; align-items: center; gap: 12px;">
                  <div style="width:40px; height:40px; background:#f3f4f6; border-radius:8px; flex-shrink:0; overflow:hidden; display:flex; align-items:center; justify-content:center;">
                    ${item.gambar ? `<img src="${item.gambar}" style="width:100%; height:100%; object-fit:cover;">` : `<span style="font-size:20px;">🧴</span>`}
                  </div>
                  <div style="flex:1; min-width:0;">
                    <div style="font-weight:600; font-size:0.9rem; color:var(--text-primary); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${item.nama_produk}</div>
                    <div style="font-size:0.75rem; color:var(--text-secondary);">${item.kategori || 'Produk'}</div>
                  </div>
                </div>
              `).join('');
              
              dropdown.querySelectorAll('.search-item').forEach(el => {
                el.addEventListener('click', () => {
                  const idx = parseInt(el.getAttribute('data-idx'));
                  const selected = results[idx];
                  if (selected.ingredients && selected.ingredients.length > 0) {
                    localStorage.setItem('bglow_scan_ingredients', JSON.stringify(selected.ingredients));
                    window.location.hash = '#/analyze';
                  } else {
                    alert('Maaf, data komposisi untuk produk ini tidak tersedia di dataset.');
                  }
                });
              });
              dropdown.style.display = 'block';
            } else {
              dropdown.innerHTML = '<div style="padding: 16px; text-align: center; color: var(--text-secondary); font-size: 0.85rem;">Produk tidak ditemukan</div>';
              dropdown.style.display = 'block';
            }
          }
        } catch (err) {
          console.error(err);
        }
      }, 300);
    });

    // Hide dropdown when clicking outside
    page.addEventListener('click', (e) => {
      if (searchInput && dropdown && !searchInput.contains(e.target) && !dropdown.contains(e.target)) {
        dropdown.style.display = 'none';
      }
    });
  }'''

content = content.replace('''      } catch (err) {
        console.log("Kamera dibatalkan atau error:", err);
      }
    });
  }''', search_logic)

if "import { API_BASE }" not in content:
    content = "import { API_BASE } from '../utils/api.js';\n" + content

with open(r'd:\TAGw\src\pages\IngredientScanner.js', 'w', encoding='utf-8') as f:
    f.write(content)
print('Patched IngredientScanner.js')
