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
    calculate_deadline
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
        padding: 2px 6px;
        border-radius: 6px;
        font-size: 11px;
        font-weight: bold;
        text-align: center;
        margin: 1px;
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
        font-size: 12px;
        text-align: center;
    }
    .table-matrix th, .table-matrix td {
        border: 1px solid #cbd5e1;
        padding: 4px 2px;
        min-width: 32px;
    }
    .table-matrix th {
        background-color: #1e293b;
        color: white;
    }
    .col-sun { background-color: #f1f5f9; }
    .col-sat { background-color: #f8fafc; }
    .col-hol { background-color: #ffe4e6; }
    .row-active { background-color: #eff6ff !important; font-weight: bold; }
    .cov-ok { background-color: #dcfce7; color: #166534; font-weight: bold; }
    .cov-err { background-color: #fee2e2; color: #991b1b; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# -------------------------------------------------------------------
# GESTIÓN DE SESIÓN
# -------------------------------------------------------------------
if "user" not in st.session_state:
    st.session_state["user"] = None

def get_all_doctors():
    with get_connection() as conn:
        return conn.execute("SELECT id, name, condition, cmp, role FROM doctors WHERE is_active = 1").fetchall()

doctors_list = [dict(d) for d in get_all_doctors()]

# -------------------------------------------------------------------
# SIDEBAR: AUTENTICACIÓN Y FILTROS
# -------------------------------------------------------------------
with st.sidebar:
    st.title("🏥 IPRESS Salud")
    st.caption("Sector Asistencial - Programación Mensual")
    st.markdown("---")

    # Selector de Mes y Año
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
    st.subheader("🔐 Acceso por Médico")

    if st.session_state["user"] is None:
        doc_options = {d["cmp"]: f"{d['name']} ({d['cmp']})" for d in doctors_list if d["role"] == "doctor"}
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
                        st.success(f"¡Bienvenido(a), {doc_db['name'].split(',')[0]}!")
                        st.rerun()
                    else:
                        st.error("PIN incorrecto.")
    else:
        u = st.session_state["user"]
        st.success(f"**{u['name']}**")
        st.caption(f"CMP: {u['cmp']} | Condición: {u['condition']} ({u['role'].capitalize()})")

        if st.button("Cerrar Sesión", use_container_width=True):
            st.session_state["user"] = None
            st.rerun()

        # Expander para cambiar PIN
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
                            log_audit(u["id"], u["name"], "Cambio de PIN Streamlit", "PIN actualizado")
                            st.success("¡PIN actualizado con éxito!")
                        else:
                            st.error("El PIN actual no es correcto.")

# -------------------------------------------------------------------
# DATOS DEL MES Y REGLAS
# -------------------------------------------------------------------
month_info = get_month_info(selected_year, selected_month)
quota = month_info["monthly_quota"]
quota_summary = get_doctor_quota_summary(selected_year, selected_month)
doc_summary = quota_summary["doctors_summary"]
coverage = get_daily_coverage(selected_year, selected_month)

# Discrepancias y Estado
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

# -------------------------------------------------------------------
# ENCABEZADO Y KPIS
# -------------------------------------------------------------------
st.title(f"Programación Asistencial: {month_info['month_name']} {selected_year}")

# Banner de Estado
if approval_status.get("is_approved"):
    st.success(f"🏆 **HORARIO APROBADO OFICIAL IPRESS** | Aprobado por: {approval_status.get('approved_by')} | Código de Integridad: `#{approval_status.get('approval_hash')}`")
elif len(open_discs) > 0:
    st.warning(f"⚠️ **Atención: Hay {len(open_discs)} discrepancia(s) activas entre médicos.** Plazo de conciliación: 72 horas (Tope máximo: Día 10 a las 00:00 hrs).")
else:
    st.info(f"ℹ️ **Fase de Propuestas:** Cada médico cirujano ingresa voluntariamente su propuesta de {quota} turnos. El corte del Día 10 a las 00:00 hrs autocompleta turnos vacíos respetando propuestas previas.")

# Indicadores en Columnas
kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)
with kpi1:
    st.metric("Meta del Mes", f"{quota} turnos", f"25 base - {month_info['holidays_mon_to_sat_count']} feriados lun-sáb")
with kpi2:
    if st.session_state["user"] and st.session_state["user"]["role"] == "doctor":
        my_s = doc_summary.get(st.session_state["user"]["id"], {})
        my_tot = my_s.get("total_equivalent_shifts", 0)
        st.metric("Mi Programación", f"{my_tot} / {quota}", f"{my_s.get('status', 'Incompleto')}")
    else:
        st.metric("Mi Programación", "-- / --", "Inicia sesión")
with kpi3:
    if st.session_state["user"] and st.session_state["user"]["role"] == "doctor":
        my_s = doc_summary.get(st.session_state["user"]["id"], {})
        cnt_12h = my_s.get("count_12h", 0)
        target = "5 obligatorios" if my_s.get("is_solano") else "1 derecho viernes"
        st.metric("Turnos 12 Horas", f"{cnt_12h}", target)
    else:
        st.metric("Turnos 12 Horas", "--", "Regla mensual")
with kpi4:
    st.metric("Discrepancias", f"{len(open_discs)} abiertas", "Colisiones de servicio")
with kpi5:
    now_dt = datetime.now()
    dl_dt, dl_str = calculate_deadline(now_dt, selected_year, selected_month)
    st.metric("Corte / Límite", "Día 10 00:00h", dl_str)

st.markdown("---")

# -------------------------------------------------------------------
# BARRA DE ACCIONES
# -------------------------------------------------------------------
is_admin_user = (st.session_state["user"] is not None and st.session_state["user"].get("role") == "admin")
b_col1, b_col2, b_col3, b_col4 = st.columns([2, 2, 2, 2])

with b_col1:
    if st.button("🪄 Corte Día 10: Asignar Automáticamente", use_container_width=True, type="secondary"):
        if not is_admin_user:
            st.warning("🔒 Acción restringida: Solo la Jefatura Médica (ADMIN) puede ejecutar el corte institucional del Día 10.")
        else:
            auto_assign_missing_shifts_day10(selected_year, selected_month)
            log_audit(st.session_state["user"]["id"], st.session_state["user"]["name"], "Corte Día 10", f"Autocompletado de turnos asistenciales para {selected_month}/{selected_year}")
            st.success("¡Turnos autocompletados con éxito respetando las propuestas existentes!")
            st.rerun()

with b_col2:
    if approval_status.get("is_approved"):
        if st.button("🔄 Reabrir Horario (Jefatura)", use_container_width=True, type="secondary"):
            if not is_admin_user:
                st.warning("🔒 Acción restringida: Solo la Jefatura Médica (ADMIN) puede reabrir un horario previamente aprobado.")
            else:
                reopen_schedule(selected_year, selected_month, st.session_state["user"]["name"])
                st.success("¡Horario reabierto con éxito para modificaciones asistenciales!")
                st.rerun()
    else:
        if st.button("✅ Validar y Aprobar Horario", use_container_width=True, type="primary"):
            if not is_admin_user:
                st.warning("🔒 Acción restringida: Solo la Jefatura Médica (ADMIN) puede certificar y aprobar oficialmente el horario.")
            else:
                val_res = validate_and_approve_schedule(selected_year, selected_month, st.session_state["user"]["name"])
                if val_res["is_approved"]:
                    st.success(f"¡{val_res['message']}! Código: #{val_res['approval_hash']}")
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

with b_col4:
    if len(open_discs) > 0:
        st.info(f"{len(open_discs)} discrepancia(s) abiertas")
    else:
        st.caption("Sin discrepancias pendientes")

# -------------------------------------------------------------------
# CENTRO DE DISCREPANCIAS (SI EXISTEN)
# -------------------------------------------------------------------
if len(open_discs) > 0:
    with st.expander("⚠️ Centro de Conciliación y Discrepancias (Plazo 72h)", expanded=True):
        for disc in open_discs:
            st.markdown(f"**Día {disc['day']} ({disc['shift_period']})**: {disc['description']}")
            st.caption(f"Médicos: **{disc['doc1_name']}** vs **{disc['doc2_name']}** | Límite máximo: **{disc['deadline_datetime']}**")
            
            c_desc, c_btn = st.columns([3, 1])
            with c_desc:
                st.info(f"💡 Alternativa Sugerida por el Sistema: Reasignar servicio complementario para evitar sobrecupo.")
            with c_btn:
                if st.button("Aceptar Sugerencia", key=f"btn_disc_{disc['id']}"):
                    if apply_discrepancy_alternative(disc["id"]):
                        st.success("Discrepancia resuelta exitosamente.")
                        st.rerun()

# -------------------------------------------------------------------
# MATRIZ VISUAL DEL ROL MENSUAL
# -------------------------------------------------------------------
st.subheader("🗓️ Matriz de Turnos del Mes")

days = month_info["days"]
doctors_only = [d for d in doctors_list if d["role"] == "doctor"]
curr_uid = st.session_state["user"]["id"] if st.session_state["user"] else None

html = ["<div style='overflow-x: auto;'><table class='table-matrix'>"]

# Cabecera Fila 1: Días
html.append("<tr>")
html.append("<th style='text-align:left; min-width:200px;'>MÉDICO CIRUJANO</th><th>COND.</th><th>CMP</th>")
for d in days:
    cls = "col-hol" if d["is_holiday"] else ""
    html.append(f"<th class='{cls}'>{d['day']}{'<br><small style=\"color:#fca5a5;\">FER</small>' if d['is_holiday'] else ''}</th>")
html.append("<th>6h</th><th>12h</th><th>TOTAL</th><th>META</th><th>ESTADO</th>")
html.append("</tr>")

# Cabecera Fila 2: Días de semana
html.append("<tr>")
html.append("<th colspan='3' style='text-align:left; font-weight:normal; font-size:10px; background-color:#334155;'>Días de la semana laboral</th>")
for d in days:
    cls = "col-sun" if d["is_sunday"] else ("col-sat" if d["is_saturday"] else "")
    html.append(f"<th class='{cls}' style='font-size:10px; background-color:#334155;'>{d['weekday_short']}</th>")
for _ in range(5):
    html.append("<th style='background-color:#334155;'></th>")
html.append("</tr>")

# Filas de Médicos
for doc in doctors_only:
    is_my_row = (curr_uid == doc["id"])
    row_cls = "row-active" if is_my_row else ""
    d_sum = doc_summary.get(doc["id"], {"total_equivalent_shifts": 0, "count_6h": 0, "count_12h": 0, "days": {}})
    
    html.append(f"<tr class='{row_cls}'>")
    html.append(f"<td style='text-align:left; padding-left:8px;'>{doc['name']} {'<span style=\"background:#2563eb; color:white; font-size:9px; padding:1px 4px; border-radius:4px;\">MI PERFIL</span>' if is_my_row else ''}</td>")
    html.append(f"<td><small>{doc['condition']}</small></td>")
    html.append(f"<td><small>{doc['cmp']}</small></td>")

    for d in days:
        cell_cls = "col-sun" if d["is_sunday"] else ("col-sat" if d["is_saturday"] else ("col-hol" if d["is_holiday"] else ""))
        shift = d_sum["days"].get(d["day"])
        
        if shift:
            sp = shift["shift_period"]
            srv = shift["service"]
            if sp == "12h":
                b_html = f"<span class='badge-shift badge-12h'>12h<br><small>{srv}</small></span>"
            elif sp == "DES":
                b_html = f"<span class='badge-shift badge-DES'>DES</span>"
            else:
                tag = f"{sp}-{srv}"
                b_cls = f"badge-{tag}" if f"badge-{tag}" in ("badge-M-TU", "badge-M-CE", "badge-M-TS", "badge-T-TU", "badge-T-CE") else "badge-12h"
                b_html = f"<span class='badge-shift {b_cls}'>{tag}</span>"
        else:
            b_html = "<span class='badge-empty'>•</span>"

        html.append(f"<td class='{cell_cls}'>{b_html}</td>")

    # Totales
    is_comp = d_sum["total_equivalent_shifts"] == quota
    stat_color = "#166534" if is_comp else "#9a3412"
    html.append(f"<td>{d_sum['count_6h']}</td>")
    html.append(f"<td>{d_sum['count_12h']}</td>")
    html.append(f"<td><b>{d_sum['total_equivalent_shifts']}</b></td>")
    html.append(f"<td>{quota}</td>")
    html.append(f"<td style='color:{stat_color}; font-weight:bold;'>{d_sum.get('status', 'Incompleto')}</td>")
    html.append("</tr>")

# Filas de Cobertura
html.append("<tr>")
html.append("<td colspan='3' style='text-align:right; font-weight:bold; font-size:11px; background-color:#f8fafc;'>Médicos Mañana (Mínimo 2):</td>")
for d in days:
    cov = coverage.get(d["day"], {"morning_count": 0, "morning_valid": False})
    c_cls = "col-sun" if d["is_sunday"] else ("cov-ok" if cov["morning_valid"] else "cov-err")
    html.append(f"<td class='{c_cls}'>{cov['morning_count'] if not d['is_sunday'] else (cov['morning_count'] or '-')}</td>")
for _ in range(5):
    html.append("<td></td>")
html.append("</tr>")

html.append("<tr>")
html.append("<td colspan='3' style='text-align:right; font-weight:bold; font-size:11px; background-color:#f8fafc;'>Médicos Tarde (Mínimo 2):</td>")
for d in days:
    cov = coverage.get(d["day"], {"afternoon_count": 0, "afternoon_valid": False})
    c_cls = "col-sun" if d["is_sunday"] else ("cov-ok" if cov["afternoon_valid"] else "cov-err")
    html.append(f"<td class='{c_cls}'>{cov['afternoon_count'] if not d['is_sunday'] else (cov['afternoon_count'] or '-')}</td>")
for _ in range(5):
    html.append("<td></td>")
html.append("</tr>")

html.append("</table></div>")
st.markdown("".join(html), unsafe_allow_html=True)

# -------------------------------------------------------------------
# PANEL DE EDICIÓN DE TURNOS (AISLAMIENTO ESTRICTO POR MÉDICO / MODO JEFATURA)
# -------------------------------------------------------------------
st.markdown("---")
if st.session_state["user"] is None:
    st.info("🔒 Para ingresar o modificar turnos, inicia sesión con tu CMP y PIN en la barra lateral izquierda.")
else:
    u = st.session_state["user"]
    is_admin = (u.get("role") == "admin")

    if is_admin:
        st.subheader("🛠️ Panel de Modificación Asistencial — Modo Jefatura Médica")
        st.caption("Como Administrador / Jefatura Médica, tienes autorización para asignar, modificar o liberar turnos de cualquier médico cirujano de la IPRESS.")
        
        target_doc_id = st.selectbox(
            "Seleccionar Médico a Programar:",
            [doc["id"] for doc in doctors_only],
            format_func=lambda did: next((f"{d['name']} ({d['condition']} - CMP: {d['cmp']})" for d in doctors_only if d["id"] == did), str(did)),
            key="admin_target_doc_select"
        )
        target_doc = next((d for d in doctors_only if d["id"] == target_doc_id), doctors_only[0])
        target_id = target_doc["id"]
        target_name = target_doc["name"]
    else:
        st.subheader(f"✍️ Programar mi Propuesta de Turnos — {u['name']}")
        st.caption(f"CMP: {u['cmp']} | Condición: {u['condition']} (Modo de edición protegido de tu propia fila)")
        target_id = u["id"]
        target_name = u["name"]

    ed_col1, ed_col2, ed_col3, ed_col4 = st.columns([1, 1.5, 2, 1.5])
    
    with ed_col1:
        edit_day = st.selectbox("Selecciona Día:", [d["day"] for d in days])
        d_info = days[edit_day - 1]
        st.caption(f"{d_info['weekday_name']}{' (FERIADO)' if d_info['is_holiday'] else ''}")
        # Mostrar turno actual asignado a este médico
        current_shift_info = doc_summary.get(target_id, {}).get("days", {}).get(edit_day)
        if current_shift_info:
            st.info(f"Asignado: **{current_shift_info['shift_period']}-{current_shift_info['service']}**")
        else:
            st.caption("Sin turno asignado actualmente.")

    with ed_col2:
        period_choice = st.radio(
            "Período del Turno:",
            ["Mañana (6h)", "Tarde (6h)", "12 Horas", "Descanso 12h (DES)", "Libre (Borrar)"]
        )

    with ed_col3:
        if "Mañana" in period_choice:
            service_choice = st.radio(
                "Servicio Asistencial (Mañana):",
                ["Tópico de Urgencias (TU)", "Consultorio Externo de Medicina (CE)", "Telesalud (TS)"]
            )
            sp_val = "M"
            srv_val = "TU" if "TU" in service_choice else ("CE" if "CE" in service_choice else "TS")
        elif "Tarde" in period_choice:
            service_choice = st.radio(
                "Servicio Asistencial (Tarde):",
                ["Tópico de Urgencias (TU)", "Consultorio Externo de Medicina (CE)"]
            )
            sp_val = "T"
            srv_val = "TU" if "TU" in service_choice else "CE"
        elif "12 Horas" in period_choice:
            service_choice = st.radio(
                "Servicio Combinado (12h):",
                ["Mañana Urgencias + Tarde Consultorio (TU+CE)", "Mañana Consultorio + Tarde Urgencias (CE+TU)", "Mañana Urgencias + Tarde Urgencias (TU+TU)"]
            )
            sp_val = "12h"
            srv_val = "TU+CE" if "TU+CE" in service_choice else ("CE+TU" if "CE+TU" in service_choice else "TU+TU")
        elif "DES" in period_choice:
            st.info("Descanso obligatorio de 12 horas posterior a un turno de 12h.")
            sp_val = "DES"
            srv_val = "DES"
        else:
            st.warning("Se eliminará cualquier turno asignado en este día.")
            sp_val = "CLEAR"
            srv_val = ""

    with ed_col4:
        st.write("")
        st.write("")
        btn_label = "💾 Guardar Turno (Jefatura)" if is_admin else "💾 Guardar Mi Turno"
        if st.button(btn_label, use_container_width=True, type="primary"):
            with get_connection() as conn:
                if sp_val == "CLEAR":
                    conn.execute("DELETE FROM shifts WHERE year = ? AND month = ? AND day = ? AND doctor_id = ?",
                                 (selected_year, selected_month, edit_day, target_id))
                    log_audit(u["id"], u["name"], "Eliminó turno", f"Día {edit_day} para {target_name}", conn=conn)
                else:
                    conn.execute("""
                        INSERT INTO shifts (year, month, day, shift_period, service, doctor_id, is_proposal, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                        ON CONFLICT(year, month, day, doctor_id) DO UPDATE SET
                            shift_period = excluded.shift_period,
                            service = excluded.service,
                            is_proposal = 1,
                            updated_at = CURRENT_TIMESTAMP
                    """, (selected_year, selected_month, edit_day, sp_val, srv_val, target_id))
                    log_audit(u["id"], u["name"], "Guardó turno", f"Día {edit_day} {sp_val}-{srv_val} para {target_name}", conn=conn)
                conn.commit()
            st.success(f"Día {edit_day} para {target_name} actualizado exitosamente.")
            st.rerun()

# -------------------------------------------------------------------
# PANELES EXCLUSIVOS DE ADMINISTRACIÓN / JEFATURA
# -------------------------------------------------------------------
if st.session_state["user"] and st.session_state["user"].get("role") == "admin":
    st.markdown("---")
    st.subheader("👑 Panel de Control de Jefatura Médica")

    adm_tab1, adm_tab2 = st.tabs(["🛡️ Auditoría y Trazabilidad de Acciones", "👥 Gestión de Personal y PINes"])

    with adm_tab1:
        st.markdown("##### Registro de Auditoría en Tiempo Real (`audit_logs`)")
        st.caption("Historial inmutable de inicios de sesión, cambios de turnos, resoluciones y aprobaciones.")
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
        doc_rows = []
        for d in doctors_list:
            doc_rows.append({
                "ID": d["id"],
                "Médico Cirujano": d["name"],
                "Condición": d["condition"],
                "CMP": d["cmp"],
                "Rol": d["role"].capitalize()
            })
        st.dataframe(pd.DataFrame(doc_rows), use_container_width=True, hide_index=True)

        st.markdown("##### 🔑 Restablecer PIN de Acceso a un Médico")
        st.caption("Si un médico cirujano olvida su clave, la Jefatura puede asignarle un nuevo PIN temporal.")
        
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
                        log_audit(u["id"], u["name"], "Reset de PIN", f"Se restableció el PIN de {target_d['name']} (CMP {target_d['cmp']}) a '{rst_pin}'", conn=conn)
                    st.success(f"¡PIN de {target_d['name']} restablecido exitosamente a: {rst_pin}!")
                    st.rerun()

st.caption("IPRESS Salud Perú © 2026 - Sistema de Programación Asistencial Oficial.")
