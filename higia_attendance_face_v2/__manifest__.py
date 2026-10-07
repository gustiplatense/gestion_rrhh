# -*- coding: utf-8 -*-
{
    "name": "HIGIA - Face Recognition Attendance V2",
    "summary": "Fichada facial V2: enrolamiento con 3 fotos, matching estricto, filtro por empresa",
    "description": """
Versión 2 de fichada por reconocimiento facial.
Mejoras respecto a la versión 1:
- Enrolamiento guiado con 3 fotos y control de calidad de imagen.
- Matching en servidor con mínima/mediana de distancias y ratio-test (margen mínimo).
- Umbrales estrictos por defecto: ante la mínima duda se solicita PIN.
- URLs propias (/face-attendance-v2 y /face-enrollment-v2) que no se cruzan con la v1.
- Filtro de búsqueda de rostros por empresa (?company_id=N en el link del kiosco).
- Botón "Aceptar" tras la fichada para evitar re-fichadas accidentales.
- Cooldown anti doble-fichada en servidor (60 segundos).
- Anti-colisión en enrolamiento: rechaza rostros que coinciden con otro empleado.
- Módulo totalmente independiente de higia_attendance_face (v1).
""",
    "version": "2.0",
    "category": "Human Resources",
    "author": "Digitalo Consulting SRL",
    "company": "Digitalo Consulting SRL",
    "maintainer": "Digitalo Consulting SRL",
    "website": "https://digitalo.com.ar",
    "license": "OPL-1",
    "depends": [
        "hr",
        "hr_attendance",
        "web",
        "base_setup",
    ],
    "data": [
        "views/hr_employee_inherit_views.xml",
        "views/templates/kiosk_templates.xml",
    ],
    "application": True,
    "installable": True,
}
