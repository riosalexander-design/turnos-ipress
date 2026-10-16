"""
exporter.py
Generador oficial de reportes en Excel (.xlsx) con formato institucional asistencial
para la programación mensual de médicos de la IPRESS usando openpyxl.
"""

import io
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from holidays_pe import get_month_info
from scheduler_engine import get_doctor_quota_summary, get_daily_coverage

# Paleta de colores asistenciales
COLOR_HEADER_BG = "1F4E79"        # Azul institucional oscuro
COLOR_HEADER_TXT = "FFFFFF"
COLOR_SUBHEADER_BG = "D9E1F2"     # Azul tenue
COLOR_WEEKEND_SAT = "E9EDF4"      # Sábado gris/azul tenue
COLOR_WEEKEND_SUN = "D9D9D9"      # Domingo gris
COLOR_HOLIDAY = "FCE4D6"          # Feriado salmón pastel

# Colores de turnos
SHIFT_COLORS = {
    "12h": {"fill": "FFE699", "font": "7F6000"},  # Amarillo/Dorado 12h
    "M-TU": {"fill": "F8CBAD", "font": "C65911"}, # Coral Urgencias
    "M-CE": {"fill": "BDD7EE", "font": "1F4E79"}, # Azul Consultorio
    "M-TS": {"fill": "C6E0B4", "font": "375623"}, # Verde Telesalud
    "T-TU": {"fill": "F4B084", "font": "833C0C"}, # Naranja Urgencias Tarde
    "T-CE": {"fill": "9BC2E6", "font": "1B365D"}, # Celeste Consultorio Tarde
    "DES":  {"fill": "E2EFDA", "font": "375623"}, # Verde descanso post-12h
    "LIBRE": {"fill": "F2F2F2", "font": "595959"} # Gris libre
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

            if shift_info:
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
                    color_cfg = SHIFT_COLORS.get(label, {"fill": "FFFFFF", "font": "000000"})
                    cell.fill = PatternFill(start_color=color_cfg["fill"], end_color=color_cfg["fill"], fill_type="solid")
                    cell.font = Font(size=9, bold=True, color=color_cfg["font"])
            else:
                if day_info["is_sunday"]:
                    cell.fill = PatternFill(start_color=COLOR_WEEKEND_SUN, end_color=COLOR_WEEKEND_SUN, fill_type="solid")
                elif day_info["is_saturday"]:
                    cell.fill = PatternFill(start_color=COLOR_WEEKEND_SAT, end_color=COLOR_WEEKEND_SAT, fill_type="solid")

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
        ("12h", "Turno 12h (Mañana + Tarde)", SHIFT_COLORS["12h"]["fill"]),
        ("DES", "Descanso de Guardia post-12h", SHIFT_COLORS["DES"]["fill"]),
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
