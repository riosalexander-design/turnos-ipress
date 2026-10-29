"""
streamlit_app.py
Aplicativo Web en Streamlit para la Programación de Turnos de Médicos Cirujanos en la IPRESS.
Compatible 100% con Streamlit Community Cloud (share.streamlit.io).
"""

import io
import os
import importlib
from datetime import datetime
import pandas as pd
import streamlit as st
import database
import holidays_pe
importlib.reload(database)
importlib.reload(holidays_pe)
import scheduler_engine
importlib.reload(scheduler_engine)
import exporter
importlib.reload(exporter)

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
    approve_all_day_discrepancies,
    revoke_day_approval,
    get_month_appointment_locks,
    check_shift_modification_allowed,
    sync_appointment_locks_from_citas_db,
    SERVICE_LABELS
)
from exporter import (
    generate_excel_schedule,
    generate_blank_proposal_template,
    parse_and_import_doctor_proposal,
    generate_proposal_receipt_excel,
    generate_proposal_receipt_html
)

# Componente interactivo de matriz mensual (permite hacer clic en una fecha o celda para abrir pop-up)
interactive_matrix_component = st.components.v2.component(
    "ipress_interactive_matrix",
    html="<div id='matrix-root'></div>",
    js="""
    export default function(component) {
        const { data, setTriggerValue, parentElement } = component;
        const root = parentElement.querySelector('#matrix-root');
        if (!root) return;
        root.innerHTML = data.html || '';
        if (data.is_editable) {
            const clickableCells = root.querySelectorAll('[data-clickable="1"]');
            clickableCells.forEach(el => {
                el.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const day = parseInt(el.getAttribute('data-day'), 10);
                    const docIdAttr = el.getAttribute('data-doc-id');
                    const docId = docIdAttr ? parseInt(docIdAttr, 10) : null;
                    setTriggerValue('cell_clicked', {
                        day: day,
                        doc_id: docId,
                        ts: Date.now()
                    });
                });
            });
        }
    }
    """
)

# Inicializar Base de Datos
init_db()

