"""
exporter.py
Generador oficial de reportes en Excel (.xlsx) con formato institucional asistencial
para la programación mensual de médicos de la IPRESS usando openpyxl.
"""

import io
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from holidays_pe import get_month_info
from database import get_connection, log_audit
from scheduler_engine import (
    get_doctor_quota_summary,
    get_daily_coverage,
    sync_discrepancies_db,
    check_shift_modification_allowed
)

# Paleta de colores asistenciales
COLOR_HEADER_BG = "1F4E79"        # Azul institucional oscuro
COLOR_HEADER_TXT = "FFFFFF"
COLOR_SUBHEADER_BG = "D9E1F2"     # Azul tenue
COLOR_WEEKEND_SAT = "E9EDF4"      # Sábado gris/azul tenue
COLOR_WEEKEND_SUN = "D9D9D9"      # Domingo gris
COLOR_HOLIDAY = "FCE4D6"          # Feriado salmón pastel

# Colores de turnos
SHIFT_COLORS = {
    "12h": {"fill": "BAE6FD", "font": "0369A1"},  # Celeste 12h
    "M-TU": {"fill": "FEF08A", "font": "713F12"}, # Amarillo Mañana Urgencias
    "M-CE": {"fill": "FEF08A", "font": "713F12"}, # Amarillo Mañana Consultorio
    "M-TS": {"fill": "FEF08A", "font": "713F12"}, # Amarillo Mañana Telesalud
    "T-TU": {"fill": "BBF7D0", "font": "14532D"}, # Verde Tarde Urgencias
    "T-CE": {"fill": "BBF7D0", "font": "14532D"}, # Verde Tarde Consultorio
    "DES":  {"fill": "EDE9FE", "font": "5B21B6"}, # Violeta descanso post-12h
    "LIBRE": {"fill": "F5F3FF", "font": "6B21A8"} # Violeta día libre
}

