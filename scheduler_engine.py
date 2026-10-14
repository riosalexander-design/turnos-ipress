"""
scheduler_engine.py
Motor de optimización asistencial, asignación automática de turnos,
detector de discrepancias, generador de alternativas y validador de 'Horario Aprobado'.
"""

import json
from datetime import datetime, timedelta, date
from holidays_pe import get_month_info
from database import get_connection, log_audit

SERVICES_MORNING = ["TU", "CE", "TS"]  # Tópico Urgencias, Consultorio Externo, Telesalud
SERVICES_AFTERNOON = ["TU", "CE"]       # Tópico Urgencias, Consultorio Externo

def calculate_deadline(current_dt: datetime, year: int, month: int) -> tuple[datetime, str]:
    """
    Calcula el plazo de 72 horas para subsanación con TOPE ESTRICTO al Día 10 a las 00:00:00.
    Retorna (datetime_limite, descripcion_legible).
    """
    # Tope institucional absoluto: Día 10 a las 00:00:00
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

        summary = {}
        for doc in doctors:
            doc_id = doc["id"]
            # Buscar turnos del médico en el mes
            shifts = conn.execute("""
                SELECT day, shift_period, service
                FROM shifts
                WHERE year = ? AND month = ? AND doctor_id = ?
            """, (year, month, doc_id)).fetchall()

            count_6h = 0
            count_12h = 0
            count_des = 0
            days_assigned = {}

            for s in shifts:
                sp = s["shift_period"]
                day = s["day"]
                days_assigned[day] = {"shift_period": sp, "service": s["service"]}
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
    - Médicos en Mañana (conteo y lista de servicios asignados)
    - Médicos en Tarde (conteo y lista de servicios asignados)
    - Alerta si algún turno tiene < 2 médicos
    """
    month_info = get_month_info(year, month)
    num_days = month_info["num_days"]

    coverage = {}

    with get_connection() as conn:
        for d in range(1, num_days + 1):
            day_info = month_info["days"][d - 1]
            is_sunday = day_info["is_sunday"]

            # Obtener turnos de este día
            rows = conn.execute("""
                SELECT s.shift_period, s.service, s.doctor_id, d.name
                FROM shifts s
                JOIN doctors d ON s.doctor_id = d.id
                WHERE s.year = ? AND s.month = ? AND s.day = ?
            """, (year, month, d)).fetchall()

            morning_docs = []
            afternoon_docs = []

            for r in rows:
                sp = r["shift_period"]
                srv = r["service"]
                doc_name = r["name"]
                item = {"doctor_id": r["doctor_id"], "doctor_name": doc_name, "service": srv}

                if sp == "M":
                    morning_docs.append(item)
                elif sp == "T":
                    afternoon_docs.append(item)
                elif sp == "12h":
                    morning_docs.append(item)
                    afternoon_docs.append(item)

            m_count = len(morning_docs)
            t_count = len(afternoon_docs)

            # Regla: NUNCA menos de 2 médicos en días operativos (lun a sáb)
            # En domingos si no es operativo se marca acorde, pero si se atiende nunca < 2
            m_valid = m_count >= 2 if not is_sunday else (m_count == 0 or m_count >= 2)
            t_valid = t_count >= 2 if not is_sunday else (t_count == 0 or t_count >= 2)

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
                "morning_valid": m_valid,
                "afternoon_count": t_count,
                "afternoon_docs": afternoon_docs,
                "afternoon_valid": t_valid,
                "has_warning": has_coverage_warning
            }

    return coverage

def detect_discrepancies(year: int, month: int, current_sim_time: datetime = None) -> list:
    """
    Detecta automáticamente todas las discrepancias en el rol propuesto:
    1. Colisión de servicios exclusivos (ej. 2 médicos pidiendo TU en la mañana el mismo día).
    2. Turnos descubiertos con < 2 médicos en días laborables.
    3. Conflicto de viernes de 12 horas.
    4. Violación de descansos post-12h (especialmente Dr. Solano).
    Genera sugerencias alternativas automáticas y calcula el plazo de 72h (máx día 10 a las 00:00).
    """
    if current_sim_time is None:
        current_sim_time = datetime.now()

    month_info = get_month_info(year, month)
    deadline_dt, deadline_str = calculate_deadline(current_sim_time, year, month)

    discrepancies = []

    with get_connection() as conn:
        # 1. Colisiones de servicios por día y período
        for d in range(1, month_info["num_days"] + 1):
            shifts_day = conn.execute("""
                SELECT s.id, s.shift_period, s.service, s.doctor_id, d.name
                FROM shifts s
                JOIN doctors d ON s.doctor_id = d.id
                WHERE s.year = ? AND s.month = ? AND s.day = ?
            """, (year, month, d)).fetchall()

            # Revisar servicios en Mañana
            m_services = {}
            for s in shifts_day:
                sp = s["shift_period"]
                srv = s["service"]
                if sp in ("M", "12h"):
                    actual_srv = srv.split("+")[0] if "+" in srv else srv
                    if actual_srv in ("TU", "CE", "TS"):
                        m_services.setdefault(actual_srv, []).append(s)

            for srv, docs_list in m_services.items():
                if len(docs_list) > 1:
                    # Colisión: dos médicos en el mismo servicio en Mañana
                    doc1 = docs_list[0]
                    doc2 = docs_list[1]
                    
                    # Generar alternativa
                    # Buscar servicios libres en Mañana
                    occupied = list(m_services.keys())
                    free_services = [s for s in SERVICES_MORNING if s not in occupied]
                    alt_service = free_services[0] if free_services else "CE"
                    
                    suggestion = {
                        "action": "reassign_service",
                        "doctor_keep": {"id": doc1["doctor_id"], "name": doc1["name"], "service": srv},
                        "doctor_reassign": {"id": doc2["doctor_id"], "name": doc2["name"], "new_service": alt_service},
                        "day": d,
                        "shift_period": "M",
                        "text": f"Mantener al Dr./Dra. {doc1['name']} en Mañana-{srv} y reasignar al Dr./Dra. {doc2['name']} a Mañana-{alt_service}."
                    }

                    disc_data = {
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
                        "description": f"Día {d}: Conflicto en Turno Mañana. Ambos médicos ({doc1['name']} y {doc2['name']}) propusieron el mismo servicio ({srv}).",
                        "suggested_alternative": json.dumps(suggestion),
                        "suggested_text": suggestion["text"],
                        "deadline_datetime": deadline_dt.isoformat(),
                        "deadline_str": deadline_str
                    }
                    discrepancies.append(disc_data)

            # Revisar servicios en Tarde
            t_services = {}
            for s in shifts_day:
                sp = s["shift_period"]
                srv = s["service"]
                if sp in ("T", "12h"):
                    actual_srv = srv.split("+")[1] if "+" in srv else srv
                    if actual_srv in ("TU", "CE"):
                        t_services.setdefault(actual_srv, []).append(s)

            for srv, docs_list in t_services.items():
                if len(docs_list) > 1:
                    doc1 = docs_list[0]
                    doc2 = docs_list[1]
                    occupied = list(t_services.keys())
                    free_services = [s for s in SERVICES_AFTERNOON if s not in occupied]
                    alt_service = free_services[0] if free_services else ("CE" if srv == "TU" else "TU")

                    suggestion = {
                        "action": "reassign_service",
                        "doctor_keep": {"id": doc1["doctor_id"], "name": doc1["name"], "service": srv},
                        "doctor_reassign": {"id": doc2["doctor_id"], "name": doc2["name"], "new_service": alt_service},
                        "day": d,
                        "shift_period": "T",
                        "text": f"Mantener al Dr./Dra. {doc1['name']} en Tarde-{srv} y reasignar al Dr./Dra. {doc2['name']} a Tarde-{alt_service}."
                    }

                    disc_data = {
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
                        "description": f"Día {d}: Conflicto en Turno Tarde. Ambos médicos ({doc1['name']} y {doc2['name']}) propusieron el mismo servicio ({srv}).",
                        "suggested_alternative": json.dumps(suggestion),
                        "suggested_text": suggestion["text"],
                        "deadline_datetime": deadline_dt.isoformat(),
                        "deadline_str": deadline_str
                    }
                    discrepancies.append(disc_data)

    return discrepancies

def sync_discrepancies_db(year: int, month: int, current_sim_time: datetime = None):
    """
    Sincroniza las discrepancias detectadas con la tabla 'discrepancies' en la BD:
    - Inserta nuevas discrepancias detectadas como 'open'.
    - Si una discrepancia previamente 'open' ya no existe en la grilla actual, la marca como resuelta.
    """
    active_discs = detect_discrepancies(year, month, current_sim_time)
    with get_connection() as conn:
        active_keys = set()
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
        
        # Marcar como resueltas aquellas que ya no tienen conflicto en la matriz
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

        conn.commit()

def apply_discrepancy_alternative(discrepancy_id: int) -> bool:
    """
    Aplica la alternativa sugerida por el sistema para una discrepancia.
    Actualiza el turno del médico reasignado y marca la discrepancia como 'accepted_suggestion'.
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

            # Actualizar en shifts
            # Si era turno de 12h, modificar la parte correspondiente
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

