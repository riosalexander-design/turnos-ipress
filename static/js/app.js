/**
 * app.js
 * Lógica Frontend del Sistema de Programación de Turnos de la IPRESS.
 * Maneja sesión médica individual, matriz interactiva con protección de filas,
 * resolución de discrepancias con plazo de 72h (máx día 10 00:00h) y aprobación oficial.
 */

let state = {
    year: 2026,
    month: 10,
    currentUser: null,
    monthInfo: null,
    scheduleData: null,
    doctorsList: [],
    activeCell: null // { doctorId, day, currentShift }
};

document.addEventListener("DOMContentLoaded", async () => {
    // Configurar selectores de año y mes
    const monthSelect = document.getElementById("selectMonth");
    const yearSelect = document.getElementById("selectYear");
    
    state.month = parseInt(monthSelect.value);
    state.year = parseInt(yearSelect.value);

    monthSelect.addEventListener("change", (e) => {
        state.month = parseInt(e.target.value);
        loadAll();
    });

    yearSelect.addEventListener("change", (e) => {
        state.year = parseInt(e.target.value);
        loadAll();
    });

    document.getElementById("btnReload").addEventListener("click", () => {
        loadAll();
    });

    // Cargar médicos y sesión
    await loadDoctors();
    await loadCurrentUser();
    await loadAll();
});

// -------------------------------------------------------------
// CARGA DE DATOS Y API
// -------------------------------------------------------------

async function loadCurrentUser() {
    try {
        const res = await fetch("/api/auth/me");
        const data = await res.json();
        if (data.authenticated) {
            state.currentUser = data.user;
        } else {
            state.currentUser = null;
        }
        renderUserBar();
        renderDoctorsSwitchMenu();
    } catch (err) {
        console.error("Error al cargar usuario:", err);
    }
}

async function loadDoctors() {
    try {
        const res = await fetch("/api/doctors");
        state.doctorsList = await res.json();
        renderDoctorsSwitchMenu();
    } catch (err) {
        console.error("Error al cargar médicos:", err);
    }
}

async function loadAll() {
    try {
        // Cargar calendario del mes y meta calculada (25 - feriados lun-sáb)
        const resMonth = await fetch(`/api/month-info?year=${state.year}&month=${state.month}`);
        state.monthInfo = await resMonth.json();

        // Cargar programación completa
        const resSched = await fetch(`/api/schedule?year=${state.year}&month=${state.month}`);
        state.scheduleData = await resSched.json();

        renderKPIs();
        renderApprovalBanner();
        renderMatrix();
    } catch (err) {
        console.error("Error al cargar datos:", err);
    }
}

// -------------------------------------------------------------
// RENDERIZADO DE USUARIO Y BARRA SUPERIOR
// -------------------------------------------------------------

function renderUserBar() {
    if (!state.currentUser) {
        document.getElementById("currentUserLabel").innerText = "Sin Sesión Médica";
        document.getElementById("currentUserCmp").innerText = "Ingresa con tu CMP y PIN";
        document.getElementById("userShortName").innerText = "Iniciar Sesión";
        return;
    }
    document.getElementById("currentUserLabel").innerText = state.currentUser.name;
    document.getElementById("currentUserCmp").innerText = `CMP: ${state.currentUser.cmp} | ${state.currentUser.condition}`;
    document.getElementById("userShortName").innerText = state.currentUser.name.split(",")[0];
}

function renderDoctorsSwitchMenu() {
    const container = document.getElementById("doctorsSwitchList");
    container.innerHTML = "";

    state.doctorsList.forEach(doc => {
        const btn = document.createElement("button");
        btn.className = "w-full text-left px-3 py-2 text-xs hover:bg-slate-50 flex items-center justify-between border-b border-slate-50 transition";
        btn.onclick = () => openLoginModalForDoctor(doc);

        const isCurrent = state.currentUser && state.currentUser.id === doc.id;
        btn.innerHTML = `
            <div>
                <div class="font-bold ${isCurrent ? 'text-blue-600' : 'text-slate-800'}">${doc.name}</div>
                <div class="text-[10px] text-slate-400">CMP: ${doc.cmp} (${doc.condition})</div>
            </div>
            ${isCurrent ? '<span class="text-[10px] bg-blue-100 text-blue-700 font-bold px-1.5 py-0.5 rounded">Activo</span>' : '<span class="text-[10px] text-slate-400"><i class="fa-solid fa-lock"></i> PIN</span>'}
        `;
        container.appendChild(btn);
    });

    // Opciones de sesión
    const optionsDiv = document.createElement("div");
    optionsDiv.className = "border-t border-slate-100 mt-1 pt-1 px-2 space-y-1";

    if (state.currentUser) {
        optionsDiv.innerHTML = `
            <button onclick="openChangePinModal()" class="w-full text-left px-3 py-1.5 text-xs text-indigo-700 hover:bg-indigo-50 rounded flex items-center gap-2">
                <i class="fa-solid fa-key"></i> Cambiar mi PIN
            </button>
            <button onclick="handleLogout()" class="w-full text-left px-3 py-1.5 text-xs text-rose-600 hover:bg-rose-50 rounded flex items-center gap-2 font-semibold">
                <i class="fa-solid fa-right-from-bracket"></i> Cerrar Sesión
            </button>
        `;
    } else {
        optionsDiv.innerHTML = `
            <button onclick="openLoginModal()" class="w-full text-left px-3 py-1.5 text-xs text-blue-600 hover:bg-blue-50 rounded flex items-center gap-2 font-bold">
                <i class="fa-solid fa-right-to-bracket"></i> Iniciar Sesión con PIN
            </button>
        `;
    }
    container.appendChild(optionsDiv);
}

function populateDoctorLoginSelect() {
    const sel = document.getElementById("loginDoctorSelect");
    if (!sel || !state.doctorsList) return;
    const curVal = sel.value;
    sel.innerHTML = '<option value="">-- Elige tu nombre de la lista --</option>';
    state.doctorsList.forEach(d => {
        const opt = document.createElement("option");
        opt.value = d.id;
        const tag = d.role === "admin" ? "JEFATURA" : (d.role === "rrhh" ? "RRHH" : d.condition);
        opt.textContent = `${d.name} (${tag})`;
        sel.appendChild(opt);
    });
    if (curVal) sel.value = curVal;
}

function openLoginModalForDoctor(doc) {
    populateDoctorLoginSelect();
    const sel = document.getElementById("loginDoctorSelect");
    if (sel) sel.value = doc.id;
    document.getElementById("loginPin").value = "";
    document.getElementById("loginErrorMsg").classList.add("hidden");
    openModal("modalLogin");
    setTimeout(() => {
        const pinInput = document.getElementById("loginPin");
        if (pinInput) pinInput.focus();
    }, 150);
}

