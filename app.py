"""
app.py
Servidor Flask y API REST para el Sistema Web de Programación de Turnos de la IPRESS.
Incluye autenticación por médico, control de acceso RBAC, gestión de propuestas,
detección y conciliación de discrepancias con plazo de 72h (máx día 10 00:00h) y exportación a Excel.
"""

import os
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session, send_file
from database import init_db, get_connection, hash_pin, log_audit, get_audit_logs, prune_audit_logs
from holidays_pe import get_month_info
from scheduler_engine import (
    get_doctor_quota_summary,
    get_daily_coverage,
    detect_discrepancies,
    sync_discrepancies_db,
    apply_discrepancy_alternative,
    auto_assign_missing_shifts_day10,
    validate_and_approve_schedule,
    reopen_schedule,
    calculate_deadline,
    record_doctor_shift,
    approve_day_discrepancy,
    approve_all_day_discrepancies
)
from exporter import (
    generate_excel_schedule,
    generate_blank_proposal_template,
    parse_and_import_doctor_proposal
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "ipress-salud-peru-secret-key-2026")

# Inicializar BD al arrancar
init_db()

# Estado de fecha simulada para pruebas interactivas (por defecto fecha actual)
SIMULATED_TIME = {
    "enabled": False,
    "dt": datetime.now()
}

def get_current_time():
    if SIMULATED_TIME["enabled"]:
        return SIMULATED_TIME["dt"]
    return datetime.now()

# -------------------------------------------------------------
# RUTAS DE AUTENTICACIÓN Y PERFIL DE MÉDICO
# -------------------------------------------------------------

@app.route("/api/auth/me", methods=["GET"])
def auth_me():
    """Retorna los datos del usuario en sesión actual."""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"authenticated": False})
    
    with get_connection() as conn:
        doc = conn.execute("SELECT id, name, condition, cmp, username, role FROM doctors WHERE id = ?", (user_id,)).fetchone()
        if not doc:
            session.clear()
            return jsonify({"authenticated": False})
        return jsonify({
            "authenticated": True,
            "user": {
                "id": doc["id"],
                "name": doc["name"],
                "condition": doc["condition"],
                "cmp": doc["cmp"],
                "username": doc["username"],
                "role": doc["role"]
            }
        })

@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    """Inicio de sesión con selección de Nombre (o ID/Usuario/CMP) y Clave."""
    data = request.json or {}
    doctor_id = data.get("doctor_id")
    user_input = (data.get("name") or data.get("username") or data.get("cmp") or data.get("user") or "").strip()
    password = (data.get("password") or data.get("pin") or "").strip()

    if not doctor_id and not user_input:
        return jsonify({"success": False, "error": "Debe seleccionar su nombre de la lista."}), 400
    if not password:
        return jsonify({"success": False, "error": "Debe ingresar su clave de acceso."}), 400

    with get_connection() as conn:
        if doctor_id:
            doc = conn.execute("""
                SELECT id, name, condition, cmp, username, pin_hash, role
                FROM doctors
                WHERE id = ? AND is_active = 1
            """, (doctor_id,)).fetchone()
        else:
            doc = conn.execute("""
                SELECT id, name, condition, cmp, username, pin_hash, role
                FROM doctors
                WHERE (LOWER(name) = LOWER(?) OR LOWER(username) = LOWER(?) OR UPPER(cmp) = UPPER(?)) AND is_active = 1
            """, (user_input, user_input, user_input)).fetchone()

            # Compatibilidad alias admin -> jefatura
            if not doc and user_input.lower() == "admin":
                doc = conn.execute("""
                    SELECT id, name, condition, cmp, username, pin_hash, role
                    FROM doctors
                    WHERE (username = 'jefatura' OR cmp = 'ADMIN') AND is_active = 1
                """).fetchone()

        if not doc:
            return jsonify({"success": False, "error": "Personal no encontrado."}), 401

        if doc["role"] not in ("admin", "rrhh"):
            return jsonify({
                "success": False,
                "error": (
                    "ACCESO REVOCADO PARA MÉDICOS: El ingreso directo al sistema está habilitado únicamente para "
                    "Jefatura Médica y Recursos Humanos (RRHH). Los médicos deben descargar su formato en blanco "
                    "y subir su propuesta de horario en Excel."
                )
            }), 403
        
        if doc["pin_hash"] != hash_pin(password):
            return jsonify({"success": False, "error": "Clave incorrecta."}), 401
        
        session["user_id"] = doc["id"]
        session["user_name"] = doc["name"]
        session["user_role"] = doc["role"]
        
        log_audit(doc["id"], doc["name"], "Inicio de Sesión", f"Acceso correcto de {doc['name']}")
        return jsonify({
            "success": True,
            "user": {
                "id": doc["id"],
                "name": doc["name"],
                "condition": doc["condition"],
                "cmp": doc["cmp"],
                "username": doc["username"],
                "role": doc["role"]
            }
        })

