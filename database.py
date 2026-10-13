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
        
        # Tabla de Médicos
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS doctors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                condition TEXT NOT NULL, -- 'CAS' o 'Nombrado'
                cmp TEXT UNIQUE NOT NULL,
                pin_hash TEXT NOT NULL,
                role TEXT DEFAULT 'doctor', -- 'doctor' o 'admin'
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

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

        conn.commit()

        # Precargar los 5 médicos oficiales con PINes independientes únicos
        initial_doctors = [
            ("Caldas Pacsi, Lily Katherine", "CAS", "71201", "4182", "doctor"),
            ("Chunga Torres, Tuly Magnolia", "CAS", "68432", "6935", "doctor"),
            ("Laureano Varillas, Elizabeth Miriam", "CAS", "75319", "5274", "doctor"),
            ("Sanchez Quintanilla, Cinthya", "CAS", "80145", "8319", "doctor"),
            ("Solano Ricaldi, Edward Jhonny", "Nombrado", "54982", "7461", "doctor"),
            ("Jefatura Médica IPRESS", "Nombrado", "ADMIN", "admin2026", "admin"),
        ]

        for name, cond, cmp_num, pin, role in initial_doctors:
            cursor.execute("SELECT id FROM doctors WHERE cmp = ?", (cmp_num,))
            row = cursor.fetchone()
            if not row:
                cursor.execute("""
                    INSERT INTO doctors (name, condition, cmp, pin_hash, role)
                    VALUES (?, ?, ?, ?, ?)
                """, (name, cond, cmp_num, hash_pin(pin), role))
            else:
                # Actualizar al PIN independiente correspondiente
                cursor.execute("""
                    UPDATE doctors
                    SET pin_hash = ?, condition = ?, name = ?
                    WHERE cmp = ?
                """, (hash_pin(pin), cond, name, cmp_num))
        
        conn.commit()

def log_audit(doctor_id: int, doctor_name: str, action: str, details: str = "", conn=None):
    """Registra una acción de auditoría con fecha y hora exacta."""
    if conn is not None:
        conn.execute("""
            INSERT INTO audit_logs (doctor_id, doctor_name, action, details, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (doctor_id, doctor_name, action, details, datetime.now().isoformat()))
    else:
        with get_connection() as c:
            c.execute("""
                INSERT INTO audit_logs (doctor_id, doctor_name, action, details, timestamp)
                VALUES (?, ?, ?, ?, ?)
            """, (doctor_id, doctor_name, action, details, datetime.now().isoformat()))
            c.commit()

if __name__ == "__main__":
    init_db()
    print("Base de datos inicializada correctamente.")
