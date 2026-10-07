# -*- coding: utf-8 -*-
import json
import logging
import math
import statistics

from odoo import fields, http
from odoo.http import request

_logger = logging.getLogger(__name__)

# Umbrales estrictos por defecto (configurables via ir.config_parameter)
MATCH_THRESHOLD = 0.38      # higia_attendance_face_v2.match_threshold
AMBIGUITY_THRESHOLD = 0.48  # higia_attendance_face_v2.ambiguity_threshold
MIN_MARGIN = 0.10           # higia_attendance_face_v2.min_margin
CHECKIN_COOLDOWN_SECONDS = 60          # higia_attendance_face_v2.cooldown_seconds
ENROLL_CONSISTENCY_THRESHOLD = 0.45    # higia_attendance_face_v2.enroll_consistency


def _get_config_float(key, default):
    get_param = request.env["ir.config_parameter"].sudo().get_param
    try:
        value = get_param(key)
        return float(value) if value not in (None, False, "") else default
    except (ValueError, TypeError):
        return default


def _get_thresholds():
    return (
        _get_config_float("higia_attendance_face_v2.match_threshold", MATCH_THRESHOLD),
        _get_config_float("higia_attendance_face_v2.ambiguity_threshold", AMBIGUITY_THRESHOLD),
        _get_config_float("higia_attendance_face_v2.min_margin", MIN_MARGIN),
    )


def _validate_coords(latitude, longitude):
    """Valida y normaliza las coordenadas recibidas desde el cliente."""
    if latitude is None or longitude is None:
        raise ValueError("Faltan coordenadas")
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        raise ValueError("Coordenadas invalidas")
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise ValueError("Coordenadas fuera de rango")
    return lat, lon


def _parse_descriptor(descriptor):
    """Valida que sea una lista de 128 floats y la devuelve normalizada."""
    if not isinstance(descriptor, list) or len(descriptor) != 128:
        raise ValueError("Descriptor invalido")
    return [float(value) for value in descriptor]


def _distance(a, b):
    return math.sqrt(sum((a[i] - float(b[i])) ** 2 for i in range(128)))


