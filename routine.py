"""
routine.py – Skincare Routine endpoints for B-Glow
Blueprint prefix: /api/routine
"""
from flask import Blueprint, request, jsonify
from datetime import date, datetime, timedelta
from db import get_connection
import mysql.connector

routine_bp = Blueprint('routine', __name__, url_prefix='/api/routine')


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _row_to_step(row):
    return {
        'id':          row[0],
        'user_id':     row[1],
        'timeOfDay':   row[2],
        'label':       row[3],
        'product':     row[4],
        'brand':       row[5],
        'emoji':       row[6],
        'bg':          row[7],
        'isSpecial':   bool(row[8]),
        'dayIndex':    row[9],
        'insertAfter': row[10],
        'sortOrder':   row[11],
    }


def _ensure_streak_row(cursor, user_id):
    """Insert a streak row for user if missing."""
    cursor.execute(
        "INSERT IGNORE INTO routine_streak (user_id) VALUES (%s)", (user_id,)
    )


def _recalc_streak(cursor, user_id):
    """
    Called after a day's log changes.
    If today's routine has ANY checked step → mark today as completed.
    Recalculate current_streak and best_streak.
    """
    today = date.today()

    # Check if there are any checked steps today for this user
    cursor.execute(
        "SELECT COUNT(*) FROM routine_logs WHERE user_id=%s AND log_date=%s",
        (user_id, today)
    )
    count = cursor.fetchone()[0]

    _ensure_streak_row(cursor, user_id)
    cursor.execute(
        "SELECT current_streak, best_streak, last_completed_date FROM routine_streak WHERE user_id=%s",
        (user_id,)
    )
    streak_row = cursor.fetchone()
    current  = streak_row[0]
    best     = streak_row[1]
    last_date = streak_row[2]  # date or None

    if count > 0:
        # Something was checked today
        if last_date is None:
            current = 1
        elif last_date == today:
            pass  # already counted
        elif last_date == today - timedelta(days=1):
            current += 1  # consecutive
        else:
            current = 1   # streak broken, restart

        best = max(best, current)
        cursor.execute(
            """UPDATE routine_streak
               SET current_streak=%s, best_streak=%s, last_completed_date=%s
               WHERE user_id=%s""",
            (current, best, today, user_id)
        )
    # If unchecking all steps for today — don't reduce streak here (UX friendlier)


# ─── GET /api/routine/steps?user_id=X ─────────────────────────────────────────

@routine_bp.route('/steps', methods=['GET'])
def get_steps():
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({'error': 'user_id diperlukan'}), 400

    conn   = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """SELECT id, user_id, time_of_day, label, product, brand,
                      emoji, bg, is_special, day_index, insert_after, sort_order
               FROM routine_steps
               WHERE user_id = %s
               ORDER BY time_of_day, is_special, sort_order, id""",
            (user_id,)
        )
        rows = cursor.fetchall()

        # If user has no steps yet, seed with default routine
        if not rows:
            _seed_default_steps(cursor, conn, user_id)
            cursor.execute(
                """SELECT id, user_id, time_of_day, label, product, brand,
                          emoji, bg, is_special, day_index, insert_after, sort_order
                   FROM routine_steps
                   WHERE user_id = %s
                   ORDER BY time_of_day, is_special, sort_order, id""",
                (user_id,)
            )
            rows = cursor.fetchall()

        return jsonify([_row_to_step(r) for r in rows])
    finally:
        cursor.close()
        conn.close()


