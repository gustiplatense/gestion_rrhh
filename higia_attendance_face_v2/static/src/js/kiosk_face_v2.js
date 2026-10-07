/**
 * HIGIA Face Attendance V2 - Standalone Kiosk (Vanilla JS)
 * Fichada: camara primero, PIN ante la minima duda (decide el servidor).
 * Enrolamiento: guiado con 3 fotos y control de calidad de imagen.
 * Filtro por empresa via ?company_id=N en el link del kiosco.
 */
(function () {
    'use strict';

    var MODELS_URL         = '/higia_attendance_face_v2/static/models';
    var RESULT_RESET_MS    = 4000;
    var RESULT_FAILSAFE_MS = 60000;   // failsafe del boton Aceptar (kiosco compartido)
    var SCAN_INTERVAL_MS   = 400;
    var MAX_NO_FACE_TRIES  = 30;

    // Parametros estrictos V2
    var MIN_CONFIDENCE = 0.6;   // score minimo del detector (nitidez)
    var MIN_FACE_SIZE  = 140;   // px minimos del bounding box (rostro cerca)
    var EDGE_MARGIN    = 10;    // px de margen para exigir rostro completo en cuadro
    var MIN_BRIGHTNESS = 55;    // brillo promedio minimo del recorte del rostro
    var MAX_BRIGHTNESS = 215;   // brillo promedio maximo (contraluz / quemado)
    var ENROLL_PHOTOS  = 3;

    var ENROLL_STEPS = [
        'Foto 1: Mira DE FRENTE a la camara, con expresion neutra.',
        'Foto 2: Gira LEVEMENTE la cabeza hacia un costado (sin dejar de mirar).',
        'Foto 3: Sonrei mirando a la camara.',
    ];

    function jsonRpc(endpoint, params) {
        return fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ jsonrpc: '2.0', method: 'call', id: 1, params: params }),
        })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.error) throw new Error((data.error.data && data.error.data.message) || data.error.message || 'Server error');
                return data.result;
            });
    }

    function stopStream(stream) {
        if (stream) stream.getTracks().forEach(function(t) { t.stop(); });
    }

    /**
     * El servidor devuelve la hora en UTC ("YYYY-MM-DD HH:MM:SS" y time_utc con sufijo Z).
     * Se convierte a la zona horaria del dispositivo del usuario.
     */
    function formatLocalTime(res) {
        var raw = (res && (res.time_utc || res.time)) || '';
        if (!raw) return '';
        var iso = raw.replace(' ', 'T');
        if (!/Z$/i.test(iso)) iso += 'Z';
        var date = new Date(iso);
        if (isNaN(date.getTime())) return raw;
        return date.toLocaleString('es-AR', {
            day: '2-digit', month: '2-digit', year: 'numeric',
            hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
        });
    }

    async function loadModels() {
        await Promise.all([
            faceapi.nets.ssdMobilenetv1.loadFromUri(MODELS_URL),
            faceapi.nets.faceLandmark68Net.loadFromUri(MODELS_URL),
            faceapi.nets.faceRecognitionNet.loadFromUri(MODELS_URL),
        ]);
    }

    function waitForVideoPlaying(video) {
        return new Promise(function(resolve) {
            if (video.readyState >= 2 && !video.paused) { resolve(); return; }
            var done = false;
            function finish() {
                if (done) return;
                done = true;
                video.removeEventListener('playing', finish);
                video.removeEventListener('timeupdate', finish);
                video.removeEventListener('canplay', finish);
                resolve();
            }
            video.addEventListener('playing', finish);
            video.addEventListener('timeupdate', finish);
            video.addEventListener('canplay', finish);
            video.play().catch(function() { finish(); });
            setTimeout(finish, 4000);
        });
    }

    // ----------------------------------------------------------------
    // Audio feedback
    // ----------------------------------------------------------------
    var audioCtx = null;
    var audioUnlocked = false;
    var soundButton = null;

    function ensureAudioContext() {
        if (!(window.AudioContext || window.webkitAudioContext)) return null;
        if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        return audioCtx;
    }

    async function unlockAudio() {
        if (audioUnlocked) return true;
        try {
            var ctx = ensureAudioContext();
            if (!ctx) return false;
            if (ctx.state === 'suspended') await ctx.resume();
            audioUnlocked = true;
            if (soundButton) {
                soundButton.textContent = 'Sonido activo';
                soundButton.classList.add('higia-sound-btn-active');
            }
            return true;
        } catch(e) {
            return false;
        }
    }

    document.addEventListener('click', function() { unlockAudio(); }, { once: true });
    document.addEventListener('touchstart', function() { unlockAudio(); }, { once: true });

    // ----------------------------------------------------------------
    // Instalacion PWA (boton visible solo si el navegador lo permite)
    // ----------------------------------------------------------------
    var deferredInstallPrompt = null;

    function isPwaInstalled() {
        return window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
    }

    function updateInstallButton() {
        var btn = document.getElementById('pwa-install-btn');
        if (!btn) return;
        btn.style.display = (deferredInstallPrompt && !isPwaInstalled()) ? '' : 'none';
    }

    window.addEventListener('beforeinstallprompt', function(event) {
        event.preventDefault();
        deferredInstallPrompt = event;
        updateInstallButton();
    });

    window.addEventListener('appinstalled', function() {
        deferredInstallPrompt = null;
        updateInstallButton();
    });

    function initInstallButton() {
        var btn = document.getElementById('pwa-install-btn');
        if (!btn) return;
        btn.addEventListener('click', async function() {
            if (!deferredInstallPrompt) { updateInstallButton(); return; }
            var prompt = deferredInstallPrompt;
            deferredInstallPrompt = null;
            try {
                prompt.prompt();
                await prompt.userChoice;
            } catch(e) { /* noop */ }
            updateInstallButton();
        });
        updateInstallButton();
    }

    function playTone(freq, start, duration, volume) {
        try {
            var ctx = ensureAudioContext();
            if (!ctx) return;
            if (!audioUnlocked && ctx.state === 'suspended') return;
            var osc = ctx.createOscillator();
            var gain = ctx.createGain();
            osc.type = 'sine';
            osc.frequency.setValueAtTime(freq, ctx.currentTime + start);
            gain.gain.setValueAtTime(0.0001, ctx.currentTime + start);
            gain.gain.exponentialRampToValueAtTime(volume || 0.18, ctx.currentTime + start + 0.02);
            gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + start + duration);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start(ctx.currentTime + start);
            osc.stop(ctx.currentTime + start + duration + 0.03);
        } catch(e) { /* noop */ }
    }

    function playSound(type) {
        if (type === 'success')      { playTone(660, 0.00, 0.12, 0.20); playTone(880, 0.14, 0.18, 0.22); }
        else if (type === 'error')   { playTone(220, 0.00, 0.20, 0.22); playTone(160, 0.22, 0.28, 0.22); }
        else if (type === 'warning') { playTone(440, 0.00, 0.12, 0.18); playTone(440, 0.18, 0.12, 0.18); }
        else                         { playTone(520, 0.00, 0.10, 0.12); }
    }

    function setMessage(el, text, type) {
        if (!el) return;
        el.textContent = text || '';
        el.classList.remove('higia-msg-info', 'higia-msg-success', 'higia-msg-warning', 'higia-msg-error');
        el.classList.add('higia-msg-' + (type || 'info'));
    }

    // ----------------------------------------------------------------
    // Control de calidad de la deteccion
    // ----------------------------------------------------------------
    function averageBrightness(video, box) {
        try {
            var canvas = document.createElement('canvas');
            var w = Math.max(1, Math.round(box.width / 4));
            var h = Math.max(1, Math.round(box.height / 4));
            canvas.width = w;
            canvas.height = h;
            var ctx = canvas.getContext('2d');
            ctx.drawImage(video, box.x, box.y, box.width, box.height, 0, 0, w, h);
            var data = ctx.getImageData(0, 0, w, h).data;
            var sum = 0;
            for (var i = 0; i < data.length; i += 4) {
                sum += 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
            }
            return sum / (data.length / 4);
        } catch(e) {
            return null; // no bloquear si el canvas falla
        }
    }

    /**
     * Devuelve null si la deteccion tiene calidad suficiente,
     * o un string con la indicacion para el usuario si no.
     */
    function checkQuality(detection, video) {
        var det = detection.detection || detection;
        var box = det.box;
        if (det.score < MIN_CONFIDENCE) {
            return 'Imagen poco nitida. Busca mejor iluminacion y no te muevas.';
        }
        if (box.width < MIN_FACE_SIZE || box.height < MIN_FACE_SIZE) {
            return 'Acercate mas a la camara.';
        }
        if (box.x < EDGE_MARGIN || box.y < EDGE_MARGIN ||
            box.x + box.width > video.videoWidth - EDGE_MARGIN ||
            box.y + box.height > video.videoHeight - EDGE_MARGIN) {
            return 'Centra tu rostro completo en la pantalla.';
        }
        var brightness = averageBrightness(video, box);
        if (brightness !== null) {
            if (brightness < MIN_BRIGHTNESS) return 'Muy oscuro. Busca mas luz.';
            if (brightness > MAX_BRIGHTNESS) return 'Demasiada luz de frente. Evita el contraluz.';
        }
        return null;
    }

    // ----------------------------------------------------------------
    // KIOSK DE FICHADA V2
    // ----------------------------------------------------------------
    async function initAttendance(appElement) {
        var stream      = null;
        var scanning    = false;
        var detecting   = false;
        var noFaceTries = 0;
        var candidates  = [];
        var pin         = '';
        var gpsCoords   = null;

        // company_id: primero del dataset (render server), luego de la URL
        var companyId = (appElement && appElement.dataset && appElement.dataset.companyId) || '';
        if (!companyId) {
            companyId = new URLSearchParams(window.location.search).get('company_id') || '';
        }

        var statusEl   = document.getElementById('camera-status');
        var pinDisplay = document.getElementById('pin-display');
        var pinErrorEl = document.getElementById('pin-error');
        soundButton = document.getElementById('sound-enable-btn');

        if (soundButton) {
            soundButton.addEventListener('click', async function() {
                var ok = await unlockAudio();
                if (ok) playSound('success');
            });
        }
        initInstallButton();

        function showScreen(name) {
            ['screen-camera', 'screen-pin', 'screen-result'].forEach(function(id) {
                var el = document.getElementById(id);
                if (el) el.style.display = (id === name) ? '' : 'none';
            });
        }

        function requestGps() {
            if (!navigator.geolocation) return;
            navigator.geolocation.getCurrentPosition(
                function(pos) { gpsCoords = { latitude: pos.coords.latitude, longitude: pos.coords.longitude }; },
                function() { gpsCoords = null; },
                { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 }
            );
        }

        (async function() {
            try {
                requestGps();
                setMessage(statusEl, 'Cargando modelos de IA...', 'info');
                await loadModels();
                setMessage(statusEl, 'Listo. Mira a la camara...', 'success');
                playSound('info');
                await startCamera();
            } catch(err) {
                setMessage(statusEl, 'Error al iniciar: ' + err.message, 'error');
                playSound('error');
            }
        })();

        async function startCamera() {
            try {
                stream = await navigator.mediaDevices.getUserMedia({
                    video: { facingMode: 'user', width: { ideal: 640 }, height: { ideal: 480 } },
                });
                var video = document.getElementById('att-video');
                video.srcObject = stream;
                await waitForVideoPlaying(video);
            } catch(e) {
                setMessage(statusEl, 'No se pudo acceder a la camara: ' + e.message, 'error');
                playSound('error');
                return;
            }
            scanning    = true;
            detecting   = false;
            noFaceTries = 0;
            scheduleScan();
        }

        function scheduleScan() {
            if (!scanning) return;
            setTimeout(runScan, SCAN_INTERVAL_MS);
        }

        async function runScan() {
            if (!scanning || detecting) return;
            detecting = true;
            var video = document.getElementById('att-video');
            var detection = null;
            try {
                detection = await faceapi
                    .detectSingleFace(video, new faceapi.SsdMobilenetv1Options({ minConfidence: MIN_CONFIDENCE }))
                    .withFaceLandmarks()
                    .withFaceDescriptor();
            } catch(e) {
                detecting = false;
                scheduleScan();
                return;
            }
            detecting = false;

            if (!detection) {
                noFaceTries++;
                if (noFaceTries >= MAX_NO_FACE_TRIES) {
                    setMessage(statusEl, 'No se detecta rostro. Acercate a la camara y mira de frente.', 'warning');
                    playSound('warning');
                    noFaceTries = 0;
                } else {
                    setMessage(statusEl, 'Mira a la camara... (' + noFaceTries + '/' + MAX_NO_FACE_TRIES + ')', 'info');
                }
                scheduleScan();
                return;
            }

            // Control de calidad tambien en la fichada: descarta frames malos
            var qualityIssue = checkQuality(detection, video);
            if (qualityIssue) {
                setMessage(statusEl, qualityIssue, 'warning');
                scheduleScan();
                return;
            }

            noFaceTries = 0;
            scanning = false;
            stopStream(stream);
            setMessage(statusEl, 'Rostro detectado. Validando en servidor...', 'success');
            await doCheckinFace(Array.from(detection.descriptor));
        }

        async function doCheckinFace(descriptor) {
            try {
                var params = { descriptor_json: JSON.stringify(descriptor) };
                if (gpsCoords) { params.latitude = gpsCoords.latitude; params.longitude = gpsCoords.longitude; }
                if (companyId) { params.company_id = companyId; }
                var res = await jsonRpc('/face-attendance-v2/checkin-face', params);

                if (res && res.need_pin) {
                    candidates = res.candidates || [];
                    if (!candidates.length) {
                        showResultError('No hay candidatos para validar PIN.');
                        return;
                    }
                    pin = '';
                    updatePinDisplay();
                    playSound('warning');
                    showScreen('screen-pin');
                    return;
                }

                showResult(res);
            } catch(err) {
                showResultError('Error al fichar: ' + err.message);
            }
        }

        document.querySelectorAll('.higia-num-btn').forEach(function(btn) {
            btn.addEventListener('click', function() {
                var d = btn.dataset.digit;
                if (d === 'clear') { pin = ''; pinErrorEl.textContent = ''; }
                else if (d === 'ok') { handlePinOk(); }
                else if (pin.length < 8) { pin += d; }
                updatePinDisplay();
            });
        });

        function updatePinDisplay() {
            pinDisplay.textContent = pin.length ? pin.replace(/./g, '*') : '----';
        }

        async function handlePinOk() {
            if (!pin) return;
            pinErrorEl.textContent = '';
            await tryPinWithCandidates(0);
        }

        async function tryPinWithCandidates(index) {
            if (index >= candidates.length) {
                pinErrorEl.textContent = 'PIN incorrecto.';
                playSound('error');
                pin = '';
                updatePinDisplay();
                return;
            }
            try {
                var params = { employee_id: candidates[index].id, pin: pin };
                if (gpsCoords) { params.latitude = gpsCoords.latitude; params.longitude = gpsCoords.longitude; }
                var res = await jsonRpc('/face-attendance-v2/checkin-pin', params);
                if (res && res.error === 'PIN incorrecto') {
                    await tryPinWithCandidates(index + 1);
                } else {
                    showResult(res);
                }
            } catch(err) {
                pinErrorEl.textContent = 'Error: ' + err.message;
                playSound('error');
            }
        }

        document.getElementById('att-cancel-pin-btn').addEventListener('click', async function() {
            pin = ''; candidates = [];
            showScreen('screen-camera');
            await startCamera();
        });

        var resultTimer = null;
        var acceptBtn   = document.getElementById('result-accept-btn');

        function cancelResultTimer() {
            if (resultTimer) { clearTimeout(resultTimer); resultTimer = null; }
        }

        async function resetToCamera() {
            cancelResultTimer();
            pin = ''; candidates = [];
            if (acceptBtn) acceptBtn.style.display = 'none';
            showScreen('screen-camera');
            await startCamera();
        }

        if (acceptBtn) {
            acceptBtn.addEventListener('click', function() { resetToCamera(); });
        }

        function showResult(res) {
            if (res && res.suspended) { showResultSuspended(res); return; }
            if (res && res.error) { showResultError(res.error); return; }
            var isIn = res.action === 'check_in';
            var icon = document.getElementById('result-icon');
            var title = document.getElementById('result-title');
            var msg = document.getElementById('result-msg');
            icon.textContent = 'OK';
            icon.className = 'higia-result-icon higia-ok';
            title.textContent = isIn ? 'Entrada registrada' : 'Salida registrada';
            title.className = 'higia-result-title higia-result-title-ok';
            msg.textContent = res.employee_name + '  ' + formatLocalTime(res);
            msg.className = 'higia-result-msg higia-result-msg-ok';
            playSound('success');
            // La camara queda DETENIDA: el usuario debe pulsar Aceptar
            // para evitar una nueva fichada accidental del mismo rostro.
            if (acceptBtn) acceptBtn.style.display = '';
            showScreen('screen-result');
            cancelResultTimer();
            resultTimer = setTimeout(resetToCamera, RESULT_FAILSAFE_MS);
        }

        function showResultSuspended(res) {
            var icon = document.getElementById('result-icon');
            var title = document.getElementById('result-title');
            var msgEl = document.getElementById('result-msg');
            icon.className = 'higia-result-icon higia-fail higia-suspended-icon';
            icon.textContent = '\u26D4';
            title.textContent = 'EMPLEADO SUSPENDIDO';
            title.className = 'higia-result-title higia-result-title-error';
            msgEl.textContent = (res.employee_name ? res.employee_name + '. ' : '') + (res.message || 'No puede fichar.');
            msgEl.className = 'higia-result-msg higia-result-msg-error';
            playSound('error');
            if (acceptBtn) acceptBtn.style.display = 'none';
            showScreen('screen-result');
            cancelResultTimer();
            resultTimer = setTimeout(resetToCamera, RESULT_FAILSAFE_MS);
        }

        function showResultError(msg) {
            var icon = document.getElementById('result-icon');
            var title = document.getElementById('result-title');
            var msgEl = document.getElementById('result-msg');
            icon.className = 'higia-result-icon higia-fail';
            icon.textContent = 'ERR';
            title.textContent = 'Error';
            title.className = 'higia-result-title higia-result-title-error';
            msgEl.textContent = msg;
            msgEl.className = 'higia-result-msg higia-result-msg-error';
            playSound('error');
            if (acceptBtn) acceptBtn.style.display = 'none';
            showScreen('screen-result');
            cancelResultTimer();
            resultTimer = setTimeout(resetToCamera, RESULT_RESET_MS);
        }
    }

    // ----------------------------------------------------------------
    // KIOSK DE ENROLAMIENTO V2 (3 fotos guiadas)
    // ----------------------------------------------------------------
    async function initEnrollment() {
        var stream          = null;
        var detecting       = false;
        var scanning        = false;
        var currentEmployee = null;
        var modelsLoaded    = false;
        var descriptors     = [];   // hasta 3 descriptores
        var firstImage      = null; // foto frontal para la ficha
        var currentStep     = 0;

        var app           = document.getElementById('higia-enroll-app');
        var token         = app ? (app.dataset.token || '') : '';
        var preloadedId   = app ? (app.dataset.employeeId || '') : '';
        var preloadedName = app ? (app.dataset.employeeName || '') : '';

        function showScreen(name) {
            ['screen-confirm', 'screen-capture', 'screen-enroll-result'].forEach(function(id) {
                var el = document.getElementById(id);
                if (el) el.style.display = (id === name) ? '' : 'none';
            });
        }

        if (token && preloadedId) {
            currentEmployee = { id: parseInt(preloadedId, 10), name: preloadedName, token: token };
        } else {
            return; // enrolamiento V2 solo por token
        }

        var captureBtn = document.getElementById('capture-btn');
        if (captureBtn) {
            captureBtn.addEventListener('click', async function() {
                descriptors = [];
                firstImage  = null;
                currentStep = 0;
                showScreen('screen-capture');
                await startGuidedCapture();
            });
        }

        function updateStepUi() {
            var title = document.getElementById('capture-step-title');
            var instruction = document.getElementById('capture-instruction');
            if (title) title.textContent = 'Foto ' + (currentStep + 1) + ' de ' + ENROLL_PHOTOS;
            if (instruction) instruction.textContent = ENROLL_STEPS[currentStep];
            for (var i = 1; i <= ENROLL_PHOTOS; i++) {
                var dot = document.getElementById('step-dot-' + i);
                if (dot) {
                    dot.className = 'higia-step-dot' +
                        (i <= descriptors.length ? ' higia-step-dot-done' :
                         (i === currentStep + 1 ? ' higia-step-dot-active' : ''));
                }
            }
        }

        async function startGuidedCapture() {
            var video  = document.getElementById('enroll-video');
            var status = document.getElementById('enroll-status');
            scanning  = true;
            detecting = false;
            var tries = 0;
            var stableOkFrames = 0;

            updateStepUi();
            status.textContent = 'Iniciando camara...';
            try {
                stream = await navigator.mediaDevices.getUserMedia({
                    video: { facingMode: 'user', width: { ideal: 640 }, height: { ideal: 480 } },
                });
                video.srcObject = stream;
            } catch(e) {
                status.textContent = 'ERROR camara: ' + e.message;
                scanning = false;
                return;
            }

            await waitForVideoPlaying(video);

            if (!modelsLoaded) {
                status.textContent = 'Cargando modelos de deteccion...';
                try {
                    await loadModels();
                    modelsLoaded = true;
                } catch(e) {
                    status.textContent = 'ERROR al cargar modelos: ' + e.message;
                    scanning = false; stopStream(stream);
                    return;
                }
            }

            await new Promise(function(r) { setTimeout(r, 500); });
            setMessage(status, ENROLL_STEPS[currentStep], 'info');

            function scheduleCapture() { if (scanning) setTimeout(runCapture, SCAN_INTERVAL_MS); }

            async function runCapture() {
                if (!scanning || detecting) return;
                detecting = true;
                tries++;

                if (video.videoWidth === 0 || video.readyState < 2) {
                    detecting = false;
                    scheduleCapture();
                    return;
                }

                var detection = null;
                try {
                    detection = await faceapi
                        .detectSingleFace(video, new faceapi.SsdMobilenetv1Options({ minConfidence: MIN_CONFIDENCE }))
                        .withFaceLandmarks()
                        .withFaceDescriptor();
                } catch(e) {
                    detecting = false;
                    scheduleCapture();
                    return;
                }
                detecting = false;

                if (!detection) {
                    stableOkFrames = 0;
                    if (tries >= MAX_NO_FACE_TRIES * ENROLL_PHOTOS) {
                        scanning = false;
                        stopStream(stream);
                        showEnrollResult(false, 'Sin deteccion',
                            'No se detecto un rostro con calidad suficiente.\n' +
                            'Asegurate de tener buena iluminacion y mirar de frente.');
                        return;
                    }
                    setMessage(status, 'Buscando rostro... ' + ENROLL_STEPS[currentStep], 'info');
                    scheduleCapture();
                    return;
                }

                // Control de calidad: si la foto no sirve, pedir que se repita
                var qualityIssue = checkQuality(detection, video);
                if (qualityIssue) {
                    stableOkFrames = 0;
                    setMessage(status, qualityIssue, 'warning');
                    scheduleCapture();
                    return;
                }

                // Exigir 2 frames buenos consecutivos antes de capturar (estabilidad)
                stableOkFrames++;
                if (stableOkFrames < 2) {
                    setMessage(status, 'Calidad OK, mantene la posicion...', 'success');
                    scheduleCapture();
                    return;
                }

                // Capturar este paso
                stableOkFrames = 0;
                descriptors.push(Array.from(detection.descriptor));
                if (currentStep === 0) {
                    firstImage = captureVideoImage(video);
                }
                playSound('info');
                setMessage(status, 'Foto ' + (currentStep + 1) + ' capturada.', 'success');
                currentStep++;
                updateStepUi();

                if (currentStep >= ENROLL_PHOTOS) {
                    scanning = false;
                    stopStream(stream);
                    setMessage(status, 'Guardando enrolamiento...', 'info');
                    await saveDescriptors();
                    return;
                }

                // Pausa breve para que el usuario cambie de pose
                setMessage(status, ENROLL_STEPS[currentStep], 'info');
                setTimeout(scheduleCapture, 1500);
            }
            scheduleCapture();
        }

        document.getElementById('enroll-cancel-btn').addEventListener('click', function() {
            scanning = false; stopStream(stream);
            descriptors = [];
            currentStep = 0;
            showScreen('screen-confirm');
        });

        function captureVideoImage(video) {
            if (!video || !video.videoWidth || !video.videoHeight) return null;
            var maxSize = 512;
            var ratio = Math.min(maxSize / video.videoWidth, maxSize / video.videoHeight, 1);
            var canvas = document.createElement('canvas');
            canvas.width = Math.round(video.videoWidth * ratio);
            canvas.height = Math.round(video.videoHeight * ratio);
            canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
            var dataUrl = canvas.toDataURL('image/jpeg', 0.80);
            return dataUrl.split(',')[1] || null;
        }

        async function saveDescriptors() {
            try {
                var res = await jsonRpc('/face-enrollment-v2/save', {
                    employee_id: currentEmployee.id,
                    descriptors_json: JSON.stringify(descriptors),
                    image_base64: firstImage || null,
                    token: currentEmployee.token || null,
                });
                if (res.error) {
                    showEnrollResult(false, 'Error', res.error);
                } else {
                    playSound('success');
                    showEnrollResult(true, 'Enrolamiento exitoso!',
                        'Rostro guardado para: ' + res.name + '\nSe registraron 3 fotos de referencia.');
                }
            } catch(err) {
                showEnrollResult(false, 'Error', err.message);
            }
        }

        function showEnrollResult(ok, title, msg) {
            document.getElementById('enroll-result-icon').textContent = ok ? 'OK' : 'ERR';
            document.getElementById('enroll-result-icon').className   = 'higia-result-icon ' + (ok ? 'higia-ok' : 'higia-fail');
            document.getElementById('enroll-result-title').textContent = title;
            document.getElementById('enroll-result-msg').textContent   = msg;
            var retryBtn = document.getElementById('enroll-retry-btn');
            if (retryBtn) retryBtn.style.display = ok ? 'none' : '';
            if (!ok) playSound('error');
            showScreen('screen-enroll-result');
        }

        var retryBtn = document.getElementById('enroll-retry-btn');
        if (retryBtn) {
            retryBtn.addEventListener('click', function() {
                descriptors = [];
                currentStep = 0;
                showScreen('screen-confirm');
            });
        }
    }

    document.addEventListener('DOMContentLoaded', function() {
        var app    = document.getElementById('higia-attendance-app');
        var enroll = document.getElementById('higia-enroll-app');
        if (app && app.dataset.mode === 'attendance') initAttendance(app).catch(console.error);
        else if (enroll && enroll.dataset.mode === 'enroll') initEnrollment().catch(console.error);
    });

})();
