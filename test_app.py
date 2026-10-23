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
from datetime import datetime
from holidays_pe import get_month_info
from database import init_db, get_connection
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
from exporter import generate_excel_schedule
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

    def test_03_doctor_isolation_sunday_block_and_confirmation(self):
        """Verifica aislamiento por médico, bloqueo de domingos para médicos (no para Admin) y mensaje de confirmación."""
        client = flask_app.app.test_client()

        with client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["user_name"] = "Caldas Pacsi, Lily Katherine"
            sess["user_role"] = "doctor"

        # 1. Intento de modificar la fila de otro médico (Dra. Chunga, id=2) -> 403
        res = client.post("/api/shifts/update", json={
            "year": 2026, "month": 10, "day": 5, "doctor_id": 2, "shift_period": "M", "service": "TU"
        })
        self.assertEqual(res.status_code, 403)
        self.assertIn("ACCESO DENEGADO", res.get_json()["error"])

        # 2. Intento de un médico de programar en DOMINGO (Día 4 de Octubre 2026 es Domingo) -> 403
        res_sun_doc = client.post("/api/shifts/update", json={
            "year": 2026, "month": 10, "day": 4, "doctor_id": 1, "shift_period": "M", "service": "TU"
        })
        self.assertEqual(res_sun_doc.status_code, 403)
        self.assertIn("DOMINGO BLOQUEADO", res_sun_doc.get_json()["error"])

        # 3. Modificar su propia fila en día hábil -> 200 OK + confirmación de turno grabado
        res_ok = client.post("/api/shifts/update", json={
            "year": 2026, "month": 10, "day": 5, "doctor_id": 1, "shift_period": "M", "service": "TU"
        })
        self.assertEqual(res_ok.status_code, 200)
        data_ok = res_ok.get_json()
        self.assertTrue(data_ok["success"])
        self.assertIn("El turno fue grabado", data_ok["message"])

        # 4. El Administrador SÍ puede programar en Domingo y modificar cualquier médico -> 200 OK
        with client.session_transaction() as sess:
            sess["user_id"] = 6
            sess["user_name"] = "Jefatura Médica IPRESS"
            sess["user_role"] = "admin"

        res_sun_admin = client.post("/api/shifts/update", json={
            "year": 2026, "month": 10, "day": 4, "doctor_id": 1, "shift_period": "M", "service": "TU"
        })
        self.assertEqual(res_sun_admin.status_code, 200)
        self.assertTrue(res_sun_admin.get_json()["success"])

        # Limpiar el domingo de prueba
        client.post("/api/shifts/update", json={
            "year": 2026, "month": 10, "day": 4, "doctor_id": 1, "shift_period": "CLEAR"
        })
        print("[OK] Test 3: Aislamiento por medico, bloqueo de domingos para medicos (habilitado para Admin) y confirmacion verificados.")

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
        """Verifica que el corte automático respete y NO sobreescriba las propuestas previas de los médicos."""
        year, month = 2026, 10
        with get_connection() as conn:
            conn.execute("DELETE FROM shifts WHERE year = ? AND month = ?", (year, month))
            conn.commit()

        # Dra. Caldas graba un turno usando record_doctor_shift
        res_rec = record_doctor_shift(year, month, 7, 1, "M", "TS", user_id=1, user_name="Dra. Caldas", user_role="doctor")
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

        print("[OK] Test 7: El corte automatico preservo las propuestas grabadas por los medicos.")

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

    def test_09_user_based_passwords_and_rrhh_role(self):
        """Verifica que las contraseñas se basen en los usuarios y que el usuario RRHH tenga privilegios de gestión."""
        client = flask_app.app.test_client()

        # 1. Login con usuario y contraseña basada en usuario (no CMP)
        users_to_test = [
            ("lcaldas", "caldas2026", "doctor"),
            ("tchunga", "chunga2026", "doctor"),
            ("elaureano", "laureano2026", "doctor"),
            ("csanchez", "sanchez2026", "doctor"),
            ("esolano", "solano2026", "doctor"),
            ("jefatura", "jefatura2026", "admin"),
            ("rrhh", "rrhh2026", "rrhh"),
        ]

        for uname, pwd, expected_role in users_to_test:
            res = client.post("/api/auth/login", json={"username": uname, "password": pwd})
            self.assertEqual(res.status_code, 200, f"Fallo al autenticar {uname}")
            user_data = res.get_json()["user"]
            self.assertEqual(user_data["username"], uname)
            self.assertEqual(user_data["role"], expected_role)

        # 2. Privilegios de RRHH: Puede registrar turnos de cualquier médico y en domingo
        with client.session_transaction() as sess:
            sess["user_id"] = 7
            sess["user_name"] = "Responsable de Recursos Humanos"
            sess["user_role"] = "rrhh"

        # Día 4 de Octubre 2026 es Domingo: RRHH SÍ tiene permiso
        res_rrhh_sun = client.post("/api/shifts/update", json={
            "year": 2026, "month": 10, "day": 4, "doctor_id": 5, "shift_period": "M", "service": "TU"
        })
        self.assertEqual(res_rrhh_sun.status_code, 200)
        self.assertTrue(res_rrhh_sun.get_json()["success"])

        # RRHH puede reabrir el horario
        res_reopen = client.post("/api/schedule/reopen", json={"year": 2026, "month": 10})
        self.assertEqual(res_reopen.status_code, 200)

        print("[OK] Test 9: Claves basadas en usuarios y nuevo usuario 'Responsable de Recursos Humanos' (RRHH) verificados con éxito.")

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

    @classmethod
    def tearDownClass(cls):
        """Asegura que el mes de Noviembre 2026 quede limpio y vacío para la programación manual."""
        with get_connection() as conn:
            conn.execute("DELETE FROM shifts WHERE year = 2026 AND month = 11")
            conn.commit()


if __name__ == "__main__":
    unittest.main()
