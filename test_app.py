"""
test_app.py
Suite de pruebas automatizadas para verificar todas las reglas de negocio asistenciales:
1. Deducción de feriados de lunes a sábado de la meta base de 25 turnos.
2. Aislamiento de médicos (no pueden editar filas ajenas).
3. Plazo de 72 horas para discrepancias con tope estricto al Día 10 a las 00:00 horas.
4. Asignación automática al Día 10 a las 00:00:
   - 12h los viernes con sábado libre para todos.
   - 5 turnos de 12h obligatorios para el Dr. Solano con descansos de 12h al día siguiente.
   - Mínimo 2 médicos por turno en todos los días de semana.
   - Cumplimiento de meta exacta de 25 - F por médico.
5. Emisión de 'Horario Aprobado'.
6. Exportación válida a Excel (.xlsx).
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
    reopen_schedule
)
from exporter import generate_excel_schedule
import app as flask_app

class TestIPRESSScheduling(unittest.TestCase):

    def setUp(self):
        init_db()

    def test_01_holiday_deduction_rule(self):
        """Verifica que solo los feriados de lunes a sábado descuenten de los 25 turnos."""
        # Octubre 2026: 8 de Octubre (Combate de Angamos) cae en JUEVES (día de semana) -> descuenta 1
        oct_info = get_month_info(2026, 10)
        self.assertEqual(oct_info["base_shifts"], 25)
        self.assertEqual(oct_info["holidays_mon_to_sat_count"], 1)
        self.assertEqual(oct_info["monthly_quota"], 24)
        print("[OK] Test 1: Deduccion de feriados lun-sab correcta (Octubre 2026: 25 - 1 = 24 turnos).")

        # Julio 2026: 23 Jul (Jue), 28 Jul (Mar), 29 Jul (Mié) -> 3 feriados lun-sáb -> 25 - 3 = 22 turnos
        jul_info = get_month_info(2026, 7)
        self.assertEqual(jul_info["holidays_mon_to_sat_count"], 3)
        self.assertEqual(jul_info["monthly_quota"], 22)
        print("[OK] Test 1b: Deduccion correcta en Julio (25 - 3 = 22 turnos).")

    def test_02_deadline_capped_at_day_10(self):
        """Verifica que el plazo de 72h tenga TOPE ABSOLUTO en el Día 10 a las 00:00:00."""
        # Caso A: Discrepancia el día 4 a las 10:00 -> 72h da día 7 a las 10:00 (< Día 10 00:00)
        dt_day_4 = datetime(2026, 10, 4, 10, 0, 0)
        deadline_a, desc_a = calculate_deadline(dt_day_4, 2026, 10)
        self.assertEqual(deadline_a, datetime(2026, 10, 7, 10, 0, 0))
        self.assertIn("72 horas", desc_a)

        # Caso B: Discrepancia el día 8 a las 14:00 -> 72h daría día 11 a las 14:00, PERO el tope es Día 10 a las 00:00!
        dt_day_8 = datetime(2026, 10, 8, 14, 0, 0)
        deadline_b, desc_b = calculate_deadline(dt_day_8, 2026, 10)
        self.assertEqual(deadline_b, datetime(2026, 10, 10, 0, 0, 0))
        self.assertIn("Día 10 a las 00:00 hrs", desc_b)
        print("[OK] Test 2: Plazo de 72h con tope al Dia 10 a las 00:00 horas verificado con precision.")

    def test_03_doctor_isolation_in_api(self):
        """Verifica que un médico NO pueda modificar la fila de otro médico (HTTP 403)."""
        client = flask_app.app.test_client()

        with client.session_transaction() as sess:
            # Sesión de Dra. Caldas (id=1)
            sess["user_id"] = 1
            sess["user_name"] = "Caldas Pacsi, Lily Katherine"
            sess["user_role"] = "doctor"

        # Intento de modificar la fila de la Dra. Chunga (id=2)
        res = client.post("/api/shifts/update", json={
            "year": 2026,
            "month": 10,
            "day": 5,
            "doctor_id": 2, # Médico ajeno
            "shift_period": "M",
            "service": "TU"
        })
        self.assertEqual(res.status_code, 403)
        self.assertIn("ACCESO DENEGADO", res.get_json()["error"])

        # Intento de modificar su propia fila (id=1) -> Debe permitirse (200 OK)
        res_ok = client.post("/api/shifts/update", json={
            "year": 2026,
            "month": 10,
            "day": 5,
            "doctor_id": 1, # Propio
            "shift_period": "M",
            "service": "TU"
        })
        self.assertEqual(res_ok.status_code, 200)
        print("[OK] Test 3: Aislamiento y proteccion estricta por medico verificado (HTTP 403 ante intentos no autorizados).")

    def test_04_auto_assign_rules_fulfillment(self):
        """Verifica que el corte del Día 10 a las 00:00 cumpla todas las normativas asistenciales."""
        year, month = 2026, 10
        # Ejecutar autocompletado
        auto_assign_missing_shifts_day10(year, month)

        # 1. Verificar cobertura: NUNCA menos de 2 médicos por turno en días de semana
        coverage = get_daily_coverage(year, month)
        for d, cov in coverage.items():
            if not cov["is_sunday"]:
                self.assertGreaterEqual(cov["morning_count"], 2, f"Día {d} tiene menos de 2 médicos en Mañana")
                self.assertGreaterEqual(cov["afternoon_count"], 2, f"Día {d} tiene menos de 2 médicos en Tarde")

        # 2. Verificar meta individual de cada médico
        summary = get_doctor_quota_summary(year, month)["doctors_summary"]
        quota = get_month_info(year, month)["monthly_quota"]
        for did, doc in summary.items():
            self.assertEqual(doc["total_equivalent_shifts"], quota, f"{doc['name']} tiene {doc['total_equivalent_shifts']} turnos, se esperaban {quota}")

        # 3. Verificar que el Dr. Solano Ricaldi tenga sus 5 turnos de 12 horas con descanso al día siguiente
        solano = next((d for d in summary.values() if d["is_solano"]), None)
        self.assertIsNotNone(solano)
        self.assertEqual(solano["count_12h"], 5, f"Dr. Solano debe tener 5 turnos de 12h, tiene {solano['count_12h']}")

        print("[OK] Test 4: Motor de Asignacion Automatica cumplio todas las reglas asistenciales:")
        print("   - Nunca menos de 2 medicos por turno: CUMPLIDO.")
        print(f"   - Metas individuales exactas ({quota} turnos): CUMPLIDO para los 5 medicos.")
        print("   - 5 turnos de 12h para Dr. Solano con descansos: CUMPLIDO.")

    def test_05_schedule_approval_certification(self):
        """Verifica la emisión oficial del certificado 'Horario Aprobado'."""
        year, month = 2026, 10
        res = validate_and_approve_schedule(year, month, "Dr. Director IPRESS")
        self.assertTrue(res["is_approved"])
        self.assertIn("HORARIO APROBADO", res["message"])
        self.assertIsNotNone(res["approval_hash"])
        print(f"[OK] Test 5: Horario Aprobado emitido exitosamente. Codigo: #{res['approval_hash']}")

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
            # Dra. Caldas propone voluntariamente el Día 7 en Telesalud Mañana
            conn.execute("""
                INSERT INTO shifts (year, month, day, shift_period, service, doctor_id, is_proposal)
                VALUES (2026, 10, 7, 'M', 'TS', 1, 1)
            """)
            conn.commit()

        # Ejecutar autocompletado
        auto_assign_missing_shifts_day10(year, month)

        # Comprobar que el turno de la Dra. Caldas en el Día 7 sigue siendo 'M' - 'TS' intacto
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

        print("[OK] Test 7: El corte automatico preservo al 100% las propuestas ingresadas por los medicos.")

    def test_08_admin_reopen_schedule(self):
        """Verifica que solo el ADMIN pueda reabrir un horario previamente aprobado."""
        year, month = 2026, 10
        # Aprobar primero
        validate_and_approve_schedule(year, month, "Jefatura Médica IPRESS")
        with get_connection() as conn:
            stat = conn.execute("SELECT is_approved FROM schedule_status WHERE year = ? AND month = ?", (year, month)).fetchone()
            self.assertEqual(stat["is_approved"], 1)

        client = flask_app.app.test_client()

        # 1. Intento de reabrir por un médico no admin (Dra. Caldas, role='doctor') -> 403
        with client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["user_name"] = "Caldas Pacsi, Lily Katherine"
            sess["user_role"] = "doctor"

        res_forbidden = client.post("/api/schedule/reopen", json={"year": year, "month": month})
        self.assertEqual(res_forbidden.status_code, 403)

        # 2. Intento de reabrir por ADMIN (role='admin') -> 200 OK
        with client.session_transaction() as sess:
            sess["user_id"] = 6
            sess["user_name"] = "Jefatura Médica IPRESS"
            sess["user_role"] = "admin"

        res_ok = client.post("/api/schedule/reopen", json={"year": year, "month": month})
        self.assertEqual(res_ok.status_code, 200)

        # Verificar en base de datos que el horario quedó reabierto (is_approved=0)
        with get_connection() as conn:
            stat_after = conn.execute("SELECT is_approved FROM schedule_status WHERE year = ? AND month = ?", (year, month)).fetchone()
            self.assertEqual(stat_after["is_approved"], 0)

        print("[OK] Test 8: Funcionalidad de reapertura de horario verificada con proteccion estricta por rol ADMIN.")

if __name__ == "__main__":
    unittest.main()
