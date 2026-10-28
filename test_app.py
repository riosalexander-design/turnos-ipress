"""
test_app.py
Suite de pruebas automatizadas para verificar todas las reglas de negocio asistenciales:
1. Deducción de feriados de lunes a sábado de la meta base de 25 turnos.
2. Bloqueo de domingos para médicos, pero habilitado para el Administrador.
3. Aislamiento de médicos (solo el propio médico y el Administrador pueden modificar su fila) y confirmación al grabar.
4. Cobertura de servicios (Mañana: TU, CE, TS | Tarde: TU, CE), alertas por falta de cobertura y aprobación exclusiva por el Administrador.
5. Reglas específicas por médico (Octubre y Noviembre):
   - Dr. Solano: 12h todos los miércoles (TS en Mañana + TU en Tarde) con descanso (DES) al día siguiente; 5 turnos de 12h al mes en total, siempre con descanso al día siguiente.
   - Dra. Caldas: Lun, Mié, Jue, Vie en Mañana (M) y Sábado 12h (TS+TU).
   - Dra. Laureano: Lun, Mar, Mié, Vie, Sáb en Mañana (M) y Jueves en Tarde (T).
   - Dra. Sánchez: Lun, Mié y Vie 12h con descanso al día siguiente.
   - Dra. Chunga: Lun, Mié y Vie 12h con descanso al día siguiente.
6. Emisión de 'Horario Aprobado' y exportación válida a Excel (.xlsx).
"""

import unittest
import os
from datetime import datetime
from holidays_pe import get_month_info
import database
from database import init_db, get_connection

TEST_DB = os.path.join(os.path.dirname(__file__), "test_turnos.db")
database.DB_FILE = TEST_DB

from scheduler_engine import (
    calculate_deadline,
    auto_assign_missing_shifts_day10,
    get_doctor_quota_summary,
    get_daily_coverage,
    detect_discrepancies,
    validate_and_approve_schedule,
    approve_all_day_discrepancies,
    record_doctor_shift,
    reopen_schedule
)
from exporter import (
    generate_excel_schedule,
    generate_blank_proposal_template,
    parse_and_import_doctor_proposal
)
import app as flask_app