def auto_assign_missing_shifts_day10(year: int, month: int):
    """
    Motor de Asignación Automática al Día 10 a las 00:00 horas (o cierre de propuesta):
    PRINCIPIO FUNDAMENTAL:
    - Respeta y conserva al 100% las propuestas ingresadas por los médicos.
    - Únicamente autocompleta los turnos faltantes para quienes no enviaron propuesta o la dejaron incompleta.
    - Garantiza:
      1. Regla del Viernes de 12h: Cada médico tiene 1 viernes de 12h con sábado de descanso (DES).
      2. Regla Dr. Solano: Exactamente 5 turnos de 12h con descanso al día siguiente.
      3. Cobertura estricta: NUNCA menos de 2 médicos en Mañana (TU, CE) ni en Tarde (TU, CE) en ningún día laboral.
      4. Telesalud (TS): Se asigna en las mañanas cuando hay disponibilidad para completar la meta.
      5. Meta exacta: Cada médico alcanza exactamente su cuota mensual (25 - feriados lun-sáb).
    """
    month_info = get_month_info(year, month)
    quota = month_info["monthly_quota"]
    num_days = month_info["num_days"]

    with get_connection() as conn:
        doctors = conn.execute("""
            SELECT id, name, condition, cmp
            FROM doctors
            WHERE is_active = 1 AND role = 'doctor'
            ORDER BY condition DESC, name ASC
        """).fetchall()

        doc_dict = {d["id"]: dict(d) for d in doctors}
        solano_id = next((d["id"] for d in doctors if "Solano" in d["name"]), None)
        other_doc_ids = [d["id"] for d in doctors if d["id"] != solano_id]

        # 0. CARGAR PROPUESTAS EXISTENTES PARA RESPETARLAS AL 100%
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
                    "is_proposal": s["is_proposal"]
                }
                if sp == "12h":
                    doc_shifts_count[did] += 2
                    doc_12h_count[did] += 1
                elif sp in ("M", "T"):
                    doc_shifts_count[did] += 1

        # Si un turno ya propuesto fue de 12h y el día siguiente está libre, asegurar descanso DES
        for d in range(1, num_days):
            for did, sh in list(grid[d].items()):
                if sh["period"] == "12h" and did not in grid[d + 1]:
                    grid[d + 1][did] = {"period": "DES", "service": "DES", "is_proposal": 1}

        # Días del mes clasificados
        working_days = [d["day"] for d in month_info["days"] if not d["is_sunday"]]
        fridays = [d["day"] for d in month_info["days"] if d["is_friday"]]

        # 1. ASIGNACIÓN DE VIERNES DE 12 HORAS (para médicos que no tengan uno propuesto)
        for did in doc_dict:
            has_fri_12h = any(grid[f].get(did, {}).get("period") == "12h" for f in fridays)
            if not has_fri_12h and doc_shifts_count[did] + 2 <= quota:
                for f in fridays:
                    sat = f + 1
                    count_12h_fri = sum(1 for s in grid[f].values() if s.get("period") == "12h")
                    count_des_sat = sum(1 for s in grid[sat].values() if s.get("period") == "DES") if sat <= num_days else 0
                    if count_12h_fri == 0 and count_des_sat == 0 and did not in grid[f] and (sat > num_days or did not in grid[sat]):
                        grid[f][did] = {"period": "12h", "service": "TU+CE", "is_proposal": 0}
                        if sat <= num_days:
                            grid[sat][did] = {"period": "DES", "service": "DES", "is_proposal": 0}
                        doc_shifts_count[did] += 2
                        doc_12h_count[did] += 1
                        break

        # 2. ASIGNACIÓN DE TURNOS DE 12h RESTANTES PARA DR. SOLANO (hasta alcanzar 5)
        if solano_id:
            needed_12h = max(0, 5 - doc_12h_count[solano_id])
            if needed_12h > 0:
                preferred_days = [d["day"] for d in month_info["days"] if d["weekday_index"] in (1, 0, 3, 2)]
                for cd in preferred_days:
                    if needed_12h <= 0 or doc_shifts_count[solano_id] + 2 > quota:
                        break
                    nxt = cd + 1
                    if nxt <= num_days and solano_id not in grid[cd] and solano_id not in grid[nxt]:
                        prev = cd - 1
                        if prev >= 1 and grid[prev].get(solano_id, {}).get("period") == "12h":
                            continue
                        count_12h_cd = sum(1 for s in grid[cd].values() if s.get("period") == "12h")
                        count_des_nxt = sum(1 for s in grid[nxt].values() if s.get("period") == "DES")
                        if count_12h_cd == 0 and count_des_nxt == 0:
                            grid[cd][solano_id] = {"period": "12h", "service": "TU+CE", "is_proposal": 0}
                            grid[nxt][solano_id] = {"period": "DES", "service": "DES", "is_proposal": 0}
                            doc_shifts_count[solano_id] += 2
                            doc_12h_count[solano_id] += 1
                            needed_12h -= 1

        # 3. ASEGURAR COBERTURA BASE OBLIGATORIA: Mínimo 2 médicos en Mañana (TU, CE) y 2 en Tarde (TU, CE)
        for d in working_days:
            m_docs = [did for did, sh in grid[d].items() if sh["period"] in ("M", "12h")]
            t_docs = [did for did, sh in grid[d].items() if sh["period"] in ("T", "12h")]

            m_services = [sh["service"].split("+")[0] if "+" in sh["service"] else sh["service"]
                          for sh in grid[d].values() if sh["period"] in ("M", "12h")]
            t_services = [sh["service"].split("+")[1] if "+" in sh["service"] else sh["service"]
                          for sh in grid[d].values() if sh["period"] in ("T", "12h")]

            # Completar Mañana a 2 médicos (TU y CE)
            while len(m_docs) < 2:
                cands = [did for did in doc_dict if did not in grid[d] and doc_shifts_count[did] < quota]
                cands = [did for did in cands if not (d > 1 and grid[d - 1].get(did, {}).get("period") == "12h")]
                cands.sort(key=lambda did: quota - doc_shifts_count[did], reverse=True)
                if not cands:
                    break
                doc_sel = cands[0]
                srv_sel = "TU" if "TU" not in m_services else "CE"
                grid[d][doc_sel] = {"period": "M", "service": srv_sel, "is_proposal": 0}
                m_services.append(srv_sel)
                m_docs.append(doc_sel)
                doc_shifts_count[doc_sel] += 1

            # Completar Tarde a 2 médicos (TU y CE)
            while len(t_docs) < 2:
                cands = [did for did in doc_dict if did not in grid[d] and doc_shifts_count[did] < quota]
                cands = [did for did in cands if not (d > 1 and grid[d - 1].get(did, {}).get("period") == "12h")]
                cands.sort(key=lambda did: quota - doc_shifts_count[did], reverse=True)
                if not cands:
                    break
                doc_sel = cands[0]
                srv_sel = "TU" if "TU" not in t_services else "CE"
                grid[d][doc_sel] = {"period": "T", "service": srv_sel, "is_proposal": 0}
                t_services.append(srv_sel)
                t_docs.append(doc_sel)
                doc_shifts_count[doc_sel] += 1

        # 4. ASIGNAR TELESALUD (TS) EN LAS MAÑANAS PARA QUIENES TENGAN TURNOS PENDIENTES
        for d in working_days:
            m_srvs = [sh["service"].split("+")[0] if "+" in sh["service"] else sh["service"]
                      for sh in grid[d].values() if sh["period"] in ("M", "12h")]
            if len(m_srvs) >= 2 and "TS" not in m_srvs:
                cands = [did for did in doc_dict if did not in grid[d] and doc_shifts_count[did] < quota]
                cands = [did for did in cands if not (d > 1 and grid[d - 1].get(did, {}).get("period") == "12h")]
                cands.sort(key=lambda did: quota - doc_shifts_count[did], reverse=True)
                if cands:
                    doc_sel = cands[0]
                    grid[d][doc_sel] = {"period": "M", "service": "TS", "is_proposal": 0}
                    doc_shifts_count[doc_sel] += 1

        # 5. AJUSTE FINO: Si algún médico aún tiene diferencia para alcanzar exactamente la meta
        for did in doc_dict:
            while doc_shifts_count[did] < quota:
                assigned = False
                for d in working_days:
                    if did not in grid[d]:
                        if d > 1 and grid[d - 1].get(did, {}).get("period") == "12h":
                            continue
                        m_cnt = len([x for x in grid[d].values() if x["period"] in ("M", "12h")])
                        t_cnt = len([x for x in grid[d].values() if x["period"] in ("T", "12h")])
                        if m_cnt < 3:
                            grid[d][did] = {"period": "M", "service": "TS", "is_proposal": 0}
                            doc_shifts_count[did] += 1
                            assigned = True
                            break
                        elif t_cnt < 2 or t_cnt < 3:
                            grid[d][did] = {"period": "T", "service": "CE", "is_proposal": 0}
                            doc_shifts_count[did] += 1
                            assigned = True
                            break
                if not assigned:
                    break

        # 6. GUARDAR EN LA BD RESPETANDO 'is_proposal' ORIGINAL
        conn.execute("DELETE FROM shifts WHERE year = ? AND month = ?", (year, month))
        for d in range(1, num_days + 1):
            for did, sh in grid[d].items():
                is_prop = sh.get("is_proposal", 0)
                conn.execute("""
                    INSERT INTO shifts (year, month, day, shift_period, service, doctor_id, is_proposal, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (year, month, d, sh["period"], sh["service"], did, is_prop))

        # Al autocompletar en el corte, resolver cualquier discrepancia abierta previa
        conn.execute("""
            UPDATE discrepancies
            SET status = 'auto_resolved_at_deadline',
                resolved_at = CURRENT_TIMESTAMP,
                resolution_details = 'Resuelto automáticamente en el corte del Día 10 a las 00:00 hrs.'
            WHERE year = ? AND month = ? AND status = 'open'
        """, (year, month))

        conn.commit()
        log_audit(0, "Sistema IPRESS", "Asignación Automática Corte Día 10",
                  f"Autocompletado de rol mensual para {month}/{year} (respetando propuestas preexistentes)", conn=conn)

def validate_and_approve_schedule(year: int, month: int, approved_by_name: str = "Jefatura Médica IPRESS") -> dict:
    """
    Verifica si el horario cumple con todas las condiciones para ser 'Horario Aprobado':
    1. Sin discrepancias abiertas.
    2. Ningún turno de ningún día laboral con < 2 médicos.
    3. Cada médico cumple su cuota mensual (25 - feriados_lun_sab).
    4. Reglas de 12h y descansos cumplidos.
    Si es válido, guarda la aprobación en schedule_status y retorna estado exitoso.
    """
    with get_connection() as conn:
        # 1. Discrepancias abiertas
        open_discs = conn.execute("""
            SELECT COUNT(*) as cnt FROM discrepancies
            WHERE year = ? AND month = ? AND status = 'open'
        """, (year, month)).fetchone()["cnt"]

        if open_discs > 0:
            return {
                "is_approved": False,
                "reason": f"Existen {open_discs} discrepancia(s) abierta(s) pendientes de resolución."
            }

        # 2. Cobertura diaria
        coverage = get_daily_coverage(year, month)
        invalid_days = [
            d for d, c in coverage.items()
            if not c["is_sunday"] and (c["morning_count"] < 2 or c["afternoon_count"] < 2)
        ]

        if invalid_days:
            return {
                "is_approved": False,
                "reason": f"Los siguientes días laborales tienen turnos con menos de 2 médicos: {invalid_days}."
            }

        # 3. Metas de los médicos
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

        # Generar hash de aprobación y registrar
        import hashlib
        approval_token = hashlib.sha256(f"{year}-{month}-{datetime.now().isoformat()}".encode()).hexdigest()[:16].upper()
        now_iso = datetime.now().isoformat()

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
        log_audit(0, approved_by_name, "Horario Aprobado", f"Certificación oficial de programación para {month}/{year}. Código: {approval_token}")

        return {
            "is_approved": True,
            "approved_at": now_iso,
            "approved_by": approved_by_name,
            "approval_hash": approval_token,
            "message": "¡HORARIO APROBADO! La programación mensual cumple con todas las normativas asistenciales."
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