# Configuración de página
st.set_page_config(
    page_title="IPRESS - Programación Asistencial Médica",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Estilos CSS especializados para badges médicos y matriz asistencial
st.markdown("""
<style>
    .metric-card {
        background-color: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 10px;
        padding: 12px 16px;
        text-align: center;
    }
    .badge-shift {
        display: inline-block;
        padding: 2px 5px;
        border-radius: 6px;
        font-size: 10.5px;
        font-weight: bold;
        text-align: center;
        margin: 1px;
        line-height: 1.15;
    }
    .badge-12h { background-color: #bae6fd; color: #0369a1; border: 1px solid #7dd3fc; }
    .badge-DES { background-color: #ede9fe; color: #5b21b6; border: 1px solid #c4b5fd; font-style: italic; }
    .badge-LIBRE { background-color: #f5f3ff; color: #6b21a8; border: 1px solid #ddd6fe; font-weight: 600; }
    .badge-M-TU, .badge-M-CE, .badge-M-TS { background-color: #fef08a; color: #713f12; border: 1px solid #facc15; }
    .badge-T-TU, .badge-T-CE { background-color: #bbf7d0; color: #14532d; border: 1px solid #86efac; }
    .badge-empty { color: #cbd5e1; }
    .table-matrix {
        width: 100%;
        border-collapse: collapse;
        font-size: 11.5px;
        text-align: center;
    }
    .table-matrix th, .table-matrix td {
        border: 1px solid #cbd5e1;
        padding: 4px 2px;
        min-width: 34px;
    }
    .table-matrix th {
        background-color: #1e293b;
        color: white;
    }
    .table-matrix th:first-child, .table-matrix td:first-child {
        position: sticky;
        left: 0;
        background-color: #f8fafc;
        z-index: 2;
        box-shadow: 2px 0 4px -2px rgba(0,0,0,0.15);
    }
    .table-matrix tr:first-child th:first-child, .table-matrix tr:nth-child(2) th:first-child {
        background-color: #1e293b;
        color: white;
        z-index: 3;
    }
    @media (max-width: 768px) {
        .badge-shift {
            font-size: 9.5px;
            padding: 2px 3px;
        }
        .table-matrix th, .table-matrix td {
            min-width: 30px;
            padding: 3px 1px;
            font-size: 10px;
        }
    }
    .col-sun { background-color: #e2e8f0; color: #64748b; }
    .col-sat { background-color: #f8fafc; }
    .col-hol { background-color: #ffe4e6; }
    .row-active { background-color: #eff6ff !important; font-weight: bold; }
    .cov-ok { background-color: #dcfce7; color: #166534; font-weight: bold; font-size: 10px; }
    .cov-adm { background-color: #fef9c3; color: #854d0e; font-weight: bold; font-size: 10px; }
    .cov-err { background-color: #fee2e2; color: #991b1b; font-weight: bold; font-size: 10px; }
</style>
""", unsafe_allow_html=True)

# -------------------------------------------------------------------
# GESTIÓN DE SESIÓN Y NOTIFICACIONES DE CONFIRMACIÓN
# -------------------------------------------------------------------
if "user" not in st.session_state:
    st.session_state["user"] = None
if "shift_saved_confirmation" not in st.session_state:
    st.session_state["shift_saved_confirmation"] = None

def get_all_doctors():
    with get_connection() as conn:
        return conn.execute("SELECT id, name, condition, cmp, username, role FROM doctors WHERE is_active = 1 ORDER BY condition DESC, name ASC").fetchall()

doctors_list = [dict(d) for d in get_all_doctors()]
doctors_only = [d for d in doctors_list if d["role"] == "doctor"]
managers_only = [d for d in doctors_list if d["role"] in ("admin", "rrhh")]

# Si en sesión había un médico con rol 'doctor', revocar la sesión
if st.session_state["user"] is not None and st.session_state["user"].get("role") not in ("admin", "rrhh"):
    st.session_state["user"] = None

# -------------------------------------------------------------------
# SIDEBAR: AUTENTICACIÓN (EXCLUSIVO JEFATURA Y RRHH) Y FILTROS
# -------------------------------------------------------------------
with st.sidebar:
    st.title("🏥 IPRESS Salud")
    st.caption("Sector Asistencial - Programación Mensual")
    st.markdown("---")

    meses_nombres = {
        1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
        7: "Julio", 8: "Agosto", 9: "Setiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre"
    }
    col_y, col_m = st.columns(2)
    with col_y:
        selected_year = st.selectbox("Año", [2026, 2027], index=0)
    with col_m:
        selected_month = st.selectbox("Mes", list(meses_nombres.keys()), index=9, format_func=lambda x: meses_nombres[x])

    st.markdown("---")
    st.subheader("🔐 Acceso Jefatura y RRHH")
    st.caption("Los permisos de ingreso directo al sistema están habilitados únicamente para **Jefatura Médica** y **RRHH**.")

    if st.session_state["user"] is None:
        mgr_map = {
            d["id"]: f"{d['name']} ({'JEFATURA' if d['role']=='admin' else 'RRHH'})"
            for d in managers_only
        }
        
        sel_doc_id = st.selectbox(
            "Perfil de Gestión:",
            list(mgr_map.keys()),
            format_func=lambda did: mgr_map[did]
        )
        input_pwd = st.text_input("Ingresa tu Clave de acceso:", type="password").strip()

        if st.button("Iniciar Sesión (Jefatura / RRHH)", use_container_width=True, type="primary"):
            if not input_pwd:
                st.error("Debes ingresar tu clave.")
            else:
                with get_connection() as conn:
                    doc_db = conn.execute("""
                        SELECT id, name, condition, cmp, username, pin_hash, role
                        FROM doctors
                        WHERE id = ? AND is_active = 1 AND role IN ('admin', 'rrhh')
                    """, (sel_doc_id,)).fetchone()
                    if doc_db and doc_db["pin_hash"] == hash_pin(input_pwd):
                        st.session_state["user"] = dict(doc_db)
                        log_audit(doc_db["id"], doc_db["name"], "Inicio de Sesión", f"Acceso correcto de {doc_db['name']}")
                        st.rerun()
                    else:
                        st.error("Clave incorrecta o perfil no autorizado.")
    else:
        u = st.session_state["user"]
        st.success(f"**{u['name']}**")
        st.caption(f"CMP: `{u['cmp']}` | Rol: `{u['role'].upper()}` | Condición: `{u['condition']}`")

        if st.button("Cerrar Sesión", use_container_width=True):
            st.session_state["user"] = None
            st.rerun()

        with st.expander("🔑 Cambiar mi Clave"):
            old_p = st.text_input("Clave Actual:", type="password", key="old_pin_sb")
            new_p = st.text_input("Nueva Clave (mín 4 caracteres):", type="password", key="new_pin_sb")
            conf_p = st.text_input("Confirmar Nueva Clave:", type="password", key="conf_pin_sb")
            if st.button("Guardar Nueva Clave", use_container_width=True):
                if len(new_p) < 4:
                    st.error("La nueva clave debe tener al menos 4 caracteres.")
                elif new_p != conf_p:
                    st.error("Las claves no coinciden.")
                else:
                    with get_connection() as conn:
                        chk = conn.execute("SELECT pin_hash FROM doctors WHERE id = ?", (u["id"],)).fetchone()
                        if chk and chk["pin_hash"] == hash_pin(old_p):
                            conn.execute("UPDATE doctors SET pin_hash = ? WHERE id = ?", (hash_pin(new_p), u["id"]))
                            conn.commit()
                            log_audit(u["id"], u["name"], "Cambio de Clave", "Clave actualizada exitosamente", conn=conn)
                            st.success("¡Clave actualizada con éxito!")
                        else:
                            st.error("La clave actual no es correcta.")

# -------------------------------------------------------------------
# DATOS DEL MES Y REGLAS
# -------------------------------------------------------------------
month_info = get_month_info(selected_year, selected_month)
quota = month_info["monthly_quota"]
days = month_info["days"]

quota_summary = get_doctor_quota_summary(selected_year, selected_month)
doc_summary = quota_summary["doctors_summary"]
coverage = get_daily_coverage(selected_year, selected_month)
appointment_locks = get_month_appointment_locks(selected_year, selected_month)

sync_discrepancies_db(selected_year, selected_month)
with get_connection() as conn:
    discrepancies = conn.execute("""
        SELECT d.*, doc1.name as doc1_name, doc2.name as doc2_name
        FROM discrepancies d
        JOIN doctors doc1 ON d.doctor1_id = doc1.id
        JOIN doctors doc2 ON d.doctor2_id = doc2.id
        WHERE d.year = ? AND d.month = ?
        ORDER BY d.day ASC, d.id ASC
    """, (selected_year, selected_month)).fetchall()
    status_row = conn.execute("SELECT * FROM schedule_status WHERE year = ? AND month = ?", (selected_year, selected_month)).fetchone()
    approval_status = dict(status_row) if status_row else {"is_approved": 0}

open_discs = [d for d in discrepancies if d["status"] == "open"]
service_alert_days = [d for d, c in coverage.items() if c["has_service_alert"]]
unapproved_service_alert_days = [d for d, c in coverage.items() if c["has_service_alert"] and not c["is_admin_approved_day"]]

is_admin_or_rrhh = (st.session_state["user"] is not None and st.session_state["user"].get("role") in ("admin", "rrhh"))
is_admin_user = is_admin_or_rrhh

# -------------------------------------------------------------------
# VENTANA POP-UP AL HACER CLIC EN UNA FECHA / CELDA (@st.dialog)
# Exclusiva para Jefatura Médica y RRHH: pregunta si desea modificar,
# solicita confirmación del deseo de modificación y graba el cambio.
# -------------------------------------------------------------------
@st.dialog("🗓️ Confirmación de Modificación de Horario (Jefatura / RRHH)", width="large")
def open_shift_recording_modal(default_day: int = None, default_doc_id: int = None):
    u = st.session_state["user"]
    if not u or u.get("role") not in ("admin", "rrhh"):
        st.error("🔒 Acceso denegado: Únicamente Jefatura Médica y RRHH pueden modificar el horario.")
        return

    role_label = "Jefatura Médica" if u.get("role") == "admin" else "Recursos Humanos"
    doc_ids_list = [doc["id"] for doc in doctors_only]
    idx_doc_default = doc_ids_list.index(default_doc_id) if (default_doc_id in doc_ids_list) else 0

    selectable_days = [d["day"] for d in days]
    idx_day_default = selectable_days.index(default_day) if (default_day in selectable_days) else 0

    day_obj = days[selectable_days[idx_day_default] - 1]
    st.warning(
        f"❓ **¿Desea modificar la programación asistencial correspondiente a la fecha "
        f"Día {day_obj['day']:02d}/{selected_month:02d}/{selected_year} ({day_obj['weekday_name']})?**"
    )
    st.caption(f"Sesión activa: **{u['name']}** ({role_label}) — Seleccione el médico, el nuevo turno y confirme su deseo de modificación para grabar el cambio.")

    c_sel_doc, c_sel_day = st.columns(2)
    with c_sel_doc:
        target_doc_id = st.selectbox(
            "1. Médico Cirujano:",
            doc_ids_list,
            index=idx_doc_default,
            format_func=lambda did: next((f"{d['name']} ({d['condition']} - CMP: {d['cmp']})" for d in doctors_only if d["id"] == did), str(did)),
            key="dlg_admin_doc"
        )
    with c_sel_day:
        edit_day = st.selectbox(
            f"2. Fecha ({month_info['month_name']} {selected_year}):",
            selectable_days,
            index=idx_day_default,
            format_func=lambda d_num: (
                f"Día {d_num:02d} — {days[d_num - 1]['weekday_name']}"
                f"{' (FERIADO: ' + days[d_num - 1]['holiday_name'] + ')' if days[d_num - 1]['is_holiday'] else ''}"
                f"{' [DOMINGO]' if days[d_num - 1]['is_sunday'] else ''}"
            ),
            key="dlg_day_select"
        )

    target_doc = next((d for d in doctors_only if d["id"] == target_doc_id), doctors_only[0])
    target_id = target_doc["id"]

    cur_shift = doc_summary.get(target_id, {}).get("days", {}).get(edit_day)
    if cur_shift and cur_shift.get("shift_period") != "LIBRE":
        st.info(f"📌 Turno actualmente registrado para **{target_doc['name']}** el Día {edit_day:02d}: **{cur_shift['shift_period']} ({cur_shift['service']})**")
    else:
        st.info(f"📌 Actualmente **{target_doc['name']}** figura como **Libre** el Día {edit_day:02d}.")

    cell_lock = appointment_locks.get((target_id, edit_day))
    if cell_lock and cell_lock.get("total_appointments", 0) > 0:
        locked_parts = []
        if cell_lock.get("M"):
            locked_parts.append(f"**Turno Mañana (CE)**: {cell_lock['M']['appointments_count']} paciente(s) citado(s)")
        if cell_lock.get("T"):
            locked_parts.append(f"**Turno Tarde (CE)**: {cell_lock['T']['appointments_count']} paciente(s) citado(s)")
        st.error(
            f"🔒 **TURNO BLOQUEADO POR CITAS DE PACIENTES (`citascsjp2`):** "
            f"Este médico ya cuenta con citas agendadas el Día {edit_day:02d}/{selected_month:02d}/{selected_year} "
            f"({', '.join(locked_parts)}). Por seguridad asistencial, **no se permite modificar ni eliminar el turno de Consultorio (CE) donde ya existen pacientes citados**."
        )

    col_p, col_s = st.columns([1.2, 1.8])
    with col_p:
        period_choice = st.radio(
            "3. Nuevo Período / Jornada:",
            [
                "Mañana (6h - Amarillo)",
                "Tarde (6h - Verde)",
                "12 Horas (Mañana + Tarde - Celeste)",
                "Descanso de Guardia (DES - Violeta)",
                "Día Libre (Libre - Violeta)"
            ],
            key="dlg_period_radio"
        )

    with col_s:
        if "Mañana (6h" in period_choice:
            service_choice = st.radio(
                "4. Servicio Asistencial (Mañana):",
                [
                    "Tópico de Urgencias (TU)",
                    "Consultorio de Medicina (CE)",
                    "Telesalud/TBC/MCI (TS)"
                ],
                key="dlg_srv_m"
            )
            sp_val = "M"
            srv_val = "TU" if "(TU)" in service_choice else ("CE" if "(CE)" in service_choice else "TS")
        elif "Tarde (6h" in period_choice:
            service_choice = st.radio(
                "4. Servicio Asistencial (Tarde):",
                [
                    "Tópico de Urgencias (TU)",
                    "Consultorio de Medicina (CE)"
                ],
                key="dlg_srv_t"
            )
            sp_val = "T"
            srv_val = "TU" if "(TU)" in service_choice else "CE"
        elif "12 Horas" in period_choice:
            service_choice = st.radio(
                "4. Servicio Combinado de 12 Horas (Mañana + Tarde):",
                [
                    "Mañana Telesalud/TBC/MCI + Tarde Tópico de Urgencias (TS+TU)",
                    "Mañana Tópico de Urgencias + Tarde Consultorio de Medicina (TU+CE)",
                    "Mañana Consultorio de Medicina + Tarde Tópico de Urgencias (CE+TU)",
                    "Mañana Telesalud/TBC/MCI + Tarde Consultorio de Medicina (TS+CE)",
                    "Mañana Tópico de Urgencias + Tarde Tópico de Urgencias (TU+TU)",
                    "Mañana Consultorio de Medicina + Tarde Consultorio de Medicina (CE+CE)"
                ],
                key="dlg_srv_12h"
            )
            sp_val = "12h"
            for code in ("TS+TU", "TU+CE", "CE+TU", "TS+CE", "TU+TU", "CE+CE"):
                if f"({code})" in service_choice:
                    srv_val = code
                    break
            st.caption("ℹ️ Al grabar un turno de 12 horas, el día siguiente sin turno recibirá descanso (`DES`).")
        elif "DES" in period_choice:
            st.info("Descanso obligatorio de guardia (`DES` - Violeta).")
            sp_val = "DES"
            srv_val = "DES"
        else:
            st.warning("La fecha quedará asignada como **Libre** (Violeta).")
            sp_val = "CLEAR"
            srv_val = ""

    st.markdown("---")
    allowed_mod_preview, lock_msg_preview = check_shift_modification_allowed(
        year=selected_year,
        month=selected_month,
        day=edit_day,
        doctor_id=target_id,
        new_shift_period=sp_val,
        new_service=srv_val
    )
    if not allowed_mod_preview:
        st.error(lock_msg_preview)

    confirm_mod = st.checkbox(
        f"✅ Confirmo que deseo modificar el horario de {target_doc['name']} en la fecha Día {edit_day:02d}/{selected_month:02d}/{selected_year}.",
        value=False,
        disabled=not allowed_mod_preview,
        key="dlg_confirm_chk"
    )

    b_cancel, b_save = st.columns([1, 2])
    with b_cancel:
        if st.button("Cancelar", use_container_width=True):
            st.rerun()
    with b_save:
        if st.button("💾 Confirmar y Grabar Cambio", type="primary", use_container_width=True, disabled=(not confirm_mod or not allowed_mod_preview)):
            res = record_doctor_shift(
                year=selected_year,
                month=selected_month,
                day=edit_day,
                doctor_id=target_id,
                shift_period=sp_val,
                service=srv_val,
                user_id=u["id"],
                user_name=u["name"],
                user_role=u["role"]
            )
            if res["success"]:
                st.session_state["shift_saved_confirmation"] = res["message"]
                st.rerun()
            else:
                st.error(res["error"])

# -------------------------------------------------------------------
# ENCABEZADO Y CONFIRMACIÓN DE GRABACIÓN
# -------------------------------------------------------------------
st.title(f"Programación Asistencial: {month_info['month_name']} {selected_year}")

# Advertencia Oficial del Día 10 de cada mes
today_dt = datetime.now()
if today_dt.day >= 10:
    st.warning(
        "⚠️ **ADVERTENCIA INSTITUCIONAL (Corte Día 10 de cada mes):**\n\n"
        "Ha finalizado el plazo reglamentario para el registro de auto-propuestas de turnos por parte de los médicos cirujanos. "
        "La **Jefatura Médica** o el **Responsable de Recursos Humanos** debe proceder a **completar manualmente** el rol de turnos asistenciales del mes siguiente, "
        "verificando el cumplimiento de la meta mensual de turnos por médico, la dotación mínima ininterrumpida de al menos **2 médicos por turno (Mañana y Tarde)** y los descansos obligatorios de guardia (DES)."
    )

# Mostrar confirmación si se acaba de grabar un turno y cerrar la ventana
if st.session_state.get("shift_saved_confirmation"):
    msg_ok = st.session_state["shift_saved_confirmation"]
    st.toast("✅ El turno fue grabado correctamente", icon="✅")
    st.success(f"✅ **CONFIRMACIÓN DE GRABACIÓN:** {msg_ok} *(Visible para todos los médicos y la Administración)*")
    st.session_state["shift_saved_confirmation"] = None

# Banner de Estado General
total_month_shifts = sum(d.get("total_equivalent_shifts", 0) for d in doc_summary.values())

if approval_status.get("is_approved"):
    st.success(f"🏆 **HORARIO APROBADO OFICIAL IPRESS** | Aprobado por: {approval_status.get('approved_by')} | Código de Integridad: `#{approval_status.get('approval_hash')}`")
elif total_month_shifts == 0:
    st.info(
        f"📋 **Mes en Fase Inicial de Programación ({month_info['month_name']} {selected_year}):** Aún no se han registrado turnos para este mes. "
        f"Cada médico cirujano, la Jefatura Médica o el Responsable de RRHH pueden iniciar sesión y pulsar **'➕ Grabar / Modificar Turno'** para registrar los turnos."
    )
elif len(unapproved_service_alert_days) > 0:
    st.warning(
        f"⚠️ **Alerta de Cobertura de Servicios:** Existen **{len(unapproved_service_alert_days)} día(s)** en los que no se cubren todos los servicios requeridos "
        f"(Mañana: Tópico de Urgencias `TU`, Consultorio de Medicina `CE` y Telesalud/TBC/MCI `TS`; Tarde: Tópico de Urgencias `TU` y Consultorio de Medicina `CE`). "
        f"**Solo la Jefatura Médica o RRHH pueden aprobar los días en los que exista esta discrepancia.**"
    )
elif len(open_discs) > 0:
    st.warning(f"⚠️ **Atención: Hay {len(open_discs)} discrepancia(s) de colisión entre médicos.**")
else:
    st.info(f"ℹ️ **Rol Mensual Activo:** Meta del mes: **{quota} turnos** por médico (25 base - {month_info['holidays_mon_to_sat_count']} feriados de lunes a sábado). Todos los servicios cubiertos o aprobados.")

# Indicadores en Columnas
kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)
with kpi1:
    st.metric("Meta del Mes", f"{quota} turnos", f"25 base - {month_info['holidays_mon_to_sat_count']} feriado(s) lun-sáb")
