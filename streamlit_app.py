"""
streamlit_app.py
Aplicativo Web en Streamlit para la Programación de Turnos de Médicos Cirujanos en la IPRESS.
Compatible 100% con Streamlit Community Cloud (share.streamlit.io).
"""

import io
from datetime import datetime
import pandas as pd
import streamlit as st
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
    reopen_schedule,
    calculate_deadline,
    record_doctor_shift,
    approve_day_discrepancy,
    approve_all_day_discrepancies,
    revoke_day_approval,
    SERVICE_LABELS
)
from exporter import generate_excel_schedule

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
    .badge-12h { background-color: #fef3c7; color: #92400e; border: 1px solid #fcd34d; }
    .badge-DES { background-color: #f3e8ff; color: #6b21a8; border: 1px solid #d8b4fe; }
    .badge-M-TU { background-color: #ffedd5; color: #9a3412; border: 1px solid #fed7aa; }
    .badge-M-CE { background-color: #dbeafe; color: #1e40af; border: 1px solid #bfdbfe; }
    .badge-M-TS { background-color: #d1fae5; color: #065f46; border: 1px solid #a7f3d0; }
    .badge-T-TU { background-color: #ffedd5; color: #9a3412; border: 1px solid #fed7aa; }
    .badge-T-CE { background-color: #dbeafe; color: #1e40af; border: 1px solid #bfdbfe; }
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
        return conn.execute("SELECT id, name, condition, cmp, role FROM doctors WHERE is_active = 1 ORDER BY condition DESC, name ASC").fetchall()

doctors_list = [dict(d) for d in get_all_doctors()]
doctors_only = [d for d in doctors_list if d["role"] == "doctor"]

# -------------------------------------------------------------------
# SIDEBAR: AUTENTICACIÓN Y FILTROS
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
    st.subheader("🔐 Acceso por Médico / Admin")

    if st.session_state["user"] is None:
        doc_options = {d["cmp"]: f"{d['name']} ({d['cmp']})" for d in doctors_only}
        doc_options["ADMIN"] = "Jefatura Médica IPRESS (ADMIN)"
        
        sel_cmp = st.selectbox("Selecciona tu perfil:", list(doc_options.keys()), format_func=lambda k: doc_options[k])
        input_pin = st.text_input("Ingresa tu PIN personal:", type="password")

        if st.button("Iniciar Sesión", use_container_width=True, type="primary"):
            if not input_pin:
                st.error("Debes ingresar tu PIN.")
            else:
                with get_connection() as conn:
                    doc_db = conn.execute("SELECT id, name, condition, cmp, pin_hash, role FROM doctors WHERE cmp = ?", (sel_cmp,)).fetchone()
                    if doc_db and doc_db["pin_hash"] == hash_pin(input_pin):
                        st.session_state["user"] = dict(doc_db)
                        log_audit(doc_db["id"], doc_db["name"], "Inicio de Sesión Streamlit", f"Acceso con CMP {sel_cmp}")
                        st.rerun()
                    else:
                        st.error("PIN incorrecto.")
    else:
        u = st.session_state["user"]
        st.success(f"**{u['name']}**")
        st.caption(f"CMP: {u['cmp']} | Condición: {u['condition']} | Rol: {u['role'].upper()}")

        if st.button("Cerrar Sesión", use_container_width=True):
            st.session_state["user"] = None
            st.rerun()

        with st.expander("🔑 Cambiar mi PIN"):
            old_p = st.text_input("PIN Actual:", type="password", key="old_pin_sb")
            new_p = st.text_input("Nuevo PIN (mín 4 dígitos):", type="password", key="new_pin_sb")
            conf_p = st.text_input("Confirmar Nuevo PIN:", type="password", key="conf_pin_sb")
            if st.button("Guardar Nuevo PIN", use_container_width=True):
                if len(new_p) < 4:
                    st.error("El nuevo PIN debe tener al menos 4 caracteres.")
                elif new_p != conf_p:
                    st.error("Los PINes nuevos no coinciden.")
                else:
                    with get_connection() as conn:
                        chk = conn.execute("SELECT pin_hash FROM doctors WHERE id = ?", (u["id"],)).fetchone()
                        if chk and chk["pin_hash"] == hash_pin(old_p):
                            conn.execute("UPDATE doctors SET pin_hash = ? WHERE id = ?", (hash_pin(new_p), u["id"]))
                            conn.commit()
                            log_audit(u["id"], u["name"], "Cambio de PIN", "PIN actualizado")
                            st.success("¡PIN actualizado con éxito!")
                        else:
                            st.error("El PIN actual no es correcto.")

# -------------------------------------------------------------------
# DATOS DEL MES Y REGLAS
# -------------------------------------------------------------------
month_info = get_month_info(selected_year, selected_month)
quota = month_info["monthly_quota"]
days = month_info["days"]

# Si el mes aún no tiene ningún turno cargado (ej. al cambiar a Noviembre), inicializar con el rol base
with get_connection() as conn:
    cnt_shifts = conn.execute("SELECT COUNT(*) as c FROM shifts WHERE year = ? AND month = ?", (selected_year, selected_month)).fetchone()["c"]
if cnt_shifts == 0 and selected_month in (10, 11):
    auto_assign_missing_shifts_day10(selected_year, selected_month)

quota_summary = get_doctor_quota_summary(selected_year, selected_month)
doc_summary = quota_summary["doctors_summary"]
coverage = get_daily_coverage(selected_year, selected_month)

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

is_admin_user = (st.session_state["user"] is not None and st.session_state["user"].get("role") == "admin")

# -------------------------------------------------------------------
# VENTANA MODAL DE GRABACIÓN DE TURNO (@st.dialog)
# Al grabar se cierra automáticamente y envía confirmación
# -------------------------------------------------------------------
@st.dialog("🗓️ Ventana de Grabación de Turno Asistencial", width="large")
def open_shift_recording_modal(default_day: int = None):
    u = st.session_state["user"]
    is_admin = (u.get("role") == "admin")

    if is_admin:
        st.markdown("**👑 Modo Administrador (Jefatura Médica):** Puedes registrar o modificar el turno de cualquier médico (incluidos los domingos).")
        target_doc_id = st.selectbox(
            "1. Seleccionar Médico Cirujano:",
            [doc["id"] for doc in doctors_only],
            format_func=lambda did: next((f"{d['name']} ({d['condition']} - CMP: {d['cmp']})" for d in doctors_only if d["id"] == did), str(did)),
            key="dlg_admin_doc"
        )
        target_doc = next((d for d in doctors_only if d["id"] == target_doc_id), doctors_only[0])
        target_id = target_doc["id"]
        target_name = target_doc["name"]
        selectable_days = [d["day"] for d in days]
    else:
        st.markdown(f"**👨‍⚕️ Médico Cirujano:** `{u['name']}` (CMP: `{u['cmp']}`)")
        st.caption("🔒 Los domingos están bloqueados para los médicos. Solo puedes modificar tu propia programación.")
        target_id = u["id"]
        target_name = u["name"]
        # Bloquear domingos para los médicos
        selectable_days = [d["day"] for d in days if not d["is_sunday"]]

    idx_default = selectable_days.index(default_day) if (default_day in selectable_days) else 0
    edit_day = st.selectbox(
        f"2. Seleccionar Fecha ({month_info['month_name']} {selected_year}):",
        selectable_days,
        index=idx_default,
        format_func=lambda d_num: (
            f"Día {d_num:02d} — {days[d_num - 1]['weekday_name']}"
            f"{' (FERIADO: ' + days[d_num - 1]['holiday_name'] + ')' if days[d_num - 1]['is_holiday'] else ''}"
            f"{' [DOMINGO - Solo Admin]' if days[d_num - 1]['is_sunday'] else ''}"
        ),
        key="dlg_day_select"
    )

    cur_shift = doc_summary.get(target_id, {}).get("days", {}).get(edit_day)
    if cur_shift:
        st.info(f"📌 Turno actualmente grabado en el Día {edit_day:02d}: **{cur_shift['shift_period']} ({cur_shift['service']})**")
    else:
        st.caption(f"📌 El Día {edit_day:02d} se encuentra actualmente sin turno asignado para este médico.")

    col_p, col_s = st.columns([1.2, 1.8])
    with col_p:
        period_choice = st.radio(
            "3. Período / Jornada:",
            [
                "Mañana (6h)",
                "Tarde (6h)",
                "12 Horas (Mañana + Tarde)",
                "Descanso de Guardia (DES)",
                "Dejar Libre (Borrar Turno)"
            ],
            key="dlg_period_radio"
        )

    with col_s:
        if "Mañana (6h)" in period_choice:
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
        elif "Tarde (6h)" in period_choice:
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
            st.caption("ℹ️ Al grabar un turno de 12 horas, el día siguiente quedará automáticamente con descanso de guardia (`DES`).")
        elif "DES" in period_choice:
            st.info("Descanso obligatorio de guardia (posterior a turno de 12 horas).")
            sp_val = "DES"
            srv_val = "DES"
        else:
            st.warning("Se liberará el turno asignado en esta fecha.")
            sp_val = "CLEAR"
            srv_val = ""

    st.markdown("---")
    b_cancel, b_save = st.columns([1, 2])
    with b_cancel:
        if st.button("Cancelar", use_container_width=True):
            st.rerun()
    with b_save:
        if st.button("💾 Grabar Turno y Cerrar Ventana", type="primary", use_container_width=True):
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

# Mostrar confirmación si se acaba de grabar un turno y cerrar la ventana
if st.session_state.get("shift_saved_confirmation"):
    msg_ok = st.session_state["shift_saved_confirmation"]
    st.toast("✅ El turno fue grabado correctamente", icon="✅")
    st.success(f"✅ **CONFIRMACIÓN DE GRABACIÓN:** {msg_ok} *(Visible para todos los médicos y el Administrador)*")
    st.session_state["shift_saved_confirmation"] = None

# Banner de Estado General
if approval_status.get("is_approved"):
    st.success(f"🏆 **HORARIO APROBADO OFICIAL IPRESS** | Aprobado por: {approval_status.get('approved_by')} | Código de Integridad: `#{approval_status.get('approval_hash')}`")
elif len(unapproved_service_alert_days) > 0:
    st.warning(
        f"⚠️ **Alerta de Cobertura de Servicios:** Existen **{len(unapproved_service_alert_days)} día(s)** en los que no se cubren todos los servicios requeridos "
        f"(Mañana: Tópico de Urgencias `TU`, Consultorio de Medicina `CE` y Telesalud/TBC/MCI `TS`; Tarde: Tópico de Urgencias `TU` y Consultorio de Medicina `CE`). "
        f"**Solo el Administrador puede aprobar los días en los que exista esta discrepancia.**"
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
    elif is_admin_user:
        complete_cnt = sum(1 for d in doc_summary.values() if d["total_equivalent_shifts"] == quota)
        st.metric("Médicos en Meta", f"{complete_cnt} / {len(doctors_only)}", "Supervisión Admin")
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
    st.metric("Domingos", "Bloqueados (Médicos)", "Habilitados para ADMIN")

st.markdown("---")

# -------------------------------------------------------------------
# BARRA DE ACCIONES PRINCIPALES
# -------------------------------------------------------------------
b_col0, b_col1, b_col2, b_col3 = st.columns([2.2, 2.2, 2.2, 2])

with b_col0:
    if st.session_state["user"] is not None:
        btn_open_txt = "➕ Grabar / Modificar Turno (Admin)" if is_admin_user else "➕ Grabar / Modificar Mi Turno"
        if st.button(btn_open_txt, use_container_width=True, type="primary"):
            open_shift_recording_modal()
    else:
        st.button("🔒 Inicia Sesión para Grabar Turnos", use_container_width=True, disabled=True)

with b_col1:
    if st.button("🪄 Aplicar Rol Institucional / Autocompletar", use_container_width=True, type="secondary"):
        if not is_admin_user:
            st.warning("🔒 Acción restringida: Solo el Administrador (ADMIN) puede ejecutar la asignación automática institucional.")
        else:
            auto_assign_missing_shifts_day10(selected_year, selected_month)
            log_audit(st.session_state["user"]["id"], st.session_state["user"]["name"], "Asignación Automática", f"Rol institucional aplicado para {selected_month}/{selected_year}")
            st.session_state["shift_saved_confirmation"] = f"Rol institucional de {month_info['month_name']} {selected_year} generado correctamente."
            st.rerun()

with b_col2:
    if approval_status.get("is_approved"):
        if st.button("🔄 Reabrir Horario (Solo ADMIN)", use_container_width=True, type="secondary"):
            if not is_admin_user:
                st.warning("🔒 Acción restringida: Solo el Administrador (ADMIN) puede reabrir un horario aprobado.")
            else:
                reopen_schedule(selected_year, selected_month, st.session_state["user"]["name"])
                st.success("¡Horario reabierto con éxito para modificaciones!")
                st.rerun()
    else:
        if st.button("✅ Aprobar Horario Mensual (Solo ADMIN)", use_container_width=True, type="secondary"):
            if not is_admin_user:
                st.warning("🔒 Acción restringida: Solo el Administrador (ADMIN) puede aprobar los días con discrepancia y certificar el horario.")
            else:
                val_res = validate_and_approve_schedule(selected_year, selected_month, st.session_state["user"]["name"], approve_pending_days=True)
                if val_res["is_approved"]:
                    st.session_state["shift_saved_confirmation"] = f"{val_res['message']} (Código: #{val_res['approval_hash']})"
                    st.rerun()
                else:
                    st.error(f"No se puede aprobar aún: {val_res['reason']}")

with b_col3:
    excel_stream = generate_excel_schedule(selected_year, selected_month, "IPRESS SALUD PERÚ")
    st.download_button(
        label="📥 Descargar Rol en Excel (.xlsx)",
        data=excel_stream.getvalue(),
        file_name=f"Rol_Turnos_IPRESS_{selected_year}_{selected_month:02d}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )

# -------------------------------------------------------------------
# MATRIZ VISUAL DEL ROL MENSUAL (VISIBLE PARA TODOS)
# -------------------------------------------------------------------
st.subheader("🗓️ Matriz Mensual de Turnos Asistenciales (Tiempo Real)")
st.caption(
    "**Servicios Mañana:** `TU` (Tópico de Urgencias), `CE` (Consultorio de Medicina), `TS` (Telesalud/TBC/MCI) | "
    "**Servicios Tarde:** `TU` (Tópico de Urgencias), `CE` (Consultorio de Medicina) | "
    "**Domingos (`🔒`):** Bloqueados para médicos, editables solo por el Administrador."
)

curr_uid = st.session_state["user"]["id"] if st.session_state["user"] else None

html = ["<div style='overflow-x: auto;'><table class='table-matrix'>"]

# Cabecera Fila 1: Días
html.append("<tr>")
html.append("<th style='text-align:left; min-width:210px;'>MÉDICO CIRUJANO</th><th>COND.</th><th>CMP</th>")
for d in days:
    cls = "col-hol" if d["is_holiday"] else ("col-sun" if d["is_sunday"] else "")
    sun_lock = " 🔒" if d["is_sunday"] and not is_admin_user else ""
    hol_tag = "<br><small style='color:#fca5a5;'>FER</small>" if d["is_holiday"] else ""
    html.append(f"<th class='{cls}'>{d['day']}{sun_lock}{hol_tag}</th>")
html.append("<th>6h</th><th>12h</th><th>DES</th><th>TOTAL</th><th>META</th><th>ESTADO</th>")
html.append("</tr>")

# Cabecera Fila 2: Días de semana
html.append("<tr>")
html.append("<th colspan='3' style='text-align:left; font-weight:normal; font-size:10px; background-color:#334155;'>Días de la semana (Domingos bloqueados para médicos)</th>")
for d in days:
    cls = "col-sun" if d["is_sunday"] else ("col-sat" if d["is_saturday"] else "")
    html.append(f"<th class='{cls}' style='font-size:10px; background-color:#334155; color:white;'>{d['weekday_short']}</th>")
for _ in range(6):
    html.append("<th style='background-color:#334155;'></th>")
html.append("</tr>")

# Filas de Médicos
for doc in doctors_only:
    is_my_row = (curr_uid == doc["id"])
    row_cls = "row-active" if is_my_row else ""
    d_sum = doc_summary.get(doc["id"], {"total_equivalent_shifts": 0, "count_6h": 0, "count_12h": 0, "count_des": 0, "days": {}})
    
    my_badge = '<span style="background:#2563eb; color:white; font-size:9px; padding:1px 4px; border-radius:4px;">MI PERFIL</span>' if is_my_row else ''
    html.append(f"<tr class='{row_cls}'>")
    html.append(f"<td style='text-align:left; padding-left:8px;'>{doc['name']} {my_badge}</td>")
    html.append(f"<td><small>{doc['condition']}</small></td>")
    html.append(f"<td><small>{doc['cmp']}</small></td>")

    for d in days:
        cell_cls = "col-sun" if d["is_sunday"] else ("col-hol" if d["is_holiday"] else ("col-sat" if d["is_saturday"] else ""))
        shift = d_sum["days"].get(d["day"])
        
        if shift:
            sp = shift["shift_period"]
            srv = shift["service"]
            if sp == "12h":
                b_html = f"<span class='badge-shift badge-12h'>12h<br><small>{srv}</small></span>"
            elif sp == "DES":
                b_html = "<span class='badge-shift badge-DES'>DES</span>"
            else:
                tag = f"{sp}-{srv}"
                b_cls = f"badge-{tag}" if f"badge-{tag}" in ("badge-M-TU", "badge-M-CE", "badge-M-TS", "badge-T-TU", "badge-T-CE") else "badge-12h"
                b_html = f"<span class='badge-shift {b_cls}'>{tag}</span>"
        else:
            b_html = "<span class='badge-empty'>🔒</span>" if (d["is_sunday"] and not is_admin_user) else "<span class='badge-empty'>•</span>"

        html.append(f"<td class='{cell_cls}'>{b_html}</td>")

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
st.markdown("".join(html), unsafe_allow_html=True)

# -------------------------------------------------------------------
# ALERTAS DE COBERTURA DE SERVICIOS DIARIOS Y APROBACIÓN POR ADMIN
# -------------------------------------------------------------------
if len(service_alert_days) > 0:
    st.markdown("---")
    with st.expander(
        f"🚨 Alertas de Cobertura de Servicios Diarios ({len(unapproved_service_alert_days)} pendientes de aprobación del Administrador)",
        expanded=(len(unapproved_service_alert_days) > 0)
    ):
        st.markdown(
            "**Regla de Cobertura de Servicios:** En las **Mañanas** se debe cubrir `Tópico de Urgencias (TU)`, `Consultorio de Medicina (CE)` y `Telesalud/TBC/MCI (TS)`. "
            "En las **Tardes** se debe cubrir `Tópico de Urgencias (TU)` y `Consultorio de Medicina (CE)`. "
            "**Solo el Administrador puede aprobar el día en el que exista esa discrepancia.**"
        )

        if is_admin_user and len(unapproved_service_alert_days) > 0:
            if st.button("✅ Aprobar Todos los Días con Discrepancia de Cobertura (Modo Administrador)", type="primary"):
                cnt = approve_all_day_discrepancies(selected_year, selected_month, st.session_state["user"]["name"])
                st.session_state["shift_saved_confirmation"] = f"Se aprobaron exitosamente {cnt} día(s) con discrepancia de cobertura por el Administrador."
                st.rerun()

        for d_num in service_alert_days:
            c_info = coverage[d_num]
            col_a1, col_a2 = st.columns([3.5, 1.5])
            with col_a1:
                status_badge = "🟢 **[APROBADO POR ADMINISTRADOR]**" if c_info["is_admin_approved_day"] else "🔴 **[PENDIENTE DE APROBACIÓN ADMIN]**"
                st.markdown(
                    f"**Día {d_num:02d} ({c_info['weekday_name']})** {status_badge} — {c_info['alert_message']}"
                )
            with col_a2:
                if c_info["is_admin_approved_day"]:
                    if is_admin_user:
                        if st.button(f"Revocar Día {d_num:02d}", key=f"rev_day_{d_num}"):
                            revoke_day_approval(selected_year, selected_month, d_num, st.session_state["user"]["name"])
                            st.rerun()
                    else:
                        st.caption(f"Aprobado por {c_info['admin_approval_info']['approved_by']}")
                else:
                    if is_admin_user:
                        if st.button(f"✅ Aprobar Día {d_num:02d}", key=f"app_day_{d_num}", type="primary"):
                            approve_day_discrepancy(selected_year, selected_month, d_num, st.session_state["user"]["name"], c_info["alert_message"])
                            st.session_state["shift_saved_confirmation"] = f"Día {d_num:02d}/{selected_month:02d}/{selected_year} aprobado por el Administrador."
                            st.rerun()
                    else:
                        st.caption("🔒 Solo el Administrador puede aprobar este día")

# -------------------------------------------------------------------
# CENTRO DE DISCREPANCIAS POR COLISIÓN DE PUESTO (SI EXISTEN)
# -------------------------------------------------------------------
if len(open_discs) > 0:
    with st.expander("⚠️ Discrepancias por Colisión de Servicio entre Médicos", expanded=True):
        for disc in open_discs:
            st.markdown(f"**Día {disc['day']} ({disc['shift_period']})**: {disc['description']}")
            c_desc, c_btn = st.columns([3, 1])
            with c_desc:
                st.info("💡 Alternativa Sugerida: Reasignar al servicio complementario libre.")
            with c_btn:
                if st.button("Aceptar Sugerencia", key=f"btn_disc_{disc['id']}"):
                    if apply_discrepancy_alternative(disc["id"]):
                        st.session_state["shift_saved_confirmation"] = "Discrepancia resuelta y turno grabado."
                        st.rerun()

# -------------------------------------------------------------------
# RESUMEN DE REGLAS VIGENTES DE LOS MÉDICOS
# -------------------------------------------------------------------
with st.expander("📋 Ver Reglas de Programación Institucional por Médico (Octubre - Noviembre)", expanded=False):
    st.markdown("""
    - **Meta Mensual:** **25 turnos** por médico menos los feriados nacionales que caigan de **lunes a sábado**.
    - **Domingos:** Bloqueados para los médicos; únicamente el **Administrador** puede programar en domingo.
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
# PANELES EXCLUSIVOS DE ADMINISTRACIÓN / JEFATURA
# -------------------------------------------------------------------
if is_admin_user:
    st.markdown("---")
    st.subheader("👑 Panel de Control de Jefatura Médica (ADMIN)")

    adm_tab1, adm_tab2 = st.tabs(["🛡️ Auditoría y Trazabilidad de Acciones", "👥 Gestión de Personal y PINes"])

    with adm_tab1:
        st.markdown("##### Registro de Auditoría en Tiempo Real (`audit_logs`)")
        with get_connection() as conn:
            logs = conn.execute("""
                SELECT timestamp, doctor_name, action, details
                FROM audit_logs
                ORDER BY id DESC
                LIMIT 50
            """).fetchall()
        
        if logs:
            df_logs = pd.DataFrame([dict(row) for row in logs])
            df_logs.rename(columns={
                "timestamp": "Fecha y Hora",
                "doctor_name": "Usuario / Médico",
                "action": "Acción Realizada",
                "details": "Detalles"
            }, inplace=True)
            st.dataframe(df_logs, use_container_width=True, hide_index=True)
        else:
            st.info("No hay registros de auditoría aún.")

    with adm_tab2:
        st.markdown("##### Nómina Médica Asistencial")
        doc_rows = [{
            "ID": d["id"],
            "Médico Cirujano": d["name"],
            "Condición": d["condition"],
            "CMP": d["cmp"],
            "Rol": d["role"].capitalize()
        } for d in doctors_list]
        st.dataframe(pd.DataFrame(doc_rows), use_container_width=True, hide_index=True)

        st.markdown("##### 🔑 Restablecer PIN de Acceso a un Médico")
        c_p1, c_p2, c_p3 = st.columns([2, 1.5, 1])
        with c_p1:
            rst_doc_id = st.selectbox(
                "Seleccionar Médico para Reset de PIN:",
                [d["id"] for d in doctors_only],
                format_func=lambda did: next((f"{d['name']} (CMP: {d['cmp']})" for d in doctors_only if d["id"] == did), str(did)),
                key="rst_doc_select"
            )
        with c_p2:
            rst_pin = st.text_input("Nuevo PIN temporal (mín 4 dígitos):", value="1234", key="rst_pin_input")
        with c_p3:
            st.write("")
            st.write("")
            if st.button("Restablecer PIN", key="btn_rst_pin", type="primary"):
                if len(rst_pin) < 4:
                    st.error("El PIN debe tener al menos 4 dígitos.")
                else:
                    target_d = next(d for d in doctors_only if d["id"] == rst_doc_id)
                    with get_connection() as conn:
                        conn.execute("UPDATE doctors SET pin_hash = ? WHERE id = ?", (hash_pin(rst_pin), rst_doc_id))
                        conn.commit()
                        log_audit(st.session_state["user"]["id"], st.session_state["user"]["name"], "Reset de PIN", f"Se restableció el PIN de {target_d['name']} (CMP {target_d['cmp']})", conn=conn)
                    st.success(f"¡PIN de {target_d['name']} restablecido exitosamente a: {rst_pin}!")

st.caption("IPRESS Salud Perú © 2026 - Sistema de Programación Asistencial Oficial.")