async function handleLogout() {
    try {
        await fetch("/api/auth/logout", { method: "POST" });
        state.currentUser = null;
        renderUserBar();
        renderDoctorsSwitchMenu();
        await loadAll();
    } catch (err) {
        console.error("Error al cerrar sesión:", err);
    }
}

// -------------------------------------------------------------
// RENDERIZADO DE INDICADORES (KPIS)
// -------------------------------------------------------------

function renderKPIs() {
    if (!state.monthInfo || !state.scheduleData) return;

    const quota = state.monthInfo.monthly_quota;
    const holCount = state.monthInfo.holidays_mon_to_sat_count;
    const curUserId = state.currentUser ? state.currentUser.id : null;
    const summary = state.scheduleData.quota_summary.doctors_summary;

    // KPI 1: Meta del Mes
    document.getElementById("kpiMonthQuota").innerText = quota;
    document.getElementById("kpiHolidaysInfo").innerHTML = `
        <i class="fa-solid fa-circle-check text-emerald-500"></i>
        <span>25 turnos base - ${holCount} feriado(s) lun-sáb</span>
    `;

    // KPI 2: Mi Programación
    if (curUserId && summary[curUserId]) {
        const myData = summary[curUserId];
        document.getElementById("kpiMyShifts").innerText = myData.total_equivalent_shifts;
        document.getElementById("kpiMyShiftsTotal").innerText = `/ ${quota} turnos`;
        const pct = Math.min(100, Math.round((myData.total_equivalent_shifts / quota) * 100));
        document.getElementById("kpiMyProgressBar").style.width = `${pct}%`;
        if (myData.total_equivalent_shifts === quota) {
            document.getElementById("kpiMyProgressBar").className = "bg-emerald-500 h-1.5 rounded-full";
        } else if (myData.total_equivalent_shifts > quota) {
            document.getElementById("kpiMyProgressBar").className = "bg-rose-500 h-1.5 rounded-full";
        } else {
            document.getElementById("kpiMyProgressBar").className = "bg-amber-500 h-1.5 rounded-full";
        }

        // KPI 3: Reglas de 12h
        document.getElementById("kpi12hShifts").innerText = myData.count_12h;
        if (myData.is_solano) {
            document.getElementById("kpi12hTarget").innerText = "/ 5 obligatorios";
            document.getElementById("kpi12hRuleText").innerText = myData.count_12h >= 5 ? "¡Meta de 5 turnos de 12h alcanzada!" : `Faltan ${5 - myData.count_12h} turnos de 12h`;
        } else {
            document.getElementById("kpi12hTarget").innerText = "turnos";
            document.getElementById("kpi12hRuleText").innerText = myData.count_12h >= 1 ? "Viernes de 12h cubierto (Sáb libre)" : "Derecho a 1 viernes de 12h al mes";
        }
    }

    // KPI 4: Discrepancias
    const openDiscs = state.scheduleData.discrepancies.filter(d => d.status === "open");
    const discCount = openDiscs.length;
    document.getElementById("kpiDiscrepanciesCount").innerText = discCount;
    document.getElementById("btnDiscCount").innerText = discCount;

    if (discCount > 0) {
        document.getElementById("kpiDiscrepanciesBadgeIcon").className = "w-7 h-7 rounded-lg bg-amber-100 text-amber-700 flex items-center justify-center font-bold animate-soft-pulse";
        document.getElementById("kpiDiscrepanciesText").innerHTML = `<span class="text-amber-600 font-bold">${discCount} colisión(es) activa(s)</span>`;
        document.getElementById("btnDiscrepancies").className = "bg-amber-100 hover:bg-amber-200 text-amber-800 font-semibold text-xs px-3.5 py-2 rounded-lg border border-amber-300 flex items-center gap-2 transition animate-soft-pulse";
    } else {
        document.getElementById("kpiDiscrepanciesBadgeIcon").className = "w-7 h-7 rounded-lg bg-emerald-100 text-emerald-700 flex items-center justify-center font-bold";
        document.getElementById("kpiDiscrepanciesText").innerText = "Sin discrepancias pendientes";
        document.getElementById("btnDiscrepancies").className = "bg-slate-100 hover:bg-slate-200 text-slate-700 font-medium text-xs px-3.5 py-2 rounded-lg border border-slate-300/80 flex items-center gap-2 transition";
    }

    // KPI 5: Límite de Subsanación
    document.getElementById("kpiDeadlineSubtext").innerText = `Límite: ${state.scheduleData.deadline_str}`;
    document.getElementById("matrixMonthLabel").innerText = `${state.monthInfo.month_name} ${state.year}`;
}

// -------------------------------------------------------------
// RENDERIZADO DEL BANNER DE APROBACIÓN
// -------------------------------------------------------------

