/* Gestión de Horas - Web app portal para gerentes (Odoo 19)
 * JS vanilla, sin dependencias. Consume la API JSON de /gestionhoras/*.
 */
(function () {
    "use strict";

    var state = {
        date: null,        // 'YYYY-MM-DD'
        mode: "manager",   // manager | attendance | department
        rows: [],
        leaveTypes: [],
        modal: {           // contexto del modal de fichada
            employeeId: null,
            employeeName: "",
            attendanceId: null,
        },
    };

    // ------------------------------------------------------------------
    // Utilidades
    // ------------------------------------------------------------------

    function $(id) { return document.getElementById(id); }

    function rpc(url, params) {
        return fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "same-origin",
            body: JSON.stringify({
                jsonrpc: "2.0",
                method: "call",
                params: params || {},
            }),
        }).then(function (resp) {
            if (!resp.ok) { throw new Error("HTTP " + resp.status); }
            return resp.json();
        }).then(function (data) {
            if (data.error) {
                var msg = (data.error.data && data.error.data.message) ||
                    data.error.message || "Error de servidor";
                throw new Error(msg);
            }
            return data.result;
        });
    }

    function showAlert(message, kind) {
        var el = $("gh-alert");
        el.textContent = message;
        el.className = "gh-alert " + (kind === "ok" ? "gh-alert-ok" : "gh-alert-error");
        el.style.display = "block";
        clearTimeout(el._timer);
        el._timer = setTimeout(function () { el.style.display = "none"; }, 5000);
    }

    function fmtHours(value) {
        var h = Math.floor(Math.abs(value));
        var m = Math.round((Math.abs(value) - h) * 60);
        if (m === 60) { h += 1; m = 0; }
        return (value < 0 ? "-" : "") + h + ":" + (m < 10 ? "0" : "") + m;
    }

    function esc(text) {
        var div = document.createElement("div");
        div.textContent = text == null ? "" : String(text);
        return div.innerHTML;
    }

    var STATUS_LABELS = {
        to_approve: { label: "A aprobar", cls: "gh-badge-warning" },
        approved: { label: "Aprobada", cls: "gh-badge-success" },
        refused: { label: "Rechazada", cls: "gh-badge-danger" },
    };

    // ------------------------------------------------------------------
    // Carga de datos
    // ------------------------------------------------------------------

    function loadData(date) {
        $("gh-loading").style.display = "block";
        $("gh-list").style.display = "none";
        $("gh-empty").style.display = "none";

        var params = { mode: state.mode };
        if (date) { params.date = date; }

        return rpc("/gestionhoras/data", params)
            .then(function (result) {
                if (result.error) { throw new Error(result.error); }
                state.date = result.date;
                state.mode = result.mode || state.mode;
                state.rows = result.rows || [];
                state.leaveTypes = result.leave_types || [];
                $("gh-date").value = state.date;
                $("gh-mode").value = state.mode;
                render();
            })
            .catch(function (err) {
                showAlert(err.message || "Error cargando datos", "error");
            })
            .finally(function () {
                $("gh-loading").style.display = "none";
            });
    }

    // ------------------------------------------------------------------
    // Render
    // ------------------------------------------------------------------

    function leaveTypeOptions() {
        var html = '<option value="">+ Novedad...</option>';
        state.leaveTypes.forEach(function (lt) {
            html += '<option value="' + lt.id + '">' + esc(lt.name) + "</option>";
        });
        return html;
    }

    function renderAttendance(att, row) {
        var status = STATUS_LABELS[att.overtime_status];
        var overtime = att.validated_overtime_hours || att.overtime_hours || 0;
        var html =
            '<div class="gh-att" data-att-id="' + att.id + '">' +
                '<div class="gh-att-times">' +
                    '<span class="gh-att-time" title="Entrada">&#8600; ' + (att.check_in || "--:--") + "</span>" +
                    '<span class="gh-att-time" title="Salida">&#8599; ' + (att.check_out || "--:--") + "</span>" +
                    '<span class="gh-att-worked" title="Horas trabajadas">' + fmtHours(att.worked_hours) + " hs</span>" +
                "</div>" +
                '<div class="gh-att-overtime">' +
                    '<label>Extras</label>' +
                    '<input type="number" step="0.25" min="0" class="gh-ot-input" ' +
                        'value="' + overtime + '" ' +
                        'data-att-id="' + att.id + '" title="Horas extras validadas"/>' +
                    (status
                        ? '<span class="gh-badge ' + status.cls + '">' + status.label + "</span>"
                        : "") +
                "</div>" +
                '<div class="gh-att-actions">' +
                    (att.overtime_status === "approved"
                        ? ""
                        : '<button type="button" class="gh-btn gh-btn-success gh-btn-sm gh-approve" data-att-id="' + att.id + '" title="Aprobar horas extras">&#10003; Aprobar</button>') +
                    '<button type="button" class="gh-btn gh-btn-danger gh-btn-sm gh-refuse" data-att-id="' + att.id + '" title="Rechazar horas extras">&#10007; Rechazar</button>' +
                    '<button type="button" class="gh-btn gh-btn-light gh-btn-sm gh-edit-att" data-att-id="' + att.id + '" data-emp-id="' + row.employee_id + '" title="Modificar fichada">&#9998; Editar</button>' +
                "</div>" +
                (att.manual_note
                    ? '<div class="gh-att-note" title="Novedad de modificación manual">&#9998; ' + esc(att.manual_note) + "</div>"
                    : "") +
            "</div>";
        return html;
    }

    function renderRow(row) {
        var html =
            '<div class="gh-card" data-emp-id="' + row.employee_id + '">' +
                '<div class="gh-card-head">' +
                    '<div class="gh-emp">' +
                        '<span class="gh-emp-name">' + esc(row.employee_name) + "</span>" +
                        (row.job_title ? '<span class="gh-emp-job">' + esc(row.job_title) + "</span>" : "") +
                    "</div>" +
                    '<button type="button" class="gh-btn gh-btn-primary gh-btn-sm gh-new-att" data-emp-id="' + row.employee_id + '" style="display:none">+ Fichada</button>' +
                "</div>";

        if (row.attendances.length) {
            row.attendances.forEach(function (att) { html += renderAttendance(att, row); });
        } else {
            html +=
                '<div class="gh-att gh-att-none">' +
                    '<span>Sin fichada este día</span>' +
                    '<button type="button" class="gh-btn gh-btn-light gh-btn-sm gh-edit-att" data-emp-id="' + row.employee_id + '" title="Generar fichada de entrada y salida">&#9998; Editar</button>' +
                "</div>";
        }

        html += '<div class="gh-leaves">';
        row.leaves.forEach(function (leave) {
            html +=
                '<span class="gh-leave-chip" title="' + esc(leave.date_from) + " a " + esc(leave.date_to) + '">' +
                    esc(leave.type_name) +
                    '<button type="button" class="gh-leave-del" data-leave-id="' + leave.id + '" title="Quitar novedad">&#215;</button>' +
                "</span>";
        });
        html +=
                '<select class="gh-leave-select" data-emp-id="' + row.employee_id + '">' +
                    leaveTypeOptions() +
                "</select>" +
            "</div>" +
        "</div>";
        return html;
    }

    function render() {
        var list = $("gh-list");
        if (!state.rows.length) {
            list.style.display = "none";
            $("gh-empty").style.display = "block";
            return;
        }
        $("gh-empty").style.display = "none";
        var html = "";
        state.rows.forEach(function (row) { html += renderRow(row); });
        list.innerHTML = html;
        list.style.display = "block";
    }

    function updateRowAttendance(attendance) {
        state.rows.forEach(function (row) {
            row.attendances.forEach(function (att, i) {
                if (att.id === attendance.id) { row.attendances[i] = attendance; }
            });
        });
        render();
    }

    // ------------------------------------------------------------------
    // Acciones: horas extras
    // ------------------------------------------------------------------

    function overtimeAction(url, attId, okMsg) {
        rpc(url, { attendance_id: attId })
            .then(function (result) {
                if (result.error) { throw new Error(result.error); }
                updateRowAttendance(result.attendance);
                showAlert(okMsg, "ok");
            })
            .catch(function (err) { showAlert(err.message, "error"); });
    }

    function setOvertime(attId, hours) {
        rpc("/gestionhoras/overtime/set", { attendance_id: attId, hours: hours })
            .then(function (result) {
                if (result.error) { throw new Error(result.error); }
                updateRowAttendance(result.attendance);
                showAlert("Horas extras actualizadas", "ok");
            })
            .catch(function (err) {
                showAlert(err.message, "error");
                loadData(state.date);
            });
    }

    // ------------------------------------------------------------------
    // Acciones: novedades
    // ------------------------------------------------------------------

    function addLeave(empId, typeId, selectEl) {
        rpc("/gestionhoras/leave/save", {
            employee_id: empId,
            date: state.date,
            leave_type_id: typeId,
        })
            .then(function (result) {
                if (result.error) { throw new Error(result.error); }
                var row = findRow(empId);
                if (row && result.leave) {
                    row.leaves.push(result.leave);
                    render();
                }
                showAlert("Novedad registrada", "ok");
            })
            .catch(function (err) {
                selectEl.value = "";
                showAlert(err.message, "error");
            });
    }

    function deleteLeave(leaveId) {
        if (!window.confirm("¿Quitar la novedad del empleado?")) { return; }
        rpc("/gestionhoras/leave/delete", { leave_id: leaveId })
            .then(function (result) {
                if (result.error) { throw new Error(result.error); }
                state.rows.forEach(function (row) {
                    row.leaves = row.leaves.filter(function (l) {
                        return l.id !== leaveId;
                    });
                });
                render();
                showAlert("Novedad eliminada", "ok");
            })
            .catch(function (err) { showAlert(err.message, "error"); });
    }

    // ------------------------------------------------------------------
    // Modal de fichada
    // ------------------------------------------------------------------

    function findRow(empId) {
        return state.rows.find(function (r) { return r.employee_id === empId; });
    }

    function openModal(empId, attId) {
        var row = findRow(empId);
        if (!row) { return; }
        var att = null;
        if (attId) {
            att = row.attendances.find(function (a) { return a.id === attId; }) || null;
        }
        state.modal.employeeId = empId;
        state.modal.employeeName = row.employee_name;
        state.modal.attendanceId = att ? att.id : null;

        $("gh-modal-title").textContent = att ? "Modificar fichada" : "Nueva fichada";
        $("gh-modal-employee").textContent = row.employee_name + " — " + state.date;
        $("gh-modal-checkin").value = (att && att.check_in) || "";
        $("gh-modal-checkout").value = (att && att.check_out) || "";
        $("gh-modal-delete").style.display = att ? "inline-block" : "none";
        $("gh-modal").style.display = "flex";
    }

    function closeModal() {
        $("gh-modal").style.display = "none";
        state.modal.employeeId = null;
        state.modal.attendanceId = null;
    }

    function saveModal() {
        var checkIn = $("gh-modal-checkin").value;
        var checkOut = $("gh-modal-checkout").value;
        if (!checkIn) {
            showAlert("La hora de entrada es obligatoria", "error");
            return;
        }
        rpc("/gestionhoras/attendance/save", {
            employee_id: state.modal.employeeId,
            date: state.date,
            attendance_id: state.modal.attendanceId,
            check_in: checkIn,
            check_out: checkOut || false,
        })
            .then(function (result) {
                if (result.error) { throw new Error(result.error); }
                closeModal();
                showAlert("Fichada guardada", "ok");
                return loadData(state.date);
            })
            .catch(function (err) { showAlert(err.message, "error"); });
    }

    function deleteModalAttendance() {
        if (!state.modal.attendanceId) { return; }
        if (!window.confirm("¿Eliminar la fichada del empleado?")) { return; }
        rpc("/gestionhoras/attendance/delete", { attendance_id: state.modal.attendanceId })
            .then(function (result) {
                if (result.error) { throw new Error(result.error); }
                closeModal();
                showAlert("Fichada eliminada", "ok");
                return loadData(state.date);
            })
            .catch(function (err) { showAlert(err.message, "error"); });
    }

    // ------------------------------------------------------------------
    // Eventos (delegación)
    // ------------------------------------------------------------------

    function bindEvents() {
        $("gh-mode").addEventListener("change", function () {
            state.mode = this.value || "manager";
            loadData(state.date);
        });
        $("gh-date").addEventListener("change", function () {
            if (this.value) { loadData(this.value); }
        });
        $("gh-refresh").addEventListener("click", function () {
            loadData(state.date);
        });

        $("gh-list").addEventListener("click", function (ev) {
            var btn = ev.target.closest("button");
            if (!btn) { return; }
            var attId = parseInt(btn.getAttribute("data-att-id"), 10);
            var empId = parseInt(btn.getAttribute("data-emp-id"), 10);

            if (btn.classList.contains("gh-approve")) {
                overtimeAction("/gestionhoras/overtime/approve", attId, "Horas extras aprobadas");
            } else if (btn.classList.contains("gh-refuse")) {
                overtimeAction("/gestionhoras/overtime/refuse", attId, "Horas extras rechazadas");
            } else if (btn.classList.contains("gh-edit-att")) {
                openModal(empId, attId);
            } else if (btn.classList.contains("gh-new-att")) {
                openModal(empId, null);
            } else if (btn.classList.contains("gh-leave-del")) {
                deleteLeave(parseInt(btn.getAttribute("data-leave-id"), 10));
            }
        });

        $("gh-list").addEventListener("change", function (ev) {
            var target = ev.target;
            if (target.classList.contains("gh-ot-input")) {
                setOvertime(parseInt(target.getAttribute("data-att-id"), 10),
                    parseFloat(target.value || "0"));
            } else if (target.classList.contains("gh-leave-select") && target.value) {
                addLeave(parseInt(target.getAttribute("data-emp-id"), 10),
                    parseInt(target.value, 10), target);
            }
        });

        $("gh-modal-cancel").addEventListener("click", closeModal);
        $("gh-modal-save").addEventListener("click", saveModal);
        $("gh-modal-delete").addEventListener("click", deleteModalAttendance);
        $("gh-modal").addEventListener("click", function (ev) {
            if (ev.target === this) { closeModal(); }
        });
        document.addEventListener("keydown", function (ev) {
            if (ev.key === "Escape") { closeModal(); }
        });
    }

    // ------------------------------------------------------------------
    // Init
    // ------------------------------------------------------------------

    document.addEventListener("DOMContentLoaded", function () {
        bindEvents();
        loadData(null); // null => backend calcula el día anterior hábil
    });
})();