with kpi2:
    if st.session_state["user"] and st.session_state["user"]["role"] == "doctor":
        my_s = doc_summary.get(st.session_state["user"]["id"], {})
        my_tot = my_s.get("total_equivalent_shifts", 0)
        st.metric("Mi Programación", f"{my_tot} / {quota}", f"{my_s.get('status', 'Incompleto')}")
    elif is_admin_or_rrhh:
        complete_cnt = sum(1 for d in doc_summary.values() if d["total_equivalent_shifts"] == quota)
        st.metric("Médicos en Meta", f"{complete_cnt} / {len(doctors_only)}", "Supervisión Jefatura/RRHH")
    else:
        st.metric("Mi Programación", "-- / --", "Inicia sesión")
with kpi3:
    solano_doc = next((d for d in doc_summary.values() if d["is_solano"]), None)
    if solano_doc:
        st.metric("Dr. Solano (12h)", f"{solano_doc['count_12h']} / 5 turnos", "Miércoles 12h + DES")
    else:
        st.metric("Turnos 12 Horas", "--", "Regla mensual")
with kpi4:
    st.metric("Días con Alerta Servicios", f"{len(unapproved_service_alert_days)} pendientes", f"{len(service_alert_days)} totales en el mes")
with kpi5:
    st.metric("Domingos", "Bloqueados (Médicos)", "Habilitados para Jefatura/RRHH")

