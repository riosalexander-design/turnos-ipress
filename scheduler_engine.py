"""
scheduler_engine.py
Motor de optimización asistencial, asignación automática de turnos,
detector de discrepancias, generador de alternativas y validador de 'Horario Aprobado'.
"""

import json
import hashlib
from datetime import datetime, timedelta, date
from holidays_pe import get_month_info
from database import get_connection, log_audit

SERVICES_MORNING = ["TU", "CE", "TS"]  # Tópico de Urgencias, Consultorio de Medicina, Telesalud/TBC/MCI
SERVICES_AFTERNOON = ["TU", "CE"]      # Tópico de Urgencias, Consultorio de Medicina

SERVICE_LABELS = {
    "TU": "Tópico de Urgencias (TU)",
    "CE": "Consultorio de Medicina (CE)",
    "TS": "Telesalud/TBC/MCI (TS)"
}

def calculate_deadline(current_dt: datetime, year: int, month: int) -> tuple[datetime, str]:
    """
    Calcula el plazo de 72 horas para subsanación con TOPE ESTRICTO al Día 10 a las 00:00:00.
    Retorna (datetime_limite, descripcion_legible).
    """
    day_10_deadline = datetime(year, month, 10, 0, 0, 0)
    proposed_deadline = current_dt + timedelta(hours=72)
    
    if proposed_deadline < day_10_deadline:
        final_deadline = proposed_deadline
        desc = f"{final_deadline.strftime('%d/%m/%Y a las %H:%M:%S')} (Plazo ordinario de 72 horas)"
    else:
        final_deadline = day_10_deadline
        desc = f"{final_deadline.strftime('%d/%m/%Y a las %H:%M:%S')} (Tope institucional máximo: Día 10 a las 00:00 hrs)"
    
    return final_deadline, desc

def get_doctor_quota_summary(year: int, month: int) -> dict:
    """
    Calcula el resumen de turnos acumulados por cada médico en el mes
    comparado con la meta mensual de 25 - feriados_lun_sab.
    """
    month_info = get_month_info(year, month)
    quota = month_info["monthly_quota"]

    with get_connection() as conn:
        doctors = conn.execute("""
            SELECT id, name, condition, cmp, role
            FROM doctors
            WHERE is_active = 1 AND role = 'doctor'
            ORDER BY condition DESC, name ASC
        """).fetchall()

        all_shifts = conn.execute("""
            SELECT doctor_id, day, shift_period, service, is_proposal
            FROM shifts
            WHERE year = ? AND month = ?
        """, (year, month)).fetchall()

        shifts_by_doc = {}
        for s in all_shifts:
            shifts_by_doc.setdefault(s["doctor_id"], []).append(s)

        summary = {}
        for doc in doctors:
            doc_id = doc["id"]
            shifts = shifts_by_doc.get(doc_id, [])

            count_6h = 0
            count_12h = 0
            count_des = 0
            days_assigned = {}

            for s in shifts:
                sp = s["shift_period"]
                day = s["day"]
                days_assigned[day] = {
                    "shift_period": sp,
                    "service": s["service"],
                    "is_proposal": s["is_proposal"]
                }
                if sp in ("M", "T"):
                    count_6h += 1
                elif sp == "12h":
                    count_12h += 1
                elif sp == "DES":
                    count_des += 1

            # Cada turno de 12h equivale a 2 turnos de 6h
            total_equivalent_shifts = count_6h + (count_12h * 2)

            is_solano = "Solano" in doc["name"]
            status_desc = "Pendiente"
            if total_equivalent_shifts == quota:
                status_desc = "Meta Completa"
            elif total_equivalent_shifts > quota:
                status_desc = f"Excedido (+{total_equivalent_shifts - quota})"
            else:
                status_desc = f"Faltan {quota - total_equivalent_shifts}"

            summary[doc_id] = {
                "id": doc_id,
                "name": doc["name"],
                "condition": doc["condition"],
                "cmp": doc["cmp"],
                "quota": quota,
                "count_6h": count_6h,
                "count_12h": count_12h,
                "count_des": count_des,
                "total_equivalent_shifts": total_equivalent_shifts,
                "difference": total_equivalent_shifts - quota,
                "status": status_desc,
                "is_solano": is_solano,
                "solano_12h_completed": count_12h >= 5 if is_solano else True,
                "days": days_assigned
            }

        return {
            "month_info": month_info,
            "doctors_summary": summary
        }