def generate_excel_schedule(year: int, month: int, ipress_name: str = "IPRESS SALUD PERÚ") -> io.BytesIO:
    """
    Genera un archivo Excel (.xlsx) en memoria con la programación mensual completa,
    resumen de horas/turnos por médico y validación de cobertura mínima.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = f"Rol Médicos {month}-{year}"
    ws.views.sheetView[0].showGridLines = True

    month_info = get_month_info(year, month)
    quota_data = get_doctor_quota_summary(year, month)
    coverage_data = get_daily_coverage(year, month)

    num_days = month_info["num_days"]
    quota = month_info["monthly_quota"]
    holidays_count = month_info["holidays_mon_to_sat_count"]

    # Bordes estándar
    thin_border = Border(
        left=Side(style='thin', color='BFBFBF'),
        right=Side(style='thin', color='BFBFBF'),
        top=Side(style='thin', color='BFBFBF'),
        bottom=Side(style='thin', color='BFBFBF')
    )
    header_border = Border(
        left=Side(style='medium', color='1F4E79'),
        right=Side(style='medium', color='1F4E79'),
        top=Side(style='medium', color='1F4E79'),
        bottom=Side(style='medium', color='1F4E79')
    )

    # 1. Títulos institucionales
    ws.merge_cells("A1:AP1")
    ws["A1"] = "MINISTERIO DE SALUD - GOBIERNO REGIONAL"
    ws["A1"].font = Font(name="Calibri", size=11, bold=True, color="595959")
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")

    ws.merge_cells("A2:AP2")
    ws["A2"] = f"ROL MENSUAL DE PROGRAMACIÓN ASISTENCIAL DE MÉDICOS - {ipress_name.upper()}"
    ws["A2"].font = Font(name="Calibri", size=14, bold=True, color="1F4E79")
    ws["A2"].alignment = Alignment(horizontal="center", vertical="center")

    ws.merge_cells("A3:AP3")
    subtitle = (f"MES: {month_info['month_name'].upper()} {year} | "
                f"BASE: 25 TURNOS | FERIADOS LUN-SÁB: {holidays_count} DÍAS | "
                f"META EFECTIVA MENSUAL: {quota} TURNOS POR MÉDICO")
    ws["A3"] = subtitle
    ws["A3"].font = Font(name="Calibri", size=10, italic=True, bold=True, color="262626")
    ws["A3"].alignment = Alignment(horizontal="center", vertical="center")

    ws.row_dimensions[1].height = 20
    ws.row_dimensions[2].height = 25
    ws.row_dimensions[3].height = 20

    # 2. Encabezados de tabla (Fila 5 y 6)
    start_row = 5
    ws.cell(row=start_row, column=1, value="N°").font = Font(bold=True, color=COLOR_HEADER_TXT)
    ws.cell(row=start_row, column=2, value="APELLIDOS Y NOMBRES").font = Font(bold=True, color=COLOR_HEADER_TXT)
    ws.cell(row=start_row, column=3, value="CONDICIÓN").font = Font(bold=True, color=COLOR_HEADER_TXT)
    ws.cell(row=start_row, column=4, value="CMP").font = Font(bold=True, color=COLOR_HEADER_TXT)

    for c in range(1, 5):
        ws.merge_cells(start_row=start_row, start_column=c, end_row=start_row+1, end_column=c)
        cell = ws.cell(row=start_row, column=c)
        cell.fill = PatternFill(start_color=COLOR_HEADER_BG, end_color=COLOR_HEADER_BG, fill_type="solid")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Columnas de días
    col_day_start = 5
    for d in range(1, num_days + 1):
        col_idx = col_day_start + (d - 1)
        day_info = month_info["days"][d - 1]

        # Fila 5: Día número
        c5 = ws.cell(row=start_row, column=col_idx, value=d)
        c5.font = Font(bold=True, color=COLOR_HEADER_TXT)
        c5.alignment = Alignment(horizontal="center", vertical="center")
        
        # Color según si es feriado o regular
        if day_info["is_holiday"]:
            c5.fill = PatternFill(start_color="C00000", end_color="C00000", fill_type="solid") # Rojo feriado
        else:
            c5.fill = PatternFill(start_color=COLOR_HEADER_BG, end_color=COLOR_HEADER_BG, fill_type="solid")

        # Fila 6: Día de semana
        c6 = ws.cell(row=start_row+1, column=col_idx, value=day_info["weekday_short"])
        c6.font = Font(size=9, bold=True)
        c6.alignment = Alignment(horizontal="center", vertical="center")
        if day_info["is_sunday"]:
            c6.fill = PatternFill(start_color=COLOR_WEEKEND_SUN, end_color=COLOR_WEEKEND_SUN, fill_type="solid")
        elif day_info["is_saturday"]:
            c6.fill = PatternFill(start_color=COLOR_WEEKEND_SAT, end_color=COLOR_WEEKEND_SAT, fill_type="solid")
        elif day_info["is_holiday"]:
            c6.fill = PatternFill(start_color=COLOR_HOLIDAY, end_color=COLOR_HOLIDAY, fill_type="solid")
        else:
            c6.fill = PatternFill(start_color=COLOR_SUBHEADER_BG, end_color=COLOR_SUBHEADER_BG, fill_type="solid")

    # Columnas de resumen
    summary_cols = ["T. 6h", "T. 12h", "TOTAL TURNOS", "META", "ESTADO"]
    col_summary_start = col_day_start + num_days
    for i, title in enumerate(summary_cols):
        col_idx = col_summary_start + i
        ws.merge_cells(start_row=start_row, start_column=col_idx, end_row=start_row+1, end_column=col_idx)
        cell = ws.cell(row=start_row, column=col_idx, value=title)
        cell.font = Font(bold=True, color=COLOR_HEADER_TXT)
        cell.fill = PatternFill(start_color=COLOR_HEADER_BG, end_color=COLOR_HEADER_BG, fill_type="solid")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # 3. Filas de Médicos
    current_row = start_row + 2
    doc_list = list(quota_data["doctors_summary"].values())
    
    for idx, doc in enumerate(doc_list, start=1):
        ws.row_dimensions[current_row].height = 24
        
        ws.cell(row=current_row, column=1, value=idx).alignment = Alignment(horizontal="center", vertical="center")
        ws.cell(row=current_row, column=2, value=doc["name"]).alignment = Alignment(horizontal="left", vertical="center")
        ws.cell(row=current_row, column=3, value=doc["condition"]).alignment = Alignment(horizontal="center", vertical="center")
        ws.cell(row=current_row, column=4, value=doc["cmp"]).alignment = Alignment(horizontal="center", vertical="center")

        for c in range(1, 5):
            ws.cell(row=current_row, column=c).border = thin_border
            ws.cell(row=current_row, column=c).font = Font(size=10, bold=(c==2))

        # Celdas de cada día
        for d in range(1, num_days + 1):
            col_idx = col_day_start + (d - 1)
            cell = ws.cell(row=current_row, column=col_idx)
            cell.border = thin_border
            cell.alignment = Alignment(horizontal="center", vertical="center")

            day_info = month_info["days"][d - 1]
            shift_info = doc["days"].get(d)

            if shift_info and shift_info.get("shift_period") != "LIBRE":
                sp = shift_info["shift_period"]
                srv = shift_info["service"]
                
                if sp == "12h":
                    cell.value = f"12h ({srv})"
                    cell.fill = PatternFill(start_color=SHIFT_COLORS["12h"]["fill"], end_color=SHIFT_COLORS["12h"]["fill"], fill_type="solid")
                    cell.font = Font(size=8, bold=True, color=SHIFT_COLORS["12h"]["font"])
                elif sp == "DES":
                    cell.value = "DES"
                    cell.fill = PatternFill(start_color=SHIFT_COLORS["DES"]["fill"], end_color=SHIFT_COLORS["DES"]["fill"], fill_type="solid")
                    cell.font = Font(size=9, italic=True, color=SHIFT_COLORS["DES"]["font"])
                elif sp in ("M", "T"):
                    label = f"{sp}-{srv}"
                    cell.value = label
                    color_cfg = SHIFT_COLORS.get(label, SHIFT_COLORS.get("M-TU" if sp=="M" else "T-TU"))
                    cell.fill = PatternFill(start_color=color_cfg["fill"], end_color=color_cfg["fill"], fill_type="solid")
                    cell.font = Font(size=9, bold=True, color=color_cfg["font"])
            else:
                # Día libre (sin turno programado o marcado como LIBRE) - violeta con etiqueta 'Libre'
                cell.value = "Libre"
                cell.fill = PatternFill(start_color=SHIFT_COLORS["LIBRE"]["fill"], end_color=SHIFT_COLORS["LIBRE"]["fill"], fill_type="solid")
                cell.font = Font(size=8, color=SHIFT_COLORS["LIBRE"]["font"])

        # Celdas de resumen por médico
        c_6h = ws.cell(row=current_row, column=col_summary_start, value=doc["count_6h"])
        c_12h = ws.cell(row=current_row, column=col_summary_start+1, value=doc["count_12h"])
        c_tot = ws.cell(row=current_row, column=col_summary_start+2, value=doc["total_equivalent_shifts"])
        c_qta = ws.cell(row=current_row, column=col_summary_start+3, value=doc["quota"])
        c_st = ws.cell(row=current_row, column=col_summary_start+4, value=doc["status"])

        for i, c_obj in enumerate([c_6h, c_12h, c_tot, c_qta, c_st]):
            c_obj.border = thin_border
            c_obj.alignment = Alignment(horizontal="center", vertical="center")
            c_obj.font = Font(size=10, bold=(i in (2, 3, 4)))
            if i == 2: # Total
                if doc["total_equivalent_shifts"] == quota:
                    c_obj.fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid") # Verde
                else:
                    c_obj.fill = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid") # Salmón

        current_row += 1

    # 4. Filas de Validación de Cobertura Diaria (Semáforo de Seguridad)
    # Fila Mañana
    ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=4)
    ws.cell(row=current_row, column=1, value="Cobertura Mañana (TU, CE, TS)").font = Font(bold=True, size=9, color="1F4E79")
    ws.cell(row=current_row, column=1).alignment = Alignment(horizontal="right", vertical="center")
    
    for d in range(1, num_days + 1):
        col_idx = col_day_start + (d - 1)
        cov = coverage_data[d]
        cell = ws.cell(row=current_row, column=col_idx, value=cov["morning_count"])
        cell.border = thin_border
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.font = Font(bold=True, size=9)
        if cov["is_sunday"]:
            cell.fill = PatternFill(start_color=COLOR_WEEKEND_SUN, end_color=COLOR_WEEKEND_SUN, fill_type="solid")
        elif cov.get("all_morning_services_covered") or cov.get("is_admin_approved_day"):
            cell.fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid") # Verde OK
        else:
            cell.fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid") # Rojo alerta
            cell.font = Font(bold=True, color="9C0006")

    current_row += 1

    # Fila Tarde
    ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=4)
    ws.cell(row=current_row, column=1, value="Cobertura Tarde (TU, CE)").font = Font(bold=True, size=9, color="1F4E79")
    ws.cell(row=current_row, column=1).alignment = Alignment(horizontal="right", vertical="center")

    for d in range(1, num_days + 1):
        col_idx = col_day_start + (d - 1)
        cov = coverage_data[d]
        cell = ws.cell(row=current_row, column=col_idx, value=cov["afternoon_count"])
        cell.border = thin_border
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.font = Font(bold=True, size=9)
        if cov["is_sunday"]:
            cell.fill = PatternFill(start_color=COLOR_WEEKEND_SUN, end_color=COLOR_WEEKEND_SUN, fill_type="solid")
        elif cov.get("all_afternoon_services_covered") or cov.get("is_admin_approved_day"):
            cell.fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid") # Verde OK
        else:
            cell.fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid") # Rojo alerta
            cell.font = Font(bold=True, color="9C0006")

    current_row += 3

    # 5. Leyenda Asistencial
    ws.cell(row=current_row, column=2, value="LEYENDA DE SERVICIOS Y TURNOS:").font = Font(bold=True, size=9)
    legends = [
        ("M-TU", "Mañana - Tópico Urgencias", SHIFT_COLORS["M-TU"]["fill"]),
        ("M-CE", "Mañana - Consultorio de Medicina", SHIFT_COLORS["M-CE"]["fill"]),
        ("M-TS", "Mañana - Telesalud/TBC/MCI", SHIFT_COLORS["M-TS"]["fill"]),
        ("T-TU", "Tarde - Tópico Urgencias", SHIFT_COLORS["T-TU"]["fill"]),
        ("T-CE", "Tarde - Consultorio de Medicina", SHIFT_COLORS["T-CE"]["fill"]),
        ("12h", "Turno 12h (Celeste - Mañana+Tarde)", SHIFT_COLORS["12h"]["fill"]),
        ("DES", "Descanso post-12h (Violeta)", SHIFT_COLORS["DES"]["fill"]),
        ("Libre", "Día Libre (Violeta)", SHIFT_COLORS["LIBRE"]["fill"]),
    ]
    leg_col = 4
    for code, desc, fill_hex in legends:
        c_code = ws.cell(row=current_row, column=leg_col, value=code)
        c_code.fill = PatternFill(start_color=fill_hex, end_color=fill_hex, fill_type="solid")
        c_code.font = Font(size=8, bold=True)
        c_code.alignment = Alignment(horizontal="center", vertical="center")
        c_desc = ws.cell(row=current_row, column=leg_col+1, value=desc)
        c_desc.font = Font(size=8)
        leg_col += 3

    current_row += 4

    # 6. Espacios para Firmas de Aprobación
    firmas = [
        ("COORDINADOR(A) DEL ROL MÉDICO", "Firma y Sello"),
        ("MÉDICO JEFE DE LA IPRESS", "Firma y Sello"),
        ("ADMINISTRACIÓN DE LA IPRESS", "Firma y Sello")
    ]
    cols_firma = [4, 15, 26]
    for (cargo, nota), col_f in zip(firmas, cols_firma):
        ws.merge_cells(start_row=current_row, start_column=col_f, end_row=current_row, end_column=col_f+6)
        c_line = ws.cell(row=current_row, column=col_f, value="_________________________________________")
        c_line.alignment = Alignment(horizontal="center", vertical="center")
        
        ws.merge_cells(start_row=current_row+1, start_column=col_f, end_row=current_row+1, end_column=col_f+6)
        c_cargo = ws.cell(row=current_row+1, column=col_f, value=cargo)
        c_cargo.font = Font(size=9, bold=True)
        c_cargo.alignment = Alignment(horizontal="center", vertical="center")

        ws.merge_cells(start_row=current_row+2, start_column=col_f, end_row=current_row+2, end_column=col_f+6)
        c_nota = ws.cell(row=current_row+2, column=col_f, value=nota)
        c_nota.font = Font(size=8, italic=True, color="595959")
        c_nota.alignment = Alignment(horizontal="center", vertical="center")

    # Ajustar anchos de columna
    ws.column_dimensions['A'].width = 4
    ws.column_dimensions['B'].width = 30
    ws.column_dimensions['C'].width = 12
    ws.column_dimensions['D'].width = 10

    for d in range(1, num_days + 1):
        col_letter = get_column_letter(col_day_start + (d - 1))
        ws.column_dimensions[col_letter].width = 6.5

    for i in range(len(summary_cols)):
        col_letter = get_column_letter(col_summary_start + i)
        ws.column_dimensions[col_letter].width = 14

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output


VALID_PROPOSAL_SHIFT_CODES = [
    "M-TU",
    "M-CE",
    "M-TS",
    "T-TU",
    "T-CE",
    "12h (TS+TU)",
    "12h (TU+CE)",
    "12h (CE+TU)",
    "12h (TS+CE)",
    "12h (TU+TU)",
    "12h (CE+CE)",
    "DES",
    "Libre"
]


def _normalize_shift_code(raw_val: str) -> tuple[str, str] | None:
    """
    Convierte el texto de una celda de propuesta Excel en (shift_period, service).
    Retorna ('LIBRE', 'LIBRE') si se indica Libre o vacío, o None si el código no es reconocido.
    """
    if raw_val is None:
        return ("LIBRE", "LIBRE")
    txt = str(raw_val).strip()
    if not txt or txt.upper() in ("LIBRE", "SIN TURNO", "-", "DOMINGO (BLOQUEADO)"):
        return ("LIBRE", "LIBRE")

    u = txt.upper().replace(" ", "")
    if u in ("M-TU", "MTU"):
        return ("M", "TU")
    if u in ("M-CE", "MCE"):
        return ("M", "CE")
    if u in ("M-TS", "MTS"):
        return ("M", "TS")
    if u in ("T-TU", "TTU"):
        return ("T", "TU")
    if u in ("T-CE", "TCE"):
        return ("T", "CE")
    if u in ("DES", "DESCANSO"):
        return ("DES", "DES")

    if u.startswith("12H"):
        for srv_comb in ("TS+TU", "TU+CE", "CE+TU", "TS+CE", "TU+TU", "CE+CE"):
            if srv_comb in u:
                return ("12h", srv_comb)
        return ("12h", "TS+TU")

    for srv_comb in ("TS+TU", "TU+CE", "CE+TU", "TS+CE", "TU+TU", "CE+CE"):
        if u == srv_comb:
            return ("12h", srv_comb)

    return None


def generate_blank_proposal_template(year: int, month: int, doctor: dict, ipress_name: str = "IPRESS SALUD PERÚ") -> io.BytesIO:
    """
    Genera en memoria un formato Excel (.xlsx) EN BLANCO personalizado para que un único médico
    registre su propuesta mensual de turnos y luego la suba al sistema.
    Incluye metadatos de validación (ID, CMP, Nombre, Mes, Año) para impedir que un médico
    presente propuestas de otro médico.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = f"Propuesta {month:02d}-{year}"
    ws.views.sheetView[0].showGridLines = True

    month_info = get_month_info(year, month)
    num_days = month_info["num_days"]
    quota = month_info["monthly_quota"]

    thin_border = Border(
        left=Side(style='thin', color='BFBFBF'),
        right=Side(style='thin', color='BFBFBF'),
        top=Side(style='thin', color='BFBFBF'),
        bottom=Side(style='thin', color='BFBFBF')
    )

    # Encabezado institucional
    ws.merge_cells("A1:E1")
    ws["A1"] = f"{ipress_name} - FORMATO OFICIAL DE PROPUESTA DE HORARIO MÉDICO"
    ws["A1"].font = Font(name="Calibri", size=13, bold=True, color="1F4E79")
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")

    ws.merge_cells("A2:E2")
    ws["A2"] = f"MES DE PROGRAMACIÓN: {month_info['month_name'].upper()} {year} | META MENSUAL: {quota} TURNOS"
    ws["A2"].font = Font(name="Calibri", size=10, bold=True, color="334155")
    ws["A2"].alignment = Alignment(horizontal="center", vertical="center")

    # Metadatos del médico titular (Filas 4 a 7) - usados para validar que solo suba su propia propuesta
    meta_rows = [
        ("MÉDICO TITULAR:", doctor["name"], "ID INTERNO:", doctor["id"]),
        ("CMP:", str(doctor["cmp"]), "CONDICIÓN:", doctor.get("condition", "")),
        ("AÑO:", year, "MES N°:", month),
    ]
    for idx_m, (lbl1, val1, lbl2, val2) in enumerate(meta_rows, start=4):
        ws.cell(row=idx_m, column=1, value=lbl1).font = Font(bold=True, size=10, color="1F4E79")
        c_v1 = ws.cell(row=idx_m, column=2, value=val1)
        c_v1.font = Font(bold=True, size=10)
        ws.cell(row=idx_m, column=3, value=lbl2).font = Font(bold=True, size=10, color="1F4E79")
        c_v2 = ws.cell(row=idx_m, column=4, value=val2)
        c_v2.font = Font(bold=True, size=10)

    # Instrucciones breves
    ws.merge_cells("A8:E8")
    ws["A8"] = (
        "INSTRUCCIONES: Seleccione su turno propuesto en la columna D para cada día laborable (Lunes a Sábado). "
        "Los domingos están bloqueados para médicos. Este formato es personal e intransferible."
    )
    ws["A8"].font = Font(size=9, italic=True, color="7F1D1D")

    # Tabla de días (Fila 10)
    header_row = 10
    headers = ["DÍA", "DÍA SEMANA", "CONDICIÓN DEL DÍA", "TURNO PROPUESTO (SELECCIONAR)", "OBSERVACIONES"]
    for col_i, h_text in enumerate(headers, start=1):
        c = ws.cell(row=header_row, column=col_i, value=h_text)
        c.font = Font(bold=True, color=COLOR_HEADER_TXT, size=10)
        c.fill = PatternFill(start_color=COLOR_HEADER_BG, end_color=COLOR_HEADER_BG, fill_type="solid")
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = thin_border
    ws.row_dimensions[header_row].height = 24

    # Lista desplegable de códigos válidos en Excel
    dv = DataValidation(
        type="list",
        formula1=f'"{",".join(VALID_PROPOSAL_SHIFT_CODES)}"',
        allow_blank=True
    )
    ws.add_data_validation(dv)

    start_data_row = 11
    for d_info in month_info["days"]:
        d_num = d_info["day"]
        r_idx = start_data_row + (d_num - 1)
        ws.row_dimensions[r_idx].height = 20

        c_day = ws.cell(row=r_idx, column=1, value=d_num)
        c_wday = ws.cell(row=r_idx, column=2, value=d_info["weekday_name"])
        if d_info["is_sunday"]:
            cond_txt = "DOMINGO (Bloqueado para médicos)"
        elif d_info["is_holiday"]:
            cond_txt = f"FERIADO ({d_info['holiday_name']})"
        else:
            cond_txt = "Día Laborable"
        c_cond = ws.cell(row=r_idx, column=3, value=cond_txt)
        c_shift = ws.cell(row=r_idx, column=4, value="")
        c_obs = ws.cell(row=r_idx, column=5, value="")

        for col_i, cell_obj in enumerate([c_day, c_wday, c_cond, c_shift, c_obs], start=1):
            cell_obj.border = thin_border
            cell_obj.alignment = Alignment(horizontal="center" if col_i in (1, 4) else "left", vertical="center")
            cell_obj.font = Font(size=10, bold=(col_i == 4))

        if d_info["is_sunday"]:
            for cell_obj in (c_day, c_wday, c_cond, c_shift, c_obs):
                cell_obj.fill = PatternFill(start_color=COLOR_WEEKEND_SUN, end_color=COLOR_WEEKEND_SUN, fill_type="solid")
            c_shift.value = "Libre"
            c_shift.font = Font(size=9, italic=True, color="595959")
        else:
            if d_info["is_holiday"]:
                for cell_obj in (c_day, c_wday, c_cond):
                    cell_obj.fill = PatternFill(start_color=COLOR_HOLIDAY, end_color=COLOR_HOLIDAY, fill_type="solid")
            c_shift.fill = PatternFill(start_color="FFFBEB", end_color="FFFBEB", fill_type="solid")
            dv.add(c_shift)

    # Leyenda de códigos válidos al costado (Columnas G y H)
    ws.cell(row=10, column=7, value="CÓDIGO DE TURNO").font = Font(bold=True, size=9, color=COLOR_HEADER_TXT)
    ws.cell(row=10, column=7).fill = PatternFill(start_color=COLOR_HEADER_BG, end_color=COLOR_HEADER_BG, fill_type="solid")
    ws.cell(row=10, column=8, value="SIGNIFICADO ASISTENCIAL").font = Font(bold=True, size=9, color=COLOR_HEADER_TXT)
    ws.cell(row=10, column=8).fill = PatternFill(start_color=COLOR_HEADER_BG, end_color=COLOR_HEADER_BG, fill_type="solid")

    legend_items = [
        ("M-TU", "Mañana (6h) - Tópico de Urgencias"),
        ("M-CE", "Mañana (6h) - Consultorio de Medicina"),
        ("M-TS", "Mañana (6h) - Telesalud / TBC / MCI"),
        ("T-TU", "Tarde (6h) - Tópico de Urgencias"),
        ("T-CE", "Tarde (6h) - Consultorio de Medicina"),
        ("12h (TS+TU)", "12 Horas: Mañana Telesalud + Tarde Urgencias"),
        ("12h (TU+CE)", "12 Horas: Mañana Urgencias + Tarde Consultorio"),
        ("12h (CE+TU)", "12 Horas: Mañana Consultorio + Tarde Urgencias"),
        ("12h (TS+CE)", "12 Horas: Mañana Telesalud + Tarde Consultorio"),
        ("12h (TU+TU)", "12 Horas: Mañana Urgencias + Tarde Urgencias"),
        ("12h (CE+CE)", "12 Horas: Mañana Consultorio + Tarde Consultorio"),
        ("DES", "Descanso post-guardia de 12 horas"),
        ("Libre", "Día Libre / Sin turno"),
    ]
    for idx_l, (code_l, desc_l) in enumerate(legend_items, start=11):
        c1 = ws.cell(row=idx_l, column=7, value=code_l)
        c2 = ws.cell(row=idx_l, column=8, value=desc_l)
        c1.font = Font(bold=True, size=9)
        c2.font = Font(size=9)
        c1.border = thin_border
        c2.border = thin_border

    ws.column_dimensions["A"].width = 8
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 28
    ws.column_dimensions["D"].width = 28
    ws.column_dimensions["E"].width = 25
    ws.column_dimensions["F"].width = 4
    ws.column_dimensions["G"].width = 18
    ws.column_dimensions["H"].width = 42

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output


