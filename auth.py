"""
auth.py – Login & Register routes for B-Glow
Prefix: /api/auth
"""
from flask import Blueprint, request, jsonify
import bcrypt
from db import get_connection
import mysql.connector

auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')


# ─── Helper ───────────────────────────────────────────────────────────────────

def _user_to_dict(row):
    """Map a DB row tuple → safe public dict (no password)."""
    return {
        'id':           row[0],
        'name':         row[1],
        'email':        row[2],
        'gender':       row[3],
        'age':          row[4],
        'skinType':     row[5],
        'skinConcern':  row[6],
    }


# ─── POST /api/auth/register ──────────────────────────────────────────────────

@auth_bp.route('/register', methods=['POST'])
def register():
    data = request.get_json(silent=True) or {}

    name     = (data.get('name') or '').strip()
    email    = (data.get('email') or '').strip().lower()
    password = (data.get('password') or '').strip()

    # ── Basic validation ──
    if not name or not email or not password:
        return jsonify({'error': 'Nama, email, dan kata sandi wajib diisi.'}), 400

    if len(password) < 8:
        return jsonify({'error': 'Kata sandi minimal 8 karakter.'}), 400

    if '@' not in email:
        return jsonify({'error': 'Format email tidak valid.'}), 400

    # ── Hash password ──
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "INSERT INTO users (name, email, password) VALUES (%s, %s, %s)",
            (name, email, hashed)
        )
        conn.commit()
        new_id = cursor.lastrowid

        return jsonify({
            'message': 'Akun berhasil dibuat.',
            'user': {
                'id':         new_id,
                'name':       name,
                'email':      email,
                'gender':     None,
                'age':        None,
                'skinType':   None,
                'skinConcern': None,
            }
        }), 201

    except mysql.connector.IntegrityError:
        return jsonify({'error': 'Email sudah terdaftar. Silakan gunakan email lain.'}), 409
    except Exception as e:
        conn.rollback()
        return jsonify({'error': f'Terjadi kesalahan server: {str(e)}'}), 500
    finally:
        cursor.close()
        conn.close()


# ─── POST /api/auth/login ─────────────────────────────────────────────────────

@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.get_json(silent=True) or {}

    email    = (data.get('email') or '').strip().lower()
    password = (data.get('password') or '').strip()

    if not email or not password:
        return jsonify({'error': 'Email dan kata sandi wajib diisi.'}), 400

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """SELECT id, name, email, gender, age, skin_type, skin_concern, password
               FROM users WHERE email = %s LIMIT 1""",
            (email,)
        )
        row = cursor.fetchone()

        if not row:
            return jsonify({'error': 'Email atau kata sandi salah.'}), 401

        stored_hash = row[7]
        if not bcrypt.checkpw(password.encode(), stored_hash.encode()):
            return jsonify({'error': 'Email atau kata sandi salah.'}), 401

        user = _user_to_dict(row)

        # Determine whether the user still needs personalization
        needs_personalization = not user['skinType'] or not user['gender']

        return jsonify({
            'message': 'Login berhasil.',
            'user': user,
            'needsPersonalization': needs_personalization,
        }), 200

    except Exception as e:
        return jsonify({'error': f'Terjadi kesalahan server: {str(e)}'}), 500
    finally:
        cursor.close()
        conn.close()


# ─── PUT /api/auth/profile ────────────────────────────────────────────────────
# Called after the personalization wizard finishes

@auth_bp.route('/profile', methods=['PUT'])
def update_profile():
    data = request.get_json(silent=True) or {}

    user_id      = data.get('id')
    gender       = data.get('gender')
    age          = data.get('age')
    skin_type    = data.get('skinType')
    skin_concern = data.get('skinConcern')

    if not user_id:
        return jsonify({'error': 'User ID diperlukan.'}), 400

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """UPDATE users
               SET gender=%s, age=%s, skin_type=%s, skin_concern=%s
               WHERE id=%s""",
            (gender, age, skin_type, skin_concern, user_id)
        )
        conn.commit()

        if cursor.rowcount == 0:
            return jsonify({'error': 'User tidak ditemukan.'}), 404

        return jsonify({'message': 'Profil berhasil diperbarui.'}), 200

    except Exception as e:
        conn.rollback()
        return jsonify({'error': f'Terjadi kesalahan server: {str(e)}'}), 500
    finally:
        cursor.close()
        conn.close()

# ─── GET /api/auth/profile/<user_id> ──────────────────────────────────────────

@auth_bp.route('/profile/<int:user_id>', methods=['GET'])
def get_profile(user_id):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """SELECT id, name, email, gender, age, skin_type, skin_concern
               FROM users WHERE id = %s LIMIT 1""",
            (user_id,)
        )
        row = cursor.fetchone()

        if not row:
            return jsonify({'error': 'User tidak ditemukan.'}), 404

        user = _user_to_dict(row)
        return jsonify(user), 200

    except Exception as e:
        return jsonify({'error': f'Terjadi kesalahan server: {str(e)}'}), 500
    finally:
        cursor.close()
        conn.close()
