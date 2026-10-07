# -*- coding: utf-8 -*-
import logging
from datetime import datetime, timedelta, time

import pytz

from odoo import fields, http, _
from odoo.http import request

_logger = logging.getLogger(__name__)


class GestionHorasController(http.Controller):
    """Web app portal /gestionhoras para gerentes.

    Seguridad:
    - Todas las rutas usan auth="user": Odoo redirige al login nativo si no hay sesión.
    - Se exige pertenencia al grupo higia_gestion_horas.group_gestion_horas_manager.
    - Los usuarios portal no tienen ACLs sobre hr.attendance / hr.leave, por lo que
      todas las operaciones se ejecutan con sudo() PERO limitadas estrictamente a
      empleados cuyo attendance_manager_id es el usuario logueado (scoping).
    """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _check_access(self):
        """Devuelve el usuario si tiene permisos, o None."""
        user = request.env.user
        if user._is_public():
            return None
        if not user.has_group("higia_gestion_horas.group_gestion_horas_manager"):
            return None
        return user

    MODES = ("manager", "attendance", "department", "work_location")

    def _user_employee(self, user):
        """Empleado vinculado al usuario logueado (gerente)."""
        return request.env["hr.employee"].sudo().search(
            [("user_id", "=", user.id)], limit=1)

    def _managed_domain(self, user, mode):
        """Dominio de empleados listados según el criterio seleccionado."""
        if mode == "attendance":
            # Empleados de los que el usuario es aprobador de asistencias
            return [("attendance_manager_id", "=", user.id)]
        me = self._user_employee(user)
        if not me:
            return None
        if mode == "department":
            # Empleados del mismo sector que el gerente (excluido él mismo)
            if not me.department_id:
                return None
            return [("department_id", "=", me.department_id.id),
                    ("id", "!=", me.id)]
        if mode == "work_location":
            # Empleados con la misma ubicación de trabajo que el gerente (excluido él mismo)
            if not me.work_location_id:
                return None
            return [("work_location_id", "=", me.work_location_id.id),
                    ("id", "!=", me.id)]
        # "manager" (por defecto): empleados que dependen del gerente
        return [("parent_id", "=", me.id)]

    def _managed_employees(self, user, mode="manager"):
        """Empleados activos visibles para el usuario según el criterio."""
        domain = self._managed_domain(user, mode)
        Employee = request.env["hr.employee"].sudo()
        if domain is None:
            return Employee.browse()
        return Employee.search(
            domain + [("active", "=", True)], order="name asc")

    def _employee_allowed(self, user, employee):
        """Scoping de escritura: el empleado cumple alguno de los criterios."""
        if employee.attendance_manager_id.id == user.id:
            return True
        me = self._user_employee(user)
        if me and employee.parent_id.id == me.id:
            return True
        if (me and me.department_id and employee.id != me.id
                and employee.department_id.id == me.department_id.id):
            return True
        if (me and me.work_location_id and employee.id != me.id
                and employee.work_location_id.id == me.work_location_id.id):
            return True
        return False

    def _get_employee_checked(self, user, employee_id):
        """Devuelve el empleado solo si está a cargo del usuario (scoping)."""
        employee = request.env["hr.employee"].sudo().browse(int(employee_id))
        if not employee.exists() or not self._employee_allowed(user, employee):
            return None
        return employee

    def _user_tz(self, user):
        try:
            return pytz.timezone(user.tz or "America/Argentina/Buenos_Aires")
        except Exception:
            return pytz.utc

    def _local_day_utc_bounds(self, day, tz):
        """Límites UTC (naive) del día local [00:00, 24:00)."""
        start_local = tz.localize(datetime.combine(day, time.min))
        end_local = start_local + timedelta(days=1)
        return (
            start_local.astimezone(pytz.utc).replace(tzinfo=None),
            end_local.astimezone(pytz.utc).replace(tzinfo=None),
        )

    def _utc_to_local_str(self, dt_utc, tz):
        """datetime naive UTC -> 'HH:MM' en tz local (o False)."""
        if not dt_utc:
            return False
        return pytz.utc.localize(dt_utc).astimezone(tz).strftime("%H:%M")

    def _local_str_to_utc(self, day, hhmm, tz):
        """fecha + 'HH:MM' local -> datetime naive UTC (o None)."""
        if not hhmm:
            return None
        parts = str(hhmm).strip().split(":")
        local_dt = tz.localize(
            datetime.combine(day, time(int(parts[0]), int(parts[1]))))
        return local_dt.astimezone(pytz.utc).replace(tzinfo=None)

    @staticmethod
    def _default_workday(today):
        """Día anterior hábil (lunes a viernes)."""
        day = today - timedelta(days=1)
        while day.weekday() >= 5:  # 5=sábado, 6=domingo
            day -= timedelta(days=1)
        return day

    def _attendance_to_dict(self, att, tz):
        has_status = "overtime_status" in att._fields
        has_validated = "validated_overtime_hours" in att._fields
        return {
            "id": att.id,
            "check_in": self._utc_to_local_str(att.check_in, tz),
            "check_out": self._utc_to_local_str(att.check_out, tz),
            "worked_hours": round(att.worked_hours or 0.0, 2),
            "overtime_hours": round(att.overtime_hours or 0.0, 2)
            if "overtime_hours" in att._fields else 0.0,
            "validated_overtime_hours": round(att.validated_overtime_hours or 0.0, 2)
            if has_validated else 0.0,
            "overtime_status": att.overtime_status if has_status else False,
            "manual_note": att.higia_manual_note or "",
        }

    def _leaves_for(self, employee, day):
        """Ausencias (novedades) del empleado que cubren el día."""
        return request.env["hr.leave"].sudo().search([
            ("employee_id", "=", employee.id),
            ("request_date_from", "<=", day),
            ("request_date_to", ">=", day),
            ("state", "not in", ("cancel", "refuse")),
        ])

    def _day_payload(self, user, day, mode="manager"):
        """Datos completos del día para todos los empleados a cargo."""
        tz = self._user_tz(user)
        start_utc, end_utc = self._local_day_utc_bounds(day, tz)
        employees = self._managed_employees(user, mode)

        Attendance = request.env["hr.attendance"].sudo()
        rows = []
        for emp in employees:
            attendances = Attendance.search([
                ("employee_id", "=", emp.id),
                ("check_in", ">=", fields.Datetime.to_string(start_utc)),
                ("check_in", "<", fields.Datetime.to_string(end_utc)),
            ], order="check_in asc")

            leaves = self._leaves_for(emp, day)
            rows.append({
                "employee_id": emp.id,
                "employee_name": emp.name,
                "job_title": emp.job_id.name or "",
                "attendances": [self._attendance_to_dict(a, tz) for a in attendances],
                "leaves": [{
                    "id": l.id,
                    "type_id": l.holiday_status_id.id,
                    "type_name": l.holiday_status_id.display_name,
                    "state": l.state,
                    "date_from": str(l.request_date_from or ""),
                    "date_to": str(l.request_date_to or ""),
                } for l in leaves],
            })

        leave_types = request.env["hr.leave.type"].sudo().search(
            [("active", "=", True)], order="sequence asc, name asc")

        return {
            "date": str(day),
            "mode": mode,
            "user_name": user.name,
            "rows": rows,
            "leave_types": [
                {"id": lt.id, "name": lt.display_name} for lt in leave_types
            ],
        }

    # ------------------------------------------------------------------
    # Página HTML
    # ------------------------------------------------------------------

    @http.route("/gestionhoras", auth="user", methods=["GET"], website=False, csrf=False)
    def gestion_horas_page(self, **kw):
        user = self._check_access()
        if not user:
            return request.render("higia_gestion_horas.no_access_page", {})
        module = request.env["ir.module.module"].sudo().search(
            [("name", "=", "higia_gestion_horas")], limit=1)
        asset_v = (module.installed_version or module.latest_version or "1").replace(".", "")
        return request.render("higia_gestion_horas.gestion_horas_page", {
            "user_name": user.name,
            "asset_v": asset_v,
        })

    # ------------------------------------------------------------------
    # API JSON
    # ------------------------------------------------------------------

    @http.route("/gestionhoras/data", auth="user", type="jsonrpc",
                methods=["POST"], csrf=False)
    def get_data(self, date=None, mode=None, **kw):
        """Empleados a cargo + fichadas + horas extras + novedades del día."""
        user = self._check_access()
        if not user:
            return {"error": _("No tiene permisos para acceder a Gestión de Horas.")}

        mode = mode if mode in self.MODES else "manager"
        tz = self._user_tz(user)
        today_local = datetime.now(tz).date()
        if date:
            try:
                day = datetime.strptime(date, "%Y-%m-%d").date()
            except ValueError:
                return {"error": _("Fecha inválida.")}
        else:
            day = self._default_workday(today_local)

        return self._day_payload(user, day, mode)

    @http.route("/gestionhoras/attendance/save", auth="user", type="jsonrpc",
                methods=["POST"], csrf=False)
    def save_attendance(self, employee_id=None, date=None, attendance_id=None,
                        check_in=None, check_out=None, **kw):
        """Crea o modifica la fichada del día del empleado."""
        user = self._check_access()
        if not user:
            return {"error": _("Sin permisos.")}
        employee = self._get_employee_checked(user, employee_id)
        if not employee:
            return {"error": _("Empleado no encontrado o fuera de su equipo.")}
        if not check_in:
            return {"error": _("La hora de entrada es obligatoria.")}

        tz = self._user_tz(user)
        try:
            day = datetime.strptime(date, "%Y-%m-%d").date()
            check_in_utc = self._local_str_to_utc(day, check_in, tz)
            check_out_utc = self._local_str_to_utc(day, check_out, tz)
        except (ValueError, IndexError):
            return {"error": _("Formato de fecha u hora inválido.")}

        if check_out_utc and check_out_utc <= check_in_utc:
            # Salida en la madrugada del día siguiente
            check_out_utc += timedelta(days=1)

        vals = {"check_in": fields.Datetime.to_string(check_in_utc)}
        vals["check_out"] = fields.Datetime.to_string(check_out_utc) if check_out_utc else False

        Attendance = request.env["hr.attendance"].sudo()
        try:
            if attendance_id:
                att = Attendance.browse(int(attendance_id))
                if not att.exists() or att.employee_id.id != employee.id:
                    return {"error": _("Fichada no encontrada.")}
                # Capturar horario original ANTES de aplicar la modificación
                orig_check_in = att.check_in
                orig_check_out = att.check_out
                had_note = bool(att.higia_manual_note)
                att.write(vals)
                # Registrar la novedad "Fichada Manual" si hubo cambio real y
                # aún no existía una nota previa (para no perder el horario original)
                if not had_note:
                    changed = 0
                    if orig_check_in != check_in_utc:
                        changed += 1
                    if orig_check_out != (check_out_utc or False):
                        changed += 1
                    if changed:
                        label = _("Fichada Manual (x2)") if changed >= 2 else _("Fichada Manual")
                        orig_in = self._utc_to_local_str(orig_check_in, tz) or "--:--"
                        orig_out = self._utc_to_local_str(orig_check_out, tz) or "--:--"
                        att.write({
                            "higia_manual_note": "%s | Original: entrada %s, salida %s" % (
                                label, orig_in, orig_out),
                        })
            else:
                vals["employee_id"] = employee.id
                # Fichada creada manualmente donde no existía registro previo:
                # registrar la novedad indicando que el original estaba "Sin Fichar".
                changed = 1 if check_out_utc else 0
                changed += 1  # la entrada siempre se genera manualmente
                label = _("Fichada Manual (x2)") if changed >= 2 else _("Fichada Manual")
                vals["higia_manual_note"] = "%s | Fichada Original: Sin Fichar" % label
                att = Attendance.create(vals)
        except Exception as exc:  # reglas nativas: solapamiento, etc.
            _logger.warning("GestionHoras: error guardando fichada: %s", exc)
            return {"error": str(exc)}

        return {"ok": True, "attendance": self._attendance_to_dict(att, tz)}

    @http.route("/gestionhoras/attendance/delete", auth="user", type="jsonrpc",
                methods=["POST"], csrf=False)
    def delete_attendance(self, attendance_id=None, **kw):
        """Elimina una fichada del día de un empleado a cargo."""
        user = self._check_access()
        if not user:
            return {"error": _("Sin permisos.")}
        att = request.env["hr.attendance"].sudo().browse(int(attendance_id or 0))
        if not att.exists() or not self._employee_allowed(user, att.employee_id):
            return {"error": _("Fichada no encontrada o fuera de su equipo.")}
        try:
            att.unlink()
        except Exception as exc:
            return {"error": str(exc)}
        return {"ok": True}

    @http.route("/gestionhoras/overtime/approve", auth="user", type="jsonrpc",
                methods=["POST"], csrf=False)
    def approve_overtime(self, attendance_id=None, **kw):
        """Aprueba las horas extras (igual que el botón de la vista Gestión)."""
        return self._overtime_action(attendance_id, "approve")

    @http.route("/gestionhoras/overtime/refuse", auth="user", type="jsonrpc",
                methods=["POST"], csrf=False)
    def refuse_overtime(self, attendance_id=None, **kw):
        """Rechaza las horas extras (igual que el botón de la vista Gestión)."""
        return self._overtime_action(attendance_id, "refuse")

    def _overtime_action(self, attendance_id, action):
        user = self._check_access()
        if not user:
            return {"error": _("Sin permisos.")}
        att = request.env["hr.attendance"].sudo().browse(int(attendance_id or 0))
        if not att.exists() or not self._employee_allowed(user, att.employee_id):
            return {"error": _("Fichada no encontrada o fuera de su equipo.")}

        try:
            if action == "approve":
                if hasattr(att, "action_approve_overtime"):
                    att.action_approve_overtime()
                elif "overtime_status" in att._fields:
                    att.write({"overtime_status": "approved"})
                else:
                    return {"error": _("La gestión de horas extras no está disponible.")}
            else:
                if hasattr(att, "action_refuse_overtime"):
                    att.action_refuse_overtime()
                elif "overtime_status" in att._fields:
                    att.write({"overtime_status": "refused"})
                else:
                    return {"error": _("La gestión de horas extras no está disponible.")}
        except Exception as exc:
            return {"error": str(exc)}

        tz = self._user_tz(user)
        return {"ok": True, "attendance": self._attendance_to_dict(att, tz)}

    @http.route("/gestionhoras/overtime/set", auth="user", type="jsonrpc",
                methods=["POST"], csrf=False)
    def set_overtime(self, attendance_id=None, hours=None, **kw):
        """Modifica la cantidad de horas extras validadas."""
        user = self._check_access()
        if not user:
            return {"error": _("Sin permisos.")}
        att = request.env["hr.attendance"].sudo().browse(int(attendance_id or 0))
        if not att.exists() or not self._employee_allowed(user, att.employee_id):
            return {"error": _("Fichada no encontrada o fuera de su equipo.")}
        if "validated_overtime_hours" not in att._fields:
            return {"error": _("El campo de horas extras validadas no está disponible.")}
        try:
            att.write({"validated_overtime_hours": float(hours or 0.0)})
        except Exception as exc:
            return {"error": str(exc)}
        tz = self._user_tz(user)
        return {"ok": True, "attendance": self._attendance_to_dict(att, tz)}

    @http.route("/gestionhoras/leave/save", auth="user", type="jsonrpc",
                methods=["POST"], csrf=False)
    def save_leave(self, employee_id=None, date=None, leave_type_id=None, **kw):
        """Crea una novedad (ausencia hr.leave de día completo) para el día."""
        user = self._check_access()
        if not user:
            return {"error": _("Sin permisos.")}
        employee = self._get_employee_checked(user, employee_id)
        if not employee:
            return {"error": _("Empleado no encontrado o fuera de su equipo.")}
        if not leave_type_id:
            return {"error": _("Debe seleccionar un tipo de novedad.")}
        try:
            day = datetime.strptime(date, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return {"error": _("Fecha inválida.")}

        leave_type = request.env["hr.leave.type"].sudo().browse(int(leave_type_id))
        if not leave_type.exists():
            return {"error": _("Tipo de novedad inexistente.")}

        # Evitar duplicados del mismo tipo en el día
        existing = self._leaves_for(employee, day).filtered(
            lambda l: l.holiday_status_id.id == leave_type.id)
        if existing:
            return {"error": _("El empleado ya tiene una novedad '%s' ese día.")
                    % leave_type.display_name}

        Leave = request.env["hr.leave"].sudo().with_context(
            leave_skip_state_check=True)
        try:
            leave = Leave.create({
                "employee_id": employee.id,
                "holiday_status_id": leave_type.id,
                "request_date_from": str(day),
                "request_date_to": str(day),
            })
        except Exception as exc:  # validaciones nativas (solapamiento, saldo)
            _logger.warning("GestionHoras: error creando novedad: %s", exc)
            return {"error": str(exc)}

        return {"ok": True, "leave": {
            "id": leave.id,
            "type_id": leave.holiday_status_id.id,
            "type_name": leave.holiday_status_id.display_name,
            "state": leave.state,
            "date_from": str(leave.request_date_from or ""),
            "date_to": str(leave.request_date_to or ""),
        }}

    @http.route("/gestionhoras/leave/delete", auth="user", type="jsonrpc",
                methods=["POST"], csrf=False)
    def delete_leave(self, leave_id=None, **kw):
        """Elimina/cancela una novedad del día de un empleado a cargo."""
        user = self._check_access()
        if not user:
            return {"error": _("Sin permisos.")}
        leave = request.env["hr.leave"].sudo().browse(int(leave_id or 0))
        if not leave.exists() or not self._employee_allowed(user, leave.employee_id):
            return {"error": _("Novedad no encontrada o fuera de su equipo.")}
        try:
            if leave.state in ("validate", "validate1"):
                leave.with_context(leave_skip_state_check=True).action_refuse()
            leave.with_context(leave_skip_state_check=True).unlink()
        except Exception as exc:
            _logger.warning("GestionHoras: error eliminando novedad: %s", exc)
            return {"error": str(exc)}
        return {"ok": True}
