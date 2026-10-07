# -*- coding: utf-8 -*-
from odoo import fields, models


class HrAttendance(models.Model):
    _inherit = "hr.attendance"

    higia_manual_note = fields.Char(
        string="Novedad Fichada Manual",
        copy=False,
        help="Registro de que la fichada fue modificada manualmente desde la "
             "web app de Gestión de Horas. Conserva el horario original de la "
             "primera modificación.",
    )
