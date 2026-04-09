#!/usr/bin/env python3
"""
BRIO Agent — Serveur Python + SQLite
=====================================
Serveur Flask léger avec base de données SQLite.
Héberge l'application et l'API pour tous les postes du réseau.

Installation :
    pip install flask flask-cors

Démarrage :
    python server.py

Accès :
    Local  : http://localhost:5000
    Réseau : http://[IP-SERVEUR]:5000
"""

import sqlite3
import json
import os
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, g
from flask_cors import CORS

# ── Configuration ────────────────────────────────────
app = Flask(__name__, static_folder='.')
CORS(app)

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
DB_PATH   = os.path.join(BASE_DIR, 'brio_data.db')
HTML_PATH = os.path.join(BASE_DIR, 'index.html')

# ── Connexion SQLite ──────────────────────────────────
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row  # retourne des dicts
    return db

@app.teardown_appcontext
def close_db(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

# ── Initialisation des tables ─────────────────────────
def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                op            TEXT    NOT NULL,
                theme         TEXT    DEFAULT '',
                code          TEXT    DEFAULT '',
                date          TEXT    DEFAULT '',
                score_global  REAL    DEFAULT 0,
                pct           INTEGER DEFAULT 0,
                nb_questions  INTEGER DEFAULT 0,
                nb_correct    INTEGER DEFAULT 0,
                cat_scores    TEXT    DEFAULT '{}',
                questions     TEXT    DEFAULT '[]',
                created_at    TEXT    DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS operators (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                name           TEXT    UNIQUE NOT NULL,
                first_seen     TEXT    DEFAULT (datetime('now','localtime')),
                last_seen      TEXT    DEFAULT (datetime('now','localtime')),
                total_sessions INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_op   ON sessions(op);
            CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions(created_at);
        """)
    print(f"✅ Base de données initialisée : {DB_PATH}")

# ── Helpers ───────────────────────────────────────────
def row_to_dict(row):
    d = dict(row)
    for field in ('cat_scores', 'questions'):
        if field in d and isinstance(d[field], str):
            try:
                d[field] = json.loads(d[field])
            except Exception:
                d[field] = {} if field == 'cat_scores' else []
    return d

# ══════════════════════════════════════════════════════
#  ROUTES API
# ══════════════════════════════════════════════════════

# ── GET /api/sessions ─────────────────────────────────
@app.route('/api/sessions', methods=['GET'])
def get_sessions():
    try:
        db = get_db()
        rows = db.execute(
            "SELECT * FROM sessions ORDER BY created_at DESC LIMIT 500"
        ).fetchall()
        sessions = [row_to_dict(r) for r in rows]
        return jsonify({"success": True, "sessions": sessions, "count": len(sessions)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ── POST /api/sessions ────────────────────────────────
@app.route('/api/sessions', methods=['POST'])
def add_session():
    try:
        data = request.get_json()
        if not data or not data.get('op'):
            return jsonify({"success": False, "error": "Champ 'op' requis"}), 400

        db = get_db()

        # Calculer score si pas fourni
        questions = data.get('questions', [])
        scores = [q['score'] for q in questions if q.get('score') is not None]
        score_global = round(sum(scores) / len(scores), 1) if scores else 0
        pct = round(score_global * 10)
        nb_correct = len([s for s in scores if s >= 7])

        # Insérer la session
        cursor = db.execute("""
            INSERT INTO sessions
                (op, theme, code, date, score_global, pct, nb_questions, nb_correct, cat_scores, questions)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data.get('op'),
            data.get('theme', ''),
            data.get('code', ''),
            data.get('date', datetime.now().strftime('%d/%m/%Y %H:%M')),
            data.get('score_global', score_global),
            data.get('pct', pct),
            data.get('nb_questions', len(scores)),
            data.get('nb_correct', nb_correct),
            json.dumps(data.get('cat_scores', {})),
            json.dumps(questions)
        ))

        # Mettre à jour la table operators (upsert)
        db.execute("""
            INSERT INTO operators (name, last_seen, total_sessions)
            VALUES (?, datetime('now','localtime'), 1)
            ON CONFLICT(name) DO UPDATE SET
                last_seen      = datetime('now','localtime'),
                total_sessions = total_sessions + 1
        """, (data.get('op'),))

        db.commit()
        return jsonify({"success": True, "id": cursor.lastrowid})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ── GET /api/operators ────────────────────────────────