function renderApprovalBanner() {
    const banner = document.getElementById("approvalBanner");
    const status = state.scheduleData.approval_status;
    const openDiscs = state.scheduleData.discrepancies.filter(d => d.status === "open");

    if (status && status.is_approved) {
        banner.className = "rounded-2xl p-4 shadow-sm border bg-gradient-to-r from-emerald-50 via-teal-50 to-green-50 border-emerald-300 flex flex-wrap items-center justify-between gap-4 block";
        banner.innerHTML = `
            <div class="flex items-center gap-3">
                <div class="w-12 h-12 rounded-full approval-seal flex items-center justify-center text-amber-900 text-2xl font-black shadow-md">
                    <i class="fa-solid fa-certificate"></i>
                </div>
                <div>
                    <div class="flex items-center gap-2">
                        <span class="text-sm font-black text-emerald-800 uppercase tracking-wide">HORARIO APROBADO</span>
                        <span class="text-[10px] bg-emerald-600 text-white font-bold px-2 py-0.5 rounded-full">Oficial IPRESS</span>
                    </div>
                    <p class="text-xs text-emerald-900 mt-0.5">
                        El rol mensual cumple con todos los turnos (${state.monthInfo.monthly_quota} por médico), dotación mínima (&ge; 2 médicos) y reglas de descanso.
                    </p>
                    <div class="text-[11px] text-emerald-700 font-mono mt-1">
                        Aprobado por: ${status.approved_by} | Código de Integridad: #${status.approval_hash || 'OK-2026'}
                    </div>
                </div>
            </div>
            <div class="flex items-center gap-2">
                <button onclick="downloadExcel()" class="bg-emerald-600 hover:bg-emerald-700 text-white font-bold text-xs px-4 py-2 rounded-xl shadow transition flex items-center gap-2">
                    <i class="fa-solid fa-file-excel"></i> Descargar Rol Oficial (.xlsx)
                </button>
            </div>
        `;
    } else if (openDiscs.length > 0) {
        banner.className = "rounded-2xl p-4 shadow-sm border bg-amber-50 border-amber-300 flex flex-wrap items-center justify-between gap-4 block";
        banner.innerHTML = `
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 rounded-full bg-amber-200 text-amber-800 flex items-center justify-center text-lg">
                    <i class="fa-solid fa-triangle-exclamation"></i>
                </div>
                <div>
                    <h4 class="text-xs font-bold text-amber-900 uppercase">Atención: ${openDiscs.length} Discrepancia(s) entre Médicos</h4>
                    <p class="text-xs text-amber-800">
                        Hay puestos con sobrecupo o colisión. Revisa las alternativas sugeridas por el sistema o ingresa un acuerdo bilateral.
                    </p>
                    <div class="text-[11px] text-amber-700 font-semibold mt-0.5">
                        <i class="fa-regular fa-clock"></i> Plazo de resolución: 72 horas | <strong>Tope máximo: Día 10 a las 00:00 hrs</strong>
                    </div>
                </div>
            </div>
            <div>
                <button onclick="openDiscrepanciesModal()" class="bg-amber-600 hover:bg-amber-700 text-white font-bold text-xs px-4 py-2 rounded-xl shadow transition flex items-center gap-1.5">
                    <i class="fa-solid fa-handshake"></i> Resolver Discrepancias
                </button>
            </div>
        `;
    } else {
        banner.className = "rounded-2xl p-3 shadow-sm border bg-blue-50/80 border-blue-200 flex flex-wrap items-center justify-between gap-2 block";
        banner.innerHTML = `
            <div class="flex items-center gap-2 text-xs text-blue-900">
                <i class="fa-solid fa-circle-info text-blue-600"></i>
                <span><strong>Fase de Propuestas Asistenciales:</strong> Los médicos ingresan su propuesta de ${state.monthInfo.monthly_quota} turnos. El día 10 de cada mes la Jefatura o RRHH completan manualmente el horario del mes siguiente.</span>
            </div>
            <button onclick="triggerValidateApproval()" class="text-xs text-blue-700 hover:text-blue-900 font-bold underline">
                Validar si ya puede ser aprobado
            </button>
        `;
    }

    // Actualizar visibilidad de la advertencia institucional del día 10
    const day10Banner = document.getElementById("day10WarningBanner");
    if (day10Banner) {
        if (state.scheduleData && state.scheduleData.is_day_10_passed) {
            day10Banner.classList.remove("hidden");
        } else {
            day10Banner.classList.add("hidden");
        }
    }
}

// -------------------------------------------------------------
// RENDERIZADO DE LA MATRIZ DEL ROL MENSUAL (GRID)
// -------------------------------------------------------------