def _seed_default_steps(cursor, conn, user_id):
    """Insert default morning & night routine for a brand-new user."""
    defaults = [
        # Morning base
        ('morning', 'Pembersih',   'Gentle Hydrating Cleanser', 'Skintific',   '🧴', '#E8F5E9', 0, None, None, 1),
        ('morning', 'Toner',       'AHA/BHA Toner',              'COSRX',       '🌿', '#F1F8E9', 0, None, None, 2),
        ('morning', 'Serum',       '10% Niacinamide Serum',      'Somethinc',   '✨', '#E8EAF6', 0, None, None, 3),
        ('morning', 'Pelembap',    '5X Ceramide Barrier Gel',    'Skintific',   '💧', '#E1F5FE', 0, None, None, 4),
        ('morning', 'Tabir Surya', 'Aqua Sun Gel SPF 50',        'Skin Aqua',   '☀️', '#FFFDE7', 0, None, None, 5),
        # Night base
        ('night',   'Pembersih',   'Low pH Gel Cleanser',        'COSRX',       '🫧', '#E3F2FD', 0, None, None, 1),
        ('night',   'Toner',       'Mugwort Essence Toner',      'Isntree',     '🍃', '#E8F5E9', 0, None, None, 2),
        ('night',   'Pelembap',    'Snail Repair Cream',         'COSRX',       '🐌', '#FFF3E0', 0, None, None, 3),
        # Night special per-day
        ('night',   'Serum Exfo',  'AHA 7 Whitehead Power',      'COSRX',       '⚗️', '#FCE4EC', 1, 1,    'Toner', 1),
        ('night',   'Serum Exfo',  'AHA BHA PHA 30 Days Serum',  'Some By Mi',  '⚗️', '#FCE4EC', 1, 2,    'Toner', 1),
        ('night',   'Masker',      'Mugwort Mask',               "I'm From",    '🧖', '#E8F5E9', 1, 3,    'Toner', 1),
        ('night',   'Masker',      'Centella Calming Mask',      'Mediheal',    '🧖', '#E8F5E9', 1, 4,    'Toner', 1),
        ('night',   'Serum Retinol','Retinol 0.5% Serum',        'Avoskin',     '🌟', '#FFF3E0', 1, 5,    'Toner', 1),
        ('night',   'Masker',      'Wash-Off Clay Mask',         'Innisfree',   '🧖', '#E8F5E9', 1, 6,    'Pembersih', 1),
        ('night',   'Serum Retinol','Retinol 0.3% in Squalane',  'The Ordinary','🌟', '#FFF3E0', 1, 0,    'Toner', 1),
    ]
    cursor.executemany(
        """INSERT INTO routine_steps
           (user_id, time_of_day, label, product, brand, emoji, bg,
            is_special, day_index, insert_after, sort_order)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        [(user_id, *d) for d in defaults]
    )
    conn.commit()


# ─── POST /api/routine/steps ───────────────────────────────────────────────────

@routine_bp.route('/steps', methods=['POST'])
def add_step():
    data = request.get_json(silent=True) or {}

    user_id      = data.get('user_id')
    time_of_day  = data.get('timeOfDay')
    label        = (data.get('label') or '').strip()
    product      = (data.get('product') or '').strip()
    brand        = (data.get('brand') or '').strip()
    emoji        = data.get('emoji', '💧')
    bg           = data.get('bg', '#E1F5FE')
    is_special   = int(bool(data.get('isSpecial', False)))
    day_index    = data.get('dayIndex')        # None for base steps
    insert_after = data.get('insertAfter')     # None for append

    if not user_id or not time_of_day or not label or not product:
        return jsonify({'error': 'user_id, timeOfDay, label, dan product wajib diisi.'}), 400

    if time_of_day not in ('morning', 'night'):
        return jsonify({'error': 'timeOfDay harus morning atau night.'}), 400

    conn   = get_connection()
    cursor = conn.cursor()
    try:
        # Determine sort_order (append at end)
        cursor.execute(
            "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM routine_steps WHERE user_id=%s AND time_of_day=%s AND is_special=%s",
            (user_id, time_of_day, is_special)
        )
        sort_order = cursor.fetchone()[0]

        cursor.execute(
            """INSERT INTO routine_steps
               (user_id, time_of_day, label, product, brand, emoji, bg,
                is_special, day_index, insert_after, sort_order)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (user_id, time_of_day, label, product, brand, emoji, bg,
             is_special, day_index, insert_after, sort_order)
        )
        conn.commit()
        new_id = cursor.lastrowid

        return jsonify({
            'message': 'Langkah berhasil ditambahkan.',
            'step': {
                'id': new_id, 'user_id': user_id,
                'timeOfDay': time_of_day, 'label': label,
                'product': product, 'brand': brand,
                'emoji': emoji, 'bg': bg,
                'isSpecial': bool(is_special), 'dayIndex': day_index,
                'insertAfter': insert_after, 'sortOrder': sort_order,
            }
        }), 201

    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