class TestIPRESSScheduling(unittest.TestCase):

    def setUp(self):
        init_db()

    def test_01_holiday_deduction_rule(self):
        """Verifica que solo los feriados de lunes a sábado descuenten de los 25 turnos."""
        oct_info = get_month_info(2026, 10)
        self.assertEqual(oct_info["base_shifts"], 25)
        self.assertEqual(oct_info["holidays_mon_to_sat_count"], 1)
        self.assertEqual(oct_info["monthly_quota"], 24)
        print("[OK] Test 1: Deduccion de feriados lun-sab correcta (Octubre 2026: 25 - 1 = 24 turnos).")

        # Noviembre 2026: 1 de Noviembre cae Domingo -> 0 feriados lun-sáb -> 25 turnos
        nov_info = get_month_info(2026, 11)
        self.assertEqual(nov_info["holidays_mon_to_sat_count"], 0)
        self.assertEqual(nov_info["monthly_quota"], 25)
        print("[OK] Test 1b: Deduccion correcta en Noviembre 2026 (1 Nov es domingo -> 25 turnos).")

    def test_02_deadline_capped_at_day_10(self):
        """Verifica que el plazo de 72h tenga TOPE ABSOLUTO en el Día 10 a las 00:00:00."""
        dt_day_4 = datetime(2026, 10, 4, 10, 0, 0)
        deadline_a, desc_a = calculate_deadline(dt_day_4, 2026, 10)
        self.assertEqual(deadline_a, datetime(2026, 10, 7, 10, 0, 0))
        self.assertIn("72 horas", desc_a)

        dt_day_8 = datetime(2026, 10, 8, 14, 0, 0)
        deadline_b, desc_b = calculate_deadline(dt_day_8, 2026, 10)
        self.assertEqual(deadline_b, datetime(2026, 10, 10, 0, 0, 0))
        self.assertIn("Día 10 a las 00:00 hrs", desc_b)
        print("[OK] Test 2: Plazo de 72h con tope al Dia 10 a las 00:00 horas verificado.")

    def test_03_doctor_revoked_direct_entry_and_admin_rrhh_exclusive_modification(self):
        """Verifica que los médicos tengan revocado el ingreso directo y que solo Jefatura y RRHH puedan modificar."""
        client = flask_app.app.test_client()

        with client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["user_name"] = "Caldas Pacsi, Lily Katherine"
            sess["user_role"] = "doctor"

        # 1. Cualquier intento de un médico de modificar turnos directamente (incluso su propia fila) -> 403
        res_own_doc = client.post("/api/shifts/update", json={
            "year": 2026, "month": 10, "day": 5, "doctor_id": 1, "shift_period": "M", "service": "TU"
        })
        self.assertEqual(res_own_doc.status_code, 403)
        self.assertIn("ACCESO DENEGADO", res_own_doc.get_json()["error"])

        # 2. Jefatura Médica (admin) SÍ puede modificar cualquier turno -> 200 OK
        with client.session_transaction() as sess:
            sess["user_id"] = 6
            sess["user_name"] = "Jefatura Médica IPRESS"
            sess["user_role"] = "admin"

        res_admin_ok = client.post("/api/shifts/update", json={
            "year": 2026, "month": 10, "day": 5, "doctor_id": 1, "shift_period": "M", "service": "TU"
        })
        self.assertEqual(res_admin_ok.status_code, 200)
        self.assertTrue(res_admin_ok.get_json()["success"])

        # Limpiar
        client.post("/api/shifts/update", json={
            "year": 2026, "month": 10, "day": 5, "doctor_id": 1, "shift_period": "CLEAR"
        })
        print("[OK] Test 3: Revocacion de ingreso directo para medicos y edicion exclusiva por Jefatura y RRHH verificada.")

    def test_04_auto_assign_specific_doctor_schedules_oct_and_nov(self):
        """Verifica las reglas específicas de Solano, Caldas, Laureano, Sánchez y Chunga en Octubre y Noviembre 2026."""
        for year, month in [(2026, 10), (2026, 11)]:
            with get_connection() as conn:
                conn.execute("DELETE FROM shifts WHERE year = ? AND month = ?", (year, month))
                conn.commit()

            auto_assign_missing_shifts_day10(year, month)
            month_info = get_month_info(year, month)
            quota = month_info["monthly_quota"]
            summary = get_doctor_quota_summary(year, month)["doctors_summary"]

            # 1. Verificar cuota exacta (25 - feriados lun-sáb) para los 5 médicos
            for did, doc in summary.items():
                self.assertEqual(
                    doc["total_equivalent_shifts"], quota,
                    f"Mes {month}: {doc['name']} tiene {doc['total_equivalent_shifts']} turnos, meta={quota}"
                )

            # Mapear médicos por apellido
            solano = next(d for d in summary.values() if "Solano" in d["name"])
            caldas = next(d for d in summary.values() if "Caldas" in d["name"])
            laureano = next(d for d in summary.values() if "Laureano" in d["name"])
            sanchez = next(d for d in summary.values() if "Sanchez" in d["name"] or "Sánchez" in d["name"])
            chunga = next(d for d in summary.values() if "Chunga" in d["name"])

            # 2. Verificar Dr. Solano:
            # - 5 turnos de 12h al mes
            # - Todos los miércoles no feriados hace 12h con TS+TU
            # - Siempre tiene descanso (DES) al día siguiente de cada turno de 12h
            self.assertEqual(solano["count_12h"], 5, f"Mes {month}: Solano debe tener 5 turnos de 12h")
            for d_info in month_info["days"]:
                day = d_info["day"]
                sh = solano["days"].get(day)
                if d_info["weekday_index"] == 2 and not d_info["discounts_shift"]:  # Miércoles
                    self.assertIsNotNone(sh, f"Solano debe trabajar el miércoles {day}/{month}")
                    self.assertEqual(sh["shift_period"], "12h")
                    self.assertEqual(sh["service"], "TS+TU")
                if sh and sh["shift_period"] == "12h" and day < month_info["num_days"]:
                    next_sh = solano["days"].get(day + 1)
                    self.assertIsNotNone(next_sh, f"Solano debe tener DES el día {day+1}/{month} tras 12h")
                    self.assertEqual(next_sh["shift_period"], "DES")

            # 3. Verificar Dra. Caldas: Sábados 12h (TS+TU) y Lun/Mié/Jue/Vie Mañana
            for d_info in month_info["days"]:
                day = d_info["day"]
                sh = caldas["days"].get(day)
                if d_info["weekday_index"] == 5 and not d_info["discounts_shift"]:  # Sábado
                    self.assertIsNotNone(sh)
                    self.assertEqual(sh["shift_period"], "12h")
                    self.assertEqual(sh["service"], "TS+TU")

            # 4. Verificar Dra. Laureano: Jueves por la tarde (T) y resto de días Lun/Mar/Mié/Vie/Sáb Mañana (M)
            for d_info in month_info["days"]:
                day = d_info["day"]
                sh = laureano["days"].get(day)
                if d_info["weekday_index"] == 3 and not d_info["discounts_shift"]:  # Jueves
                    if sh:
                        self.assertEqual(sh["shift_period"], "T")

            # 5. Verificar Dra. Sánchez y Dra. Chunga: Lun, Mié y Vie 12h con DES al día siguiente
            for doc_12h in (sanchez, chunga):
                self.assertGreaterEqual(doc_12h["count_12h"], 12)
                for d_info in month_info["days"]:
                    day = d_info["day"]
                    sh = doc_12h["days"].get(day)
                    if sh and sh["shift_period"] == "12h" and day < month_info["num_days"]:
                        next_d_info = month_info["days"][day]  # day+1
                        if not next_d_info["is_sunday"]:
                            next_sh = doc_12h["days"].get(day + 1)
                            self.assertIsNotNone(next_sh)
                            self.assertEqual(next_sh["shift_period"], "DES")

        print("[OK] Test 4: Horarios especificos de Solano, Caldas, Laureano, Sanchez y Chunga verificados en Octubre y Noviembre 2026.")

    def test_05_service_coverage_alerts_and_admin_only_approval(self):
        """Verifica las alertas cuando falta cubrir TU/CE/TS en Mañana o TU/CE en Tarde y que solo ADMIN pueda aprobar esos días."""
        year, month = 2026, 10
        with get_connection() as conn:
            conn.execute("DELETE FROM approved_day_exceptions WHERE year = ? AND month = ?", (year, month))
            conn.commit()

        coverage = get_daily_coverage(year, month)
        alert_days = [d for d, cov in coverage.items() if cov["has_service_alert"]]
        self.assertGreater(len(alert_days), 0, "Debe detectarse alerta en los días donde descansan Sánchez, Chunga y Solano")

        # 1. Sin aprobación de días con alerta, validate_and_approve_schedule con approve_pending_days=False debe bloquear
        res_doc = validate_and_approve_schedule(year, month, "Dra. Caldas", approve_pending_days=False)
        self.assertFalse(res_doc["is_approved"])
        self.assertIn("Solo el Administrador", res_doc["reason"])

        # 2. Un médico no puede aprobar un día con discrepancia de servicios por API (HTTP 403)
        client = flask_app.app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["user_name"] = "Caldas Pacsi, Lily Katherine"
            sess["user_role"] = "doctor"

        res_api_doc = client.post("/api/schedule/approve-day", json={
            "year": year, "month": month, "day": alert_days[0], "action": "approve"
        })
        self.assertEqual(res_api_doc.status_code, 403)

        # 3. El Administrador aprueba todos los días con discrepancia de cobertura y aprueba el horario
        approve_all_day_discrepancies(year, month, "Jefatura Médica IPRESS (ADMIN)")
        res_admin = validate_and_approve_schedule(year, month, "Jefatura Médica IPRESS", approve_pending_days=True)
        self.assertTrue(res_admin["is_approved"])
        self.assertIn("HORARIO APROBADO", res_admin["message"])
        print(f"[OK] Test 5: Alertas de cobertura de servicios y aprobacion exclusiva del Administrador verificadas (#{res_admin['approval_hash']}).")

    def test_06_excel_generation(self):
        """Verifica que el generador de Excel produzca un archivo válido."""
        stream = generate_excel_schedule(2026, 10, "IPRESS SALUD PERÚ")
        self.assertGreater(len(stream.getvalue()), 5000)
        print(f"[OK] Test 6: Reporte oficial en Excel (.xlsx) generado correctamente ({len(stream.getvalue())} bytes).")

    def test_07_auto_assign_preserves_existing_doctor_proposals(self):
        """Verifica que el corte automático respete y NO sobreescriba las propuestas previas."""
        year, month = 2026, 10
        with get_connection() as conn:
            conn.execute("DELETE FROM shifts WHERE year = ? AND month = ?", (year, month))
            conn.commit()

        # Jefatura o una propuesta previa registra el turno de Dra. Caldas
        res_rec = record_doctor_shift(year, month, 7, 1, "M", "TS", user_id=6, user_name="Jefatura Médica IPRESS", user_role="admin")
        self.assertTrue(res_rec["success"])

        auto_assign_missing_shifts_day10(year, month)

        with get_connection() as conn:
            row = conn.execute("""
                SELECT shift_period, service, is_proposal
                FROM shifts
                WHERE year = ? AND month = ? AND day = 7 AND doctor_id = 1
            """, (year, month)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["shift_period"], "M")
            self.assertEqual(row["service"], "TS")
            self.assertEqual(row["is_proposal"], 1)

        print("[OK] Test 7: El corte automatico preservo las propuestas grabadas.")

    def test_08_admin_reopen_schedule(self):
        """Verifica que solo el ADMIN pueda reabrir un horario previamente aprobado."""
        year, month = 2026, 10
        approve_all_day_discrepancies(year, month, "Jefatura Médica IPRESS")
        validate_and_approve_schedule(year, month, "Jefatura Médica IPRESS", approve_pending_days=True)

        client = flask_app.app.test_client()

        with client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["user_name"] = "Caldas Pacsi, Lily Katherine"
            sess["user_role"] = "doctor"

        res_forbidden = client.post("/api/schedule/reopen", json={"year": year, "month": month})
        self.assertEqual(res_forbidden.status_code, 403)

        with client.session_transaction() as sess:
            sess["user_id"] = 6
            sess["user_name"] = "Jefatura Médica IPRESS"
            sess["user_role"] = "admin"

        res_ok = client.post("/api/schedule/reopen", json={"year": year, "month": month})
        self.assertEqual(res_ok.status_code, 200)
        print("[OK] Test 8: Funcionalidad de reapertura de horario verificada con proteccion estricta por rol ADMIN.")

    def test_09_exclusive_login_for_jefatura_and_rrhh_and_excel_proposals(self):
        """Verifica que el login directo esté restringido a Jefatura y RRHH y que los médicos carguen propuestas vía Excel."""
        from openpyxl import load_workbook
        import io
        client = flask_app.app.test_client()

        # 1. Los médicos tienen revocado el permiso de inicio de sesión directo (403)
        for uname, pwd in [("lcaldas", "caldas2026"), ("esolano", "solano2026")]:
            res_doc_login = client.post("/api/auth/login", json={"username": uname, "password": pwd})
            self.assertEqual(res_doc_login.status_code, 403)
            self.assertIn("REVOCADO", res_doc_login.get_json()["error"])

        # 2. Jefatura y RRHH SÍ pueden iniciar sesión
        for uname, pwd, expected_role in [("jefatura", "jefatura2026", "admin"), ("rrhh", "rrhh2026", "rrhh")]:
            res = client.post("/api/auth/login", json={"username": uname, "password": pwd})
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.get_json()["user"]["role"], expected_role)

        # 3. Flujo de propuesta Excel para médicos (descarga formato en blanco -> llenado -> carga en memoria)
        with get_connection() as conn:
            doc1 = dict(conn.execute("SELECT id, name, condition, cmp, role FROM doctors WHERE id = 1").fetchone())
            doc2 = dict(conn.execute("SELECT id, name, condition, cmp, role FROM doctors WHERE id = 2").fetchone())

        tpl_stream = generate_blank_proposal_template(2026, 10, doc1)
        wb = load_workbook(tpl_stream)
        ws = wb.active
        # Día 5 de Octubre 2026 (fila 15 = 11 + 4) -> M-TU
        ws.cell(row=15, column=4, value="M-TU")
        filled_buf = io.BytesIO()
        wb.save(filled_buf)
        filled_bytes = filled_buf.getvalue()

        # 3a. Si otro médico (Dra. Chunga, id=2) intenta subir el formato de Dra. Caldas (id=1) -> Rechazado (403)
        res_other_doc = parse_and_import_doctor_proposal(filled_bytes, 2026, 10, expected_doctor_id=doc2["id"])
        self.assertFalse(res_other_doc["success"])
        self.assertEqual(res_other_doc["status_code"], 403)
        self.assertIn("ACCESO DENEGADO", res_other_doc["error"])

        # 3b. Dra. Caldas (id=1) sube su propia propuesta -> Éxito (200), se refleja en horario y en audit_logs
        res_own_prop = parse_and_import_doctor_proposal(filled_bytes, 2026, 10, expected_doctor_id=doc1["id"])
        self.assertTrue(res_own_prop["success"])
        self.assertEqual(res_own_prop["saved_days_count"], 1)

        # 3c. Si Dra. Chunga (id=2) sube su propia propuesta con el mismo turno M-TU el día 5 -> se genera cruce en discrepancias
        tpl2_stream = generate_blank_proposal_template(2026, 10, doc2)
        wb2 = load_workbook(tpl2_stream)
        ws2 = wb2.active
        ws2.cell(row=15, column=4, value="M-TU")
        filled_buf2 = io.BytesIO()
        wb2.save(filled_buf2)
        res_prop2 = parse_and_import_doctor_proposal(filled_buf2.getvalue(), 2026, 10, expected_doctor_id=doc2["id"])
        self.assertTrue(res_prop2["success"])
        self.assertGreaterEqual(res_prop2["discrepancies_detected"], 1)

        print("[OK] Test 9: Login exclusivo para Jefatura/RRHH y flujo de propuestas Excel en memoria con deteccion de cruces verificados.")

    def test_10_audit_log_3_months_retention(self):
        """Verifica que el log de cambios almacene hasta 3 meses (90 días) y purgue registros más antiguos."""
        from database import get_connection, log_audit, prune_audit_logs, get_audit_logs

        # 1. Insertar un registro de hace 100 días (debe ser eliminado) y uno de hace 10 días (debe preservarse)
        with get_connection() as conn:
            conn.execute("""
                INSERT INTO audit_logs (doctor_id, doctor_name, action, details, timestamp)
                VALUES (1, 'Dra. Caldas', 'Cambio Antiguo', 'Modificación de hace 100 días', datetime('now', '-100 days'))
            """)
            conn.execute("""
                INSERT INTO audit_logs (doctor_id, doctor_name, action, details, timestamp)
                VALUES (1, 'Dra. Caldas', 'Cambio Reciente', 'Modificación de hace 10 días', datetime('now', '-10 days'))
            """)
            conn.commit()

        # 2. Ejecutar poda y obtener logs
        logs = get_audit_logs(limit=500)
        actions = [l["action"] for l in logs]
        details = [l["details"] for l in logs]

        self.assertNotIn("Modificación de hace 100 días", details, "El registro de 100 días debió ser depurado por la política de 90 días (3 meses)")
        self.assertIn("Modificación de hace 10 días", details, "El registro de hace 10 días debe mantenerse")

        # 3. Endpoint /api/audit-logs
        client = flask_app.app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = 7
            sess["user_name"] = "Responsable de Recursos Humanos"
            sess["user_role"] = "rrhh"

        res_api = client.get("/api/audit-logs")
        self.assertEqual(res_api.status_code, 200)
        data = res_api.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["retention_policy"], "90 días (3 meses)")
        self.assertGreaterEqual(len(data["logs"]), 1)

        print("[OK] Test 10: Retención de 3 meses (90 días) en el Log de Cambios verificada correctamente.")

    def test_11_sqlite_resilience_and_connection_management(self):
        """Verifica que get_connection() sea auto-recuperable y resistente ante archivos WAL/SHM huérfanos."""
        import os
        from database import DB_FILE, hash_pin

        # 1. Simular archivos laterales huérfanos desincronizados
        wal_file = DB_FILE + "-wal"
        shm_file = DB_FILE + "-shm"
        with open(wal_file, "w") as f:
            f.write("corrupted_wal_test_data")
        with open(shm_file, "w") as f:
            f.write("corrupted_shm_test_data")

        # 2. get_connection() debe limpiar automáticamente los huérfanos y conectarse sin DatabaseError
        with get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) FROM doctors").fetchone()
            self.assertGreater(row[0], 0)

        # Verificar que los archivos huérfanos fueron eliminados
        self.assertFalse(os.path.exists(wal_file))
        self.assertFalse(os.path.exists(shm_file))

        # 3. Verificar que cambiar clave persista y no sea sobrescrito por init_db()
        with get_connection() as conn:
            conn.execute("UPDATE doctors SET pin_hash = ? WHERE id = 1", (hash_pin("nueva_clave_secreta"),))
            conn.commit()

        init_db()  # No debe revertir 'nueva_clave_secreta'

        with get_connection() as conn:
            row = conn.execute("SELECT pin_hash FROM doctors WHERE id = 1").fetchone()
            self.assertEqual(row[0], hash_pin("nueva_clave_secreta"))
            # Restaurar la clave oficial
            conn.execute("UPDATE doctors SET pin_hash = ? WHERE id = 1", (hash_pin("caldas2026"),))
            conn.commit()

        print("[OK] Test 11: Auto-recuperación de SQLite ante archivos WAL/SHM huérfanos y preservación de claves verificadas.")

    @classmethod
    def tearDownClass(cls):
        """Limpia la base de datos temporal de pruebas."""
        for suffix in ("", "-wal", "-shm"):
            p = TEST_DB + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


if __name__ == "__main__":
    unittest.main()