function renderMatrix() {
    if (!state.monthInfo || !state.scheduleData) return;

    const days = state.monthInfo.days;
    const doctors = state.doctorsList.filter(d => d.role === "doctor");
    const summary = state.scheduleData.quota_summary.doctors_summary;
    const coverage = state.scheduleData.coverage;
    const curUserId = state.currentUser ? state.currentUser.id : null;
    const isCurAdmin = state.currentUser && (state.currentUser.role === "admin" || state.currentUser.role === "rrhh");

    // 1. Cabecera Fila 1: Días
    const headerDaysRow = document.getElementById("headerDaysRow");
    headerDaysRow.innerHTML = `
        <th class="py-2 px-3 text-left w-64 border-r border-slate-700">MÉDICO CIRUJANO</th>
        <th class="py-2 px-2 w-16 border-r border-slate-700">COND.</th>
        <th class="py-2 px-2 w-16 border-r border-slate-700">CMP</th>
    `;

    days.forEach(d => {
        const th = document.createElement("th");
        th.className = `matrix-cell border-r border-slate-700 font-bold ${d.is_holiday ? 'bg-rose-700 text-white' : ''}`;
        th.title = d.is_holiday ? `${d.holiday_name} (Descuenta turno: ${d.discounts_shift ? 'Sí' : 'No'})` : `Día ${d.day}`;
        th.innerHTML = `
            <div class="text-[11px] leading-tight">${d.day}</div>
            ${d.is_holiday ? '<div class="text-[8px] text-rose-200">FER</div>' : ''}
        `;
        headerDaysRow.appendChild(th);
    });

    // Columnas de totales en Fila 1
    const totHeaders = ["6h", "12h", "TOTAL", "META", "ESTADO"];
    totHeaders.forEach(thName => {
        const th = document.createElement("th");
        th.className = "py-2 px-2 text-center text-[11px] border-r border-slate-700 bg-slate-900";
        th.innerText = thName;
        headerDaysRow.appendChild(th);
    });

    // 2. Cabecera Fila 2: Días de la semana
    const headerWeekdaysRow = document.getElementById("headerWeekdaysRow");
    headerWeekdaysRow.innerHTML = `
        <th colspan="3" class="py-1 px-3 text-left text-[11px] text-slate-500 border-r border-slate-200 font-normal">
            Días de la semana laboral (Lun - Sáb)
        </th>
    `;

    days.forEach(d => {
        const th = document.createElement("th");
        let cls = "matrix-cell border-r border-slate-200 text-[10px] font-semibold ";
        if (d.is_sunday) cls += "col-sunday text-slate-400";
        else if (d.is_saturday) cls += "col-saturday text-slate-500";
        else if (d.is_holiday) cls += "col-holiday text-rose-700 font-bold";
        else cls += "text-slate-600";
        th.className = cls;
        th.innerText = d.weekday_short;
        headerWeekdaysRow.appendChild(th);
    });

    for (let i = 0; i < totHeaders.length; i++) {
        const th = document.createElement("th");
        th.className = "py-1 px-2 border-r border-slate-200 text-[10px] bg-slate-100";
        headerWeekdaysRow.appendChild(th);
    }

    // 3. Filas de Médicos
    const matrixBody = document.getElementById("matrixBody");
    matrixBody.innerHTML = "";

    doctors.forEach(doc => {
        const docSummary = summary[doc.id] || { total_equivalent_shifts: 0, count_6h: 0, count_12h: 0, days: {} };
        const isMyRow = (curUserId === doc.id);
        const canEditThisRow = isMyRow || isCurAdmin;

        const tr = document.createElement("tr");
        tr.className = `border-b border-slate-200 transition ${isMyRow ? 'doctor-row-active' : 'hover:bg-slate-50/60'}`;

        // Datos del médico
        tr.innerHTML = `
            <td class="py-2.5 px-3 text-left font-bold ${isMyRow ? 'text-blue-700' : 'text-slate-800'} border-r border-slate-200">
                <div class="flex items-center justify-between gap-1">
                    <span class="truncate">${doc.name}</span>
                    ${isMyRow ? '<span class="text-[9px] bg-blue-600 text-white px-1.5 py-0.5 rounded font-bold">MI PERFIL</span>' : ''}
                </div>
            </td>
            <td class="py-2.5 px-2 text-center text-[10px] font-semibold text-slate-600 border-r border-slate-200">
                <span class="px-1.5 py-0.5 rounded ${doc.condition === 'Nombrado' ? 'bg-purple-100 text-purple-800 font-bold' : 'bg-slate-100 text-slate-700'}">
                    ${doc.condition}
                </span>
            </td>
            <td class="py-2.5 px-2 text-center text-[10px] font-mono text-slate-500 border-r border-slate-200">
                ${doc.cmp}
            </td>
        `;

        // Celdas de cada día
        days.forEach(d => {
            const td = document.createElement("td");
            td.className = `matrix-cell border-r border-slate-200 ${canEditThisRow ? 'doctor-cell-editable' : 'doctor-cell-locked'}`;
            if (!canEditThisRow) {
                td.title = `🔒 Bloqueado: Fila de ${doc.name}. Solo modificable con su propio PIN.`;
            }

            if (d.is_sunday) td.classList.add("col-sunday");
            else if (d.is_saturday) td.classList.add("col-saturday");
            else if (d.is_holiday) td.classList.add("col-holiday");

            const shift = docSummary.days[d.day];

            if (shift && shift.shift_period !== "LIBRE") {
                const sp = shift.shift_period;
                const srv = shift.service;
                let badgeClass = "shift-badge ";
                let badgeText = "";
                let subText = "";

                if (sp === "12h") {
                    badgeClass += "shift-12h";
                    badgeText = "12h";
                    subText = srv;
                } else if (sp === "DES") {
                    badgeClass += "shift-DES";
                    badgeText = "DES";
                    subText = "12h";
                } else if (sp === "M") {
                    badgeClass += `shift-M shift-M-${srv}`;
                    badgeText = `M-${srv}`;
                    subText = "Mañ";
                } else if (sp === "T") {
                    badgeClass += `shift-T shift-T-${srv}`;
                    badgeText = `T-${srv}`;
                    subText = "Tar";
                } else {
                    const tag = `${sp}-${srv}`;
                    badgeClass += `shift-${tag}`;
                    badgeText = tag;
                    subText = sp === "M" ? "Mañ" : (sp === "T" ? "Tar" : sp);
                }

                td.innerHTML = `
                    <div class="${badgeClass}" title="${getShiftFullDescription(sp, srv, doc.name, d.day)}">
                        <span>${badgeText}</span>
                        ${subText ? `<span class="text-[8px] opacity-80">${subText}</span>` : ''}
                    </div>
                `;
            } else {
                // Día libre (sin turno programado o marcado como LIBRE) - violeta con etiqueta 'Libre'
                td.innerHTML = `
                    <div class="shift-badge shift-LIBRE" title="${getShiftFullDescription('LIBRE', 'LIBRE', doc.name, d.day)}">
                        <span class="text-[9px]">Libre</span>
                    </div>
                `;
            }

            // Click listener estricto por médico
            td.onclick = () => {
                if (!state.currentUser) {
                    alert("Debes iniciar sesión con tu CMP y PIN personal para programar tus turnos.");
                    openLoginModalForDoctor(doc);
                    return;
                }
                if (!canEditThisRow) {
                    alert(`🚫 ACCESO DENEGADO - FILA BLOQUEADA:\n\nHas iniciado sesión como:\n${state.currentUser.name} (CMP ${state.currentUser.cmp})\n\nEsta fila pertenece a:\n${doc.name} (CMP ${doc.cmp})\n\nPor normativa de la IPRESS, solo el propio médico o el Administrador pueden modificar esta fila.`);
                    return;
                }
                if (d.is_sunday && !isCurAdmin) {
                    alert(`🚫 DOMINGO BLOQUEADO:\n\nEl Día ${d.day} es Domingo. Los domingos están bloqueados para los médicos y solo pueden ser habilitados o modificados por el Administrador.`);
                    return;
                }
                openShiftEditModal(doc, d, shift);
            };

            tr.appendChild(td);
        });

        // Columnas de Totales (usando insertAdjacentHTML para preservar los eventos onclick de los días)
        const isCompleted = docSummary.total_equivalent_shifts === state.monthInfo.monthly_quota;
        const totalBg = isCompleted ? 'bg-emerald-50 text-emerald-800' : 'bg-slate-50 text-slate-800';

        tr.insertAdjacentHTML("beforeend", `
            <td class="py-2 px-1 text-center font-semibold text-slate-600 border-r border-slate-200 text-[11px]">${docSummary.count_6h}</td>
            <td class="py-2 px-1 text-center font-semibold text-purple-700 border-r border-slate-200 text-[11px]">${docSummary.count_12h}</td>
            <td class="py-2 px-1 text-center font-bold border-r border-slate-200 text-[11px] ${totalBg}">${docSummary.total_equivalent_shifts}</td>
            <td class="py-2 px-1 text-center font-bold text-slate-500 border-r border-slate-200 text-[11px]">${state.monthInfo.monthly_quota}</td>
            <td class="py-2 px-2 text-center text-[10px] font-bold border-r border-slate-200">
                <span class="px-2 py-0.5 rounded-full ${isCompleted ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800'}">
                    ${docSummary.status}
                </span>
            </td>
        `);

        matrixBody.appendChild(tr);
    });

    // 4. Filas de Cobertura Diaria por Servicios (Mañana: TU, CE, TS | Tarde: TU, CE)
    const covMRow = document.getElementById("coverageMorningRow");
    covMRow.innerHTML = `
        <td colspan="3" class="py-2 px-3 text-right text-[11px] text-blue-900 border-r border-slate-300">
            Mañana (TU + CE + TS):
        </td>
    `;

    days.forEach(d => {
        const cov = coverage[d.day] || { morning_count: 0, morning_valid: false };
        const td = document.createElement("td");
        td.className = "matrix-cell border-r border-slate-200 text-[10px] font-bold ";
        if (d.is_sunday) {
            td.classList.add("col-sunday", "text-slate-400");
            td.innerText = cov.morning_count || "-";
        } else if (cov.morning_all_services_ok) {
            td.className += "bg-emerald-100 text-emerald-800";
            td.innerText = `${cov.morning_count}✓`;
            td.title = `Día ${d.day} Mañana: Cubiertos TU, CE y Telesalud/TBC/MCI (TS)`;
        } else if (cov.is_admin_approved_day) {
            td.className += "bg-amber-100 text-amber-900";
            td.innerText = `${cov.morning_count} (Aprob)`;
            td.title = `Día ${d.day} aprobado por Administrador (${cov.approved_by || 'ADMIN'})`;
        } else {
            td.className += "bg-rose-200 text-rose-900 animate-pulse cursor-pointer";
            td.innerText = `${cov.morning_count}⚠️`;
            const missing = (cov.missing_morning_services || []).join(", ");
            td.title = `ALERTA DÍA ${d.day}: Falta cubrir en Mañana: ${missing}. ${isCurAdmin ? 'Clic para aprobar este día como Administrador.' : 'Solo el Administrador puede aprobar este día.'}`;
            if (isCurAdmin) {
                td.onclick = () => approveDayDiscrepancy(d.day, cov.alert_message);
            }
        }
        covMRow.appendChild(td);
    });

    for (let i = 0; i < totHeaders.length; i++) {
        covMRow.appendChild(document.createElement("td"));
    }

    const covTRow = document.getElementById("coverageAfternoonRow");
    covTRow.innerHTML = `
        <td colspan="3" class="py-2 px-3 text-right text-[11px] text-slate-700 border-r border-slate-300">
            Tarde (TU + CE):
        </td>
    `;

    days.forEach(d => {
        const cov = coverage[d.day] || { afternoon_count: 0, afternoon_valid: false };
        const td = document.createElement("td");
        td.className = "matrix-cell border-r border-slate-200 text-[10px] font-bold ";
        if (d.is_sunday) {
            td.classList.add("col-sunday", "text-slate-400");
            td.innerText = cov.afternoon_count || "-";
        } else if (cov.afternoon_all_services_ok) {
            td.className += "bg-emerald-100 text-emerald-800";
            td.innerText = `${cov.afternoon_count}✓`;
            td.title = `Día ${d.day} Tarde: Cubiertos TU y CE`;
        } else if (cov.is_admin_approved_day) {
            td.className += "bg-amber-100 text-amber-900";
            td.innerText = `${cov.afternoon_count} (Aprob)`;
            td.title = `Día ${d.day} aprobado por Administrador (${cov.approved_by || 'ADMIN'})`;
        } else {
            td.className += "bg-rose-200 text-rose-900 animate-pulse cursor-pointer";
            td.innerText = `${cov.afternoon_count}⚠️`;
            const missing = (cov.missing_afternoon_services || []).join(", ");
            td.title = `ALERTA DÍA ${d.day}: Falta cubrir en Tarde: ${missing}. ${isCurAdmin ? 'Clic para aprobar este día como Administrador.' : 'Solo el Administrador puede aprobar este día.'}`;
            if (isCurAdmin) {
                td.onclick = () => approveDayDiscrepancy(d.day, cov.alert_message);
            }
        }
        covTRow.appendChild(td);
    });

    for (let i = 0; i < totHeaders.length; i++) {
        covTRow.appendChild(document.createElement("td"));
    }
}