# ─── DELETE /api/routine/steps/<step_id> ──────────────────────────────────────

@routine_bp.route('/steps/<int:step_id>', methods=['DELETE'])
def delete_step(step_id):
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({'error': 'user_id diperlukan'}), 400

    conn   = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM routine_steps WHERE id=%s AND user_id=%s",
            (step_id, user_id)
        )
        conn.commit()
        if cursor.rowcount == 0:
            return jsonify({'error': 'Langkah tidak ditemukan.'}), 404
        return jsonify({'message': 'Langkah berhasil dihapus.'}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


# ─── GET /api/routine/log?user_id=X&date=YYYY-MM-DD ──────────────────────────

@routine_bp.route('/log', methods=['GET'])
def get_log():
    user_id  = request.args.get('user_id')
    log_date = request.args.get('date', str(date.today()))

    if not user_id:
        return jsonify({'error': 'user_id diperlukan'}), 400

    conn   = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT step_id FROM routine_logs WHERE user_id=%s AND log_date=%s",
            (user_id, log_date)
        )
        checked_ids = [r[0] for r in cursor.fetchall()]
        return jsonify({'date': log_date, 'checkedStepIds': checked_ids})
    finally:
        cursor.close()
        conn.close()


# ─── POST /api/routine/log ────────────────────────────────────────────────────
# Body: { user_id, step_id, date, checked: true/false }

@routine_bp.route('/log', methods=['POST'])
def update_log():
    data    = request.get_json(silent=True) or {}
    user_id = data.get('user_id')
    step_id = data.get('step_id')
    log_date = data.get('date', str(date.today()))
    checked  = bool(data.get('checked', True))

    if not user_id or not step_id:
        return jsonify({'error': 'user_id dan step_id diperlukan.'}), 400

    conn   = get_connection()
    cursor = conn.cursor()
    try:
        if checked:
            cursor.execute(
                """INSERT IGNORE INTO routine_logs (user_id, step_id, log_date)
                   VALUES (%s, %s, %s)""",
                (user_id, step_id, log_date)
            )
        else:
            cursor.execute(
                "DELETE FROM routine_logs WHERE user_id=%s AND step_id=%s AND log_date=%s",
                (user_id, step_id, log_date)
            )

        _recalc_streak(cursor, user_id)
        conn.commit()
        return jsonify({'message': 'Log diperbarui.'}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


# ─── GET /api/routine/streak?user_id=X ───────────────────────────────────────

@routine_bp.route('/streak', methods=['GET'])
def get_streak():
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({'error': 'user_id diperlukan'}), 400

    conn   = get_connection()
    cursor = conn.cursor()
    try:
        _ensure_streak_row(cursor, user_id)
        conn.commit()

        cursor.execute(
            "SELECT current_streak, best_streak, last_completed_date FROM routine_streak WHERE user_id=%s",
            (user_id,)
        )
        row = cursor.fetchone()
        current = row[0]
        best    = row[1]

        # Build completedDays: last 7 days, today is index 6
        today = date.today()
        completed_days = []
        for i in range(6, -1, -1):
            d = today - timedelta(days=i)
            cursor.execute(
                "SELECT COUNT(*) FROM routine_logs WHERE user_id=%s AND log_date=%s",
                (user_id, d)
            )
            completed_days.append(cursor.fetchone()[0] > 0)

        return jsonify({
            'current':       current,
            'best':          best,
            'completedDays': completed_days,
        })
    finally:
        cursor.close()
        conn.close()
