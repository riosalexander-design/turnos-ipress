# Aplicativo Web de Programación de Turnos Médicos - IPRESS

Sistema web para la planificación, conciliación y aprobación mensual del rol asistencial de los médicos cirujanos en una IPRESS (Sector Salud - Perú).

---

## 🌟 Características Principales

### 1. Reglas de Negocio Asistenciales
- **Meta Base:** 25 turnos al mes por médico de 6 horas ordinarias (150 horas).
- **Deducción de Feriados Nacionales:** Si un feriado nacional cae en día laboral (**lunes a sábado**), descuenta 1 turno de la meta mensual:
  $$\text{Meta Mensual} = 25 - \text{Feriados (Lunes a Sábado)}$$
  *(Los feriados en domingo no descuentan).*
- **Servicios por Turno:**
  - **Turno Mañana (M):** Tópico de Urgencias (TU), Consultorio Externo de Medicina (CE), Telesalud (TS).
  - **Turno Tarde (T):** Tópico de Urgencias (TU), Consultorio Externo de Medicina (CE).
- **Dotación Mínima Obligatoria:** **Nunca menos de 2 médicos** en cualquier turno de cualquier día de la semana.

### 2. Nómina y Reglas Específicas por Médico
- **Médicos Cirujanos (Condición CAS):**
  1. Caldas Pacsi, Lily Katherine *(CMP: 71201)*
  2. Chunga Torres, Tuly Magnolia *(CMP: 68432)*
  3. Laureano Varillas, Elizabeth Miriam *(CMP: 75319)*
  4. Sanchez Quintanilla, Cinthya *(CMP: 80145)*
- **Médicos Cirujanos (Condición Nombrados):**
  5. Solano Ricaldi, Edward Jhonny *(CMP: 54982)*
- **Regla del Viernes de 12 Horas:**
  - Todos los médicos tienen derecho a realizar **1 turno de 12 horas en viernes al mes**, tomando descanso obligatorio de 12 horas el sábado siguiente (`DES`).
- **Regla Obligatoria para el Dr. Solano Ricaldi:**
  - Obligación de realizar **5 turnos de 12 horas al mes** (incluyendo el viernes).
  - Cada vez que realice un turno de 12 horas, **el día inmediatamente posterior descansa 12 horas de manera obligatoria** (`DES`).

### 3. Identificación Individual, Seguridad y Perfil Jefatura (ADMIN)
- Cada médico ingresa con su **CMP** y su **PIN confidencial**.
- **Aislamiento Estricto:** Cada médico cirujano solo puede programar o modificar **su propia fila**. Las filas de los demás colegas se muestran en modo solo lectura.
- **Perfil Jefatura Médica (ADMIN / PIN `admin2026`):**
  - **Modo Supervisión:** Puede seleccionar y modificar la programación asistencial de cualquier médico cirujano para contingencias o coberturas especiales.
  - **Control de Aprobación y Reapertura:** Emisión oficial del certificado "Horario Aprobado" con hash de integridad, o reapertura del rol para modificaciones posteriores.
  - **Ejecución del Corte Día 10:** Disparar el autocompletado normativo de turnos.
  - **Auditoría en Tiempo Real:** Visualización del historial inmutable de acciones (`audit_logs`) con fecha, hora, usuario y detalle de cambios.
  - **Gestión de Personal y Seguridad:** Visualización de nómina y restablecimiento de PINes para médicos que hayan extraviado su clave.

### 4. Corte del Día 10 a las 00:00 Horas y Asignación Automática
- Los médicos pueden auto-proponer sus turnos hasta el **Día 10 a las 00:00 horas**.
- Llegada esa hora, el sistema autocompleta los turnos de quienes no propusieron o dejaron turnos pendientes respetando todas las reglas.

### 5. Detección de Discrepancias y Conciliación (Plazo de 72 Horas)
- Si hay colisiones de puesto o sobrecupo, el sistema oferta una **alternativa inteligente** que puede aplicarse con un solo clic.
- Si no están de acuerdo, se abre un plazo de **72 horas** para ingresar un acuerdo bilateral indicando fecha, turno y servicio para ambos médicos.
- **Tope Máximo:** El plazo de 72 horas **nunca sobrepasa el Día 10 a las 00:00 horas**.

### 6. Emisión Oficial de "Horario Aprobado" y Exportación a Excel
- Al no haber discrepancias y cumplirse todas las condiciones, se emite la certificación **"HORARIO APROBADO"** con código de integridad.
- Descarga en **Excel (.xlsx)** con formato institucional listo para firmas de Jefatura y Administración.

---

## 🚀 Cómo Iniciar la Aplicación

### Opción 1: Con doble clic en Windows
Hacer doble clic en el archivo:
`run_app.bat`

Se abrirá el servidor y tu navegador en:
`http://localhost:5000`

### Opción 2: Desde la terminal de PowerShell
```powershell
cd "c:\Users\Alexander\OneDrive - EduCorpPERU (1)\2026\2026-2\app_turnos_ipress"
python app.py
```

### Ejecutar las Pruebas Automatizadas
```powershell
python test_app.py
```