async function approveDayDiscrepancy(day, alertMsg) {
    if (!confirm(`⚠️ ALERTA DE COBERTURA DE SERVICIOS (Día ${day}):\n\n${alertMsg}\n\nComo Administrador, ¿deseas APROBAR este día con esta discrepancia de cobertura?`)) {
        return;
    }
    try {
        const res = await fetch("/api/schedule/approve-day", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ year: state.year, month: state.month, day: day, action: "approve" })
        });
        const data = await res.json();
        if (data.success) {
            showConfirmationToast(data.message);
            await loadAll();
        } else {
            alert(data.error || "Solo el Administrador puede aprobar días con discrepancia.");
        }
    } catch (err) {
        console.error("Error al aprobar día:", err);
    }
}

function getShiftFullDescription(sp, srv, docName, day) {
    if (sp === "12h") return `Día ${day}: ${docName} - Turno 12h (${srv})`;
    if (sp === "DES") return `Día ${day}: ${docName} - Descanso de guardia obligatorio post-12h`;
    if (sp === "LIBRE") return `Día ${day}: ${docName} - Día Libre`;
    const period = sp === "M" ? "Mañana" : "Tarde";
    const serviceName = srv === "TU" ? "Tópico de Urgencias" : (srv === "CE" ? "Consultorio de Medicina" : "Telesalud/TBC/MCI");
    return `Día ${day}: ${docName} - ${period} en ${serviceName}`;
}

// -------------------------------------------------------------
// MODAL DE ASIGNAR / EDITAR TURNO PROPIO
// -------------------------------------------------------------

function openShiftEditModal(doctor, dayInfo, existingShift) {
    state.activeCell = {
        doctorId: doctor.id,
        doctorName: doctor.name,
        day: dayInfo.day,
        dayDate: dayInfo.date,
        weekdayName: dayInfo.weekday_name,
        existingShift: existingShift
    };

    document.getElementById("modalShiftTitle").innerText = `Programar Turno: ${doctor.name.split(',')[0]}`;
    document.getElementById("modalShiftDocName").innerText = `${doctor.name} (${doctor.condition} - CMP ${doctor.cmp})`;
    document.getElementById("modalShiftDayDate").innerText = `Día ${dayInfo.day} - ${dayInfo.weekday_name} (${dayInfo.is_holiday ? dayInfo.holiday_name : 'Día Ordinario'})`;

    // Seleccionar valor anterior o valor por defecto
    const currentPeriod = existingShift ? existingShift.shift_period : "M";
    const currentService = existingShift ? existingShift.service : "TU";

    const periodRadios = document.querySelectorAll("input[name='optPeriod']");
    periodRadios.forEach(r => {
        r.checked = (r.value === currentPeriod);
    });

    renderServiceOptions(currentPeriod, currentService);
    openModal("modalShiftEdit");
}

function handlePeriodChange() {
    const selectedPeriod = document.querySelector("input[name='optPeriod']:checked").value;
    const defaultSrv = selectedPeriod === "12h" ? "TS+TU" : "TU";
    renderServiceOptions(selectedPeriod, defaultSrv);
}

