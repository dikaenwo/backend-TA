from flask import Flask, jsonify, request
from flask_cors import CORS
import pandas as pd
import os

from db import init_db
from auth import auth_bp
from routine import routine_bp
from recommend import recommend_bp

app = Flask(__name__)
CORS(app)

# ─── Register Blueprints ───────────────────────────────────────────────────────
app.register_blueprint(auth_bp)
app.register_blueprint(routine_bp)
app.register_blueprint(recommend_bp)

@app.route('/')
def index():
    return jsonify({"status": "B-Glow API is running!"})

# ─── Init DB on startup ────────────────────────────────────────────────────────
try:
    init_db()
except Exception as e:
    print(f"[WARNING] DB init failed: {e}. Check your MySQL config in db.py.")

# ─── Ingredients (CSV) ────────────────────────────────────────────────────────
def load_ingredients():
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_dir = os.path.dirname(current_dir)
        csv_file = os.path.join(project_dir, 'Dataset TA - Ingredients.csv')

        df = pd.read_csv(csv_file).fillna('')
        ingredients = df.to_dict(orient='records')

        cleaned_ingredients = []
        for index, item in enumerate(ingredients):
            cleaned_ingredients.append({
                "id": str(index + 1),
                "slug": item.get('Slug', '').strip(),
                "name": item.get('Ingredient Name', '').strip(),
                "alsoCalled": item.get('Also Called', '').strip(),
                "functions": item.get('Functions', '').strip(),
                "description": item.get('Description', '').strip(),
                "suitableFor": item.get('Jenis Kulit Cocok', '').strip(),
                "image": "/dummy_img.png",
            })

        return cleaned_ingredients
    except Exception as e:
        print(f"Error reading CSV: {e}")
        return []

DATASET_CACHE = load_ingredients()

@app.route('/api/ingredients', methods=['GET'])
def get_ingredients():
    query = request.args.get('q', '').lower()
    if query:
        filtered = [
            i for i in DATASET_CACHE
            if query in i['name'].lower() or query in i['description'].lower()
        ]
        return jsonify(filtered)
    return jsonify(DATASET_CACHE)

@app.route('/api/ingredients/<slug>', methods=['GET'])
def get_ingredient_by_slug(slug):
    for item in DATASET_CACHE:
        if item['slug'] == slug or item['id'] == slug:
            return jsonify(item)
    return jsonify({"error": "Ingredient not found"}), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
