"""
streamlit_app.py
Aplicativo Web en Streamlit para la Programación de Turnos de Médicos Cirujanos en la IPRESS.
Compatible 100% con Streamlit Community Cloud (share.streamlit.io).
Optimizado para experiencia móvil (smartphone) y ventanas emergentes (modal dialogs).
"""

import io
from datetime import datetime
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
    initial_sidebar_state="collapsed"
)

# Estilos CSS especializados para vista móvil, badges y ventanas emergentes
st.markdown("""
<style>
    /* Estilos móviles y responsive */
    .mobile-card {
        background: white;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 10px 14px;
        margin-bottom: 8px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        display: flex;
        align-items: center;
        justify-content: space-between;
    }
    .badge-shift {
        display: inline-block;
        padding: 3px 8px;
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
    .badge-empty { color: #94a3b8; font-style: italic; font-size: 11px; }
    
    .table-matrix {
        width: 100%;
        border-collapse: collapse;
        font-size: 11px;
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
    .col-sun { background-color: #f1f5f9; }
    .col-sat { background-color: #f8fafc; }
    .col-hol { background-color: #ffe4e6; }
    .row-active { background-color: #eff6ff !important; font-weight: bold; }
    .cov-ok { background-color: #dcfce7; color: #166534; font-weight: bold; }
    .cov-err { background-color: #fee2e2; color: #991b1b; font-weight: bold; }
    
    /* Botones táctiles para smartphone */
    .stButton button {
        border-radius: 8px;
    }
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
doctors_only = [d for d in doctors_list if d["role"] == "doctor"]

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
    st.subheader("🔐 Identificación Médica")

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
# DATOS DEL MES Y REGLAS ASISTENCIALES
# -------------------------------------------------------------------
month_info = get_month_info(selected_year, selected_month)
quota = month_info["monthly_quota"]
quota_summary = get_doctor_quota_summary(selected_year, selected_month)
doc_summary = quota_summary["doctors_summary"]
coverage = get_daily_coverage(selected_year, selected_month)
days = month_info["days"]

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
# VENTANA EMERGENTE (MODAL POPUP) CON @st.dialog
# -------------------------------------------------------------------
@st.dialog("📋 Programar Turno Asistencial", width="large")
def modal_programar_turno(day_num, target_id, target_name):
    """Ventana emergente interactiva para programar un día específico."""
    d_info = days[day_num - 1]
    cur_shift = doc_summary.get(target_id, {}).get("days", {}).get(day_num)

    st.markdown(f"#### 📅 Día {day_num} de {month_info['month_name']} — {d_info['weekday_name']}")
    if d_info['is_holiday']:
        st.error(f"🚩 **FERIADO NACIONAL:** {d_info['holiday_name']} (Turno descontado de la cuota)")
    
    st.write(f"Médico: **{target_name}**")
    if cur_shift:
        st.info(f"Turno actualmente programado: **{cur_shift['shift_period']} - {cur_shift['service']}**")
    else:
        st.caption("Estado: *Libre / Sin turno asignado*")

    st.markdown("---")

    # Selección de Período
    period_options = [
        "Mañana (6h: 08:00 - 14:00)",
        "Tarde (6h: 14:00 - 20:00)",
        "12 Horas (08:00 - 20:00)",
        "Descanso 12h (DES)",
        "Libre (Borrar turno)"
    ]
    
    def_idx = 0
    if cur_shift:
        sp = cur_shift["shift_period"]
        if sp == "M": def_idx = 0
        elif sp == "T": def_idx = 1
        elif sp == "12h": def_idx = 2
        elif sp == "DES": def_idx = 3

    period_choice = st.radio("Selecciona el Período de Atención:", period_options, index=def_idx)

    sp_val = "M"
    srv_val = "TU"

    if "Mañana" in period_choice:
        sp_val = "M"
        srv_opts = [
            "Tópico de Urgencias (TU)",
            "Consultorio Externo de Medicina (CE)",
            "Telesalud (TS)"
        ]
        cur_srv = cur_shift["service"] if (cur_shift and cur_shift["shift_period"] == "M") else "TU"
        srv_idx = 0
        if cur_srv == "CE": srv_idx = 1
        elif cur_srv == "TS": srv_idx = 2
        srv_sel = st.radio("Servicio Asistencial (Mañana):", srv_opts, index=srv_idx)
        srv_val = "TU" if "TU" in srv_sel else ("CE" if "CE" in srv_sel else "TS")

    elif "Tarde" in period_choice:
        sp_val = "T"
        srv_opts = [
            "Tópico de Urgencias (TU)",
            "Consultorio Externo de Medicina (CE)"
        ]
        cur_srv = cur_shift["service"] if (cur_shift and cur_shift["shift_period"] == "T") else "TU"
        srv_idx = 1 if cur_srv == "CE" else 0
        srv_sel = st.radio("Servicio Asistencial (Tarde):", srv_opts, index=srv_idx)
        srv_val = "TU" if "TU" in srv_sel else "CE"

    elif "12 Horas" in period_choice:
        sp_val = "12h"
        st.info("💡 **Regla de 12h:** Servicio continuo de 12h. El día siguiente debe registrar descanso obligatorio (DES).")
        srv_opts = [
            "Mañana Urgencias + Tarde Consultorio (TU+CE)",
            "Mañana Consultorio + Tarde Urgencias (CE+TU)",
            "Mañana Urgencias + Tarde Urgencias (TU+TU)"
        ]
        cur_srv = cur_shift["service"] if (cur_shift and cur_shift["shift_period"] == "12h") else "TU+CE"
        srv_idx = 0
        if cur_srv == "CE+TU": srv_idx = 1
        elif cur_srv == "TU+TU": srv_idx = 2
        srv_sel = st.radio("Servicio Combinado:", srv_opts, index=srv_idx)
        srv_val = "TU+CE" if "TU+CE" in srv_sel else ("CE+TU" if "CE+TU" in srv_sel else "TU+TU")

    elif "DES" in period_choice:
        sp_val = "DES"
        srv_val = "DES"
        st.caption("Descanso obligatorio de 12 horas posterior a un turno de 12h.")

    else:
        sp_val = "CLEAR"
        srv_val = ""
        st.warning("Se eliminará cualquier asignación en este día.")

    st.markdown("---")
    c_save, c_del, c_close = st.columns([2, 1.5, 1])
    
    with c_save:
        if st.button("💾 Guardar Turno", use_container_width=True, type="primary"):
            with get_connection() as conn:
                if sp_val == "CLEAR":
                    conn.execute("DELETE FROM shifts WHERE year = ? AND month = ? AND day = ? AND doctor_id = ?",
                                 (selected_year, selected_month, day_num, target_id))
                    log_audit(target_id, target_name, "Eliminó turno", f"Día {day_num}", conn=conn)
                else:
                    conn.execute("""
                        INSERT INTO shifts (year, month, day, shift_period, service, doctor_id, is_proposal, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                        ON CONFLICT(year, month, day, doctor_id) DO UPDATE SET
                            shift_period = excluded.shift_period,
                            service = excluded.service,
                            is_proposal = 1,
                            updated_at = CURRENT_TIMESTAMP
                    """, (selected_year, selected_month, day_num, sp_val, srv_val, target_id))
                    log_audit(target_id, target_name, "Guardó propuesta", f"Día {day_num} {sp_val}-{srv_val}", conn=conn)
                conn.commit()
            st.success("¡Turno guardado correctamente!")
            st.rerun()

    with c_del:
        if cur_shift and st.button("🗑️ Borrar", use_container_width=True):
            with get_connection() as conn:
                conn.execute("DELETE FROM shifts WHERE year = ? AND month = ? AND day = ? AND doctor_id = ?",
                             (selected_year, selected_month, day_num, target_id))
                log_audit(target_id, target_name, "Eliminó turno", f"Día {day_num}", conn=conn)
                conn.commit()
            st.rerun()

    with c_close:
        if st.button("Cerrar", use_container_width=True):
            st.rerun()

# -------------------------------------------------------------------
# ENCABEZADO Y KPIS DEL MES
# -------------------------------------------------------------------
st.title(f"Programación: {month_info['month_name']} {selected_year}")

# Banner de Estado
if approval_status.get("is_approved"):
    st.success(f"🏆 **HORARIO APROBADO OFICIAL** | Aprobado por: {approval_status.get('approved_by')} | Código: `#{approval_status.get('approval_hash')}`")
elif len(open_discs) > 0:
    st.warning(f"⚠️ **Atención: Hay {len(open_discs)} discrepancia(s) entre médicos.** Plazo de conciliación: 72 horas (Tope máximo: Día 10 a las 00:00 hrs).")
else:
    st.info(f"ℹ️ **Fase de Propuestas:** Cada médico ingresa sus {quota} turnos. El corte del Día 10 autocompleta turnos vacíos respetando propuestas previas.")

# Indicadores en Columnas (adaptables a pantalla móvil)
kpi1, kpi2, kpi3, kpi4 = st.columns(4)
with kpi1:
    st.metric("Meta del Mes", f"{quota} turnos", f"25 base - {month_info['holidays_mon_to_sat_count']} feriado(s)")
with kpi2:
    if st.session_state["user"] and st.session_state["user"]["role"] == "doctor":
        my_s = doc_summary.get(st.session_state["user"]["id"], {})
        my_tot = my_s.get("total_equivalent_shifts", 0)
        st.metric("Mi Programación", f"{my_tot} / {quota}", f"{my_s.get('status', 'Incompleto')}")
    else:
        st.metric("Sesión", "Sin Iniciar", "Accede con tu PIN")
with kpi3:
    if st.session_state["user"] and st.session_state["user"]["role"] == "doctor":
        my_s = doc_summary.get(st.session_state["user"]["id"], {})
        cnt_12h = my_s.get("count_12h", 0)
        target = "5 obligatorios" if my_s.get("is_solano") else "1 derecho viernes"
        st.metric("Turnos 12h", f"{cnt_12h}", target)
    else:
        st.metric("Turnos 12h", "--", "Regla mensual")
with kpi4:
    now_dt = datetime.now()
    dl_dt, dl_str = calculate_deadline(now_dt, selected_year, selected_month)
    st.metric("Corte / Límite", "Día 10 00:00h", dl_str)

st.markdown("---")

# -------------------------------------------------------------------
# BARRA DE ACCIONES EJECUTIVAS
# -------------------------------------------------------------------
b_col1, b_col2, b_col3 = st.columns([2, 2, 2])

with b_col1:
    if st.button("🪄 Corte Día 10: Asignar Automáticamente", use_container_width=True):
        auto_assign_missing_shifts_day10(selected_year, selected_month)
        st.success("¡Turnos autocompletados con éxito respetando propuestas previas!")
        st.rerun()

with b_col2:
    is_admin = (st.session_state["user"] and st.session_state["user"].get("role") == "admin")
    if approval_status.get("is_approved"):
        if is_admin:
            if st.button("🔓 Reabrir Horario (Modo Jefatura)", use_container_width=True):
                reopen_schedule(selected_year, selected_month, st.session_state["user"]["name"])
                st.success("Horario reabierto para modificaciones.")
                st.rerun()
        else:
            st.button("✅ Horario Aprobado (Bloqueado)", disabled=True, use_container_width=True)
    else:
        if st.button("✅ Validar y Aprobar Horario", use_container_width=True, type="primary"):
            val_res = validate_and_approve_schedule(selected_year, selected_month, st.session_state["user"]["name"] if st.session_state["user"] else "Jefatura Médica IPRESS")
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
                st.info("💡 Alternativa Sugerida: Reasignación de servicio complementario para evitar colisión.")
            with c_btn:
                if st.button("Aceptar Sugerencia", key=f"btn_disc_{disc['id']}"):
                    if apply_discrepancy_alternative(disc["id"]):
                        st.success("Discrepancia resuelta exitosamente.")
                        st.rerun()

st.markdown("---")

# ===================================================================
# DOS VISTAS: 1) VISTA MÓVIL (TÁCTIL) Y 2) MATRIZ GENERAL
# ===================================================================
tab_mobile, tab_desktop = st.tabs([
    "📱 Mi Calendario Móvil (Táctil)",
    "🖥️ Matriz General IPRESS (5 Médicos)"
])

# -------------------------------------------------------------------
# PESTAÑA 1: VISTA MÓVIL ERGONÓMICA PARA SMARTPHONE
# -------------------------------------------------------------------
with tab_mobile:
    if st.session_state["user"] is None:
        st.info("🔒 **Inicia sesión en la barra lateral con tu CMP y PIN** para ver y programar tu calendario mensual cómodamente desde el celular.")
    else:
        u = st.session_state["user"]
        is_admin = (u.get("role") == "admin")

        if is_admin:
            st.subheader("📱 Gestión Móvil — Modo Jefatura")
            sel_doc_id = st.selectbox(
                "Selecciona el médico que deseas programar:",
                [d["id"] for d in doctors_only],
                format_func=lambda did: next((f"{d['name']} ({d['condition']})" for d in doctors_only if d["id"] == did), str(did)),
                key="mob_admin_doc"
            )
            target_doc = next((d for d in doctors_only if d["id"] == sel_doc_id), doctors_only[0])
            target_id = target_doc["id"]
            target_name = target_doc["name"]
        else:
            st.subheader(f"📱 Mi Calendario — {u['name']}")
            st.caption(f"CMP: {u['cmp']} | {u['condition']}")
            target_id = u["id"]
            target_name = u["name"]

        my_shifts_dict = doc_summary.get(target_id, {}).get("days", {})
        my_total = doc_summary.get(target_id, {}).get("total_equivalent_shifts", 0)

        # Barra de progreso móvil
        pct = min(100, int((my_total / quota) * 100))
        st.progress(pct / 100, text=f"Progreso: {my_total} de {quota} turnos ({pct}%)")
        
        st.markdown("👉 **Toca cualquier día para abrir la ventana emergente y elegir tu turno:**")

        # Agrupación por Semanas para scroll móvil vertical limpio
        num_days = month_info["num_days"]
        weeks_chunks = [
            ("Semana 1 (Días 1 - 7)", range(1, min(8, num_days + 1))),
            ("Semana 2 (Días 8 - 14)", range(8, min(15, num_days + 1))),
            ("Semana 3 (Días 15 - 21)", range(15, min(22, num_days + 1))),
            ("Semana 4 (Días 22 - 28)", range(22, min(29, num_days + 1))),
            ("Semana 5 (Días 29 - fin de mes)", range(29, num_days + 1))
        ]

        for w_title, w_days in weeks_chunks:
            if not list(w_days):
                continue
            with st.expander(f"🗓️ **{w_title}**", expanded=True):
                for d_num in w_days:
                    d_info = days[d_num - 1]
                    sh = my_shifts_dict.get(d_num)

                    # Etiqueta del turno
                    if sh:
                        sp = sh["shift_period"]
                        srv = sh["service"]
                        if sp == "12h":
                            shift_label = f"🟡 12h: {srv}"
                        elif sp == "DES":
                            shift_label = "🟣 Descanso 12h (DES)"
                        elif sp == "M":
                            shift_label = f"🟠 Mañana: {srv}"
                        else:
                            shift_label = f"🔵 Tarde: {srv}"
                    else:
                        shift_label = "⚪ Libre (Sin turno)"

                    if d_info["is_holiday"]:
                        shift_label += " 🚩 FERIADO"

                    # Fila táctil: Información a la izquierda, botón a la derecha
                    col_info, col_btn = st.columns([3, 1])
                    with col_info:
                        st.markdown(f"**Día {d_num}** ({d_info['weekday_short']}) — {shift_label}")
                    with col_btn:
                        # Al presionar, abre la ventana emergente directamente
                        if st.button("✏️ Elegir", key=f"mob_btn_day_{d_num}", use_container_width=True):
                            modal_programar_turno(d_num, target_id, target_name)

# -------------------------------------------------------------------
# PESTAÑA 2: MATRIZ GENERAL IPRESS (5 MÉDICOS)
# -------------------------------------------------------------------
with tab_desktop:
    st.subheader("🖥️ Matriz General de Programación Asistencial")
    
    # Selector rápido para abrir modal emergente desde la vista general
    if st.session_state["user"] is not None:
        u = st.session_state["user"]
        is_admin = (u.get("role") == "admin")
        
        st.write("👉 **Selecciona un día para abrir la ventana emergente:**")
        sel_col_doc, sel_col_days = st.columns([1.5, 3])
        
        with sel_col_doc:
            if is_admin:
                quick_doc_id = st.selectbox(
                    "Médico:",
                    [d["id"] for d in doctors_only],
                    format_func=lambda did: next((d["name"].split(",")[0] for d in doctors_only if d["id"] == did), str(did)),
                    key="quick_admin_doc"
                )
                quick_doc_name = next((d["name"] for d in doctors_only if d["id"] == quick_doc_id), "")
            else:
                quick_doc_id = u["id"]
                quick_doc_name = u["name"]
                st.info(f"Médico: **{u['name'].split(',')[0]}**")

        with sel_col_days:
            # Botonera de días usando st.pills
            day_chosen = st.pills(
                "Día a programar:",
                options=[d["day"] for d in days],
                key="pills_quick_day"
            )
            if day_chosen:
                modal_programar_turno(day_chosen, quick_doc_id, quick_doc_name)

    # Renderizado HTML de la Matriz General
    curr_uid = st.session_state["user"]["id"] if st.session_state["user"] else None
    html = ["<div style='overflow-x: auto;'><table class='table-matrix'>"]

    # Cabecera 1
    html.append("<tr>")
    html.append("<th style='text-align:left; min-width:180px;'>MÉDICO CIRUJANO</th><th>COND.</th><th>CMP</th>")
    for d in days:
        cls = "col-hol" if d["is_holiday"] else ""
        html.append(f"<th class='{cls}'>{d['day']}{'<br><small style=\"color:#fca5a5;\">FER</small>' if d['is_holiday'] else ''}</th>")
    html.append("<th>6h</th><th>12h</th><th>TOTAL</th><th>META</th><th>ESTADO</th>")
    html.append("</tr>")

    # Cabecera 2 (Días de semana)
    html.append("<tr>")
    html.append("<th colspan='3' style='text-align:left; font-size:10px; background-color:#334155;'>Días de la semana</th>")
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

st.caption("IPRESS Salud Perú © 2026 - Sistema de Programación Asistencial Oficial.")