function renderServiceOptions(period, defaultService) {
    const container = document.getElementById("servicesRadioGroup");
    container.innerHTML = "";

    if (period === "DES") {
        container.innerHTML = `
            <div class="p-2.5 rounded-lg bg-purple-50 border border-purple-200 text-xs text-purple-900 flex items-center gap-2">
                <i class="fa-solid fa-moon text-purple-600"></i>
                <span>Descanso de guardia obligatorio posterior a un turno de 12 horas (Violeta).</span>
            </div>
        `;
        return;
    }

    if (period === "LIBRE") {
        container.innerHTML = `
            <div class="p-2.5 rounded-lg bg-purple-50 border border-purple-200 text-xs text-purple-900 flex items-center gap-2">
                <i class="fa-solid fa-umbrella-beach text-purple-600"></i>
                <span>Día Libre programado para el médico (Violeta con etiqueta Libre).</span>
            </div>
        `;
        return;
    }

    let options = [];
    if (period === "M") {
        options = [
            { code: "TU", name: "Tópico de Urgencias (TU)", badge: "bg-yellow-100 text-yellow-800 border border-yellow-300" },
            { code: "CE", name: "Consultorio de Medicina (CE)", badge: "bg-yellow-100 text-yellow-800 border border-yellow-300" },
            { code: "TS", name: "Telesalud/TBC/MCI (TS)", badge: "bg-yellow-100 text-yellow-800 border border-yellow-300" }
        ];
    } else if (period === "T") {
        options = [
            { code: "TU", name: "Tópico de Urgencias (TU)", badge: "bg-green-100 text-green-800 border border-green-300" },
            { code: "CE", name: "Consultorio de Medicina (CE)", badge: "bg-green-100 text-green-800 border border-green-300" }
        ];
    } else if (period === "12h") {
        options = [
            { code: "TS+TU", name: "Mañana Telesalud/TBC/MCI + Tarde Tópico de Urgencias (TS+TU)", badge: "bg-sky-100 text-sky-800 border border-sky-300" },
            { code: "TU+CE", name: "Mañana Urgencias + Tarde Consultorio (TU+CE)", badge: "bg-sky-100 text-sky-800 border border-sky-300" },
            { code: "CE+TU", name: "Mañana Consultorio + Tarde Urgencias (CE+TU)", badge: "bg-sky-100 text-sky-800 border border-sky-300" },
            { code: "TU+TU", name: "Mañana Urgencias + Tarde Urgencias (TU+TU)", badge: "bg-sky-100 text-sky-800 border border-sky-300" }
        ];
    }

    options.forEach(opt => {
        const isChecked = (opt.code === defaultService) || (!defaultService && opt.code === options[0].code);
        const label = document.createElement("label");
        label.className = "flex items-center justify-between p-2 rounded-lg border border-slate-200 cursor-pointer hover:bg-slate-50 has-[:checked]:border-blue-500 has-[:checked]:bg-blue-50 text-xs";
        label.innerHTML = `
            <div class="flex items-center gap-2">
                <input type="radio" name="optService" value="${opt.code}" ${isChecked ? 'checked' : ''}>
                <span class="font-medium text-slate-800">${opt.name}</span>
            </div>
            <span class="text-[10px] font-bold px-1.5 py-0.5 rounded ${opt.badge}">${opt.code}</span>
        `;
        container.appendChild(label);
    });
}

function showConfirmationToast(message) {
    let toast = document.getElementById("shiftToastNotification");
    if (!toast) {
        toast = document.createElement("div");
        toast.id = "shiftToastNotification";
        toast.className = "fixed bottom-5 right-5 z-50 bg-emerald-700 text-white px-5 py-3.5 rounded-xl shadow-2xl border border-emerald-400 flex items-center gap-3 text-xs font-bold transition-all duration-300";
        document.body.appendChild(toast);
    }
    toast.innerHTML = `<i class="fa-solid fa-circle-check text-lg text-emerald-200"></i> <span>${message}</span>`;
    toast.style.display = "flex";
    toast.style.opacity = "1";
    setTimeout(() => {
        toast.style.opacity = "0";
        setTimeout(() => { toast.style.display = "none"; }, 300);
    }, 4500);
}

async function handleSaveCurrentShift() {
    if (!state.activeCell) return;
    const period = document.querySelector("input[name='optPeriod']:checked").value;
    let service = "DES";
    if (period === "DES") {
        service = "DES";
    } else if (period === "LIBRE") {
        service = "LIBRE";
    } else {
        const checkedSrv = document.querySelector("input[name='optService']:checked");
        service = checkedSrv ? checkedSrv.value : "TU";
    }

    try {
        const res = await fetch("/api/shifts/update", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                year: state.year,
                month: state.month,
                day: state.activeCell.day,
                doctor_id: state.activeCell.doctorId,
                shift_period: period,
                service: service
            })
        });
        const data = await res.json();
        if (data.success) {
            closeModal("modalShiftEdit");
            await loadAll();
            showConfirmationToast(data.message || "✅ El turno fue grabado correctamente y ya es visible para todos los médicos y el Administrador.");
        } else {
            alert(data.error || "Error al guardar el turno.");
        }
    } catch (err) {
        console.error("Error al guardar:", err);
    }
}

async function handleDeleteCurrentShift() {
    if (!state.activeCell) return;
    try {
        const res = await fetch("/api/shifts/update", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                year: state.year,
                month: state.month,
                day: state.activeCell.day,
                doctor_id: state.activeCell.doctorId,
                shift_period: "CLEAR"
            })
        });
        const data = await res.json();
        if (data.success) {
            closeModal("modalShiftEdit");
            await loadAll();
            showConfirmationToast(data.message || "✅ El turno fue liberado y grabado correctamente.");
        } else {
            alert(data.error || "Error al eliminar el turno.");
        }
    } catch (err) {
        console.error("Error al borrar:", err);
    }
}

// -------------------------------------------------------------
// CENTRO DE DISCREPANCIAS Y CONCILIACIÓN (72H)
// -------------------------------------------------------------

function openDiscrepanciesModal() {
    renderDiscrepanciesList();
    openModal("modalDiscrepancies");
}

