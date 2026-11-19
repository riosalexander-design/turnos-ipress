"""
database.py
Módulo de base de datos dual (Supabase PostgreSQL persistente en la nube + respaldo SQLite local)
para el sistema de programación de turnos de la IPRESS.
- En producción (Streamlit Cloud), si se configura DATABASE_URL en Secrets/entorno,
  persiste todos los datos de forma permanente en Supabase (PostgreSQL).
- En desarrollo local o pruebas unitarias (sin DATABASE_URL), utiliza SQLite local
  con cierre garantizado de conexiones, journal_mode=DELETE y auto-recuperación.
"""

import sqlite3
import hashlib
import os
import re
from datetime import datetime, timedelta

try:
    import psycopg2
    import psycopg2.extras
    HAS_PSYCOPG2 = True
except ImportError:
    psycopg2 = None
    HAS_PSYCOPG2 = False

DB_FILE = os.path.join(os.path.dirname(__file__), "ipress_turnos.db")

# Bandera para pruebas unitarias (fuerza SQLite aislado aunque exista DATABASE_URL en entorno)
FORCE_SQLITE = False

# Estado de inicialización en memoria por backend para evitar DDL repetitivo en cada rerun remoto
_INITIALIZED_BACKEND_KEY = None

# Rutas candidatas a la base de datos del proyecto vinculado de Citas para Pacientes (citascsjp2)
EXTERNAL_CITAS_DB_CANDIDATES = [
    r"C:\Users\Alexander\OneDrive\Apps\citas-ipress-jp2\citas_jp2_v2.db",
    r"C:\Users\Alexander\OneDrive - EduCorpPERU (1)\2026\2026-2\citas-ipress-jp2\citas_jp2_v2.db",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "citas-ipress-jp2", "citas_jp2_v2.db")),
]


def find_external_citas_db() -> str | None:
    """Retorna la ruta de citas_jp2_v2.db si está disponible localmente."""
    for path in EXTERNAL_CITAS_DB_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def get_database_url() -> str | None:
    """
    Obtiene la URL de conexión PostgreSQL (Supabase) desde variables de entorno
    o desde st.secrets en Streamlit Community Cloud.
    """
    if FORCE_SQLITE:
        return None

    env_url = (os.environ.get("DATABASE_URL") or "").strip()
    if env_url:
        return env_url

    try:
        import streamlit as st
        if hasattr(st, "secrets"):
            if "DATABASE_URL" in st.secrets:
                val = str(st.secrets["DATABASE_URL"]).strip()
                if val:
                    return val
            if "supabase" in st.secrets and "database_url" in st.secrets["supabase"]:
                val = str(st.secrets["supabase"]["database_url"]).strip()
                if val:
                    return val
    except Exception:
        pass

    return None


def is_postgres_active() -> bool:
    """Indica si el sistema está operando sobre PostgreSQL (Supabase)."""
    url = get_database_url()
    return bool(url and HAS_PSYCOPG2 and (url.startswith("postgres://") or url.startswith("postgresql://")))


def get_db_backend_info() -> dict:
    """Retorna información legible del motor de base de datos activo."""
    if is_postgres_active():
        return {
            "engine": "postgresql",
            "persistent": True,
            "label": "Supabase PostgreSQL (Nube Persistente)",
        }
    return {
        "engine": "sqlite",
        "persistent": False,
        "label": "SQLite Local",
    }


def _normalize_pg_url(url: str) -> str:
    """Normaliza el prefijo postgres:// -> postgresql:// y garantiza sslmode=require."""
    normalized = url.strip()
    if normalized.startswith("postgres://"):
        normalized = "postgresql://" + normalized[len("postgres://"):]
    if "sslmode=" not in normalized:
        sep = "&" if "?" in normalized else "?"
        normalized = f"{normalized}{sep}sslmode=require"
    return normalized