def parse_and_import_doctor_proposal(
    file_bytes: bytes,
    expected_year: int,
    expected_month: int,
    expected_doctor_id: int
) -> dict:
    """
    Procesa en memoria (io.BytesIO, sin guardar el archivo en disco) la propuesta Excel subida por un médico.
    Reglas estrictas:
    1. Un médico SOLO puede subir la propuesta que le corresponde a él (valida CMP / ID / Nombre del archivo).
       Si el archivo pertenece a otro médico o incluye filas de otro médico, se rechaza con error 403.
    2. Registra los turnos como propuesta (is_proposal = 1) en la tabla 'shifts'.
    3. Detecta automáticamente cualquier cruce con las propuestas de otros médicos y lo sincroniza en 'discrepancies'.
    4. Registra la carga de la propuesta en el log de auditoría ('audit_logs').
    5. No almacena el archivo Excel en disco en ningún momento.
    """
    with get_connection() as conn:
        doc_row = conn.execute(
            "SELECT id, name, cmp, condition, role FROM doctors WHERE id = ? AND is_active = 1",
            (expected_doctor_id,)
        ).fetchone()
        if not doc_row or doc_row["role"] != "doctor":
            return {"success": False, "error": "Médico inválido o no autorizado para presentar propuestas.", "status_code": 403}
        doctor = dict(doc_row)

    try:
        stream = io.BytesIO(file_bytes)
        wb = load_workbook(stream, data_only=True)
        ws = wb.active
    except Exception as exc:
        return {"success": False, "error": f"El archivo subido no es un Excel (.xlsx) válido: {exc}", "status_code": 400}

    # Validar identidad del médico en el formato (Fila 4 y Fila 5)
    file_doc_name = str(ws.cell(row=4, column=2).value or "").strip()
    file_doc_id = ws.cell(row=4, column=4).value
    file_doc_cmp = str(ws.cell(row=5, column=2).value or "").strip()
    file_year = ws.cell(row=6, column=2).value
    file_month = ws.cell(row=6, column=4).value

    # Verificar que el archivo no corresponda a otro médico
    if file_doc_cmp and file_doc_cmp != str(doctor["cmp"]).strip():
        return {
            "success": False,
            "error": (
                f"ACCESO DENEGADO: El archivo cargado pertenece al CMP {file_doc_cmp} ({file_doc_name}), "
                f"pero usted ha seleccionado a {doctor['name']} (CMP {doctor['cmp']}). "
                f"Un médico solo puede cargar la propuesta que le corresponde a él mismo."
            ),
            "status_code": 403
        }

    if file_doc_id is not None and str(file_doc_id).strip().isdigit():
        if int(str(file_doc_id).strip()) != int(doctor["id"]):
            return {
                "success": False,
                "error": (
                    f"ACCESO DENEGADO: El formato corresponde a otro médico ({file_doc_name}). "
                    f"Un médico no puede proponer el horario de otro médico."
                ),
                "status_code": 403
            }

    if file_doc_name and doctor["name"].lower() not in file_doc_name.lower() and file_doc_name.lower() not in doctor["name"].lower():
        return {
            "success": False,
            "error": (
                f"ACCESO DENEGADO: El nombre del formato ('{file_doc_name}') no coincide con el médico titular ('{doctor['name']}'). "
                f"Un médico solo puede subir su propia propuesta."
            ),
            "status_code": 403
        }

    if not file_doc_cmp and not file_doc_name:
        return {
            "success": False,
            "error": "Formato inválido: Descargue primero su plantilla oficial en blanco desde el aplicativo.",
            "status_code": 400
        }

    if file_year is not None and str(file_year).strip().isdigit() and int(str(file_year).strip()) != int(expected_year):
        return {
            "success": False,
            "error": f"El archivo corresponde al año {file_year}, pero está visualizando el año {expected_year}.",
            "status_code": 400
        }

    if file_month is not None and str(file_month).strip().isdigit() and int(str(file_month).strip()) != int(expected_month):
        return {
            "success": False,
            "error": f"El archivo corresponde al mes {file_month}, pero está visualizando el mes {expected_month}.",
            "status_code": 400
        }

    month_info = get_month_info(expected_year, expected_month)
    num_days = month_info["num_days"]
    days_meta = {d["day"]: d for d in month_info["days"]}

    parsed_shifts = {}
    start_data_row = 11
    for r_idx in range(start_data_row, start_data_row + num_days):
        day_val = ws.cell(row=r_idx, column=1).value
        if day_val is None or not str(day_val).strip().isdigit():
            continue
        d_num = int(str(day_val).strip())
        if d_num < 1 or d_num > num_days:
            continue

        raw_shift = ws.cell(row=r_idx, column=4).value
        norm = _normalize_shift_code(raw_shift)
        if norm is None:
            return {
                "success": False,
                "error": f"Código de turno no válido en el Día {d_num}: '{raw_shift}'. Use los valores de la lista desplegable.",
                "status_code": 400
            }

        sp, srv = norm
        # Los domingos están bloqueados para propuestas médicas
        if days_meta[d_num]["is_sunday"] and sp not in ("LIBRE",):
            return {
                "success": False,
                "error": f"Día {d_num} es Domingo: Los médicos no pueden proponer turnos en domingo (solo Jefatura o RRHH).",
                "status_code": 403
            }
        parsed_shifts[d_num] = (sp, srv)

    # Cerrar el workbook y descartar el buffer de memoria explícitamente
    wb.close()
    stream.close()

    # Verificar que la propuesta no modifique ni elimine ningún turno de Consultorio (CE)
    # donde este médico ya tenga pacientes citados en el aplicativo de citas
    for d_num in range(1, num_days + 1):
        sp_new, srv_new = parsed_shifts.get(d_num, ("LIBRE", "LIBRE"))
        allowed_mod, lock_err = check_shift_modification_allowed(
            year=expected_year,
            month=expected_month,
            day=d_num,
            doctor_id=doctor["id"],
            new_shift_period="" if sp_new == "LIBRE" else sp_new,
            new_service="" if sp_new == "LIBRE" else srv_new
        )
        if not allowed_mod:
            return {"success": False, "error": lock_err, "status_code": 409}

    # Guardar propuesta en la base de datos para este único médico
    saved_count = 0
    summary_parts = []
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM shifts WHERE year = ? AND month = ? AND doctor_id = ?",
            (expected_year, expected_month, doctor["id"])
        )

        for d_num in range(1, num_days + 1):
            sp, srv = parsed_shifts.get(d_num, ("LIBRE", "LIBRE"))
            if sp != "LIBRE":
                conn.execute("""
                    INSERT INTO shifts (year, month, day, shift_period, service, doctor_id, is_proposal, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                    ON CONFLICT(year, month, day, doctor_id) DO UPDATE SET
                        shift_period = excluded.shift_period,
                        service = excluded.service,
                        is_proposal = 1,
                        updated_at = CURRENT_TIMESTAMP
                """, (expected_year, expected_month, d_num, sp, srv, doctor["id"]))
                saved_count += 1
                summary_parts.append(f"Día {d_num:02d}:{sp}({srv})")

                # Si es 12h, asegurar descanso post-guardia (DES) al día siguiente si es laborable y no fue especificado
                if sp == "12h" and (d_num + 1) <= num_days and not days_meta[d_num + 1]["is_sunday"]:
                    next_sp, _ = parsed_shifts.get(d_num + 1, ("LIBRE", "LIBRE"))
                    if next_sp == "LIBRE":
                        parsed_shifts[d_num + 1] = ("DES", "DES")

        conn.commit()

        details_log = (
            f"Carga de propuesta mensual ({month_info['month_name']} {expected_year}) por {doctor['name']} "
            f"(CMP {doctor['cmp']}): {saved_count} días registrados [{', '.join(summary_parts[:12])}"
            f"{'...' if len(summary_parts) > 12 else ''}]. Archivo procesado en memoria y descartado."
        )
        log_audit(
            doctor["id"],
            doctor["name"],
            "Carga de Propuesta (Excel)",
            details_log,
            conn=conn
        )

    # Sincronizar discrepancias para detectar cualquier cruce con otros médicos
    sync_discrepancies_db(expected_year, expected_month)

    with get_connection() as conn:
        doctor_discs = conn.execute("""
            SELECT COUNT(*) as cnt FROM discrepancies
            WHERE year = ? AND month = ? AND status = 'open'
              AND (doctor1_id = ? OR doctor2_id = ?)
        """, (expected_year, expected_month, doctor["id"], doctor["id"])).fetchone()["cnt"]

    return {
        "success": True,
        "doctor_id": doctor["id"],
        "doctor_name": doctor["name"],
        "saved_days_count": saved_count,
        "discrepancies_detected": doctor_discs,
        "message": (
            f"Propuesta de {doctor['name']} cargada con éxito ({saved_count} días programados). "
            f"{f'Se detectaron {doctor_discs} cruce(s) de horario en la sección de Discrepancias.' if doctor_discs > 0 else 'Sin cruces detectados.'}"
        ),
        "status_code": 200
    }
