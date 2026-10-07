# Instructivo — Fichada Facial V2 (higia_attendance_face_v2)

## 1. Links del sistema

### Fichada (kiosco)

El link de fichada es fijo y se comparte con los empleados o se deja abierto en la tablet/celular del kiosco.

**Importante:** siempre incluir el parámetro `company_id` para que el reconocimiento facial busque solo entre los empleados de esa empresa.

| Empresa | Link de fichada |
|---|---|
| Empresa con ID 1 | `https://<tu-dominio>/face-attendance-v2?company_id=1` |
| Empresa con ID 2 | `https://<tu-dominio>/face-attendance-v2?company_id=2` |
| Empresa con ID N | `https://<tu-dominio>/face-attendance-v2?company_id=N` |

> Para conocer el ID de cada empresa: **Ajustes → Usuarios y empresas → Empresas**, abrir la empresa y ver el número que figura en la URL (`.../res.company/<ID>`).

Si se omite `company_id`, el sistema busca entre **todos** los empleados enrolados de todas las empresas (no recomendado).

### Enrolamiento (registro de rostro)

El enrolamiento es **personal y por token**: cada empleado recibe un link único de un solo uso, válido por **72 horas**.

Formato del link (se genera automáticamente, no se arma a mano):

```
https://<tu-dominio>/face-enrollment-v2/<token-único>
```

## 2. Cómo enviar el link de enrolamiento a un empleado

1. Ir a **Empleados** y abrir la ficha del empleado.
2. Ir a la pestaña **Face Recognition V2**.
3. Pulsar **"Enviar link de enrolamiento V2 (WhatsApp)"**.
   - Se genera el token y se abre WhatsApp con el mensaje y el link ya armados.
   - Requiere que el empleado tenga cargado el teléfono móvil o laboral.
4. El empleado abre el link desde su celular y sigue el asistente de **3 fotos**:
   - Foto 1: de frente, expresión neutra.
   - Foto 2: girando levemente la cabeza.
   - Foto 3: sonriendo.
   - El sistema valida la calidad de cada foto (luz, distancia, encuadre) y pide repetirla si no sirve.

Desde la misma pestaña también se puede:
- **Enviar link de fichada V2 (WhatsApp)**: envía el link de fichada con el `company_id` de la empresa del empleado ya incluido.
- **Borrar datos faciales V2**: elimina los descriptores para re-enrolar.

## 3. Cómo ficha el empleado

1. Abrir el link de fichada de su empresa (o usar el kiosco fijo).
2. Permitir el acceso a la **cámara** y a la **ubicación** (GPS obligatorio).
3. Mirar a la cámara: si el reconocimiento es claro, la fichada se registra sola.
4. Si hay alguna duda, el sistema pide el **PIN** del empleado (configurado en su ficha, pestaña Ajustes de RRHH).
5. Al finalizar, pulsar **"Aceptar"** en la pantalla de confirmación.

Notas:
- La misma pantalla registra **entrada** y **salida** alternadamente.
- Hay una protección de **60 segundos** entre fichadas del mismo empleado.

## 4. Parámetros de ajuste (opcional, para el administrador)

En **Ajustes → Técnico → Parámetros del sistema** (modo desarrollador):

| Parámetro | Valor por defecto | Descripción |
|---|---|---|
| `higia_attendance_face_v2.match_threshold` | 0.38 | Distancia máxima para fichada directa (menor = más estricto) |
| `higia_attendance_face_v2.ambiguity_threshold` | 0.48 | Distancia máxima para ser candidato (pide PIN) |
| `higia_attendance_face_v2.min_margin` | 0.10 | Margen mínimo entre 1° y 2° candidato |
| `higia_attendance_face_v2.cooldown_seconds` | 60 | Segundos mínimos entre fichadas del mismo empleado |
| `higia_attendance_face_v2.enroll_consistency` | 0.45 | Consistencia máxima exigida entre las 3 fotos de enrolamiento |
