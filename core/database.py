import os
import json
import sqlite3
import datetime

try:
    from core.config import DB_PATH
except Exception:
    DB_PATH = os.path.join(os.path.dirname(__file__), 'jarvis_vault.db')

def get_connection():
    return sqlite3.connect(DB_PATH)

def initialize_db():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('PRAGMA journal_mode=WAL;')
    except Exception: pass
    cursor.execute('CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS nodes (id TEXT PRIMARY KEY, project_id TEXT, data TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS skills (name TEXT PRIMARY KEY, json_data TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS user_memory (id INTEGER PRIMARY KEY AUTOINCREMENT, fact TEXT, category TEXT, date_learned TEXT, emotional_tag TEXT)')
    try:
        cursor.execute('ALTER TABLE user_memory ADD COLUMN emotional_tag TEXT')
    except Exception:
        pass
    cursor.execute('CREATE TABLE IF NOT EXISTS proactive_events (id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT, priority INTEGER, title TEXT, message TEXT, data TEXT, timestamp TEXT)')
    conn.commit()
    conn.close()

def create_project(name):
    import uuid
    project_id = str(uuid.uuid4())
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO projects (id, name) VALUES (?, ?)', (project_id, name))
    conn.commit()
    conn.close()
    return project_id

def save_node(project_id, node_id, data):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO nodes (id, project_id, data) VALUES (?, ?, ?)', (node_id, project_id, data))
    conn.commit()
    conn.close()

def save_skill(skill_name, json_data):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO skills (name, json_data) VALUES (?, ?)', (skill_name, json_data))
    conn.commit()
    conn.close()

def get_skill(skill_name):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT json_data FROM skills WHERE name = ?', (skill_name,))
    row = cursor.fetchone()
    conn.close()
    if row:
        import json
        try:
            return json.loads(row[0])
        except:
            return row[0]
    return None

def get_all_skills():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT name FROM skills')
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]

def save_user_fact(fact, category, emotional_tag=None):
    conn = get_connection()
    cursor = conn.cursor()
    date_learned = datetime.datetime.now().isoformat()
    try:
        cursor.execute('INSERT INTO user_memory (fact, category, date_learned, emotional_tag) VALUES (?, ?, ?, ?)', (fact, category, date_learned, emotional_tag))
    except Exception:
        cursor.execute('INSERT INTO user_memory (fact, category, date_learned) VALUES (?, ?, ?)', (fact, category, date_learned))
    conn.commit()
    conn.close()

def log_proactive_event(category, priority, title, message, data):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.datetime.now().isoformat()
    cursor.execute('INSERT INTO proactive_events (category, priority, title, message, data, timestamp) VALUES (?, ?, ?, ?, ?, ?)', 
                   (category, priority, title, message, data, timestamp))
    conn.commit()
    conn.close()


try:
    initialize_db()
except Exception:
    pass