@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    """Cierra la sesión del usuario actual."""
    user_id = session.get("user_id")
    user_name = session.get("user_name", "Desconocido")
    if user_id:
        log_audit(user_id, user_name, "Cierre de Sesión", "Salida del sistema")
    session.clear()
    return jsonify({"success": True})

@app.route("/api/auth/change-pin", methods=["POST"])
def auth_change_pin():
    """Permite al médico autenticado cambiar su PIN personal."""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "Debe iniciar sesión para cambiar su PIN."}), 401
    
    data = request.json or {}
    old_pin = (data.get("old_pin") or "").strip()
    new_pin = (data.get("new_pin") or "").strip()

    if len(new_pin) < 4:
        return jsonify({"success": False, "error": "El nuevo PIN debe tener al menos 4 caracteres/dígitos."}), 400

    with get_connection() as conn:
        doc = conn.execute("SELECT id, name, pin_hash FROM doctors WHERE id = ?", (user_id,)).fetchone()
        if not doc:
            return jsonify({"success": False, "error": "Médico no encontrado."}), 404
        
        if doc["pin_hash"] != hash_pin(old_pin):
            return jsonify({"success": False, "error": "El PIN actual es incorrecto."}), 400
        
        conn.execute("UPDATE doctors SET pin_hash = ? WHERE id = ?", (hash_pin(new_pin), user_id))
        conn.commit()
        log_audit(user_id, doc["name"], "Cambio de PIN", "PIN actualizado correctamente", conn=conn)

    return jsonify({"success": True, "message": "PIN actualizado con éxito."})

# -------------------------------------------------------------
# RUTAS DE INFORMACIÓN DEL MES Y MÉDICOS
# -------------------------------------------------------------

@app.route("/api/month-info", methods=["GET"])
def api_month_info():
    """Obtiene el calendario del mes, feriados nacionales, descuento lun-sáb y meta de turnos."""
    year = int(request.args.get("year", datetime.now().year))
    month = int(request.args.get("month", datetime.now().month))
    info = get_month_info(year, month)
    return jsonify(info)

@app.route("/api/doctors", methods=["GET"])
def api_doctors():
    """Retorna la lista de médicos y usuarios activos de la IPRESS."""
    with get_connection() as conn:
        docs = conn.execute("""
            SELECT id, name, condition, cmp, username, role
            FROM doctors
            WHERE is_active = 1
            ORDER BY role DESC, condition DESC, name ASC
        """).fetchall()
        return jsonify([dict(d) for d in docs])

# -------------------------------------------------------------
# RUTAS DE PROGRAMACIÓN Y PROPUESTAS DE TURNOS
# -------------------------------------------------------------

@app.route("/api/schedule", methods=["GET"])
def api_get_schedule():
    """
    Retorna la matriz completa del mes:
    - Turnos día por día y por médico
    - Resumen de cuotas y horas
    - Cobertura diaria y alertas de < 2 médicos
    - Discrepancias activas
    - Estado de Horario Aprobado
    """
    year = int(request.args.get("year", datetime.now().year))
    month = int(request.args.get("month", datetime.now().month))
    curr_time = get_current_time()

    # Sincronizar discrepancias en BD
    sync_discrepancies_db(year, month, curr_time)

    quota_data = get_doctor_quota_summary(year, month)
    coverage_data = get_daily_coverage(year, month)

    with get_connection() as conn:
        # Estado de aprobación
        status_row = conn.execute("SELECT * FROM schedule_status WHERE year = ? AND month = ?", (year, month)).fetchone()
        approval_status = dict(status_row) if status_row else {"is_approved": 0}

        # Discrepancias abiertas
        discs = conn.execute("""
            SELECT d.*, doc1.name as doc1_name, doc2.name as doc2_name
            FROM discrepancies d
            JOIN doctors doc1 ON d.doctor1_id = doc1.id
            JOIN doctors doc2 ON d.doctor2_id = doc2.id
            WHERE d.year = ? AND d.month = ?
            ORDER BY d.day ASC, d.id ASC
        """, (year, month)).fetchall()
        disc_list = [dict(d) for d in discs]

    # Calcular plazo límite institucional para el frontend
    deadline_dt, deadline_str = calculate_deadline(curr_time, year, month)

    # Advertencia del Día 10:
    # A partir del día 10 de cada mes se vence el plazo de propuestas y Jefatura/RRHH debe completar manualmente el horario
    show_day10_warning = curr_time.day >= 10
    day10_warning_message = (
        "⚠️ ADVERTENCIA INSTITUCIONAL (Corte Día 10): Ha finalizado el plazo de propuestas de turnos. "
        "La Jefatura Médica o el Responsable de Recursos Humanos debe completar manualmente el horario del mes siguiente, "
        "garantizando la meta mensual por médico, la dotación mínima de 2 médicos por turno (Mañana y Tarde) y los descansos obligatorios."
    )

    return jsonify({
        "year": year,
        "month": month,
        "current_time": curr_time.isoformat(),
        "deadline_datetime": deadline_dt.isoformat(),
        "deadline_str": deadline_str,
        "is_day_10_passed": show_day10_warning,
        "day10_warning_message": day10_warning_message,
        "quota_summary": quota_data,
        "coverage": coverage_data,
        "discrepancies": disc_list,
        "approval_status": approval_status
    })

