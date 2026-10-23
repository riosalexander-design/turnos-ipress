"""
database.py
Módulo de base de datos SQLite para el sistema de programación de turnos de la IPRESS.
Maneja modelos de médicos, propuestas, discrepancias, logs de auditoría y aprobación.
Configurado con WAL mode y timeout para alta concurrencia sin bloqueos.
"""

import sqlite3
import hashlib
import os
from datetime import datetime

DB_FILE = os.path.join(os.path.dirname(__file__), "ipress_turnos.db")

def hash_pin(pin: str) -> str:
    """Hashea un PIN o contraseña con SHA-256."""
    return hashlib.sha256(pin.encode("utf-8")).hexdigest()

def get_connection():
    """Retorna una conexión a la base de datos con WAL mode y timeout."""
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn

def init_db():
    """Inicializa las tablas y los datos base (los 5 médicos y jefatura)."""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # Tabla de Médicos y Usuarios del Sistema
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS doctors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                condition TEXT NOT NULL, -- 'CAS' o 'Nombrado'
                cmp TEXT UNIQUE NOT NULL,
                username TEXT UNIQUE,
                pin_hash TEXT NOT NULL,
                role TEXT DEFAULT 'doctor', -- 'doctor', 'admin', 'rrhh'
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Migración segura si la columna username no existe
        cursor.execute("PRAGMA table_info(doctors)")
        existing_cols = [row["name"] for row in cursor.fetchall()]
        if "username" not in existing_cols:
            cursor.execute("ALTER TABLE doctors ADD COLUMN username TEXT")
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_doctors_username ON doctors(username)")

        # Tabla de Turnos y Propuestas
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS shifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                day INTEGER NOT NULL,
                shift_period TEXT NOT NULL, -- 'M', 'T', '12h', 'DES', 'LIBRE'
                service TEXT NOT NULL,       -- 'TU', 'CE', 'TS', 'TU+CE', 'DES', 'LIBRE'
                doctor_id INTEGER NOT NULL REFERENCES doctors(id),
                is_proposal INTEGER DEFAULT 1, -- 1=Propuesta del médico, 0=Asignado final
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(year, month, day, doctor_id)
            )
        """)

        # Tabla de Discrepancias y Conciliación
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS discrepancies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                day INTEGER NOT NULL,
                shift_period TEXT NOT NULL,
                service TEXT NOT NULL,
                doctor1_id INTEGER NOT NULL REFERENCES doctors(id),
                doctor2_id INTEGER NOT NULL REFERENCES doctors(id),
                conflict_type TEXT NOT NULL,
                description TEXT NOT NULL,
                suggested_alternative TEXT NOT NULL,
                deadline_datetime TEXT NOT NULL,
                status TEXT DEFAULT 'open',
                resolution_details TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                resolved_at TEXT
            )
        """)

        # Tabla de Auditoría
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doctor_id INTEGER,
                doctor_name TEXT,
                action TEXT NOT NULL,
                details TEXT,
                timestamp TEXT NOT NULL
            )
        """)

        # Tabla de Estado de Aprobación del Horario
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schedule_status (
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                is_approved INTEGER DEFAULT 0,
                approved_at TEXT,
                approved_by TEXT,
                approval_hash TEXT,
                PRIMARY KEY(year, month)
            )
        """)

        # Tabla de Feriados Personalizados
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS custom_holidays (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                discounts_shift INTEGER DEFAULT 1
            )
        """)

        # Tabla de Excepciones Diarias Aprobadas por el Administrador
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS approved_day_exceptions (
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                day INTEGER NOT NULL,
                approved_by TEXT NOT NULL,
                approved_at TEXT NOT NULL,
                notes TEXT,
                PRIMARY KEY(year, month, day)
            )
        """)

        conn.commit()

        # Usuarios oficiales del sistema con credenciales basadas en su usuario
        initial_doctors = [
            ("Caldas Pacsi, Lily Katherine", "CAS", "71201", "lcaldas", "caldas2026", "doctor"),
            ("Chunga Torres, Tuly Magnolia", "CAS", "68432", "tchunga", "chunga2026", "doctor"),
            ("Laureano Varillas, Elizabeth Miriam", "CAS", "75319", "elaureano", "laureano2026", "doctor"),
            ("Sanchez Quintanilla, Cinthya", "CAS", "80145", "csanchez", "sanchez2026", "doctor"),
            ("Solano Ricaldi, Edward Jhonny", "Nombrado", "54982", "esolano", "solano2026", "doctor"),
            ("Jefatura Médica IPRESS", "Nombrado", "ADMIN", "jefatura", "jefatura2026", "admin"),
            ("Responsable de Recursos Humanos", "Nombrado", "RRHH", "rrhh", "rrhh2026", "rrhh"),
        ]

        for name, cond, cmp_num, uname, pwd, role in initial_doctors:
            cursor.execute("SELECT id FROM doctors WHERE cmp = ? OR username = ?", (cmp_num, uname))
            row = cursor.fetchone()
            if not row:
                cursor.execute("""
                    INSERT INTO doctors (name, condition, cmp, username, pin_hash, role)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (name, cond, cmp_num, uname, hash_pin(pwd), role))
            else:
                cursor.execute("""
                    UPDATE doctors
                    SET condition = ?, name = ?, username = ?, pin_hash = ?, role = ?
                    WHERE id = ?
                """, (cond, name, uname, hash_pin(pwd), role, row["id"]))
        
        # Poda automática de logs de auditoría mayores a 3 meses (90 días)
        cursor.execute("DELETE FROM audit_logs WHERE timestamp < datetime('now', '-90 days')")
        conn.commit()

def prune_audit_logs(conn=None):
    """Elimina del log de auditoría los registros de más de 3 meses (90 días de retención)."""
    sql = "DELETE FROM audit_logs WHERE timestamp < datetime('now', '-90 days')"
    if conn is not None:
        conn.execute(sql)
    else:
        with get_connection() as c:
            c.execute(sql)
            c.commit()

def get_audit_logs(limit: int = 500, filter_user: str = None, filter_action: str = None):
    """Retorna los registros de auditoría de los últimos 3 meses con filtros opcionales."""
    prune_audit_logs()
    query = "SELECT * FROM audit_logs WHERE timestamp >= datetime('now', '-90 days')"
    params = []
    if filter_user:
        query += " AND doctor_name LIKE ?"
        params.append(f"%{filter_user}%")
    if filter_action:
        query += " AND action LIKE ?"
        params.append(f"%{filter_action}%")
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]

def log_audit(doctor_id: int, doctor_name: str, action: str, details: str = "", conn=None):
    """Registra una acción de auditoría con fecha y hora exacta y aplica retención de 3 meses."""
    if conn is not None:
        conn.execute("""
            INSERT INTO audit_logs (doctor_id, doctor_name, action, details, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (doctor_id, doctor_name, action, details, datetime.now().isoformat()))
        prune_audit_logs(conn)
    else:
        with get_connection() as c:
            c.execute("""
                INSERT INTO audit_logs (doctor_id, doctor_name, action, details, timestamp)
                VALUES (?, ?, ?, ?, ?)
            """, (doctor_id, doctor_name, action, details, datetime.now().isoformat()))
            prune_audit_logs(c)
            c.commit()

if __name__ == "__main__":
    init_db()
    print("Base de datos inicializada correctamente.")
