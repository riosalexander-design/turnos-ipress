"""
database.py
Módulo de base de datos SQLite para el sistema de programación de turnos de la IPRESS.
Maneja modelos de médicos, propuestas, discrepancias, logs de auditoría y aprobación.
Configurado con cierre garantizado de conexiones, journal_mode=DELETE (compatible con
despliegues en Streamlit Cloud y Git) y auto-recuperación ante archivos WAL huérfanos.
"""

import sqlite3
import hashlib
import os
from datetime import datetime

DB_FILE = os.path.join(os.path.dirname(__file__), "ipress_turnos.db")

# Rutas candidatas a la base de datos del proyecto vinculado de Citas para Pacientes (citascsjp2)
EXTERNAL_CITAS_DB_CANDIDATES = [
    r"C:\Users\Alexander\OneDrive - EduCorpPERU (1)\2026\2026-2\citas-ipress-jp2\citas_jp2_v2.db",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "citas-ipress-jp2", "citas_jp2_v2.db")),
]


def find_external_citas_db() -> str | None:
    """Retorna la ruta de citas_jp2_v2.db si está disponible localmente."""
    for path in EXTERNAL_CITAS_DB_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


class ClosingConnection(sqlite3.Connection):
    """
    Subclase de sqlite3.Connection que cierra automáticamente el archivo
    al salir del bloque 'with get_connection() as conn:', evitando fugas
    de descriptores y bloqueos en entornos multi-hilo como Streamlit.
    """
    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            return super().__exit__(exc_type, exc_val, exc_tb)
        finally:
            try:
                self.close()
            except Exception:
                pass


def hash_pin(pin: str) -> str:
    """Hashea un PIN o contraseña con SHA-256."""
    return hashlib.sha256(pin.encode("utf-8")).hexdigest()


def _cleanup_stale_wal_files(db_path: str):
    """
    Elimina archivos laterales -wal y -shm huérfanos si existen junto a una BD
    que opera en modo DELETE (evita sqlite3.DatabaseError tras git pull en Streamlit Cloud).
    """
    for suffix in ("-wal", "-shm"):
        side_file = db_path + suffix
        if os.path.exists(side_file):
            try:
                os.remove(side_file)
            except OSError:
                pass


def _open_and_configure(db_path: str) -> sqlite3.Connection:
    """Abre y configura una conexión SQLite verificando integridad básica."""
    conn = sqlite3.connect(
        db_path,
        timeout=30.0,
        check_same_thread=False,
        factory=ClosingConnection
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = DELETE")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def get_connection():
    """
    Retorna una conexión a la base de datos con auto-cierre y auto-recuperación
    en caso de corrupción por archivos -wal/-shm desincronizados en la nube.
    """
    _cleanup_stale_wal_files(DB_FILE)
    try:
        return _open_and_configure(DB_FILE)
    except sqlite3.DatabaseError:
        # Si el archivo .db o sus archivos laterales quedaron corruptos tras un git pull,
        # limpiar y reconstruir automáticamente para no interrumpir el servicio.
        _cleanup_stale_wal_files(DB_FILE)
        try:
            if os.path.exists(DB_FILE):
                os.remove(DB_FILE)
        except OSError:
            pass
        return _open_and_configure(DB_FILE)

def _init_db_tables_and_users():
    """Crea tablas y asegura la existencia de los usuarios base sin sobrescribir claves personalizadas."""
    with get_connection() as conn:
        cursor = conn.cursor()

        # Verificar integridad rápida de la BD
        chk = cursor.execute("PRAGMA quick_check").fetchone()
        if not chk or chk[0] != "ok":
            raise sqlite3.DatabaseError("PRAGMA quick_check falló")

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

        # Tabla de Bloqueos de Turnos por Citas de Pacientes (Sincronizada con citascsjp2.streamlit.app)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS shift_appointment_locks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                day INTEGER NOT NULL,
                shift_period TEXT NOT NULL, -- 'M' o 'T'
                doctor_id INTEGER NOT NULL REFERENCES doctors(id),
                doctor_cmp TEXT NOT NULL,
                appointments_count INTEGER DEFAULT 1,
                sample_tickets TEXT,
                synced_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(year, month, day, shift_period, doctor_id)
            )
        """)

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

        # Hashes antiguos para migrar automáticamente si aún existen
        legacy_pin_hashes = {
            hash_pin("1201"), hash_pin("8432"), hash_pin("5319"),
            hash_pin("0145"), hash_pin("4982"), hash_pin("9999"),
            hash_pin("4182"), hash_pin("6935"), hash_pin("5274"),
            hash_pin("8319"), hash_pin("7461"), hash_pin("admin2026"),
        }

        for name, cond, cmp_num, uname, pwd, role in initial_doctors:
            cursor.execute("SELECT id, condition, name, username, pin_hash, role FROM doctors WHERE cmp = ? OR username = ?", (cmp_num, uname))
            row = cursor.fetchone()
            if not row:
                cursor.execute("""
                    INSERT INTO doctors (name, condition, cmp, username, pin_hash, role)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (name, cond, cmp_num, uname, hash_pin(pwd), role))
            else:
                # Para Jefatura ('admin') y RRHH ('rrhh') garantizar siempre las claves oficiales (jefatura2026 / rrhh2026)
                if role in ("admin", "rrhh") or not row["pin_hash"] or row["pin_hash"] in legacy_pin_hashes:
                    new_pin_hash = hash_pin(pwd)
                else:
                    new_pin_hash = row["pin_hash"]
                if (row["condition"] != cond or row["name"] != name or
                        row["username"] != uname or row["role"] != role or
                        row["pin_hash"] != new_pin_hash):
                    cursor.execute("""
                        UPDATE doctors
                        SET condition = ?, name = ?, username = ?, pin_hash = ?, role = ?
                        WHERE id = ?
                    """, (cond, name, uname, new_pin_hash, role, row["id"]))

        # Poda automática de logs de auditoría mayores a 3 meses (90 días)
        cursor.execute("DELETE FROM audit_logs WHERE timestamp < datetime('now', '-90 days')")

        conn.commit()


def init_db():
    """Inicializa las tablas y datos base con recuperación automática ante corrupción de SQLite."""
    try:
        _init_db_tables_and_users()
    except sqlite3.DatabaseError:
        _cleanup_stale_wal_files(DB_FILE)
        try:
            if os.path.exists(DB_FILE):
                os.remove(DB_FILE)
        except OSError:
            pass
        _init_db_tables_and_users()

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
