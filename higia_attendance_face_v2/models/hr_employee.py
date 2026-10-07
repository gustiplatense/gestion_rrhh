# -*- coding: utf-8 -*-
import base64
import binascii
import json
import logging
import secrets
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

TOKEN_EXPIRY_HOURS_V2 = 72


class HrEmployeeFaceV2(models.Model):
    _inherit = "hr.employee"

    face_descriptors_v2 = fields.Text(
        string="Descriptores faciales V2",
        help="Lista JSON con 3 descriptores de 128 floats (enrolamiento multi-foto).",
        groups="hr.group_hr_user",
    )
    face_enrolled_v2 = fields.Boolean(
        string="Rostro enrolado V2",
        compute="_compute_face_enrolled_v2",
        store=True,
    )
    enroll_token_v2 = fields.Char(
        string="Token de enrolamiento V2",
        copy=False,
        groups="hr.group_hr_user",
    )
    enroll_token_expiry_v2 = fields.Datetime(
        string="Vencimiento token V2",
        copy=False,
        groups="hr.group_hr_user",
    )

    @api.depends("face_descriptors_v2")
    def _compute_face_enrolled_v2(self):
        for rec in self:
            rec.face_enrolled_v2 = bool(rec.face_descriptors_v2)

    # ------------------------------------------------------------------
    # Face data V2
    # ------------------------------------------------------------------

    def _get_descriptors_v2(self):
        """Devuelve la lista de descriptores validos (cada uno: 128 floats) o []."""
        self.ensure_one()
        try:
            data = json.loads(self.face_descriptors_v2 or "[]")
        except (ValueError, TypeError):
            return []
        result = []
        for descriptor in data:
            if isinstance(descriptor, list) and len(descriptor) == 128:
                try:
                    result.append([float(value) for value in descriptor])
                except (ValueError, TypeError):
                    continue
        return result

    def action_clear_face_v2(self):
        self.ensure_one()
        self.face_descriptors_v2 = False
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Datos faciales V2 eliminados",
                "message": "Descriptores faciales V2 eliminados para %s." % self.name,
                "sticky": False,
                "type": "warning",
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def action_import_face_from_v1(self):
        """Importa el descriptor facial de la version 1 (si existe) como
        enrolamiento V2 de una sola foto.

        No depende del modulo v1: verifica en tiempo de ejecucion que el
        campo face_descriptor exista (v1 instalado) y tenga datos.
        Nota: con una sola foto el matching es menos robusto que con las 3
        fotos del enrolamiento V2; se recomienda re-enrolar cuando sea posible.
        """
        self.ensure_one()
        if "face_descriptor" not in self._fields:
            raise UserError(
                "El modulo de la version 1 (higia_attendance_face) no esta instalado: "
                "no hay datos de enrolamiento para importar."
            )
        if not self.face_descriptor:
            raise UserError(
                "El empleado %s no tiene rostro enrolado en la version 1." % self.name
            )
        try:
            descriptor = json.loads(self.face_descriptor)
            if not isinstance(descriptor, list) or len(descriptor) != 128:
                raise ValueError
            descriptor = [float(value) for value in descriptor]
        except (ValueError, TypeError):
            raise UserError(
                "El descriptor facial V1 de %s es invalido. Re-enrolar en V2." % self.name
            )
        self.write({"face_descriptors_v2": json.dumps([descriptor])})
        _logger.info("FaceV2 import desde V1: %s (id=%s), 1 descriptor", self.name, self.id)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Importacion desde V1 exitosa",
                "message": (
                    "%s quedo enrolado en V2 con 1 foto (importada de la version 1). "
                    "Recomendado: re-enrolar con el asistente de 3 fotos para mayor precision."
                ) % self.name,
                "sticky": False,
                "type": "success",
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    # ------------------------------------------------------------------
    # Token de enrolamiento V2
    # ------------------------------------------------------------------

    def _generate_enroll_token_v2(self):
        """Genera (o renueva) el token de enrolamiento V2. Retorna la URL."""
        self.ensure_one()
        token = secrets.token_urlsafe(32)
        self.enroll_token_v2 = token
        self.enroll_token_expiry_v2 = fields.Datetime.now() + timedelta(hours=TOKEN_EXPIRY_HOURS_V2)
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        return "%s/face-enrollment-v2/%s" % (base_url, token)

    def get_employee_by_token_v2(self, token):
        """Valida el token V2 y devuelve datos basicos del empleado."""
        employee = self.sudo().search([("enroll_token_v2", "=", token)], limit=1)
        if not employee:
            return {"error": "Token invalido"}
        if not employee.enroll_token_expiry_v2 or fields.Datetime.now() > employee.enroll_token_expiry_v2:
            return {"error": "Token vencido. Solicita un nuevo link."}
        return {
            "id": employee.id,
            "name": employee.name,
            "enrolled": bool(employee.face_descriptors_v2),
        }

    def action_send_enroll_link_whatsapp_v2(self):
        """Genera token V2 y abre WhatsApp con el link de enrolamiento V2."""
        self.ensure_one()
        phone = self._get_whatsapp_phone_v2()
        enroll_url = self._generate_enroll_token_v2()
        msg = (
            "Hola %s, te enviamos tu link personal para registrar tu rostro "
            "en el sistema de asistencia.\n\n%s\n\n"
            "El link es valido por %d horas." % (self.name, enroll_url, TOKEN_EXPIRY_HOURS_V2)
        )
        wa_url = "https://wa.me/%s?text=%s" % (phone, self._wa_encode_v2(msg))
        return {
            "type": "ir.actions.act_url",
            "url": wa_url,
            "target": "new",
        }

    def action_send_attendance_link_whatsapp_v2(self):
        """Envia por WhatsApp el link de fichada V2 (con filtro de empresa)."""
        if not self:
            return
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        employee = self[0]
        att_url = "%s/face-attendance-v2?company_id=%d" % (base_url, employee.company_id.id)
        phone = employee._get_whatsapp_phone_v2()
        msg = (
            "Hola %s, este es el link para registrar tu asistencia "
            "con reconocimiento facial.\n\n%s" % (employee.name, att_url)
        )
        wa_url = "https://wa.me/%s?text=%s" % (phone, self._wa_encode_v2(msg))
        return {
            "type": "ir.actions.act_url",
            "url": wa_url,
            "target": "new",
        }

    # ------------------------------------------------------------------
    # Helpers propios V2 (sin dependencia del modulo v1)
    # ------------------------------------------------------------------

    def _get_whatsapp_phone_v2(self):
        """Retorna el telefono limpio en formato internacional (solo digitos)."""
        self.ensure_one()
        phone = self.mobile_phone or self.work_phone or ""
        cleaned = "".join(c for c in phone if c.isdigit())
        if not cleaned:
            raise UserError(
                "El empleado %s no tiene numero de telefono/movil cargado." % self.name
            )
        return cleaned

    @staticmethod
    def _wa_encode_v2(text):
        from urllib.parse import quote
        return quote(text, safe="")

    def _save_enrollment_image_v2(self, image_base64):
        """Guarda la imagen de enrolamiento en el empleado y contactos relacionados."""
        self.ensure_one()
        if not image_base64:
            return {"image_saved": False, "image_debug": "Sin imagen recibida."}
        try:
            if "," in image_base64:
                image_base64 = image_base64.split(",", 1)[1]
            image_base64 = image_base64.strip()
            raw = base64.b64decode(image_base64, validate=True)
        except (binascii.Error, ValueError, TypeError) as err:
            return {"image_saved": False, "image_debug": "Imagen base64 invalida: %s" % err}
        if len(raw) < 1000:
            return {"image_saved": False, "image_debug": "Imagen demasiado pequena: %s bytes." % len(raw)}

        self.sudo().write({"image_1920": image_base64})

        if "resource_id" in self._fields and self.resource_id and "image_1920" in self.resource_id._fields:
            self.resource_id.sudo().write({"image_1920": image_base64})

        partners = self.env["res.partner"].sudo()
        for field_name in ("work_contact_id", "address_home_id", "private_contact_id"):
            if field_name in self._fields and self[field_name]:
                partners |= self[field_name]
        if "user_id" in self._fields and self.user_id and self.user_id.partner_id:
            partners |= self.user_id.partner_id
        if partners:
            partners.write({"image_1920": image_base64})

        return {"image_saved": True, "image_debug": "Imagen guardada: %s bytes." % len(raw)}