def get_daily_coverage(year: int, month: int) -> dict:
    """
    Calcula para cada día del mes:
    - Médicos en Mañana y servicios cubiertos (deben cubrirse: TU, CE, TS)
    - Médicos en Tarde y servicios cubiertos (deben cubrirse: TU, CE)
    - Alerta cuando no se cubran todos los servicios obligatorios (TU, CE, TS en M; TU, CE en T)
    - Estado de aprobación excepcional del día por el Administrador (approved_day_exceptions)
    """
    month_info = get_month_info(year, month)
    num_days = month_info["num_days"]

    coverage = {}

    with get_connection() as conn:
        exc_rows = conn.execute("""
            SELECT day, approved_by, approved_at, notes
            FROM approved_day_exceptions
            WHERE year = ? AND month = ?
        """, (year, month)).fetchall()
        approved_exceptions = {r["day"]: dict(r) for r in exc_rows}

        all_month_rows = conn.execute("""
            SELECT s.day, s.shift_period, s.service, s.doctor_id, d.name
            FROM shifts s
            JOIN doctors d ON s.doctor_id = d.id
            WHERE s.year = ? AND s.month = ?
        """, (year, month)).fetchall()

        rows_by_day = {}
        for r in all_month_rows:
            rows_by_day.setdefault(r["day"], []).append(r)

        for d in range(1, num_days + 1):
            day_info = month_info["days"][d - 1]
            is_sunday = day_info["is_sunday"]
            rows = rows_by_day.get(d, [])

            morning_docs = []
            afternoon_docs = []
            m_services_covered = []
            t_services_covered = []

            for r in rows:
                sp = r["shift_period"]
                srv = r["service"]
                doc_name = r["name"]

                if sp == "M":
                    morning_docs.append({"doctor_id": r["doctor_id"], "doctor_name": doc_name, "service": srv})
                    if srv in SERVICES_MORNING and srv not in m_services_covered:
                        m_services_covered.append(srv)
                elif sp == "T":
                    afternoon_docs.append({"doctor_id": r["doctor_id"], "doctor_name": doc_name, "service": srv})
                    if srv in SERVICES_AFTERNOON and srv not in t_services_covered:
                        t_services_covered.append(srv)
                elif sp == "12h":
                    parts = srv.split("+") if "+" in srv else [srv, srv]
                    m_srv = parts[0]
                    t_srv = parts[1] if len(parts) > 1 else parts[0]
                    morning_docs.append({"doctor_id": r["doctor_id"], "doctor_name": doc_name, "service": m_srv})
                    afternoon_docs.append({"doctor_id": r["doctor_id"], "doctor_name": doc_name, "service": t_srv})
                    if m_srv in SERVICES_MORNING and m_srv not in m_services_covered:
                        m_services_covered.append(m_srv)
                    if t_srv in SERVICES_AFTERNOON and t_srv not in t_services_covered:
                        t_services_covered.append(t_srv)

            m_count = len(morning_docs)
            t_count = len(afternoon_docs)

            # Servicios obligatorios de Lunes a Sábado:
            # Mañana: TU (Tópico de Urgencias), CE (Consultorio de Medicina), TS (Telesalud/TBC/MCI)
            # Tarde: TU (Tópico de Urgencias), CE (Consultorio de Medicina)
            if not is_sunday:
                missing_morning = [s for s in SERVICES_MORNING if s not in m_services_covered]
                missing_afternoon = [s for s in SERVICES_AFTERNOON if s not in t_services_covered]
            else:
                missing_morning = []
                missing_afternoon = []

            all_m_covered = (len(missing_morning) == 0)
            all_t_covered = (len(missing_afternoon) == 0)

            admin_exc = approved_exceptions.get(d)
            is_admin_approved_day = (admin_exc is not None)

            has_service_alert = (not is_sunday) and (not all_m_covered or not all_t_covered)

            alert_parts = []
            if missing_morning:
                alert_parts.append(f"Mañana sin cubrir: {', '.join(SERVICE_LABELS[s] for s in missing_morning)}")
            if missing_afternoon:
                alert_parts.append(f"Tarde sin cubrir: {', '.join(SERVICE_LABELS[s] for s in missing_afternoon)}")
            alert_message = " | ".join(alert_parts) if alert_parts else "Todos los servicios cubiertos"

            m_valid = True if is_sunday else (all_m_covered or is_admin_approved_day)
            t_valid = True if is_sunday else (all_t_covered or is_admin_approved_day)
            has_coverage_warning = (not m_valid) or (not t_valid)

            coverage[d] = {
                "day": d,
                "weekday_name": day_info["weekday_name"],
                "weekday_short": day_info["weekday_short"],
                "is_holiday": day_info["is_holiday"],
                "holiday_name": day_info["holiday_name"],
                "is_sunday": is_sunday,
                "morning_count": m_count,
                "morning_docs": morning_docs,
                "morning_services_covered": m_services_covered,
                "missing_morning_services": missing_morning,
                "all_morning_services_covered": all_m_covered,
                "morning_valid": m_valid,
                "afternoon_count": t_count,
                "afternoon_docs": afternoon_docs,
                "afternoon_services_covered": t_services_covered,
                "missing_afternoon_services": missing_afternoon,
                "all_afternoon_services_covered": all_t_covered,
                "afternoon_valid": t_valid,
                "has_service_alert": has_service_alert,
                "is_admin_approved_day": is_admin_approved_day,
                "admin_approval_info": admin_exc,
                "alert_message": alert_message,
                "has_warning": has_coverage_warning
            }

    return coverage