def _translate_sql_to_pg(sql: str, has_params: bool = False) -> str:
    """
    Traduce placeholders '?' de SQLite a '%s' de psycopg2 respetando literales entre comillas simples,
    y adapta sintaxis básica DDL/DML para PostgreSQL.
    """
    sql_out = re.sub(
        r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
        "SERIAL PRIMARY KEY",
        sql,
        flags=re.IGNORECASE,
    )

    if re.search(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", sql_out, flags=re.IGNORECASE):
        sql_out = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", sql_out, flags=re.IGNORECASE)
        if "ON CONFLICT" not in sql_out.upper():
            sql_out = sql_out.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

    out = []
    in_single = False
    i = 0
    n = len(sql_out)
    while i < n:
        ch = sql_out[i]
        if ch == "'":
            out.append(ch)
            if in_single and i + 1 < n and sql_out[i + 1] == "'":
                out.append("'")
                i += 2
                continue
            in_single = not in_single
        elif ch == "?" and not in_single:
            out.append("%s")
        elif ch == "%" and has_params:
            out.append("%%")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


class PostgresCursorWrapper:
    """Envoltorio de cursor psycopg2 compatible con la interfaz de sqlite3.Cursor."""

    def __init__(self, pg_cursor):
        self._cursor = pg_cursor

    def execute(self, sql: str, params=None):
        has_params = params is not None and len(params) > 0
        pg_sql = _translate_sql_to_pg(sql, has_params=has_params)
        if params is not None:
            self._cursor.execute(pg_sql, tuple(params))
        else:
            self._cursor.execute(pg_sql)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def close(self):
        try:
            self._cursor.close()
        except Exception:
            pass


class PostgresConnectionWrapper:
    """
    Envoltorio de conexión psycopg2 compatible con 'with get_connection() as conn:'
    y con conn.execute(...) tal como sqlite3.Connection.
    """

    def __init__(self, pg_conn):
        self._conn = pg_conn

    def cursor(self):
        return PostgresCursorWrapper(self._conn.cursor(cursor_factory=psycopg2.extras.DictCursor))

    def execute(self, sql: str, params=None):
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self.close()
        return False


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


def _cutoff_90_days_iso() -> str:
    """Retorna la fecha límite de hace 90 días en formato ISO (compatible con SQLite y PostgreSQL)."""
    return (datetime.now() - timedelta(days=90)).isoformat()


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


def _open_and_configure_sqlite(db_path: str) -> sqlite3.Connection:
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
    Retorna una conexión a la base de datos activa:
    - Supabase PostgreSQL si DATABASE_URL está configurada.
    - SQLite local con auto-cierre y auto-recuperación en caso contrario.
    """
    if is_postgres_active():
        pg_url = _normalize_pg_url(get_database_url())
        pg_conn = psycopg2.connect(pg_url, connect_timeout=10)
        return PostgresConnectionWrapper(pg_conn)

    _cleanup_stale_wal_files(DB_FILE)
    try:
        return _open_and_configure_sqlite(DB_FILE)
    except sqlite3.DatabaseError:
        _cleanup_stale_wal_files(DB_FILE)
        try:
            if os.path.exists(DB_FILE):
                os.remove(DB_FILE)
        except OSError:
            pass
        return _open_and_configure_sqlite(DB_FILE)


def _migrate_sqlite_to_postgres_if_empty(pg_conn):
    """
    Si la base de datos PostgreSQL (Supabase) recién se inicializa y aún no tiene turnos,
    migra automáticamente los datos existentes del archivo local ipress_turnos.db (una sola vez).
    """
    if not os.path.exists(DB_FILE):
        return

    cur_pg = pg_conn.cursor()
    pg_shifts_count = cur_pg.execute("SELECT COUNT(*) FROM shifts").fetchone()[0]
    if pg_shifts_count > 0:
        return

    try:
        sq_conn = _open_and_configure_sqlite(DB_FILE)
    except Exception:
        return

    try:
        sq_shifts_count = sq_conn.execute("SELECT COUNT(*) FROM shifts").fetchone()[0]
        if sq_shifts_count == 0:
            return

        # Mapear doctor_id de SQLite -> CMP -> doctor_id de PostgreSQL
        pg_docs_by_cmp = {
            r["cmp"]: r["id"]
            for r in cur_pg.execute("SELECT id, cmp FROM doctors").fetchall()
        }
        sq_id_to_pg_id = {}
        for r in sq_conn.execute("SELECT id, cmp, pin_hash FROM doctors").fetchall():
            pg_id = pg_docs_by_cmp.get(r["cmp"])
            if pg_id:
                sq_id_to_pg_id[r["id"]] = pg_id

        # 1. Migrar turnos (shifts)
        migrated_shifts = 0
        for s in sq_conn.execute(
            "SELECT year, month, day, shift_period, service, doctor_id, is_proposal, created_at, updated_at FROM shifts"
        ).fetchall():
            pg_doc_id = sq_id_to_pg_id.get(s["doctor_id"])
            if pg_doc_id:
                cur_pg.execute("""
                    INSERT INTO shifts (year, month, day, shift_period, service, doctor_id, is_proposal, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(year, month, day, doctor_id) DO NOTHING
                """, (
                    s["year"], s["month"], s["day"], s["shift_period"], s["service"],
                    pg_doc_id, s["is_proposal"], s["created_at"], s["updated_at"]
                ))
                migrated_shifts += 1

        # 2. Migrar excepciones diarias aprobadas
        for e in sq_conn.execute(
            "SELECT year, month, day, approved_by, approved_at, notes FROM approved_day_exceptions"
        ).fetchall():
            cur_pg.execute("""
                INSERT INTO approved_day_exceptions (year, month, day, approved_by, approved_at, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(year, month, day) DO NOTHING
            """, (e["year"], e["month"], e["day"], e["approved_by"], e["approved_at"], e["notes"]))

        # 3. Migrar estado de aprobación mensual
        for st_r in sq_conn.execute(
            "SELECT year, month, is_approved, approved_at, approved_by, approval_hash FROM schedule_status"
        ).fetchall():
            cur_pg.execute("""
                INSERT INTO schedule_status (year, month, is_approved, approved_at, approved_by, approval_hash)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(year, month) DO NOTHING
            """, (
                st_r["year"], st_r["month"], st_r["is_approved"],
                st_r["approved_at"], st_r["approved_by"], st_r["approval_hash"]
            ))

        # 4. Migrar bloqueos por citas si existen
        try:
            for lk in sq_conn.execute(
                "SELECT year, month, day, shift_period, doctor_id, doctor_cmp, appointments_count, sample_tickets, synced_at FROM shift_appointment_locks"
            ).fetchall():
                pg_doc_id = sq_id_to_pg_id.get(lk["doctor_id"])
                if pg_doc_id:
                    cur_pg.execute("""
                        INSERT INTO shift_appointment_locks
                            (year, month, day, shift_period, doctor_id, doctor_cmp, appointments_count, sample_tickets, synced_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(year, month, day, shift_period, doctor_id) DO NOTHING
                    """, (
                        lk["year"], lk["month"], lk["day"], lk["shift_period"],
                        pg_doc_id, lk["doctor_cmp"], lk["appointments_count"],
                        lk["sample_tickets"], lk["synced_at"]
                    ))
        except Exception:
            pass

        # 5. Registrar auditoría de migración
        cur_pg.execute("""
            INSERT INTO audit_logs (doctor_id, doctor_name, action, details, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (
            0,
            "Sistema IPRESS",
            "Migración a Nube Supabase",
            f"Migración inicial automática completada: {migrated_shifts} turno(s) transferidos a Supabase PostgreSQL.",
            datetime.now().isoformat(),
        ))
        pg_conn.commit()
    except Exception:
        pg_conn.rollback()
    finally:
        sq_conn.close()


def _init_db_tables_and_users():
    """Crea tablas y asegura la existencia de los usuarios base sin sobrescribir claves personalizadas."""
    use_pg = is_postgres_active()

    with get_connection() as conn:
        cursor = conn.cursor()

        # Verificar integridad rápida solo en SQLite
        if not use_pg:
            chk = cursor.execute("PRAGMA quick_check").fetchone()
            if not chk or chk[0] != "ok":
                raise sqlite3.DatabaseError("PRAGMA quick_check falló")

        # Tabla de Médicos y Usuarios del Sistema
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS doctors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                condition TEXT NOT NULL,
                cmp TEXT UNIQUE NOT NULL,
                username TEXT UNIQUE,
                pin_hash TEXT NOT NULL,
                role TEXT DEFAULT 'doctor',
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Migración segura en SQLite si la columna username no existe
        if not use_pg:
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
                shift_period TEXT NOT NULL,
                service TEXT NOT NULL,
                doctor_id INTEGER NOT NULL REFERENCES doctors(id),
                is_proposal INTEGER DEFAULT 1,
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
                shift_period TEXT NOT NULL,
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
            cursor.execute(
                "SELECT id, condition, name, username, pin_hash, role FROM doctors WHERE cmp = ? OR username = ?",
                (cmp_num, uname),
            )
            row = cursor.fetchone()
            if not row:
                cursor.execute("""
                    INSERT INTO doctors (name, condition, cmp, username, pin_hash, role)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (name, cond, cmp_num, uname, hash_pin(pwd), role))
            else:
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
        cursor.execute("DELETE FROM audit_logs WHERE timestamp < ?", (_cutoff_90_days_iso(),))
        conn.commit()

        # Si es PostgreSQL y está vacío, migrar turnos existentes desde SQLite local automáticamente
        if use_pg:
            _migrate_sqlite_to_postgres_if_empty(conn)


def init_db(force: bool = False):
    """Inicializa las tablas y datos base (evitando DDL repetitivo en cada rerun remoto)."""
    global _INITIALIZED_BACKEND_KEY
    current_key = f"pg:{get_database_url()}" if is_postgres_active() else f"sqlite:{DB_FILE}"

    if not force and _INITIALIZED_BACKEND_KEY == current_key:
        if is_postgres_active() or os.path.exists(DB_FILE):
            return

    try:
        _init_db_tables_and_users()
        _INITIALIZED_BACKEND_KEY = current_key
    except sqlite3.DatabaseError:
        _cleanup_stale_wal_files(DB_FILE)
        try:
            if os.path.exists(DB_FILE):
                os.remove(DB_FILE)
        except OSError:
            pass
        _init_db_tables_and_users()
        _INITIALIZED_BACKEND_KEY = current_key


def prune_audit_logs(conn=None):
    """Elimina del log de auditoría los registros de más de 3 meses (90 días de retención)."""
    sql = "DELETE FROM audit_logs WHERE timestamp < ?"
    cutoff = _cutoff_90_days_iso()
    if conn is not None:
        conn.execute(sql, (cutoff,))
    else:
        with get_connection() as c:
            c.execute(sql, (cutoff,))
            c.commit()


def get_audit_logs(limit: int = 500, filter_user: str = None, filter_action: str = None):
    """Retorna los registros de auditoría de los últimos 3 meses con filtros opcionales."""
    prune_audit_logs()
    query = "SELECT * FROM audit_logs WHERE timestamp >= ?"
    params = [_cutoff_90_days_iso()]
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
    init_db(force=True)
    info = get_db_backend_info()
    print(f"Base de datos inicializada correctamente ({info['label']}).")