@app.route('/api/operators', methods=['GET'])
def get_operators():
    try:
        db = get_db()
        rows = db.execute("""
            SELECT
                o.name,
                o.total_sessions,
                o.first_seen,
                o.last_seen,
                COUNT(s.id)          AS session_count,
                ROUND(AVG(s.score_global), 1) AS avg_score,
                MAX(s.score_global)  AS best_score,
                MIN(s.score_global)  AS worst_score,
                SUM(s.nb_questions)  AS total_questions,
                SUM(s.nb_correct)    AS total_correct
            FROM operators o
            LEFT JOIN sessions s ON s.op = o.name
            GROUP BY o.name
            ORDER BY avg_score DESC NULLS LAST
        """).fetchall()
        return jsonify({"success": True, "operators": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ── GET /api/stats ────────────────────────────────────
@app.route('/api/stats', methods=['GET'])
def get_stats():
    try:
        db = get_db()
        row = db.execute("""
            SELECT
                COUNT(*)                        AS total_sessions,
                COUNT(DISTINCT op)              AS total_operators,
                ROUND(AVG(score_global), 1)     AS avg_score,
                MAX(score_global)               AS best_score,
                MIN(score_global)               AS worst_score,
                SUM(nb_questions)               AS total_questions,
                SUM(nb_correct)                 AS total_correct
            FROM sessions
        """).fetchone()
        return jsonify({"success": True, "stats": dict(row)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ── DELETE /api/sessions ──────────────────────────────
@app.route('/api/sessions', methods=['DELETE'])
def delete_sessions():
    try:
        db = get_db()
        db.execute("DELETE FROM sessions")
        db.execute("DELETE FROM operators")
        db.commit()
        return jsonify({"success": True, "message": "Toutes les sessions supprimées"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ── GET /api/export ───────────────────────────────────
@app.route('/api/export', methods=['GET'])
def export_sessions():
    try:
        db = get_db()
        rows = db.execute("SELECT * FROM sessions ORDER BY created_at DESC").fetchall()
        sessions = [row_to_dict(r) for r in rows]
        from flask import Response
        return Response(
            json.dumps(sessions, ensure_ascii=False, indent=2),
            mimetype='application/json',
            headers={'Content-Disposition': f'attachment; filename=brio_sessions_{datetime.now().strftime("%Y%m%d")}.json'}
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ── GET /api/health ───────────────────────────────────
@app.route('/api/health', methods=['GET'])
def health():
    try:
        db = get_db()
        count = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        return jsonify({
            "status": "ok",
            "sessions": count,
            "db": DB_PATH,
            "time": datetime.now().strftime('%d/%m/%Y %H:%M:%S')
        })
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

# ── PUT /api/sessions/<id> — Mise à jour session en cours ────
@app.route('/api/sessions/<int:session_id>', methods=['PUT'])
def update_session(session_id):
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Donnees manquantes"}), 400
        db = get_db()
        questions = data.get('questions', [])
        scores = [q['score'] for q in questions if q.get('score') is not None]
        score_global = round(sum(scores) / len(scores), 1) if scores else 0
        pct = round(score_global * 10)
        nb_correct = len([s for s in scores if s >= 7])
        db.execute(
            "UPDATE sessions SET score_global=?, pct=?, nb_questions=?, nb_correct=?, cat_scores=?, questions=? WHERE id=?",
            (score_global, pct, len(scores), nb_correct,
             json.dumps(data.get('cat_scores', {})),
             json.dumps(questions), session_id)
        )
        db.commit()
        return jsonify({"success": True, "id": session_id})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ── Servir le fichier HTML ────────────────────────────
@app.route('/')
def index():
    return send_from_directory(BASE_DIR, 'index.html')

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory(BASE_DIR, path)

# ── Démarrage ─────────────────────────────────────────
if __name__ == '__main__':
    init_db()

    import socket
    try:
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
    except Exception:
        local_ip = '127.0.0.1'

    print("\n" + "="*50)
    print("  🚀  BRIO Agent — Serveur Python")
    print("="*50)
    print(f"  Local  : http://localhost:5000")
    print(f"  Réseau : http://{local_ip}:5000")
    print(f"  Base   : {DB_PATH}")
    print("="*50)
    print("  Ctrl+C pour arrêter\n")

    app.run(host='0.0.0.0', port=5000, debug=False)