function renderDiscrepanciesList() {
    const container = document.getElementById("discrepanciesListContainer");
    container.innerHTML = "";

    const discs = state.scheduleData.discrepancies;
    if (!discs || discs.length === 0) {
        container.innerHTML = `
            <div class="text-center py-8 text-slate-500 text-xs">
                <i class="fa-solid fa-circle-check text-4xl text-emerald-500 mb-2"></i>
                <p class="font-bold text-slate-700">¡No hay discrepancias registradas!</p>
                <p class="text-[11px] text-slate-500">Todas las propuestas actuales respetan los servicios y cupos disponibles.</p>
            </div>
        `;
        return;
    }

    discs.forEach(disc => {
        const isOpen = disc.status === "open";
        let altData = {};
        try {
            altData = JSON.parse(disc.suggested_alternative);
        } catch (e) {
            altData = { text: disc.suggested_alternative };
        }

        const card = document.createElement("div");
        card.className = `p-4 rounded-xl border ${isOpen ? 'bg-amber-50/50 border-amber-300' : 'bg-slate-50 border-slate-200 opacity-75'} text-xs space-y-3`;

        card.innerHTML = `
            <div class="flex justify-between items-start">
                <div>
                    <span class="inline-block px-2 py-0.5 rounded text-[10px] font-bold uppercase ${isOpen ? 'bg-amber-200 text-amber-900' : 'bg-emerald-100 text-emerald-800'}">
                        ${isOpen ? 'Discrepancia Abierta' : 'Resuelta'}
                    </span>
                    <h4 class="font-bold text-slate-800 text-xs mt-1">${disc.description}</h4>
                    <div class="text-[11px] text-slate-500">
                        Médicos involucrados: <strong>${disc.doc1_name}</strong> y <strong>${disc.doc2_name}</strong>
                    </div>
                </div>
                <div class="text-right">
                    <div class="text-[10px] text-slate-500 font-semibold">Plazo Máx (72h / Tope Día 10 00:00h):</div>
                    <div class="text-xs font-mono font-bold text-rose-700">${disc.deadline_datetime ? disc.deadline_datetime.replace('T', ' ') : 'Día 10 a las 00:00 h'}</div>
                </div>
            </div>

            <!-- Alternativa Sugerida por el Sistema -->
            <div class="bg-white p-3 rounded-lg border border-amber-200 shadow-sm">
                <div class="font-bold text-amber-900 flex items-center gap-1.5 text-[11px]">
                    <i class="fa-solid fa-lightbulb text-amber-500"></i> ALTERNATIVA OFERTADA POR EL SISTEMA:
                </div>
                <p class="mt-1 text-slate-700 text-xs font-medium">
                    ${altData.text || 'Reasignación de servicio complementario para evitar colisión.'}
                </p>
                ${isOpen ? `
                    <div class="mt-2.5 flex items-center justify-between border-t border-slate-100 pt-2">
                        <span class="text-[10px] text-slate-500 italic">Si ambos están de acuerdo, aplica la sugerencia:</span>
                        <button onclick="acceptAlternative(${disc.id})" class="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white font-bold text-xs rounded-lg shadow-sm flex items-center gap-1.5 transition">
                            <i class="fa-solid fa-check"></i> Aceptar Sugerencia del Sistema
                        </button>
                    </div>
                ` : `
                    <div class="mt-2 text-[10px] text-emerald-700 font-semibold">
                        <i class="fa-solid fa-check-double"></i> ${disc.resolution_details || 'Acuerdo consolidado.'}
                    </div>
                `}
            </div>
        `;

        container.appendChild(card);
    });
}

async function acceptAlternative(discId) {
    try {
        const res = await fetch("/api/discrepancies/accept-suggestion", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ discrepancy_id: discId })
        });
        const data = await res.json();
        if (data.success) {
            await loadAll();
            renderDiscrepanciesList();
        } else {
            alert(data.error || "No se pudo aplicar la alternativa.");
        }
    } catch (err) {
        console.error("Error al aceptar alternativa:", err);
    }
}

// -------------------------------------------------------------
// CORTE DÍA 10 Y APROBACIÓN OFICIAL
// -------------------------------------------------------------

async function triggerAutoAssignDay10() {
    if (!confirm(`¿Deseas ejecutar el Corte del Día 10 a las 00:00 horas?\n\nEl sistema completará automáticamente los turnos de aquellos médicos que no enviaron propuesta o la dejaron incompleta, respetando todas las reglas:\n- Meta de turnos (${state.monthInfo.monthly_quota})\n- Mínimo 2 médicos por turno\n- 12h los viernes y 5 turnos de 12h para el Dr. Solano con descansos de 12h.`)) {
        return;
    }

    try {
        const res = await fetch("/api/schedule/auto-assign-day10", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ year: state.year, month: state.month })
        });
        const data = await res.json();
        if (data.success) {
            alert(data.message);
            await loadAll();
        } else {
            alert("Error al ejecutar el corte.");
        }
    } catch (err) {
        console.error("Error en auto-assign:", err);
    }
}

async function triggerValidateApproval() {
    try {
        const res = await fetch("/api/schedule/validate-approval", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ year: state.year, month: state.month })
        });
        const data = await res.json();
        if (data.is_approved) {
            alert(`✅ ¡HORARIO APROBADO!\n\n${data.message}\n\nCódigo Oficial: #${data.approval_hash}`);
            await loadAll();
        } else {
            alert(`⚠️ El horario aún no puede ser aprobado:\n\n${data.reason}`);
        }
    } catch (err) {
        console.error("Error en validación:", err);
    }
}

function downloadExcel() {
    window.location.href = `/api/export-excel?year=${state.year}&month=${state.month}`;
}

// -------------------------------------------------------------
// MODAL LOGIN
// -------------------------------------------------------------

function openLoginModal() {
    populateDoctorLoginSelect();
    const sel = document.getElementById("loginDoctorSelect");
    if (sel) sel.value = "";
    document.getElementById("loginPin").value = "";
    document.getElementById("loginErrorMsg").classList.add("hidden");
    openModal("modalLogin");
}

async function handleLoginSubmit(e) {
    e.preventDefault();
    const docId = document.getElementById("loginDoctorSelect").value;
    const pwdVal = document.getElementById("loginPin").value;
    const errorMsg = document.getElementById("loginErrorMsg");

    if (!docId) {
        errorMsg.innerText = "Por favor selecciona tu nombre de la lista.";
        errorMsg.classList.remove("hidden");
        return;
    }

    try {
        const res = await fetch("/api/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                doctor_id: parseInt(docId),
                password: pwdVal,
                pin: pwdVal
            })
        });
        const data = await res.json();
        if (data.success) {
            state.currentUser = data.user;
            closeModal("modalLogin");
            renderUserBar();
            renderDoctorsSwitchMenu();
            renderKPIs();
            renderMatrix();
        } else {
            errorMsg.innerText = data.error || "Clave incorrecta.";
            errorMsg.classList.remove("hidden");
        }
    } catch (err) {
        errorMsg.innerText = "Error de conexión al servidor.";
        errorMsg.classList.remove("hidden");
    }
}

// -------------------------------------------------------------
// LOG DE CAMBIOS Y TRAZABILIDAD (ÚLTIMOS 3 MESES / 90 DÍAS)
// -------------------------------------------------------------

let currentAuditLogs = [];

function openAuditLogModal() {
    openModal("modalAuditLog");
    loadAuditLogs();
}