class FaceAttendanceKioskV2Controller(http.Controller):

    # ------------------------------------------------------------------
    # Paginas HTML (sin login)
    # ------------------------------------------------------------------

    @http.route("/face-attendance-v2", auth="public", methods=["GET"], website=False, csrf=False)
    def kiosk_attendance_page_v2(self, company_id=None, **kw):
        company_id_int = None
        if company_id and str(company_id).isdigit():
            company_id_int = int(company_id)
        return request.render("higia_attendance_face_v2.kiosk_attendance_page_v2", {
            "company_id": company_id_int,
        })

    @http.route("/face-attendance-v2/manifest.webmanifest", auth="public", methods=["GET"], website=False, csrf=False)
    def attendance_manifest_v2(self, company_id=None, **kw):
        start_url = "/face-attendance-v2"
        if company_id and str(company_id).isdigit():
            start_url += "?company_id=%s" % company_id
        manifest = {
            "name": "Digitalo Control Horario",
            "short_name": "Control Horario",
            "description": "Fichada por reconocimiento facial.",
            "id": start_url,
            "start_url": start_url,
            "scope": "/face-attendance-v2",
            "display": "standalone",
            "orientation": "portrait",
            "background_color": "#0f172a",
            "theme_color": "#0f172a",
            "icons": [
                {
                    "src": "/higia_attendance_face_v2/static/src/images/pwa-192.png",
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "any maskable",
                },
                {
                    "src": "/higia_attendance_face_v2/static/src/images/pwa-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "any maskable",
                },
            ],
        }
        return request.make_response(
            json.dumps(manifest, ensure_ascii=False),
            headers=[
                ("Content-Type", "application/manifest+json; charset=utf-8"),
                ("Cache-Control", "no-cache"),
            ],
        )

    @http.route("/face-attendance-v2/service-worker.js", auth="public", methods=["GET"], website=False, csrf=False)
    def attendance_service_worker_v2(self, **kw):
        # Worker minimo: solo habilita la instalacion. No cachea la pagina
        # (la fichada requiere servidor y modelos actualizados).
        script = """
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});
""".strip()
        return request.make_response(
            script,
            headers=[
                ("Content-Type", "application/javascript; charset=utf-8"),
                ("Cache-Control", "no-cache"),
                ("Service-Worker-Allowed", "/face-attendance-v2"),
            ],
        )

    @http.route(
        "/face-enrollment-v2/<string:token>",
        auth="public", methods=["GET"], website=False, csrf=False,
    )
    def enroll_page_by_token_v2(self, token, **kw):
        """Pagina de enrolamiento V2 (solo por token personal)."""
        emp_data = request.env["hr.employee"].sudo().get_employee_by_token_v2(token)
        if emp_data.get("error"):
            return request.render(
                "higia_attendance_face_v2.kiosk_enroll_page_v2",
                {"enroll_token": None, "employee_name": None, "token_error": emp_data["error"]},
            )
        return request.render(
            "higia_attendance_face_v2.kiosk_enroll_page_v2",
            {
                "enroll_token": token,
                "employee_id": emp_data["id"],
                "employee_name": emp_data["name"],
                "already_enrolled": emp_data["enrolled"],
            },
        )

    # ------------------------------------------------------------------
    # JSON API - fichada
    # ------------------------------------------------------------------

    @http.route(
        "/face-attendance-v2/checkin-face",
        auth="public", type="jsonrpc", methods=["POST"], csrf=False,
    )
    def checkin_face_v2(self, descriptor_json=None, latitude=None, longitude=None, company_id=None, **kw):
        """Fichada por rostro V2: matching estricto en servidor con multi-descriptor."""
        if not descriptor_json:
            return {"error": "Falta descriptor facial"}

        try:
            lat, lon = _validate_coords(latitude, longitude)
        except ValueError as exc:
            return {"error": "Se requiere geolocalizacion valida para fichar: %s" % str(exc)}

        try:
            current_descriptor = _parse_descriptor(json.loads(descriptor_json))
        except (ValueError, TypeError):
            return {"error": "Descriptor invalido"}

        # Filtro por empresa si el link del kiosco lo especifica
        domain = [("face_descriptors_v2", "!=", False)]
        if company_id:
            try:
                domain.append(("company_id", "=", int(company_id)))
            except (ValueError, TypeError):
                return {"error": "Empresa invalida en el link de fichada"}

        employees = request.env["hr.employee"].sudo().search(domain)
        match_threshold, ambiguity_threshold, min_margin = _get_thresholds()

        matches = []
        for employee in employees:
            descriptors = employee._get_descriptors_v2()
            if not descriptors:
                continue
            distances = [_distance(current_descriptor, stored) for stored in descriptors]
            # Score robusto: promedio entre la minima y la mediana de las distancias
            score = (min(distances) + statistics.median(distances)) / 2.0
            if score < ambiguity_threshold:
                matches.append({
                    "id": employee.id,
                    "name": employee.name,
                    "distance": score,
                })

        matches.sort(key=lambda match: match["distance"])
        # Log de calibracion: distancias de los mejores candidatos
        _logger.info(
            "FaceV2 checkin (company_id=%s): top=%s",
            company_id,
            [(m["name"], round(m["distance"], 4)) for m in matches[:3]],
        )

        if not matches:
            return {"error": "Rostro no reconocido"}

        best = matches[0]
        second = matches[1] if len(matches) > 1 else None

        # Fichada directa SOLO si el match es claro Y se destaca del segundo candidato
        if best["distance"] < match_threshold and (
            second is None or (second["distance"] - best["distance"]) >= min_margin
        ):
            return self._do_checkin(best["id"], latitude=lat, longitude=lon)

        # Ante la minima duda: PIN
        return {
            "need_pin": True,
            "message": "Verificacion adicional requerida. Ingrese PIN.",
            "candidates": [
                {"id": match["id"], "name": match["name"]}
                for match in matches[:5]
            ],
        }

    @http.route(
        "/face-attendance-v2/checkin-pin",
        auth="public", type="jsonrpc", methods=["POST"], csrf=False,
    )
    def checkin_pin_v2(self, employee_id=None, pin=None, latitude=None, longitude=None, **kw):
        """Fichada con PIN (desambiguacion)."""
        if not employee_id or pin is None:
            return {"error": "Faltan datos"}
        try:
            lat, lon = _validate_coords(latitude, longitude)
        except ValueError as exc:
            return {"error": "Se requiere geolocalizacion valida para fichar: %s" % str(exc)}
        employee = request.env["hr.employee"].sudo().browse(int(employee_id))
        if not employee.exists():
            return {"error": "Empleado no encontrado"}
        if employee.pin != str(pin):
            return {"error": "PIN incorrecto"}
        return self._do_checkin(int(employee_id), latitude=lat, longitude=lon)

    @staticmethod
    def _do_checkin(employee_id, latitude=None, longitude=None):
        employee = request.env["hr.employee"].sudo().browse(employee_id)
        if not employee.exists():
            return {"error": "Empleado no encontrado"}

        # Chequeo de suspension: solo si el modulo de suspensiones esta instalado
        if "hr.employee.suspension" in request.env:
            suspension = request.env["hr.employee.suspension"].sudo().is_employee_suspended(employee.id)
            if suspension:
                return {
                    "suspended": True,
                    "employee_name": employee.name,
                    "message": "Empleado suspendido del %s al %s. No puede fichar." % (
                        suspension.date_from, suspension.date_to,
                    ),
                }

        action_date = fields.Datetime.now()

        # Cooldown anti doble-fichada: ignorar si ficho hace menos de N segundos
        cooldown = _get_config_float("higia_attendance_face_v2.cooldown_seconds", CHECKIN_COOLDOWN_SECONDS)
        last = request.env["hr.attendance"].sudo().search(
            [("employee_id", "=", employee.id)],
            order="write_date desc", limit=1,
        )
        if last and (action_date - last.write_date).total_seconds() < cooldown:
            return {
                "error": "Ya registraste una fichada hace instantes. Aguarda un momento.",
                "cooldown": True,
                "employee_name": employee.name,
            }

        in_geo = {}
        out_geo = {}
        try:
            if latitude is not None and longitude is not None:
                in_geo["in_latitude"] = float(latitude)
                in_geo["in_longitude"] = float(longitude)
                out_geo["out_latitude"] = float(latitude)
                out_geo["out_longitude"] = float(longitude)
        except (TypeError, ValueError):
            pass

        attendance = request.env["hr.attendance"].sudo().search(
            [("employee_id", "=", employee.id), ("check_out", "=", False)],
            limit=1,
        )
        if attendance:
            vals = {"check_out": action_date}
            vals.update(out_geo)
            attendance.write(vals)
            action = "check_out"
        else:
            vals = {"employee_id": employee.id, "check_in": action_date}
            vals.update(in_geo)
            request.env["hr.attendance"].sudo().create(vals)
            action = "check_in"
        return {
            "action": action,
            "employee_name": employee.name,
            "time": fields.Datetime.to_string(action_date),
            "time_utc": fields.Datetime.to_string(action_date) + "Z",
        }

    # ------------------------------------------------------------------
    # JSON API - enrolamiento (3 fotos)
    # ------------------------------------------------------------------

    @http.route(
        "/face-enrollment-v2/by-token",
        auth="public", type="jsonrpc", methods=["POST"], csrf=False,
    )
    def get_employee_by_token_v2(self, token=None, **kw):
        if not token:
            return {"error": "Token no provisto"}
        return request.env["hr.employee"].sudo().get_employee_by_token_v2(token)

    @http.route(
        "/face-enrollment-v2/save",
        auth="public", type="jsonrpc", methods=["POST"], csrf=False,
    )
    def save_face_v2(self, employee_id=None, descriptors_json=None, image_base64=None, token=None, **kw):
        """Guarda los 3 descriptores del enrolamiento V2 con validaciones."""
        if not employee_id or not descriptors_json:
            return {"error": "Faltan datos"}
        if not token:
            return {"error": "Token de enrolamiento requerido"}

        try:
            raw = json.loads(descriptors_json)
            if not isinstance(raw, list) or len(raw) != 3:
                return {"error": "Se esperan exactamente 3 descriptores faciales"}
            descriptors = [_parse_descriptor(d) for d in raw]
        except (ValueError, TypeError):
            return {"error": "Descriptores invalidos (se esperan 3 listas de 128 floats)"}

        # Consistencia entre las 3 fotos: deben ser de la misma persona
        consistency = _get_config_float(
            "higia_attendance_face_v2.enroll_consistency", ENROLL_CONSISTENCY_THRESHOLD)
        pair_distances = [
            _distance(descriptors[0], descriptors[1]),
            _distance(descriptors[0], descriptors[2]),
            _distance(descriptors[1], descriptors[2]),
        ]
        if max(pair_distances) > consistency:
            return {
                "error": "Las fotos no son consistentes entre si (posible cambio de persona "
                         "o mala calidad). Repetir el enrolamiento.",
            }

        employee = request.env["hr.employee"].sudo().browse(int(employee_id))
        if not employee.exists():
            return {"error": "Empleado no encontrado"}
        if employee.enroll_token_v2 != token:
            return {"error": "Token de enrolamiento invalido"}
        if not employee.enroll_token_expiry_v2 or fields.Datetime.now() > employee.enroll_token_expiry_v2:
            return {"error": "Token de enrolamiento vencido"}

        # Anti-colision: rechazar si el rostro matchea fuerte con OTRO empleado enrolado
        match_threshold, _ambiguity, _margin = _get_thresholds()
        others = request.env["hr.employee"].sudo().search([
            ("face_descriptors_v2", "!=", False),
            ("id", "!=", employee.id),
        ])
        for other in others:
            for stored in other._get_descriptors_v2():
                if any(_distance(new_d, stored) < match_threshold for new_d in descriptors):
                    _logger.warning(
                        "FaceV2 enroll: rostro de %s (id=%s) colisiona con %s (id=%s)",
                        employee.name, employee.id, other.name, other.id,
                    )
                    return {
                        "error": "El rostro coincide con otro empleado ya enrolado. "
                                 "Contacta a RRHH para revisar el caso.",
                    }

        employee.write({"face_descriptors_v2": json.dumps(descriptors)})
        image_result = {}
        if image_base64:
            image_result = employee._save_enrollment_image_v2(image_base64) or {}

        employee.write({
            "enroll_token_v2": False,
            "enroll_token_expiry_v2": False,
        })
        _logger.info("FaceV2 enroll OK: %s (id=%s), consistencia max=%.4f",
                     employee.name, employee.id, max(pair_distances))
        return {
            "success": True,
            "name": employee.name,
            **image_result,
        }
