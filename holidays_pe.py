"""
holidays_pe.py
Módulo de cálculo y gestión de feriados nacionales de Perú y cálculo de la
meta de turnos mensuales (25 menos feriados nacionales que caen de lunes a sábado).
"""

import calendar
from datetime import date, datetime, timedelta

def calculate_easter(year: int) -> date:
    """Calcula el Domingo de Resurrección usando el algoritmo de Butcher."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)

def get_official_peru_holidays(year: int) -> dict:
    """
    Retorna el diccionario de feriados nacionales oficiales de Perú
    (D.Leg. 713, Ley 31530 y normas conexas).
    Key: date object, Value: nombre del feriado.
    """
    holidays = {
        date(year, 1, 1): "Año Nuevo",
        date(year, 5, 1): "Día del Trabajo",
        date(year, 6, 7): "Batalla de Arica y Día de la Bandera",
        date(year, 6, 29): "San Pedro y San Pablo",
        date(year, 7, 23): "Día de la Fuerza Aérea del Perú",
        date(year, 7, 28): "Fiestas Patrias (Independencia)",
        date(year, 7, 29): "Fiestas Patrias (Fuerzas Armadas)",
        date(year, 8, 6): "Batalla de Junín",
        date(year, 8, 30): "Santa Rosa de Lima",
        date(year, 10, 8): "Combate de Angamos",
        date(year, 11, 1): "Día de Todos los Santos",
        date(year, 12, 8): "Inmaculada Concepción",
        date(year, 12, 9): "Batalla de Ayacucho",
        date(year, 12, 25): "Navidad"
    }

    # Feriados movibles de Semana Santa
    easter = calculate_easter(year)
    jueves_santo = easter - timedelta(days=3)
    viernes_santo = easter - timedelta(days=2)
    holidays[jueves_santo] = "Jueves Santo"
    holidays[viernes_santo] = "Viernes Santo"

    return holidays

def get_month_info(year: int, month: int, custom_holidays: list = None) -> dict:
    """
    Calcula toda la información del mes:
    - Días del mes (1..N con nombres de día)
    - Feriados totales
    - Feriados que caen de lunes a sábado (descuentan turno)
    - Feriados que caen en domingo (no descuentan turno)
    - Meta mensual de turnos por médico = 25 - feriados_lun_sab
    """
    _, num_days = calendar.monthrange(year, month)
    official_holidays = get_official_peru_holidays(year)
    
    # Agregar feriados personalizados si se definieron
    if custom_holidays:
        for ch in custom_holidays:
            d = datetime.strptime(ch['date'], '%Y-%m-%d').date()
            if d.year == year and d.month == month:
                official_holidays[d] = ch.get('name', 'Feriado / Día No Laborable')

    days_list = []
    holidays_mon_to_sat = []
    holidays_sunday = []

    weekday_names_es = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    weekday_short_es = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]

    for d in range(1, num_days + 1):
        cur_date = date(year, month, d)
        w_idx = cur_date.weekday() # 0 = Lunes, 5 = Sábado, 6 = Domingo
        is_holiday = cur_date in official_holidays
        holiday_name = official_holidays.get(cur_date, None)
        
        # Descuenta si es feriado y cae de lunes a sábado (0..5)
        discounts = False
        if is_holiday:
            if w_idx < 6: # Lunes a Sábado
                discounts = True
                holidays_mon_to_sat.append({
                    "day": d,
                    "date": cur_date.isoformat(),
                    "name": holiday_name,
                    "weekday": weekday_names_es[w_idx]
                })
            else: # Domingo
                holidays_sunday.append({
                    "day": d,
                    "date": cur_date.isoformat(),
                    "name": holiday_name,
                    "weekday": weekday_names_es[w_idx]
                })

        days_list.append({
            "day": d,
            "date": cur_date.isoformat(),
            "weekday_index": w_idx,
            "weekday_name": weekday_names_es[w_idx],
            "weekday_short": weekday_short_es[w_idx],
            "is_weekend": (w_idx in (5, 6)),
            "is_sunday": (w_idx == 6),
            "is_friday": (w_idx == 4),
            "is_saturday": (w_idx == 5),
            "is_holiday": is_holiday,
            "holiday_name": holiday_name,
            "discounts_shift": discounts
        })

    discounts_count = len(holidays_mon_to_sat)
    monthly_quota = max(1, 25 - discounts_count)

    month_names_es = [
        "", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
        "Julio", "Agosto", "Setiembre", "Octubre", "Noviembre", "Diciembre"
    ]

    return {
        "year": year,
        "month": month,
        "month_name": month_names_es[month],
        "num_days": num_days,
        "base_shifts": 25,
        "holidays_mon_to_sat_count": discounts_count,
        "holidays_mon_to_sat": holidays_mon_to_sat,
        "holidays_sunday_count": len(holidays_sunday),
        "holidays_sunday": holidays_sunday,
        "monthly_quota": monthly_quota,
        "days": days_list
    }