async function loadAuditLogs() {
    const container = document.getElementById("auditLogContent");
    if (!container) return;
    container.innerHTML = '<div class="text-center py-6 text-slate-400 text-xs"><i class="fa-solid fa-spinner fa-spin mr-2"></i>Cargando historial de auditoría de los últimos 3 meses...</div>';
    try {
        const res = await fetch("/api/audit-logs");
        const data = await res.json();
        if (data.success && data.logs) {
            currentAuditLogs = data.logs;
            renderAuditLogsTable(currentAuditLogs);
        } else {
            container.innerHTML = `<div class="p-4 text-center text-rose-500 text-xs">${data.error || "No se pudo cargar el log de cambios."}</div>`;
        }
    } catch (err) {
        container.innerHTML = '<div class="p-4 text-center text-rose-500 text-xs">Error al conectar con el servidor de auditoría.</div>';
    }
}

function renderAuditLogsTable(logs) {
    const container = document.getElementById("auditLogContent");
    if (!container) return;
    if (!logs || logs.length === 0) {
        container.innerHTML = '<div class="text-center py-8 text-slate-400 text-xs">No hay registros de cambios en los últimos 3 meses.</div>';
        return;
    }
    let html = `
        <div class="overflow-x-auto border border-slate-200 rounded-xl">
            <table class="min-w-full divide-y divide-slate-200 text-xs">
                <thead class="bg-slate-100 text-slate-700 font-bold">
                    <tr>
                        <th class="py-2.5 px-3 text-left">Fecha y Hora</th>
                        <th class="py-2.5 px-3 text-left">Usuario Responsable</th>
                        <th class="py-2.5 px-3 text-left">Acción</th>
                        <th class="py-2.5 px-3 text-left">Detalle del Cambio</th>
                    </tr>
                </thead>
                <tbody class="divide-y divide-slate-100 bg-white">
    `;
    logs.forEach(l => {
        const tsFormatted = l.timestamp ? l.timestamp.substring(0, 19).replace("T", " ") : "";
        html += `
            <tr class="hover:bg-slate-50 transition">
                <td class="py-2 px-3 text-slate-500 whitespace-nowrap">${tsFormatted}</td>
                <td class="py-2 px-3 font-semibold text-slate-800">${l.doctor_name || "Sistema"}</td>
                <td class="py-2 px-3"><span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-indigo-50 text-indigo-700 border border-indigo-200">${l.action}</span></td>
                <td class="py-2 px-3 text-slate-600">${l.details || ""}</td>
            </tr>
        `;
    });
    html += `
                </tbody>
            </table>
        </div>
    `;
    container.innerHTML = html;
}

function filterAuditLogsTable() {
    const input = document.getElementById("auditSearchInput");
    if (!input) return;
    const term = input.value.trim().toLowerCase();
    if (!term) {
        renderAuditLogsTable(currentAuditLogs);
        return;
    }
    const filtered = currentAuditLogs.filter(l => 
        (l.doctor_name && l.doctor_name.toLowerCase().includes(term)) ||
        (l.action && l.action.toLowerCase().includes(term)) ||
        (l.details && l.details.toLowerCase().includes(term))
    );
    renderAuditLogsTable(filtered);
}

// -------------------------------------------------------------
// SIMULADOR DE TIEMPO
// -------------------------------------------------------------

function openSimTimeModal() {
    const input = document.getElementById("simTimeInput");
    const now = new Date();
    // Default a día 8 del mes a las 14:00
    input.value = `${state.year}-${String(state.month).padStart(2, '0')}-08T14:00`;
    openModal("modalSimTime");
}

function applySimTimePreset(day) {
    const input = document.getElementById("simTimeInput");
    const h = day === 10 ? "00:00" : "15:30";
    input.value = `${state.year}-${String(state.month).padStart(2, '0')}-${String(day).padStart(2, '0')}T${h}`;
}

async function saveSimTime() {
    const dt = document.getElementById("simTimeInput").value;
    if (!dt) return;
    try {
        await fetch("/api/sim-time", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ datetime: dt })
        });
        document.getElementById("simTimeBadge").innerText = `Sim: ${dt.split('T')[0]}`;
        closeModal("modalSimTime");
        await loadAll();
    } catch (err) {
        console.error(err);
    }
}

async function resetSimTime() {
    try {
        await fetch("/api/sim-time", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ reset: true })
        });
        document.getElementById("simTimeBadge").innerText = "Simulador";
        closeModal("modalSimTime");
        await loadAll();
    } catch (err) {
        console.error(err);
    }
}

// -------------------------------------------------------------
// UTILITARIOS DE MODALES
// -------------------------------------------------------------

function openModal(id) {
    const modal = document.getElementById(id);
    if (modal) {
        modal.classList.remove("hidden");
        modal.style.display = "flex";
    }
}

function closeModal(id) {
    const modal = document.getElementById(id);
    if (modal) {
        modal.classList.add("hidden");
        modal.style.display = "none";
    }
}

// -------------------------------------------------------------
// GESTIÓN DE CAMBIO DE PIN PERSONAL
// -------------------------------------------------------------

function openChangePinModal() {
    document.getElementById("changeOldPin").value = "";
    document.getElementById("changeNewPin").value = "";
    document.getElementById("changeConfirmPin").value = "";
    document.getElementById("changePinErrorMsg").classList.add("hidden");
    document.getElementById("changePinSuccessMsg").classList.add("hidden");
    openModal("modalChangePin");
}

async function handleChangePinSubmit(e) {
    e.preventDefault();
    const oldPin = document.getElementById("changeOldPin").value.trim();
    const newPin = document.getElementById("changeNewPin").value.trim();
    const confirmPin = document.getElementById("changeConfirmPin").value.trim();
    const errorMsg = document.getElementById("changePinErrorMsg");
    const successMsg = document.getElementById("changePinSuccessMsg");

    errorMsg.classList.add("hidden");
    successMsg.classList.add("hidden");

    if (newPin !== confirmPin) {
        errorMsg.innerText = "Los PINes nuevos no coinciden.";
        errorMsg.classList.remove("hidden");
        return;
    }

    try {
        const res = await fetch("/api/auth/change-pin", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ old_pin: oldPin, new_pin: newPin })
        });
        const data = await res.json();
        if (data.success) {
            successMsg.innerText = data.message;
            successMsg.classList.remove("hidden");
            setTimeout(() => {
                closeModal("modalChangePin");
                alert("¡Tu PIN ha sido actualizado exitosamente!");
            }, 1000);
        } else {
            errorMsg.innerText = data.error || "Error al actualizar el PIN.";
            errorMsg.classList.remove("hidden");
        }
    } catch (err) {
        errorMsg.innerText = "Error de conexión con el servidor.";
        errorMsg.classList.remove("hidden");
    }
}