def approve_day_discrepancy(year: int, month: int, day: int, admin_name: str = "Jefatura Médica IPRESS", notes: str = "") -> dict:
    """
    Permite ÚNICAMENTE al Administrador aprobar un día en el que exista discrepancia o alerta
    por falta de cobertura de alguno de los servicios (TU, CE, TS en mañana o TU, CE en tarde).
    """
    now_iso = datetime.now().isoformat()
    if not notes:
        notes = "Día con discrepancia de cobertura aprobado excepcionalmente por el Administrador."
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO approved_day_exceptions (year, month, day, approved_by, approved_at, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(year, month, day) DO UPDATE SET
                approved_by = excluded.approved_by,
                approved_at = excluded.approved_at,
                notes = excluded.notes
        """, (year, month, day, admin_name, now_iso, notes))

        conn.execute("""
            UPDATE discrepancies
            SET status = 'approved_by_admin',
                resolved_at = ?,
                resolution_details = ?
            WHERE year = ? AND month = ? AND day = ? AND status = 'open'
        """, (now_iso, f"Aprobado por Administrador ({admin_name}): {notes}", year, month, day))

        conn.commit()
        log_audit(0, admin_name, "Aprobó Día con Discrepancia", f"Día {day:02d}/{month:02d}/{year}: {notes}", conn=conn)

    return {"success": True, "day": day, "approved_by": admin_name, "approved_at": now_iso}

def approve_all_day_discrepancies(year: int, month: int, admin_name: str = "Jefatura Médica IPRESS", notes: str = "") -> int:
    """
    Permite al Administrador aprobar todos los días del mes que presenten alertas de cobertura de servicios.
    Retorna la cantidad de días aprobados.
    """
    cov = get_daily_coverage(year, month)
    count = 0
    for d, info in cov.items():
        if info["has_service_alert"] and not info["is_admin_approved_day"]:
            approve_day_discrepancy(year, month, d, admin_name, notes or f"Aprobación administrativa de cobertura: {info['alert_message']}")
            count += 1
    return count

def revoke_day_approval(year: int, month: int, day: int, admin_name: str = "Jefatura Médica IPRESS") -> bool:
    """Revoca la aprobación excepcional de un día."""
    with get_connection() as conn:
        conn.execute("DELETE FROM approved_day_exceptions WHERE year = ? AND month = ? AND day = ?", (year, month, day))
        conn.commit()
        log_audit(0, admin_name, "Revocó Aprobación de Día", f"Día {day:02d}/{month:02d}/{year}", conn=conn)
    return True

def record_doctor_shift(
    year: int,
    month: int,
    day: int,
    doctor_id: int,
    shift_period: str,
    service: str,
    user_id: int,
    user_name: str,
    user_role: str
) -> dict:
    """
    Graba o modifica el turno de un médico cumpliendo todas las reglas de negocio:
    1. EXCLUSIVO para Jefatura Médica ('admin') y Recursos Humanos ('rrhh'). Los médicos tienen revocado
       el permiso de ingreso manual y únicamente pueden presentar propuestas mediante el formato Excel.
    2. Siempre que se registre un turno de 12 horas ('12h'), el día siguiente tendrá descanso ('DES')
       si es día laborable dentro del mes y aún no tiene turno asignado.
    3. Queda grabado y visible inmediatamente en el horario en pantalla y en el log de cambios.
    """
    if not user_id:
        return {"success": False, "error": "Debe iniciar sesión como Jefatura o RRHH para modificar turnos.", "status_code": 401}

    if user_role not in ("admin", "rrhh"):
        return {
            "success": False,
            "error": (
                "ACCESO DENEGADO: Se han revocado los permisos de ingreso directo para médicos. "
                "Únicamente Jefatura Médica y RRHH pueden aprobar o modificar turnos directamente. "
                "Los médicos deben descargar su formato en blanco y subir su archivo de propuesta."
            ),
            "status_code": 403
        }

    month_info = get_month_info(year, month)
    num_days = month_info["num_days"]
    if day < 1 or day > num_days:
        return {"success": False, "error": "Día inválido para el mes seleccionado.", "status_code": 400}

    with get_connection() as conn:
        doc = conn.execute("SELECT name FROM doctors WHERE id = ?", (doctor_id,)).fetchone()
        doc_name = doc["name"] if doc else f"Médico ID {doctor_id}"

        if shift_period in ("CLEAR", "", None):
            # Verificar si el turno que se borra era de 12h para limpiar el DES automático del día siguiente
            prev_shift = conn.execute(
                "SELECT shift_period FROM shifts WHERE year = ? AND month = ? AND day = ? AND doctor_id = ?",
                (year, month, day, doctor_id)
            ).fetchone()
            conn.execute(
                "DELETE FROM shifts WHERE year = ? AND month = ? AND day = ? AND doctor_id = ?",
                (year, month, day, doctor_id)
            )
            if prev_shift and prev_shift["shift_period"] == "12h" and day + 1 <= num_days:
                conn.execute(
                    "DELETE FROM shifts WHERE year = ? AND month = ? AND day = ? AND doctor_id = ? AND shift_period = 'DES'",
                    (year, month, day + 1, doctor_id)
                )
            log_audit(user_id, user_name, "Eliminó turno", f"Día {day:02d}/{month:02d}/{year} para {doc_name}", conn=conn)
            confirm_msg = f"El turno del día {day:02d}/{month:02d}/{year} ({doc_name}) fue liberado y grabado correctamente."
        else:
            conn.execute("""
                INSERT INTO shifts (year, month, day, shift_period, service, doctor_id, is_proposal, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(year, month, day, doctor_id) DO UPDATE SET
                    shift_period = excluded.shift_period,
                    service = excluded.service,
                    is_proposal = 1,
                    updated_at = CURRENT_TIMESTAMP
            """, (year, month, day, shift_period, service, doctor_id))

            # Regla obligatoria: Asignar descanso (DES) al día siguiente de un turno de 12 horas
            # SOLO si el día siguiente aún no tiene un turno asistencial programado (no sobrescribir turnos existentes)
            if shift_period == "12h" and (day + 1) <= num_days:
                next_day_info = month_info["days"][day]  # índice day corresponde al día day+1
                if not next_day_info["is_sunday"]:
                    conn.execute("""
                        INSERT OR IGNORE INTO shifts (year, month, day, shift_period, service, doctor_id, is_proposal, updated_at)
                        VALUES (?, ?, ?, 'DES', 'DES', ?, 1, CURRENT_TIMESTAMP)
                    """, (year, month, day + 1, doctor_id))

            log_audit(
                user_id,
                user_name,
                "Grabó turno",
                f"Día {day:02d}/{month:02d}/{year}: {shift_period} ({service}) para {doc_name}",
                conn=conn
            )
            confirm_msg = f"El turno fue grabado correctamente: {doc_name} — Día {day:02d}/{month:02d}/{year} ({shift_period} - {service})."

        conn.commit()

    sync_discrepancies_db(year, month)
    return {"success": True, "message": confirm_msg, "status_code": 200}

def detect_discrepancies(year: int, month: int, current_sim_time: datetime = None) -> list:
    """
    Detecta automáticamente las discrepancias en el rol propuesto:
    1. Colisión de servicios cuando dos médicos eligen el mismo servicio habiendo otro servicio descubierto.
    Genera sugerencias alternativas automáticas y calcula el plazo de 72h (máx día 10 a las 00:00).
    """
    if current_sim_time is None:
        current_sim_time = datetime.now()

    month_info = get_month_info(year, month)
    deadline_dt, deadline_str = calculate_deadline(current_sim_time, year, month)

    discrepancies = []

    with get_connection() as conn:
        all_month_shifts = conn.execute("""
            SELECT s.id, s.day, s.shift_period, s.service, s.doctor_id, d.name
            FROM shifts s
            JOIN doctors d ON s.doctor_id = d.id
            WHERE s.year = ? AND s.month = ?
        """, (year, month)).fetchall()

        shifts_by_day = {}
        for s in all_month_shifts:
            shifts_by_day.setdefault(s["day"], []).append(s)

        for d in range(1, month_info["num_days"] + 1):
            shifts_day = shifts_by_day.get(d, [])

            # Revisar servicios en Mañana
            m_services = {}
            for s in shifts_day:
                sp = s["shift_period"]
                srv = s["service"]
                if sp in ("M", "12h"):
                    actual_srv = srv.split("+")[0] if "+" in srv else srv
                    if actual_srv in ("TU", "CE", "TS"):
                        m_services.setdefault(actual_srv, []).append(s)

            occupied_m = list(m_services.keys())
            free_m_services = [s for s in SERVICES_MORNING if s not in occupied_m]

            for srv, docs_list in m_services.items():
                # Si hay colisión y aún quedan servicios libres en la mañana (o son turnos simples duplicados)
                if len(docs_list) > 1 and (len(free_m_services) > 0 or all(x["shift_period"] == "M" for x in docs_list)):
                    doc1 = docs_list[0]
                    doc2 = docs_list[1]
                    alt_service = free_m_services[0] if free_m_services else ("CE" if srv != "CE" else "TS")

                    suggestion = {
                        "action": "reassign_service",
                        "doctor_keep": {"id": doc1["doctor_id"], "name": doc1["name"], "service": srv},
                        "doctor_reassign": {"id": doc2["doctor_id"], "name": doc2["name"], "new_service": alt_service},
                        "day": d,
                        "shift_period": "M",
                        "text": f"Mantener a {doc1['name']} en Mañana-{srv} y reasignar a {doc2['name']} a Mañana-{alt_service}."
                    }

                    discrepancies.append({
                        "year": year,
                        "month": month,
                        "day": d,
                        "shift_period": "M",
                        "service": srv,
                        "doctor1_id": doc1["doctor_id"],
                        "doctor1_name": doc1["name"],
                        "doctor2_id": doc2["doctor_id"],
                        "doctor2_name": doc2["name"],
                        "conflict_type": "puesto_duplicado",
                        "description": f"Día {d}: Conflicto en Turno Mañana. Ambos médicos ({doc1['name']} y {doc2['name']}) registraron el mismo servicio ({srv}).",
                        "suggested_alternative": json.dumps(suggestion),
                        "suggested_text": suggestion["text"],
                        "deadline_datetime": deadline_dt.isoformat(),
                        "deadline_str": deadline_str
                    })

            # Revisar servicios en Tarde
            t_services = {}
            for s in shifts_day:
                sp = s["shift_period"]
                srv = s["service"]
                if sp in ("T", "12h"):
                    actual_srv = srv.split("+")[1] if "+" in srv else srv
                    if actual_srv in ("TU", "CE"):
                        t_services.setdefault(actual_srv, []).append(s)

            occupied_t = list(t_services.keys())
            free_t_services = [s for s in SERVICES_AFTERNOON if s not in occupied_t]

            for srv, docs_list in t_services.items():
                if len(docs_list) > 1 and (len(free_t_services) > 0 or all(x["shift_period"] == "T" for x in docs_list)):
                    doc1 = docs_list[0]
                    doc2 = docs_list[1]
                    alt_service = free_t_services[0] if free_t_services else ("CE" if srv == "TU" else "TU")

                    suggestion = {
                        "action": "reassign_service",
                        "doctor_keep": {"id": doc1["doctor_id"], "name": doc1["name"], "service": srv},
                        "doctor_reassign": {"id": doc2["doctor_id"], "name": doc2["name"], "new_service": alt_service},
                        "day": d,
                        "shift_period": "T",
                        "text": f"Mantener a {doc1['name']} en Tarde-{srv} y reasignar a {doc2['name']} a Tarde-{alt_service}."
                    }

                    discrepancies.append({
                        "year": year,
                        "month": month,
                        "day": d,
                        "shift_period": "T",
                        "service": srv,
                        "doctor1_id": doc1["doctor_id"],
                        "doctor1_name": doc1["name"],
                        "doctor2_id": doc2["doctor_id"],
                        "doctor2_name": doc2["name"],
                        "conflict_type": "puesto_duplicado",
                        "description": f"Día {d}: Conflicto en Turno Tarde. Ambos médicos ({doc1['name']} y {doc2['name']}) registraron el mismo servicio ({srv}).",
                        "suggested_alternative": json.dumps(suggestion),
                        "suggested_text": suggestion["text"],
                        "deadline_datetime": deadline_dt.isoformat(),
                        "deadline_str": deadline_str
                    })

    return discrepancies

def sync_discrepancies_db(year: int, month: int, current_sim_time: datetime = None):
    """
    Sincroniza las discrepancias detectadas con la tabla 'discrepancies' en la BD.
    """
    active_discs = detect_discrepancies(year, month, current_sim_time)
    with get_connection() as conn:
        active_keys = set()
        has_changes = False
        for d in active_discs:
            key = (d["year"], d["month"], d["day"], d["shift_period"], d["service"], d["doctor1_id"], d["doctor2_id"])
            active_keys.add(key)
            exists = conn.execute("""
                SELECT id, status FROM discrepancies
                WHERE year = ? AND month = ? AND day = ? AND shift_period = ? AND service = ?
                  AND doctor1_id = ? AND doctor2_id = ? AND status = 'open'
            """, key).fetchone()
            
            if not exists:
                conn.execute("""
                    INSERT INTO discrepancies (
                        year, month, day, shift_period, service,
                        doctor1_id, doctor2_id, conflict_type, description,
                        suggested_alternative, deadline_datetime, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
                """, (
                    d["year"], d["month"], d["day"], d["shift_period"], d["service"],
                    d["doctor1_id"], d["doctor2_id"], d["conflict_type"], d["description"],
                    d["suggested_alternative"], d["deadline_datetime"]
                ))
                has_changes = True
        
        open_db = conn.execute("""
            SELECT id, year, month, day, shift_period, service, doctor1_id, doctor2_id
            FROM discrepancies
            WHERE year = ? AND month = ? AND status = 'open'
        """, (year, month)).fetchall()

        for row in open_db:
            k = (row["year"], row["month"], row["day"], row["shift_period"], row["service"], row["doctor1_id"], row["doctor2_id"])
            if k not in active_keys:
                conn.execute("""
                    UPDATE discrepancies
                    SET status = 'resolved_by_correction',
                        resolved_at = CURRENT_TIMESTAMP,
                        resolution_details = 'Conflicto subsanado en la programación.'
                    WHERE id = ?
                """, (row["id"],))
                has_changes = True

        if has_changes:
            conn.commit()

def apply_discrepancy_alternative(discrepancy_id: int) -> bool:
    """
    Aplica la alternativa sugerida por el sistema para una discrepancia.
    """
    with get_connection() as conn:
        disc = conn.execute("SELECT * FROM discrepancies WHERE id = ?", (discrepancy_id,)).fetchone()
        if not disc or disc["status"] != "open":
            return False

        alt = json.loads(disc["suggested_alternative"])
        action = alt.get("action")

        if action == "reassign_service":
            reassign_info = alt["doctor_reassign"]
            target_doc_id = reassign_info["id"]
            new_service = reassign_info["new_service"]
            day = alt["day"]
            sp = alt["shift_period"]

            cur_shift = conn.execute("""
                SELECT id, shift_period, service FROM shifts
                WHERE year = ? AND month = ? AND day = ? AND doctor_id = ?
            """, (disc["year"], disc["month"], day, target_doc_id)).fetchone()

            if cur_shift:
                if cur_shift["shift_period"] == "12h":
                    parts = cur_shift["service"].split("+")
                    m_part = new_service if sp == "M" else parts[0]
                    t_part = new_service if sp == "T" else (parts[1] if len(parts) > 1 else "CE")
                    updated_service = f"{m_part}+{t_part}"
                else:
                    updated_service = new_service

                conn.execute("""
                    UPDATE shifts
                    SET service = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (updated_service, cur_shift["id"]))

            conn.execute("""
                UPDATE discrepancies
                SET status = 'accepted_suggestion',
                    resolved_at = CURRENT_TIMESTAMP,
                    resolution_details = ?
                WHERE id = ?
            """, (f"Aceptada alternativa automática: {alt['text']}", discrepancy_id))

            conn.commit()
            log_audit(target_doc_id, reassign_info["name"], "Aceptó sugerencia de discrepancia", alt["text"])
            return True

    return False

def _pick_needed_morning_service(grid_day: dict, preferred_order=("TU", "CE", "TS")) -> str:
    """Retorna el primer servicio de Mañana que aún no esté cubierto en ese día."""
    covered = set()
    for sh in grid_day.values():
        if sh["period"] == "M":
            covered.add(sh["service"])
        elif sh["period"] == "12h":
            covered.add(sh["service"].split("+")[0] if "+" in sh["service"] else sh["service"])
    for srv in preferred_order:
        if srv not in covered:
            return srv
    return preferred_order[0]

def _pick_needed_afternoon_service(grid_day: dict, preferred_order=("TU", "CE")) -> str:
    """Retorna el primer servicio de Tarde que aún no esté cubierto en ese día."""
    covered = set()
    for sh in grid_day.values():
        if sh["period"] == "T":
            covered.add(sh["service"])
        elif sh["period"] == "12h":
            parts = sh["service"].split("+")
            covered.add(parts[1] if len(parts) > 1 else parts[0])
    for srv in preferred_order:
        if srv not in covered:
            return srv
    return preferred_order[0]

def auto_assign_missing_shifts_day10(year: int, month: int):
    """
    Motor de Asignación Automática conforme a las reglas institucionales de la IPRESS:
    - Meta por médico: 25 turnos menos feriados de lunes a sábado.
    - Respeta al 100% los turnos ya grabados por los médicos (is_proposal = 1).
    - Reglas específicas por médico:
      1. Dr. Solano Ricaldi:
         - Todos los miércoles de cada mes: 12 horas ('12h') con 'TS+TU'
           (Telesalud/TBC/MCI en la mañana y Tópico de Urgencia por la tarde) y jueves 'DES'.
         - Total de 5 turnos de 12 horas al mes (incluidos los miércoles), siempre con descanso 'DES' al día siguiente.
         - Resto de turnos hasta cumplir la meta mensual en sus días laborables disponibles.
      2. Dra. Caldas Pacsi (Octubre y Noviembre / Estándar):
         - Lunes, Miércoles, Jueves y Viernes en las Mañanas ('M').
         - Sábados 12 horas ('12h') con 'TS+TU' (Telesalud/TBC/MCI en la mañana y Tópico de Urgencia en la tarde).
      3. Dra. Laureano Varillas:
         - Lunes, Martes, Miércoles, Viernes y Sábado por la Mañana ('M').
         - Jueves por la Tarde ('T').
      4. Dra. Sanchez Quintanilla (Octubre a Noviembre):
         - Lunes, Miércoles y Viernes 12 horas ('12h') con descanso ('DES') al día siguiente.
      5. Dra. Chunga Torres (Octubre a Noviembre):
         - Lunes, Miércoles y Viernes 12 horas ('12h') con descanso ('DES') al día siguiente.
    """
    month_info = get_month_info(year, month)
    quota = month_info["monthly_quota"]
    num_days = month_info["num_days"]
    days_meta = {d["day"]: d for d in month_info["days"]}

    with get_connection() as conn:
        doctors = conn.execute("""
            SELECT id, name, condition, cmp
            FROM doctors
            WHERE is_active = 1 AND role = 'doctor'
            ORDER BY condition DESC, name ASC
        """).fetchall()

        doc_dict = {d["id"]: dict(d) for d in doctors}
        solano_id = next((d["id"] for d in doctors if "Solano" in d["name"]), None)
        caldas_id = next((d["id"] for d in doctors if "Caldas" in d["name"]), None)
        laureano_id = next((d["id"] for d in doctors if "Laureano" in d["name"]), None)
        sanchez_id = next((d["id"] for d in doctors if "Sanchez" in d["name"]), None)
        chunga_id = next((d["id"] for d in doctors if "Chunga" in d["name"]), None)

        # 0. CARGAR TURNOS Y PROPUESTAS EXISTENTES PARA RESPETARLOS AL 100%
        existing_shifts = conn.execute("""
            SELECT day, shift_period, service, doctor_id, is_proposal
            FROM shifts
            WHERE year = ? AND month = ?
        """, (year, month)).fetchall()

        grid = {d: {} for d in range(1, num_days + 1)}
        doc_shifts_count = {doc_id: 0 for doc_id in doc_dict}
        doc_12h_count = {doc_id: 0 for doc_id in doc_dict}

        for s in existing_shifts:
            did = s["doctor_id"]
            if did in doc_dict:
                d = s["day"]
                sp = s["shift_period"]
                grid[d][did] = {
                    "period": sp,
                    "service": s["service"],
                    "is_proposal": 1
                }
                if sp == "12h":
                    doc_shifts_count[did] += 2
                    doc_12h_count[did] += 1
                elif sp in ("M", "T"):
                    doc_shifts_count[did] += 1

        # Asegurar DES al día siguiente de cualquier turno 12h ya propuesto
        for d in range(1, num_days):
            for did, sh in list(grid[d].items()):
                if sh["period"] == "12h" and did not in grid[d + 1] and not days_meta[d + 1]["is_sunday"]:
                    grid[d + 1][did] = {"period": "DES", "service": "DES", "is_proposal": 1}

        working_days = [d["day"] for d in month_info["days"] if not d["is_sunday"]]
        non_holiday_working_days = [d["day"] for d in month_info["days"] if not d["is_sunday"] and not d["is_holiday"]]
        holiday_working_days = [d["day"] for d in month_info["days"] if not d["is_sunday"] and d["is_holiday"]]
        ordered_working_days = non_holiday_working_days + holiday_working_days

        def assign_12h(did: int, day_num: int, srv: str):
            """Asigna un turno de 12h en day_num y su respectivo DES en day_num + 1."""
            grid[day_num][did] = {"period": "12h", "service": srv, "is_proposal": 0}
            doc_shifts_count[did] += 2
            doc_12h_count[did] += 1
            if day_num + 1 <= num_days and not days_meta[day_num + 1]["is_sunday"]:
                if did not in grid[day_num + 1]:
                    grid[day_num + 1][did] = {"period": "DES", "service": "DES", "is_proposal": 0}

        def can_assign_12h(did: int, day_num: int) -> bool:
            """Verifica si el médico puede tomar 12h en day_num respetando descansos previos y posteriores."""
            if doc_shifts_count[did] + 2 > quota:
                return False
            if did in grid[day_num]:
                return False
            if day_num > 1 and grid[day_num - 1].get(did, {}).get("period") == "12h":
                return False
            if day_num + 1 <= num_days and not days_meta[day_num + 1]["is_sunday"]:
                if did in grid[day_num + 1]:
                    return False
            return True

        def can_assign_6h(did: int, day_num: int) -> bool:
            if doc_shifts_count[did] + 1 > quota:
                return False
            if did in grid[day_num]:
                return False
            if day_num > 1 and grid[day_num - 1].get(did, {}).get("period") == "12h":
                return False
            return True

        # =========================================================================
        # 1. DR. SOLANO RICALDI:
        #    - Todos los miércoles 12h ('TS+TU') y jueves 'DES'.
        #    - Total 5 turnos de 12h al mes con 'DES' al día siguiente.
        # =========================================================================
        if solano_id:
            wednesdays = [d["day"] for d in month_info["days"] if d["weekday_index"] == 2]
            for w in wednesdays:
                if doc_12h_count[solano_id] < 5 and can_assign_12h(solano_id, w):
                    assign_12h(solano_id, w, "TS+TU")

            # Completar hasta 5 turnos de 12h para Solano (preferencia: viernes o lunes donde el día siguiente sea laborable para DES)
            candidate_12h_days = [
                d["day"] for d in month_info["days"]
                if d["weekday_index"] in (4, 0, 1, 5) and not d["is_holiday"]
            ] + [
                d["day"] for d in month_info["days"]
                if d["weekday_index"] in (4, 0, 1, 5) and d["is_holiday"]
            ]
            for cd in candidate_12h_days:
                if doc_12h_count[solano_id] >= 5:
                    break
                if can_assign_12h(solano_id, cd):
                    m_srv = _pick_needed_morning_service(grid[cd], ("TS", "TU", "CE"))
                    t_srv = _pick_needed_afternoon_service(grid[cd], ("TU", "CE"))
                    assign_12h(solano_id, cd, f"{m_srv}+{t_srv}")

        # =========================================================================
        # 2. DRA. SANCHEZ QUINTANILLA:
        #    - Lunes (0), Miércoles (2) y Viernes (4): 12 horas con DES al día siguiente.
        # =========================================================================
        if sanchez_id:
            mwf_days = [d for d in ordered_working_days if days_meta[d]["weekday_index"] in (0, 2, 4)]
            for d in mwf_days:
                if can_assign_12h(sanchez_id, d):
                    assign_12h(sanchez_id, d, "TU+CE")
            # Si la meta es impar (ej. 25 turnos) y falta 1 turno de 6h, asignarlo en el siguiente Lunes/Miércoles/Viernes libre
            for d in mwf_days + ordered_working_days:
                if doc_shifts_count[sanchez_id] >= quota:
                    break
                if can_assign_6h(sanchez_id, d):
                    srv = _pick_needed_morning_service(grid[d], ("TU", "CE", "TS"))
                    grid[d][sanchez_id] = {"period": "M", "service": srv, "is_proposal": 0}
                    doc_shifts_count[sanchez_id] += 1

        # =========================================================================
        # 3. DRA. CHUNGA TORRES:
        #    - Lunes (0), Miércoles (2) y Viernes (4): 12 horas con DES al día siguiente.
        # =========================================================================
        if chunga_id:
            mwf_days = [d for d in ordered_working_days if days_meta[d]["weekday_index"] in (0, 2, 4)]
            for d in mwf_days:
                if can_assign_12h(chunga_id, d):
                    # En miércoles Solano hace TS+TU y Sanchez TU+CE -> Chunga cubre CE en mañana
                    srv_12 = "CE+CE" if days_meta[d]["weekday_index"] == 2 else "CE+TU"
                    assign_12h(chunga_id, d, srv_12)
            for d in mwf_days + ordered_working_days:
                if doc_shifts_count[chunga_id] >= quota:
                    break
                if can_assign_6h(chunga_id, d):
                    srv = _pick_needed_morning_service(grid[d], ("CE", "TU", "TS"))
                    grid[d][chunga_id] = {"period": "M", "service": srv, "is_proposal": 0}
                    doc_shifts_count[chunga_id] += 1

        # =========================================================================
        # 4. DRA. CALDAS PACSI:
        #    - Sábados (5): 12 horas ('TS+TU': Mañana Telesalud/TBC/MCI + Tarde Tópico de Urgencia)
        #    - Lunes (0), Miércoles (2), Jueves (3), Viernes (4): Mañana ('M')
        # =========================================================================
        if caldas_id:
            saturdays = [d for d in ordered_working_days if days_meta[d]["weekday_index"] == 5]
            for sat in saturdays:
                if can_assign_12h(caldas_id, sat):
                    assign_12h(caldas_id, sat, "TS+TU")

            caldas_morning_days = [d for d in ordered_working_days if days_meta[d]["weekday_index"] in (0, 2, 3, 4)]
            for d in caldas_morning_days:
                if doc_shifts_count[caldas_id] >= quota:
                    break
                if can_assign_6h(caldas_id, d):
                    srv = _pick_needed_morning_service(grid[d], ("TS", "TU", "CE"))
                    grid[d][caldas_id] = {"period": "M", "service": srv, "is_proposal": 0}
                    doc_shifts_count[caldas_id] += 1

            # Si aún faltara algún turno para la meta, completar en días hábiles restantes por la mañana
            for d in ordered_working_days:
                if doc_shifts_count[caldas_id] >= quota:
                    break
                if can_assign_6h(caldas_id, d):
                    srv = _pick_needed_morning_service(grid[d], ("TS", "TU", "CE"))
                    grid[d][caldas_id] = {"period": "M", "service": srv, "is_proposal": 0}
                    doc_shifts_count[caldas_id] += 1

        # =========================================================================
        # 5. DRA. LAUREANO VARILLAS:
        #    - Lunes (0), Martes (1), Miércoles (2), Viernes (4) y Sábado (5): Mañana ('M')
        #    - Jueves (3): Tarde ('T')
        # =========================================================================
        if laureano_id:
            for d in ordered_working_days:
                if doc_shifts_count[laureano_id] >= quota:
                    break
                if not can_assign_6h(laureano_id, d):
                    continue
                w_idx = days_meta[d]["weekday_index"]
                if w_idx == 3:  # Jueves por la tarde
                    srv = _pick_needed_afternoon_service(grid[d], ("CE", "TU"))
                    grid[d][laureano_id] = {"period": "T", "service": srv, "is_proposal": 0}
                    doc_shifts_count[laureano_id] += 1
                elif w_idx in (0, 1, 2, 4, 5):  # Lun, Mar, Mié, Vie, Sáb por la mañana
                    srv = _pick_needed_morning_service(grid[d], ("CE", "TU", "TS"))
                    grid[d][laureano_id] = {"period": "M", "service": srv, "is_proposal": 0}
                    doc_shifts_count[laureano_id] += 1

        # =========================================================================
        # 6. COMPLETAR TURNOS DE 6H RESTANTES DEL DR. SOLANO HASTA SU META (quota)
        # =========================================================================
        if solano_id:
            # Priorizar días con menos cobertura (Martes, Sábados, Viernes, Lunes)
            solano_fill_days = sorted(
                ordered_working_days,
                key=lambda d: (days_meta[d]["is_holiday"], len(grid[d]), d)
            )
            for d in solano_fill_days:
                if doc_shifts_count[solano_id] >= quota:
                    break
                if not can_assign_6h(solano_id, d):
                    continue
                # Revisar si falta cubrir servicios en Tarde o en Mañana
                t_covered = {
                    (sh["service"].split("+")[1] if "+" in sh["service"] else sh["service"])
                    for sh in grid[d].values() if sh["period"] in ("T", "12h")
                }
                m_covered = {
                    (sh["service"].split("+")[0] if "+" in sh["service"] else sh["service"])
                    for sh in grid[d].values() if sh["period"] in ("M", "12h")
                }
                if len(m_covered) < 3 and len(m_covered) <= len(t_covered):
                    srv = _pick_needed_morning_service(grid[d], ("TU", "CE", "TS"))
                    grid[d][solano_id] = {"period": "M", "service": srv, "is_proposal": 0}
                else:
                    srv = _pick_needed_afternoon_service(grid[d], ("TU", "CE"))
                    grid[d][solano_id] = {"period": "T", "service": srv, "is_proposal": 0}
                doc_shifts_count[solano_id] += 1

        # =========================================================================
        # 7. GUARDAR EN BASE DE DATOS
        # =========================================================================
        conn.execute("DELETE FROM shifts WHERE year = ? AND month = ?", (year, month))
        for d in range(1, num_days + 1):
            for did, sh in grid[d].items():
                is_prop = sh.get("is_proposal", 0)
                conn.execute("""
                    INSERT INTO shifts (year, month, day, shift_period, service, doctor_id, is_proposal, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (year, month, d, sh["period"], sh["service"], did, is_prop))

        conn.execute("""
            UPDATE discrepancies
            SET status = 'auto_resolved_at_deadline',
                resolved_at = CURRENT_TIMESTAMP,
                resolution_details = 'Actualizado según reglas asistenciales del rol mensual.'
            WHERE year = ? AND month = ? AND status = 'open'
        """, (year, month))

        conn.commit()
        log_audit(0, "Sistema IPRESS", "Asignación Automática de Rol",
                  f"Autocompletado de rol mensual para {month}/{year} cumpliendo reglas específicas por médico.", conn=conn)

def validate_and_approve_schedule(
    year: int,
    month: int,
    approved_by_name: str = "Jefatura Médica IPRESS",
    approve_pending_days: bool = True
) -> dict:
    """
    Verifica si el horario cumple las condiciones para ser 'Horario Aprobado':
    1. Sin discrepancias de colisión abiertas.
    2. Cada médico cumple su cuota mensual (25 - feriados_lun_sab).
    3. Dr. Solano cuenta con sus 5 turnos de 12h y descansos al día siguiente.
    4. Si existen días donde no se cubran todos los servicios (TU, CE, TS en mañana; TU, CE en tarde),
       solo el Administrador puede aprobar esos días (si approve_pending_days=True y es Administrador,
       aprueba las excepciones del día y emite el Horario Aprobado; de lo contrario informa la alerta).
    """
    with get_connection() as conn:
        open_discs = conn.execute("""
            SELECT COUNT(*) as cnt FROM discrepancies
            WHERE year = ? AND month = ? AND status = 'open'
        """, (year, month)).fetchone()["cnt"]

        if open_discs > 0:
            return {
                "is_approved": False,
                "reason": f"Existen {open_discs} discrepancia(s) de colisión abierta(s) pendientes de resolución."
            }

        quota_summary = get_doctor_quota_summary(year, month)
        incomplete_docs = [
            d["name"] for d in quota_summary["doctors_summary"].values()
            if d["total_equivalent_shifts"] != d["quota"]
        ]

        if incomplete_docs:
            return {
                "is_approved": False,
                "reason": f"Los siguientes médicos no tienen la meta exacta de turnos ({quota_summary['month_info']['monthly_quota']}): {', '.join(incomplete_docs)}."
            }

    coverage = get_daily_coverage(year, month)
    unapproved_alert_days = [
        d for d, c in coverage.items()
        if c["has_service_alert"] and not c["is_admin_approved_day"]
    ]

    if unapproved_alert_days:
        if approve_pending_days:
            approve_all_day_discrepancies(
                year, month, approved_by_name,
                "Aprobado por el Administrador al certificar el Horario Mensual."
            )
        else:
            return {
                "is_approved": False,
                "unapproved_days": unapproved_alert_days,
                "reason": (
                    f"Los días {unapproved_alert_days} presentan alerta de servicios no cubiertos "
                    f"(Mañana requiere TU, CE, TS; Tarde requiere TU, CE). "
                    f"Solo el Administrador puede aprobar los días con esta discrepancia."
                )
            }

    approval_token = hashlib.sha256(f"{year}-{month}-{datetime.now().isoformat()}".encode()).hexdigest()[:16].upper()
    now_iso = datetime.now().isoformat()

    with get_connection() as conn:
        conn.execute("""
            INSERT INTO schedule_status (year, month, is_approved, approved_at, approved_by, approval_hash)
            VALUES (?, ?, 1, ?, ?, ?)
            ON CONFLICT(year, month) DO UPDATE SET
                is_approved = 1,
                approved_at = excluded.approved_at,
                approved_by = excluded.approved_by,
                approval_hash = excluded.approval_hash
        """, (year, month, now_iso, approved_by_name, approval_token))

        conn.commit()
        log_audit(0, approved_by_name, "Horario Aprobado", f"Certificación oficial de programación para {month}/{year}. Código: {approval_token}", conn=conn)

    return {
        "is_approved": True,
        "approved_at": now_iso,
        "approved_by": approved_by_name,
        "approval_hash": approval_token,
        "message": "¡HORARIO APROBADO! La programación mensual ha sido certificada por la Administración."
    }

def reopen_schedule(year: int, month: int, reopened_by_name: str = "Jefatura Médica IPRESS") -> bool:
    """
    Reabre una programación mensual previamente aprobada para permitir modificaciones extraordinarias.
    Solo puede ser ejecutada por la Jefatura Médica (ADMIN).
    """
    with get_connection() as conn:
        conn.execute("""
            UPDATE schedule_status
            SET is_approved = 0,
                approved_at = NULL,
                approved_by = NULL,
                approval_hash = NULL
            WHERE year = ? AND month = ?
        """, (year, month))
        conn.commit()
        log_audit(0, reopened_by_name, "Reapertura de Horario", f"Se revocó la aprobación del rol de {month}/{year} para modificaciones asistenciales.", conn=conn)
    return True
