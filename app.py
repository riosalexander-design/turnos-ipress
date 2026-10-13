"""
app.py
Servidor Flask y API REST para el Sistema Web de Programación de Turnos de la IPRESS.
Incluye autenticación por médico, control de acceso RBAC, gestión de propuestas,
detección y conciliación de discrepancias con plazo de 72h (máx día 10 00:00h) y exportación a Excel.
"""

import os
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session, send_file
from database import init_db, get_connection, hash_pin, log_audit
from holidays_pe import get_month_info
from scheduler_engine import (
    get_doctor_quota_summary,
    get_daily_coverage,
    detect_discrepancies,
    sync_discrepancies_db,
    apply_discrepancy_alternative,
    auto_assign_missing_shifts_day10,
    validate_and_approve_schedule,
    calculate_deadline
)
from exporter import generate_excel_schedule

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
        doc = conn.execute("SELECT id, name, condition, cmp, role FROM doctors WHERE id = ?", (user_id,)).fetchone()
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
                "role": doc["role"]
            }
        })

@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    """Inicio de sesión con CMP y PIN."""
    data = request.json or {}
    cmp_num = (data.get("cmp") or "").strip()
    pin = (data.get("pin") or "").strip()

    if not cmp_num or not pin:
        return jsonify({"success": False, "error": "Debe ingresar su CMP y PIN."}), 400

    with get_connection() as conn:
        doc = conn.execute("SELECT id, name, condition, cmp, pin_hash, role FROM doctors WHERE cmp = ? AND is_active = 1", (cmp_num,)).fetchone()
        if not doc:
            return jsonify({"success": False, "error": "Número de CMP o usuario no encontrado."}), 401
        
        if doc["pin_hash"] != hash_pin(pin):
            return jsonify({"success": False, "error": "PIN / Contraseña incorrecta."}), 401
        
        session["user_id"] = doc["id"]
        session["user_name"] = doc["name"]
        session["user_role"] = doc["role"]
        
        log_audit(doc["id"], doc["name"], "Inicio de Sesión", f"Acceso correcto con CMP {cmp_num}")
        return jsonify({
            "success": True,
            "user": {
                "id": doc["id"],
                "name": doc["name"],
                "condition": doc["condition"],
                "cmp": doc["cmp"],
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
    """Retorna la lista de médicos activos de la IPRESS."""
    with get_connection() as conn:
        docs = conn.execute("""
            SELECT id, name, condition, cmp, role
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

    return jsonify({
        "year": year,
        "month": month,
        "current_time": curr_time.isoformat(),
        "deadline_datetime": deadline_dt.isoformat(),
        "deadline_str": deadline_str,
        "quota_summary": quota_data,
        "coverage": coverage_data,
        "discrepancies": disc_list,
        "approval_status": approval_status
    })

@app.route("/api/shifts/update", methods=["POST"])
def api_update_shift():
    """
    Guarda o actualiza el turno propuesto por un médico.
    CONTROL ESTRICTO: Un médico solo puede actualizar su propia fila a menos que sea Admin.
    """
    data = request.json or {}
    year = int(data.get("year"))
    month = int(data.get("month"))
    day = int(data.get("day"))
    doctor_id = int(data.get("doctor_id"))
    shift_period = data.get("shift_period") # 'M', 'T', '12h', 'DES', 'LIBRE', 'CLEAR'
    service = data.get("service", "")       # 'TU', 'CE', 'TS', 'TU+CE', etc.

    # Verificación de identidad y permisos
    current_user_id = session.get("user_id")
    current_user_role = session.get("user_role")

    if not current_user_id:
        return jsonify({"success": False, "error": "Debe iniciar sesión para realizar cambios."}), 401

    if current_user_role != "admin" and current_user_id != doctor_id:
        return jsonify({
            "success": False,
            "error": "ACCESO DENEGADO: No tiene autorización para modificar la propuesta de otro médico."
        }), 403

    with get_connection() as conn:
        doc = conn.execute("SELECT name FROM doctors WHERE id = ?", (doctor_id,)).fetchone()
        doc_name = doc["name"] if doc else f"Dr. ID {doctor_id}"

        if shift_period in ("CLEAR", "", None):
            conn.execute("DELETE FROM shifts WHERE year = ? AND month = ? AND day = ? AND doctor_id = ?",
                         (year, month, day, doctor_id))
            log_audit(current_user_id, session.get("user_name", ""), "Eliminó turno", f"Día {day} para {doc_name}", conn=conn)
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
            log_audit(current_user_id, session.get("user_name", ""), "Actualizó turno",
                      f"Día {day}: {shift_period} ({service}) para {doc_name}", conn=conn)

        conn.commit()

    # Sincronizar discrepancias tras el cambio
    sync_discrepancies_db(year, month, get_current_time())

    return jsonify({"success": True})

# -------------------------------------------------------------
# RUTAS DE RESOLUCIÓN DE DISCREPANCIAS
# -------------------------------------------------------------

@app.route("/api/discrepancies/accept-suggestion", methods=["POST"])
def api_accept_suggestion():
    """Aplica la sugerencia alternativa del sistema con 1 clic."""
    data = request.json or {}
    disc_id = int(data.get("discrepancy_id"))
    success = apply_discrepancy_alternative(disc_id)
    if success:
        return jsonify({"success": True, "message": "Alternativa aplicada correctamente."})
    return jsonify({"success": False, "error": "No se pudo aplicar la alternativa."}), 400

@app.route("/api/discrepancies/resolve-custom", methods=["POST"])
def api_resolve_custom():
    """
    Subsanación acordada bilateralmente dentro del plazo de 72 horas:
    Ambos médicos ingresan sus modificaciones (fecha, turno y servicio).
    """
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
    """Verifica si el horario está listo para la emisión de 'Horario Aprobado'."""
    data = request.json or {}
    year = int(data.get("year"))
    month = int(data.get("month"))
    approved_by = session.get("user_name", "Jefatura Médica IPRESS")
    
    result = validate_and_approve_schedule(year, month, approved_by)
    return jsonify(result)

# -------------------------------------------------------------
# EXPORTACIÓN A EXCEL (.xlsx)
# -------------------------------------------------------------

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