@app.route("/api/shifts/update", methods=["POST"])
def api_update_shift():
    """
    Guarda o actualiza el turno propuesto por un médico o el Administrador.
    CONTROL ESTRICTO:
    - Un médico solo puede actualizar su propia fila.
    - Los domingos están bloqueados para los médicos (solo Admin).
    - Todo turno de 12h asigna automáticamente DES al día siguiente.
    """
    data = request.json or {}
    year = int(data.get("year"))
    month = int(data.get("month"))
    day = int(data.get("day"))
    doctor_id = int(data.get("doctor_id"))
    shift_period = data.get("shift_period")
    service = data.get("service", "")

    current_user_id = session.get("user_id")
    current_user_name = session.get("user_name", "")
    current_user_role = session.get("user_role", "doctor")

    res = record_doctor_shift(
        year=year,
        month=month,
        day=day,
        doctor_id=doctor_id,
        shift_period=shift_period,
        service=service,
        user_id=current_user_id,
        user_name=current_user_name,
        user_role=current_user_role
    )
    if not res["success"]:
        return jsonify({"success": False, "error": res["error"]}), res.get("status_code", 400)

    return jsonify({"success": True, "message": res["message"]})

# -------------------------------------------------------------
# RUTAS DE RESOLUCIÓN DE DISCREPANCIAS (SOLO JEFATURA Y RRHH)
# -------------------------------------------------------------

@app.route("/api/discrepancies/accept-suggestion", methods=["POST"])
def api_accept_suggestion():
    """Aplica la sugerencia alternativa del sistema con 1 clic (Exclusivo Jefatura y RRHH)."""
    if session.get("user_role") not in ("admin", "rrhh"):
        return jsonify({
            "success": False,
            "error": "ACCESO DENEGADO: Únicamente Jefatura Médica y RRHH pueden aprobar o modificar discrepancias."
        }), 403
    data = request.json or {}
    disc_id = int(data.get("discrepancy_id"))
    success = apply_discrepancy_alternative(disc_id)
    if success:
        return jsonify({"success": True, "message": "Alternativa aplicada correctamente."})
    return jsonify({"success": False, "error": "No se pudo aplicar la alternativa."}), 400

@app.route("/api/discrepancies/resolve-custom", methods=["POST"])
def api_resolve_custom():
    """
    Subsanación de discrepancia aprobada por Jefatura o RRHH.
    """
    if session.get("user_role") not in ("admin", "rrhh"):
        return jsonify({
            "success": False,
            "error": "ACCESO DENEGADO: Únicamente Jefatura Médica y RRHH pueden aprobar o modificar discrepancias."
        }), 403
    data = request.json or {}
    disc_id = int(data.get("discrepancy_id"))
    year = int(data.get("year"))
    month = int(data.get("month"))
    
    # Datos de los dos médicos
    doc1_id = int(data.get("doctor1_id"))
    doc1_day = int(data.get("doctor1_day"))
    doc1_period = data.get("doctor1_period")
    doc1_service = data.get("doctor1_service")

    doc2_id = int(data.get("doctor2_id"))
    doc2_day = int(data.get("doctor2_day"))
    doc2_period = data.get("doctor2_period")
    doc2_service = data.get("doctor2_service")

    notes = data.get("notes", "Acuerdo bilateral entre los médicos.")

    with get_connection() as conn:
        # Actualizar médico 1
        conn.execute("""
            INSERT INTO shifts (year, month, day, shift_period, service, doctor_id, is_proposal, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(year, month, day, doctor_id) DO UPDATE SET
                shift_period = excluded.shift_period,
                service = excluded.service,
                updated_at = CURRENT_TIMESTAMP
        """, (year, month, doc1_day, doc1_period, doc1_service, doc1_id))

        # Actualizar médico 2
        conn.execute("""
            INSERT INTO shifts (year, month, day, shift_period, service, doctor_id, is_proposal, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(year, month, day, doctor_id) DO UPDATE SET
                shift_period = excluded.shift_period,
                service = excluded.service,
                updated_at = CURRENT_TIMESTAMP
        """, (year, month, doc2_day, doc2_period, doc2_service, doc2_id))

        # Cerrar discrepancia
        conn.execute("""
            UPDATE discrepancies
            SET status = 'resolved_by_agreement',
                resolved_at = CURRENT_TIMESTAMP,
                resolution_details = ?
            WHERE id = ?
        """, (f"Resuelto por acuerdo mutuo: {notes}", disc_id))

        conn.commit()

        log_audit(session.get("user_id", 0), session.get("user_name", "Médico"), "Subsanación de Discrepancia", notes)

    return jsonify({"success": True, "message": "Discrepancia subsanada por acuerdo bilateral."})