st.markdown("---")

# -------------------------------------------------------------------
# SECCIÓN DE PROPUESTA DE HORARIO PARA MÉDICOS (FORMATO EXCEL EN BLANCO + SUBIDA EN MEMORIA)
# -------------------------------------------------------------------
with st.expander("📄 Propuesta de Horario para Médicos Cirujanos (Descargar Formato en Blanco / Subir Propuesta)", expanded=not is_admin_or_rrhh):
    st.markdown(
        "**Normativa vigente:** Los médicos cirujanos ya no tienen permiso de ingreso directo. "
        "Para registrar su propuesta mensual de turnos deben **descargar su formato en blanco personalizado (`.xlsx`)** "
        "y luego **subir el archivo con su propuesta**. Un médico **solo puede presentar la propuesta que le corresponde a él** "
        "(no puede proponer el horario de otro médico). Una vez procesada en pantalla y en el log de cambios, **el archivo no se guarda en el servidor**."
    )
    prop_col1, prop_col2 = st.columns([1.3, 1.7])
    with prop_col1:
        prop_doc_id = st.selectbox(
            "1. Seleccione su Nombre (Médico Titular):",
            [d["id"] for d in doctors_only],
            format_func=lambda did: next((f"{d['name']} (CMP: {d['cmp']} - {d['condition']})" for d in doctors_only if d["id"] == did), str(did)),
            key="prop_doc_select"
        )
        prop_doc_obj = next(d for d in doctors_only if d["id"] == prop_doc_id)
        blank_template_stream = generate_blank_proposal_template(selected_year, selected_month, prop_doc_obj, "IPRESS SALUD PERÚ")
        safe_surname = prop_doc_obj["name"].split(",")[0].strip().replace(" ", "_")
        st.download_button(
            label=f"📥 2. Descargar Formato en Blanco ({prop_doc_obj['name'].split(',')[0]})",
            data=blank_template_stream.getvalue(),
            file_name=f"Formato_Propuesta_{safe_surname}_{selected_month:02d}_{selected_year}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            type="secondary"
        )
    with prop_col2:
        uploaded_prop_file = st.file_uploader(
            f"📤 3. Subir archivo Excel (.xlsx) con la propuesta de {prop_doc_obj['name']}:",
            type=["xlsx"],
            key=f"prop_uploader_{selected_year}_{selected_month}_{prop_doc_id}"
        )
        if uploaded_prop_file is not None:
            if st.button("✅ Procesar y Reflejar Mi Propuesta en el Horario", type="primary", use_container_width=True):
                file_bytes = uploaded_prop_file.getvalue()
                res_imp = parse_and_import_doctor_proposal(
                    file_bytes=file_bytes,
                    expected_year=selected_year,
                    expected_month=selected_month,
                    expected_doctor_id=prop_doc_id
                )
                if res_imp["success"]:
                    st.session_state["shift_saved_confirmation"] = res_imp["message"]
                    st.session_state["last_proposal_receipt"] = res_imp.get("receipt")
                    st.rerun()
                else:
                    st.error(res_imp["error"])

    # Mostrar Constancia Oficial de Recepción de Propuesta (para fotografiar o descargar)
    if st.session_state.get("last_proposal_receipt"):
        rcpt = st.session_state["last_proposal_receipt"]
        shifts_badges_html = " ".join(
            f"<span style='display:inline-block;background:#e0f2fe;color:#0369a1;border:1px solid #bae6fd;"
            f"border-radius:5px;padding:2px 7px;margin:2px;font-size:11.5px;font-weight:bold;'>"
            f"Día {s['day']:02d} ({s['weekday_short']}): {s['label']}</span>"
            for s in rcpt.get("shifts_list", [])
        ) or "<em>Sin turnos registrados</em>"

        st.markdown("---")
        st.markdown(
            f"""
            <div style="border: 2px solid #166534; background-color: #f0fdf4; border-radius: 12px; padding: 16px 20px; margin-bottom: 12px;">
                <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; border-bottom:1px solid #bbf7d0; padding-bottom:8px; margin-bottom:10px;">
                    <div>
                        <span style="font-size:15px; font-weight:800; color:#14532d;">✅ CONSTANCIA OFICIAL DE RECEPCIÓN DE PROPUESTA DE HORARIO</span><br>
                        <span style="font-size:12px; color:#166534;">📷 <em>Puede tomar captura de pantalla o fotografía de esta constancia, o descargar su cargo abajo.</em></span>
                    </div>
                    <div style="background:#dcfce7; border:1px solid #86efac; padding:6px 12px; border-radius:8px; text-align:right;">
                        <span style="font-size:11px; color:#166534; font-weight:bold;">CÓDIGO DE CONSTANCIA</span><br>
                        <span style="font-size:13.5px; font-weight:900; color:#14532d; font-family:monospace;">{rcpt['receipt_code']}</span>
                    </div>
                </div>
                <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap:8px; font-size:12.5px; color:#1e293b; margin-bottom:10px;">
                    <div><strong>Médico Titular:</strong> {rcpt['doctor_name']}</div>
                    <div><strong>CMP / Condición:</strong> {rcpt['doctor_cmp']} ({rcpt.get('doctor_condition', '')})</div>
                    <div><strong>Mes de Programación:</strong> {rcpt['month_name']} {rcpt['year']}</div>
                    <div><strong>Fecha y Hora Exacta:</strong> {rcpt['timestamp_str']}</div>
                    <div><strong>Turnos Equivalentes / Meta:</strong> {rcpt['total_equivalent_shifts']} / {rcpt['monthly_quota']} turnos (6h: {rcpt['count_6h']}, 12h: {rcpt['count_12h']}, DES: {rcpt['count_des']})</div>
                    <div><strong>Registro en Log de Auditoría:</strong> ID #{rcpt.get('audit_log_id', '-')} ({rcpt['discrepancies_detected']} cruce(s) detectado(s))</div>
                </div>
                <div style="background:#ffffff; border:1px solid #cbd5e1; border-radius:8px; padding:10px;">
                    <div style="font-size:11.5px; font-weight:bold; color:#334155; margin-bottom:6px;">
                        Detalle de fechas propuestas registradas ({rcpt['saved_days_count']} días):
                    </div>
                    <div>{shifts_badges_html}</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )
        dl_c1, dl_c2, dl_c3 = st.columns([2, 2, 1])
        rcpt_surname = rcpt["doctor_name"].split(",")[0].strip().replace(" ", "_")
        with dl_c1:
            rcpt_excel_stream = generate_proposal_receipt_excel(rcpt, "IPRESS SALUD PERÚ")
            st.download_button(
                label="📥 Descargar Cargo / Constancia en Excel (.xlsx)",
                data=rcpt_excel_stream.getvalue(),
                file_name=f"Constancia_Propuesta_{rcpt_surname}_{rcpt['month']:02d}_{rcpt['year']}_{rcpt['receipt_code']}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="btn_dl_rcpt_xlsx"
            )
        with dl_c2:
            rcpt_html_str = generate_proposal_receipt_html(rcpt, "IPRESS SALUD PERÚ")
            st.download_button(
                label="🖨️ Descargar Constancia para Imprimir / PDF (.html)",
                data=rcpt_html_str.encode("utf-8"),
                file_name=f"Constancia_Propuesta_{rcpt_surname}_{rcpt['month']:02d}_{rcpt['year']}_{rcpt['receipt_code']}.html",
                mime="text/html",
                use_container_width=True,
                key="btn_dl_rcpt_html"
            )
        with dl_c3:
            if st.button("✖ Cerrar Constancia", use_container_width=True, key="btn_close_rcpt"):
                st.session_state["last_proposal_receipt"] = None
                st.rerun()

# -------------------------------------------------------------------
# BARRA DE ACCIONES INSTITUCIONALES (SIN BOTÓN DE GRABAR TURNO; SE EDITA HACIENDO CLIC EN LA FECHA/CELDA)
# -------------------------------------------------------------------
b_col1, b_col2 = st.columns([2.5, 2.5])

with b_col1:
    if approval_status.get("is_approved"):
        if st.button("🔄 Reabrir Horario (Jefatura / RRHH)", use_container_width=True, type="secondary"):
            if not is_admin_or_rrhh:
                st.warning("🔒 Acción restringida: Solo la Jefatura Médica o RRHH pueden reabrir un horario aprobado.")
            else:
                reopen_schedule(selected_year, selected_month, st.session_state["user"]["name"])
                st.success("¡Horario reabierto con éxito para modificaciones!")
                st.rerun()
    else:
        if st.button("✅ Aprobar Horario Mensual (Jefatura / RRHH)", use_container_width=True, type="secondary"):
            if not is_admin_or_rrhh:
                st.warning("🔒 Acción restringida: Solo la Jefatura Médica o RRHH pueden certificar el horario mensual.")
            else:
                val_res = validate_and_approve_schedule(selected_year, selected_month, st.session_state["user"]["name"], approve_pending_days=True)
                if val_res["is_approved"]:
                    st.session_state["shift_saved_confirmation"] = f"{val_res['message']} (Código: #{val_res['approval_hash']})"
                    st.rerun()
                else:
                    st.error(f"No se puede aprobar aún: {val_res['reason']}")

with b_col2:
    excel_stream = generate_excel_schedule(selected_year, selected_month, "IPRESS SALUD PERÚ")
    st.download_button(
        label="📥 Descargar Rol Consolidado en Excel (.xlsx)",
        data=excel_stream.getvalue(),
        file_name=f"Rol_Turnos_IPRESS_{selected_year}_{selected_month:02d}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )

# -------------------------------------------------------------------
# MATRIZ VISUAL DEL ROL MENSUAL (INTERACTIVA AL HACER CLIC EN FECHA/CELDA PARA JEFATURA Y RRHH)
# -------------------------------------------------------------------
st.subheader("🗓️ Matriz Mensual de Turnos Asistenciales (Tiempo Real)")
if is_admin_or_rrhh:
    st.info(
        "👆 **Modo Interactivo Activo (Jefatura / RRHH):** Haga **clic directamente sobre cualquier número de fecha o celda del horario** "
        "para abrir la ventana emergente (pop-up), confirmar si desea modificar ese turno y grabar el cambio."
    )
else:
    st.caption(
        "**Servicios Mañana:** `TU` (Tópico de Urgencias), `CE` (Consultorio de Medicina), `TS` (Telesalud/TBC/MCI) | "
        "**Servicios Tarde:** `TU` (Tópico de Urgencias), `CE` (Consultorio de Medicina) | "
        "**Modificación y Aprobación:** Exclusiva para Jefatura Médica y RRHH haciendo clic sobre la fecha."
    )

curr_uid = st.session_state["user"]["id"] if st.session_state["user"] else None

matrix_styles = """
<style>
    .table-matrix {
        width: 100%;
        border-collapse: collapse;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        font-size: 11.5px;
        text-align: center;
    }
    .table-matrix th, .table-matrix td {
        border: 1px solid #cbd5e1;
        padding: 4px 2px;
        min-width: 34px;
    }
    .table-matrix th {
        background-color: #1e293b;
        color: white;
    }
    .table-matrix th:first-child, .table-matrix td:first-child {
        position: sticky;
        left: 0;
        background-color: #f8fafc;
        z-index: 2;
        box-shadow: 2px 0 4px -2px rgba(0,0,0,0.15);
    }
    .table-matrix tr:first-child th:first-child, .table-matrix tr:nth-child(2) th:first-child {
        background-color: #1e293b;
        color: white;
        z-index: 3;
    }
    .badge-shift {
        display: inline-block;
        padding: 2px 5px;
        border-radius: 6px;
        font-size: 10.5px;
        font-weight: bold;
        text-align: center;
        margin: 1px;
        line-height: 1.15;
    }
    .badge-12h { background-color: #bae6fd; color: #0369a1; border: 1px solid #7dd3fc; }
    .badge-DES { background-color: #ede9fe; color: #5b21b6; border: 1px solid #c4b5fd; font-style: italic; }
    .badge-LIBRE { background-color: #f5f3ff; color: #6b21a8; border: 1px solid #ddd6fe; font-weight: 600; }
    .badge-M-TU, .badge-M-CE, .badge-M-TS { background-color: #fef08a; color: #713f12; border: 1px solid #facc15; }
    .badge-T-TU, .badge-T-CE { background-color: #bbf7d0; color: #14532d; border: 1px solid #86efac; }
    .col-sun { background-color: #e2e8f0; color: #64748b; }
    .col-sat { background-color: #f8fafc; }
    .col-hol { background-color: #ffe4e6; }
    .cov-ok { background-color: #dcfce7; color: #166534; font-weight: bold; font-size: 10px; }
    .cov-adm { background-color: #fef9c3; color: #854d0e; font-weight: bold; font-size: 10px; }
    .cov-err { background-color: #fee2e2; color: #991b1b; font-weight: bold; font-size: 10px; }
    [data-clickable="1"] {
        cursor: pointer;
        transition: transform 0.1s ease, box-shadow 0.1s ease;
    }
    [data-clickable="1"]:hover {
        outline: 2px solid #2563eb;
        background-color: #eff6ff !important;
    }
</style>
"""

html = [matrix_styles, "<div style='overflow-x: auto;'><table class='table-matrix'>"]

# Cabecera Fila 1: Días (clicables para Jefatura/RRHH)
html.append("<tr>")
html.append("<th style='text-align:left; min-width:210px;'>MÉDICO CIRUJANO</th><th>COND.</th><th>CMP</th>")
for d in days:
    cls = "col-hol" if d["is_holiday"] else ("col-sun" if d["is_sunday"] else "")
    sun_lock = " 🔒" if d["is_sunday"] and not is_admin_user else ""
    hol_tag = "<br><small style='color:#fca5a5;'>FER</small>" if d["is_holiday"] else ""
    click_attr = f"data-clickable='1' data-day='{d['day']}' title='Clic para modificar turnos del Día {d['day']}'" if is_admin_or_rrhh else ""
    html.append(f"<th class='{cls}' {click_attr}>{d['day']}{sun_lock}{hol_tag}</th>")
html.append("<th>6h</th><th>12h</th><th>DES</th><th>TOTAL</th><th>META</th><th>ESTADO</th>")
html.append("</tr>")

# Cabecera Fila 2: Días de semana
html.append("<tr>")
html.append("<th colspan='3' style='text-align:left; font-weight:normal; font-size:10px; background-color:#334155;'>Días de la semana (Clic en fecha/celda para modificar — Jefatura/RRHH)</th>")
for d in days:
    cls = "col-sun" if d["is_sunday"] else ("col-sat" if d["is_saturday"] else "")
    click_attr = f"data-clickable='1' data-day='{d['day']}'" if is_admin_or_rrhh else ""
    html.append(f"<th class='{cls}' {click_attr} style='font-size:10px; background-color:#334155; color:white;'>{d['weekday_short']}</th>")
for _ in range(6):
    html.append("<th style='background-color:#334155;'></th>")
html.append("</tr>")

# Filas de Médicos
for doc in doctors_only:
    d_sum = doc_summary.get(doc["id"], {"total_equivalent_shifts": 0, "count_6h": 0, "count_12h": 0, "count_des": 0, "days": {}})
    html.append("<tr>")
    html.append(f"<td style='text-align:left; padding-left:8px; font-weight:600;'>{doc['name']}</td>")
    html.append(f"<td><small>{doc['condition']}</small></td>")
    html.append(f"<td><small>{doc['cmp']}</small></td>")

    for d in days:
        cell_cls = "col-sun" if d["is_sunday"] else ("col-hol" if d["is_holiday"] else ("col-sat" if d["is_saturday"] else ""))
        shift = d_sum["days"].get(d["day"])
        cell_locks = appointment_locks.get((doc["id"], d["day"]), {})
        m_cnt = cell_locks.get("M", {}).get("appointments_count", 0)
        t_cnt = cell_locks.get("T", {}).get("appointments_count", 0)
        tot_citas = m_cnt + t_cnt
        lock_badge = f"<br><span style='font-size:8.5px; color:#991b1b; font-weight:800;'>🔒{tot_citas} cita(s)</span>" if tot_citas > 0 else ""

        if shift and shift.get("shift_period") != "LIBRE":
            sp = shift["shift_period"]
            srv = shift["service"]
            if sp == "12h":
                b_html = f"<span class='badge-shift badge-12h'>12h<br><small>{srv}</small>{lock_badge}</span>"
            elif sp == "DES":
                b_html = "<span class='badge-shift badge-DES'>DES</span>"
            else:
                tag = f"{sp}-{srv}"
                b_cls = f"badge-{tag}" if f"badge-{tag}" in ("badge-M-TU", "badge-M-CE", "badge-M-TS", "badge-T-TU", "badge-T-CE") else "badge-12h"
                b_html = f"<span class='badge-shift {b_cls}'>{tag}{lock_badge}</span>"
        else:
            b_html = f"<span class='badge-shift badge-LIBRE'>Libre{lock_badge}</span>"

        lock_tooltip = (
            f" | 🔒 BLOQUEADO POR CITAS EN CE (Mañana: {m_cnt}, Tarde: {t_cnt})"
            if tot_citas > 0 else ""
        )
        click_attr = (
            f"data-clickable='1' data-day='{d['day']}' data-doc-id='{doc['id']}' "
            f"title='Clic para ver/modificar turno de {doc['name']} el Día {d['day']}{lock_tooltip}'"
        ) if is_admin_or_rrhh else f"title='Día {d['day']}: {doc['name']}{lock_tooltip}'"
        html.append(f"<td class='{cell_cls}' {click_attr}>{b_html}</td>")

    # Totales
    is_comp = d_sum["total_equivalent_shifts"] == quota
    stat_color = "#166534" if is_comp else "#9a3412"
    html.append(f"<td>{d_sum['count_6h']}</td>")
    html.append(f"<td>{d_sum['count_12h']}</td>")
    html.append(f"<td>{d_sum.get('count_des', 0)}</td>")
    html.append(f"<td><b>{d_sum['total_equivalent_shifts']}</b></td>")
    html.append(f"<td>{quota}</td>")
    html.append(f"<td style='color:{stat_color}; font-weight:bold;'>{d_sum.get('status', 'Incompleto')}</td>")
    html.append("</tr>")

# Fila de Cobertura de Servicios Mañana (TU, CE, TS)
html.append("<tr>")
html.append("<td colspan='3' style='text-align:right; font-weight:bold; font-size:10.5px; background-color:#f8fafc;'>Mañana (Req: TU, CE, TS):</td>")
for d in days:
    cov = coverage.get(d["day"], {})
    if d["is_sunday"]:
        html.append(f"<td class='col-sun'>{cov.get('morning_count') or '-'}</td>")
    else:
        m_srvs = cov.get("morning_services_covered", [])
        miss_m = cov.get("missing_morning_services", [])
        miss_m_str = ",".join(miss_m)
        m_srvs_str = ",".join(m_srvs) or "0"
        if not miss_m:
            html.append("<td class='cov-ok' title='TU, CE y TS cubiertos'>✓ TU·CE·TS</td>")
        elif cov.get("is_admin_approved_day"):
            html.append(f"<td class='cov-adm' title='Aprobado por Admin. Faltaba: {miss_m_str}'>✓ Admin<br><small>({m_srvs_str})</small></td>")
        else:
            html.append(f"<td class='cov-err' title='Falta cubrir: {miss_m_str}'>⚠️ Falta:<br>{miss_m_str}</td>")
for _ in range(6):
    html.append("<td></td>")
html.append("</tr>")

# Fila de Cobertura de Servicios Tarde (TU, CE)
html.append("<tr>")
html.append("<td colspan='3' style='text-align:right; font-weight:bold; font-size:10.5px; background-color:#f8fafc;'>Tarde (Req: TU, CE):</td>")
for d in days:
    cov = coverage.get(d["day"], {})
    if d["is_sunday"]:
        html.append(f"<td class='col-sun'>{cov.get('afternoon_count') or '-'}</td>")
    else:
        t_srvs = cov.get("afternoon_services_covered", [])
        miss_t = cov.get("missing_afternoon_services", [])
        miss_t_str = ",".join(miss_t)
        t_srvs_str = ",".join(t_srvs) or "0"
        if not miss_t:
            html.append("<td class='cov-ok' title='TU y CE cubiertos'>✓ TU·CE</td>")
        elif cov.get("is_admin_approved_day"):
            html.append(f"<td class='cov-adm' title='Aprobado por Admin. Faltaba: {miss_t_str}'>✓ Admin<br><small>({t_srvs_str})</small></td>")
        else:
            html.append(f"<td class='cov-err' title='Falta cubrir: {miss_t_str}'>⚠️ Falta:<br>{miss_t_str}</td>")
for _ in range(6):
    html.append("<td></td>")
html.append("</tr>")

html.append("</table></div>")

matrix_event = interactive_matrix_component(
    data={"html": "".join(html), "is_editable": bool(is_admin_or_rrhh)},
    key=f"matrix_comp_{selected_year}_{selected_month}_{is_admin_or_rrhh}"
)

if is_admin_or_rrhh and matrix_event and getattr(matrix_event, "cell_clicked", None):
    clicked_info = matrix_event.cell_clicked
    clicked_day = clicked_info.get("day")
    clicked_doc_id = clicked_info.get("doc_id")
    open_shift_recording_modal(default_day=clicked_day, default_doc_id=clicked_doc_id)

# -------------------------------------------------------------------
# -------------------------------------------------------------------
# ALERTAS DE COBERTURA DE SERVICIOS DIARIOS Y APROBACIÓN POR JEFATURA / RRHH
# -------------------------------------------------------------------
if len(service_alert_days) > 0:
    st.markdown("---")
    with st.expander(
        f"🚨 Alertas de Cobertura de Servicios Diarios ({len(unapproved_service_alert_days)} pendientes de aprobación de Jefatura/RRHH)",
        expanded=(len(unapproved_service_alert_days) > 0 and total_month_shifts > 0)
    ):
        st.markdown(
            "**Regla de Cobertura de Servicios:** En las **Mañanas** se debe cubrir `Tópico de Urgencias (TU)`, `Consultorio de Medicina (CE)` y `Telesalud/TBC/MCI (TS)`. "
            "En las **Tardes** se debe cubrir `Tópico de Urgencias (TU)` y `Consultorio de Medicina (CE)`. "
            "**Solo la Jefatura Médica o el Responsable de Recursos Humanos pueden aprobar los días en los que exista esta discrepancia.**"
        )

        if is_admin_or_rrhh and len(unapproved_service_alert_days) > 0:
            if st.button("✅ Aprobar Todos los Días con Discrepancia de Cobertura (Modo Jefatura / RRHH)", type="primary"):
                cnt = approve_all_day_discrepancies(selected_year, selected_month, st.session_state["user"]["name"])
                st.session_state["shift_saved_confirmation"] = f"Se aprobaron exitosamente {cnt} día(s) con discrepancia de cobertura por {st.session_state['user']['name']}."
                st.rerun()

        for d_num in service_alert_days:
            c_info = coverage[d_num]
            col_a1, col_a2 = st.columns([3.5, 1.5])
            with col_a1:
                status_badge = "🟢 **[APROBADO POR GESTIÓN]**" if c_info["is_admin_approved_day"] else "🔴 **[PENDIENTE DE APROBACIÓN JEFATURA/RRHH]**"
                st.markdown(
                    f"**Día {d_num:02d} ({c_info['weekday_name']})** {status_badge} — {c_info['alert_message']}"
                )
            with col_a2:
                if c_info["is_admin_approved_day"]:
                    if is_admin_or_rrhh:
                        if st.button(f"Revocar Día {d_num:02d}", key=f"rev_day_{d_num}"):
                            revoke_day_approval(selected_year, selected_month, d_num, st.session_state["user"]["name"])
                            st.rerun()
                    else:
                        st.caption(f"Aprobado por {c_info['admin_approval_info']['approved_by']}")
                else:
                    if is_admin_or_rrhh:
                        if st.button(f"✅ Aprobar Día {d_num:02d}", key=f"app_day_{d_num}", type="primary"):
                            approve_day_discrepancy(selected_year, selected_month, d_num, st.session_state["user"]["name"], c_info["alert_message"])
                            st.session_state["shift_saved_confirmation"] = f"Día {d_num:02d}/{selected_month:02d}/{selected_year} aprobado por {st.session_state['user']['name']}."
                            st.rerun()
                    else:
                        st.caption("🔒 Solo Jefatura Médica o RRHH pueden aprobar este día")

# -------------------------------------------------------------------
# CENTRO DE DISCREPANCIAS POR CRUCES DE PROPUESTAS (SIEMPRE VISIBLE)
# Solo Jefatura y RRHH pueden aprobar o modificar
# -------------------------------------------------------------------
with st.expander(f"⚠️ Discrepancias por Cruces de Propuestas ({len(open_discs)} cruces activos)", expanded=(len(open_discs) > 0)):
    st.caption(
        "Si en las propuestas recibidas se observan cruces de servicio en el mismo turno, se muestran aquí. "
        "**Únicamente Jefatura Médica y RRHH pueden aprobar o modificar las propuestas.**"
    )
    if len(open_discs) == 0:
        st.success("✅ Actualmente no se registran cruces de servicios entre las propuestas cargadas.")
    else:
        for disc in open_discs:
            st.markdown(f"**{disc['description']}**")
            try:
                import json as _json
                alt_payload = _json.loads(disc["suggested_alternative"])
                sug_text = alt_payload.get("text") or str(disc["suggested_alternative"])
            except Exception:
                sug_text = str(disc["suggested_alternative"])
            c_desc, c_btn = st.columns([3, 1])
            with c_desc:
                st.info(f"💡 **Sugerencia:** {sug_text}")
            with c_btn:
                if is_admin_or_rrhh:
                    if st.button("✅ Aprobar Sugerencia", key=f"btn_disc_{disc['id']}", type="primary"):
                        if apply_discrepancy_alternative(disc["id"]):
                            st.session_state["shift_saved_confirmation"] = "Cruce subsanado por Jefatura/RRHH y turno grabado."
                            st.rerun()
                else:
                    st.caption("🔒 Solo Jefatura o RRHH pueden aprobar o modificar este cruce.")

# -------------------------------------------------------------------
# RESUMEN DE REGLAS VIGENTES DE LOS MÉDICOS
# -------------------------------------------------------------------
with st.expander("📋 Ver Reglas de Programación Institucional por Médico (Octubre - Noviembre)", expanded=False):
    st.markdown("""
    - **Meta Mensual:** **25 turnos** por médico menos los feriados nacionales que caigan de **lunes a sábado**.
    - **Domingos:** Bloqueados para los médicos; únicamente la **Jefatura Médica** o el **Responsable de Recursos Humanos** pueden programar en domingo.
    - **Servicios Obligatorios:**
      - **Mañana:** 1 médico en **Tópico de Urgencias (`TU`)**, 1 en **Consultorio de Medicina (`CE`)** y 1 en **Telesalud/TBC/MCI (`TS`)**.
      - **Tarde:** 1 médico en **Tópico de Urgencias (`TU`)** y 1 en **Consultorio de Medicina (`CE`)**.
    - **Dr. Solano Ricaldi, Edward Jhonny:**
      - Todos los **miércoles** de cada mes realiza **12 horas (`TS+TU`)**: *Telesalud/TBC/MCI en la mañana y Tópico de Urgencia en la tarde*, con descanso de guardia (**`DES`**) obligatorio el jueves.
      - Realiza **5 turnos de 12 horas al mes** (incluidos los miércoles), siempre con descanso (`DES`) al día siguiente.
    - **Dra. Caldas Pacsi, Lily Katherine (Octubre y Noviembre):**
      - **Lunes, Miércoles, Jueves y Viernes:** Turno Mañana (`M`).
      - **Sábados:** **12 horas (`TS+TU`)** (*Telesalud/TBC/MCI en la mañana y Tópico de Urgencia por la tarde*).
    - **Dra. Laureano Varillas, Elizabeth Miriam:**
      - **Lunes, Martes, Miércoles, Viernes y Sábado:** Turno Mañana (`M`).
      - **Jueves:** Turno Tarde (`T`).
    - **Dra. Sanchez Quintanilla, Cinthya (Octubre a Noviembre):**
      - **Lunes, Miércoles y Viernes:** **12 horas (`12h`)** con descanso obligatorio (`DES`) al día siguiente.
    - **Dra. Chunga Torres, Tuly Magnolia (Octubre a Noviembre):**
      - **Lunes, Miércoles y Viernes:** **12 horas (`12h`)** con descanso obligatorio (`DES`) al día siguiente.
    """)

# -------------------------------------------------------------------
# LOG DE CAMBIOS Y TRAZABILIDAD (ÚLTIMOS 3 MESES / 90 DÍAS)
# -------------------------------------------------------------------
with st.expander("📜 Log de Cambios y Trazabilidad (Últimos 3 Meses - Retención 90 Días)", expanded=False):
    st.markdown("""
    **Políticas de Auditoría Institucional:**
    - **Retención:** Se almacenan y resguardan de forma automática todos los eventos de los últimos **3 meses (90 días)**.
    - **Trazabilidad:** Cada acción registra el usuario ejecutor, médico afectado, fecha y hora exacta, tipo de acción y detalle de cambios.
    """)

    all_logs = get_audit_logs(limit=1000)

    m_col1, m_col2, m_col3 = st.columns(3)
    with m_col1:
        st.metric("Registros en 90 Días", len(all_logs))
    with m_col2:
        st.metric("Ventana de Retención", "90 días (3 meses)")
    with m_col3:
        last_ts = all_logs[0]["timestamp"][:19].replace("T", " ") if all_logs else "Sin registros"
        st.metric("Último Registro", last_ts)

    f_col1, f_col2, f_col3 = st.columns([1.5, 1.5, 2])
    with f_col1:
        users_in_logs = sorted(list(set(l["doctor_name"] for l in all_logs if l.get("doctor_name"))))
        sel_filter_user = st.selectbox("Filtrar por Usuario / Médico:", ["Todos"] + users_in_logs, key="log_flt_user")
    with f_col2:
        actions_in_logs = sorted(list(set(l["action"] for l in all_logs if l.get("action"))))
        sel_filter_action = st.selectbox("Filtrar por Tipo de Acción:", ["Todas"] + actions_in_logs, key="log_flt_act")
    with f_col3:
        search_text = st.text_input("Buscar texto en detalles:", key="log_flt_txt", placeholder="Ej. Día 05, Dra. Caldas, etc.")

    filtered = all_logs
    if sel_filter_user != "Todos":
        filtered = [l for l in filtered if l["doctor_name"] == sel_filter_user]
    if sel_filter_action != "Todas":
        filtered = [l for l in filtered if l["action"] == sel_filter_action]
    if search_text:
        filtered = [l for l in filtered if search_text.lower() in (l.get("details") or "").lower() or search_text.lower() in (l.get("doctor_name") or "").lower()]

    if filtered:
        df_show = pd.DataFrame([{
            "Fecha y Hora": r["timestamp"][:19].replace("T", " "),
            "Usuario / Responsable": r["doctor_name"],
            "Acción": r["action"],
            "Detalle del Cambio": r["details"]
        } for r in filtered])
        st.dataframe(df_show, use_container_width=True, hide_index=True)

        csv_data = df_show.to_csv(index=False).encode("utf-8")
        st.download_button(
            "📥 Descargar Log de Auditoría (CSV)",
            data=csv_data,
            file_name=f"Log_Auditoria_IPRESS_90dias_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
            key="btn_dl_audit_csv"
        )
    else:
        st.info("No se encontraron registros de cambios con los filtros seleccionados.")

# -------------------------------------------------------------------
# PANELES EXCLUSIVOS DE ADMINISTRACIÓN (JEFATURA MÉDICA / RRHH)
# -------------------------------------------------------------------
if is_admin_or_rrhh:
    st.markdown("---")
    curr_u_role = st.session_state["user"]["role"]
    role_header = "Jefatura Médica" if curr_u_role == "admin" else "Responsable de Recursos Humanos"
    st.subheader(f"👑 Panel de Control Institucional ({role_header})")

    adm_tab1, adm_tab2, adm_tab3 = st.tabs([
        "🛡️ Auditoría en Vivo",
        "👥 Gestión de Personal y Contraseñas",
        "🔗 Sincronización con Citas de Pacientes (citascsjp2)"
    ])

    with adm_tab1:
        st.markdown("##### Registro de Auditoría Reciente (`audit_logs`)")
        logs_recent = get_audit_logs(limit=50)
        if logs_recent:
            df_logs = pd.DataFrame([{
                "Fecha y Hora": r["timestamp"][:19].replace("T", " "),
                "Usuario / Médico": r["doctor_name"],
                "Acción Realizada": r["action"],
                "Detalles": r["details"]
            } for r in logs_recent])
            st.dataframe(df_logs, use_container_width=True, hide_index=True)
        else:
            st.info("No hay registros de auditoría aún.")

    with adm_tab2:
        st.markdown("##### Nómina Institucional de Personal")
        doc_rows = [{
            "ID": d["id"],
            "Nombre Completo": d["name"],
            "Condición": d["condition"],
            "CMP": d["cmp"],
            "Rol": d["role"].upper()
        } for d in doctors_list]
        st.dataframe(pd.DataFrame(doc_rows), use_container_width=True, hide_index=True)

        st.markdown("##### 🔑 Restablecer Clave de Acceso")
        c_p1, c_p2, c_p3 = st.columns([2, 1.5, 1])
        with c_p1:
            rst_doc_id = st.selectbox(
                "Seleccionar Personal para Reset de Clave:",
                [d["id"] for d in doctors_list],
                format_func=lambda did: next((f"{d['name']} ({d['cmp']})" for d in doctors_list if d["id"] == did), str(did)),
                key="rst_doc_select"
            )
        with c_p2:
            rst_pin = st.text_input("Nueva Clave temporal (mín 4 caracteres):", value="1234", key="rst_pin_input")
        with c_p3:
            st.write("")
            st.write("")
            if st.button("Restablecer Clave", key="btn_rst_pin", type="primary"):
                if len(rst_pin) < 4:
                    st.error("La clave debe tener al menos 4 caracteres.")
                else:
                    target_d = next(d for d in doctors_list if d["id"] == rst_doc_id)
                    with get_connection() as conn:
                        conn.execute("UPDATE doctors SET pin_hash = ? WHERE id = ?", (hash_pin(rst_pin), rst_doc_id))
                        conn.commit()
                        log_audit(
                            st.session_state["user"]["id"],
                            st.session_state["user"]["name"],
                            "Reset de Clave",
                            f"Se restableció la clave de {target_d['name']} ({target_d['cmp']})",
                            conn=conn
                        )
                    st.success(f"¡Clave de {target_d['name']} restablecida exitosamente a: {rst_pin}!")

    with adm_tab3:
        st.markdown("##### 🔗 Vinculación de Bloqueo por Citas Médicas (`https://citascsjp2.streamlit.app/`)")
        st.info(
            "**Regla de Protección de Pacientes Citados:** Cuando un paciente saca una cita en el portal de citas "
            "(`citascsjp2.streamlit.app`), el turno de **Consultorio de Medicina (`CE`)** de ese médico en esa fecha y turno (`M` o `T`) "
            "queda **bloqueado automáticamente (`🔒`)** para que no pueda ser eliminado ni cambiado de servicio en este horario."
        )
        locks_rows = []
        for (ldoc_id, lday), pmap in sorted(appointment_locks.items(), key=lambda x: (x[0][1], x[0][0])):
            dname = next((d["name"] for d in doctors_list if d["id"] == ldoc_id), f"ID {ldoc_id}")
            for sp_k, linfo in pmap.items():
                locks_rows.append({
                    "Día": f"{lday:02d}/{selected_month:02d}/{selected_year}",
                    "Turno Protegido": "Mañana (M-CE)" if sp_k == "M" else "Tarde (T-CE)",
                    "Médico Titular": dname,
                    "Citas Activas": linfo["appointments_count"],
                    "Tickets de Referencia": linfo.get("sample_tickets", "")
                })
        if locks_rows:
            st.dataframe(pd.DataFrame(locks_rows), use_container_width=True, hide_index=True)
        else:
            st.caption("No se registran turnos bloqueados por citas de pacientes en el mes seleccionado.")

        up_citas_db = st.file_uploader(
            "Subir base de datos de citas (`citas_jp2_v2.db`) para sincronizar bloqueos manualmente:",
            type=["db", "sqlite", "sqlite3"],
            key="up_citas_db_sync"
        )
        if up_citas_db is not None:
            if st.button("🔄 Sincronizar Bloqueos desde citas_jp2_v2.db", type="primary"):
                import tempfile
                with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp_c:
                    tmp_c.write(up_citas_db.getvalue())
                    tmp_c_path = tmp_c.name
                sync_res = sync_appointment_locks_from_citas_db(source_citas_db_path=tmp_c_path)
                try:
                    os.remove(tmp_c_path)
                except OSError:
                    pass
                if sync_res["synced"]:
                    st.success(f"¡Bloqueos sincronizados con éxito! ({sync_res['locks_count']} turno(s) con citas activas bloqueados).")
                    st.rerun()
                else:
                    st.error(sync_res["message"])

st.caption("IPRESS Salud Perú © 2026 - Sistema de Programación Asistencial Oficial.")