# -------------------------------------------------------------
# RUTAS DE CORTE AL DÍA 10 Y APROBACIÓN OFICIAL
# -------------------------------------------------------------

@app.route("/api/schedule/auto-assign-day10", methods=["POST"])
def api_auto_assign_day10():
    """Ejecuta el corte institucional del día 10 a las 00:00 y autocompleta turnos no sugeridos."""
    data = request.json or {}
    year = int(data.get("year"))
    month = int(data.get("month"))
    
    auto_assign_missing_shifts_day10(year, month)
    sync_discrepancies_db(year, month, get_current_time())
    
    return jsonify({
        "success": True,
        "message": f"Corte aplicado: Turnos autocompletados para el rol de {month}/{year} respetando todas las normativas."
    })

@app.route("/api/schedule/validate-approval", methods=["POST"])
def api_validate_approval():
    """Verifica si el horario está listo para la emisión de 'Horario Aprobado' (Exclusivo Jefatura y RRHH)."""
    if session.get("user_role") not in ("admin", "rrhh"):
        return jsonify({
            "is_approved": False,
            "reason": "ACCESO DENEGADO: Únicamente Jefatura Médica y RRHH pueden aprobar el horario."
        }), 403
    data = request.json or {}
    year = int(data.get("year"))
    month = int(data.get("month"))
    approved_by = session.get("user_name", "Jefatura Médica IPRESS")
    
    result = validate_and_approve_schedule(year, month, approved_by)
    return jsonify(result)

@app.route("/api/schedule/reopen", methods=["POST"])
def api_reopen_schedule():
    """Reabre el horario para permitir modificaciones tras haber sido aprobado (Solo Jefatura o RRHH)."""
    if session.get("user_role") not in ("admin", "rrhh"):
        return jsonify({"success": False, "error": "ACCESO DENEGADO: Solo la Jefatura Médica o el Responsable de RRHH pueden reabrir el horario."}), 403
    data = request.json or {}
    year = int(data.get("year"))
    month = int(data.get("month"))
    reopen_schedule(year, month, session.get("user_name", "Jefatura Médica IPRESS"))
    return jsonify({"success": True, "message": "Horario reabierto con éxito para modificaciones."})

@app.route("/api/schedule/approve-day", methods=["POST"])
def api_approve_day_discrepancy():
    """Aprueba un día con discrepancia de cobertura de servicios (Exclusivo para Jefatura Médica o RRHH)."""
    if session.get("user_role") not in ("admin", "rrhh"):
        return jsonify({
            "success": False,
            "error": "ACCESO DENEGADO: Solo la Jefatura Médica o el Responsable de RRHH pueden aprobar días con discrepancia de cobertura."
        }), 403
    data = request.json or {}
    year = int(data.get("year"))
    month = int(data.get("month"))
    day = int(data.get("day"))
    notes = data.get("notes", "")
    res = approve_day_discrepancy(year, month, day, session.get("user_name", "Jefatura Médica IPRESS"), notes)
    return jsonify(res)

@app.route("/api/audit-logs", methods=["GET"])
def api_get_audit_logs():
    """Retorna los registros de auditoría de los últimos 3 meses (90 días) con filtros opcionales."""
    filter_user = request.args.get("user")
    filter_action = request.args.get("action")
    limit = int(request.args.get("limit", 500))
    logs = get_audit_logs(limit=limit, filter_user=filter_user, filter_action=filter_action)
    return jsonify({
        "success": True,
        "logs": logs,
        "count": len(logs),
        "retention_policy": "90 días (3 meses)"
    })

# -------------------------------------------------------------
# EXPORTACIÓN A EXCEL (.xlsx) Y PROPUESTAS DE MÉDICOS EN EXCEL
# -------------------------------------------------------------

@app.route("/api/proposals/template", methods=["GET"])
def api_download_proposal_template():
    """Descarga el formato Excel (.xlsx) en blanco personalizado para un médico cirujano."""
    year = int(request.args.get("year", datetime.now().year))
    month = int(request.args.get("month", datetime.now().month))
    doctor_id = int(request.args.get("doctor_id", 0))

    if not doctor_id:
        return jsonify({"success": False, "error": "Debe seleccionar un médico cirujano."}), 400

    with get_connection() as conn:
        doc = conn.execute(
            "SELECT id, name, condition, cmp, role FROM doctors WHERE id = ? AND is_active = 1 AND role = 'doctor'",
            (doctor_id,)
        ).fetchone()
        if not doc:
            return jsonify({"success": False, "error": "Médico no encontrado."}), 404
        doc_dict = dict(doc)

    excel_stream = generate_blank_proposal_template(year, month, doc_dict, "IPRESS SALUD PERÚ")
    safe_surname = doc_dict["name"].split(",")[0].strip().replace(" ", "_")
    filename = f"Formato_Propuesta_{safe_surname}_{month:02d}_{year}.xlsx"

    return send_file(
        excel_stream,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename
    )

@app.route("/api/proposals/upload", methods=["POST"])
def api_upload_proposal():
    """
    Recibe el archivo Excel (.xlsx) con la propuesta de un médico, lo procesa exclusivamente en memoria
    (sin guardar el archivo en disco), valida que solo corresponda al médico titular, actualiza el horario
    en pantalla, detecta cruces en la sección de discrepancias y lo registra en el log de cambios.
    """
    year = int(request.form.get("year", datetime.now().year))
    month = int(request.form.get("month", datetime.now().month))
    doctor_id = int(request.form.get("doctor_id", 0))

    if not doctor_id:
        return jsonify({"success": False, "error": "Debe seleccionar su nombre de médico titular."}), 400

    if "file" not in request.files:
        return jsonify({"success": False, "error": "Debe adjuntar su archivo Excel (.xlsx) de propuesta."}), 400

    uploaded_file = request.files["file"]
    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"success": False, "error": "Archivo vacío o no seleccionado."}), 400

    file_bytes = uploaded_file.read()
    res = parse_and_import_doctor_proposal(
        file_bytes=file_bytes,
        expected_year=year,
        expected_month=month,
        expected_doctor_id=doctor_id
    )
    if not res["success"]:
        return jsonify({"success": False, "error": res["error"]}), res.get("status_code", 400)

    return jsonify(res), 200

@app.route("/api/export-excel", methods=["GET"])
def api_export_excel():
    """Descarga el rol mensual en formato oficial de Excel."""
    year = int(request.args.get("year", datetime.now().year))
    month = int(request.args.get("month", datetime.now().month))
    
    excel_stream = generate_excel_schedule(year, month, "IPRESS SALUD PERÚ")
    filename = f"Rol_Medicos_IPRESS_{month:02d}_{year}.xlsx"
    
    return send_file(
        excel_stream,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename
    )

# -------------------------------------------------------------
# SIMULADOR DE TIEMPO (Para auditar el corte del Día 10 y plazo de 72h)
# -------------------------------------------------------------

@app.route("/api/sim-time", methods=["GET", "POST"])
def api_sim_time():
    """Permite fijar una fecha/hora simulada para verificar el comportamiento de plazos."""
    global SIMULATED_TIME
    if request.method == "POST":
        data = request.json or {}
        if data.get("reset"):
            SIMULATED_TIME["enabled"] = False
            SIMULATED_TIME["dt"] = datetime.now()
        else:
            sim_dt_str = data.get("datetime")
            if sim_dt_str:
                SIMULATED_TIME["enabled"] = True
                SIMULATED_TIME["dt"] = datetime.fromisoformat(sim_dt_str)
    
    return jsonify({
        "simulated": SIMULATED_TIME["enabled"],
        "current_time": get_current_time().isoformat(),
        "is_day_10_passed": get_current_time().day >= 10
    })

# -------------------------------------------------------------
# VISTA PRINCIPAL
# -------------------------------------------------------------

@app.route("/")
def index():
    """Renderiza la interfaz web principal del aplicativo."""
    return render_template("index.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("FLASK_DEBUG", "True").lower() in ("true", "1")
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
