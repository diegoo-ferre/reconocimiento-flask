from flask import Flask, request, jsonify
from flask_cors import CORS
import base64
import numpy as np
import cv2
import face_recognition
import psycopg2

from datetime import datetime, date, timedelta
import pytz

app = Flask(__name__)
CORS(app)

# =========================
# ZONA HORARIA PARAGUAY
# =========================
zona_py = pytz.timezone("America/Asuncion")


@app.route('/')
def home():
    return "El servidor está funcionando correctamente."


# =========================
# CONEXIÓN POSTGRES
# =========================
def get_connection():
    return psycopg2.connect(
        host="ep-ancient-haze-aca057wp-pooler.sa-east-1.aws.neon.tech",
        database="neondb",
        user="neondb_owner",
        password="npg_6rt8OdayAHcm",
        sslmode="require"
    )


# =========================
# RECONOCIMIENTO
# =========================
@app.route('/reconocer', methods=['POST'])
def reconocer():

    try:

        data = request.get_json()

        if 'foto' not in data:
            return jsonify({
                "resultado": "error",
                "mensaje": "Falta la imagen"
            })

        foto_base64 = data['foto'].split(',')[1]

        imagen = base64.b64decode(foto_base64)

        np_arr = np.frombuffer(imagen, np.uint8)

        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        rostros = face_recognition.face_locations(rgb)

        if len(rostros) == 0:
            return jsonify({
                "resultado": "sin_rostro"
            })

        encoding_actual = face_recognition.face_encodings(rgb, rostros)[0]

        conn = get_connection()

        cur = conn.cursor()

        # =========================
        # LEER CONFIGURACIÓN HORARIO
        # =========================
        cur.execute("""
            SELECT
                hora_entrada,
                tolerancia_minutos,
                hora_salida
            FROM configuracion_horario
            LIMIT 1
        """)

        config = cur.fetchone()

        if not config:

            return jsonify({
                "resultado": "error",
                "mensaje": "No existe configuración de horario"
            })

        hora_entrada_config = config[0]
        tolerancia = config[1]
        hora_salida_config = config[2]

        # =========================
        # HORA PARAGUAY
        # =========================
        ahora_py = datetime.now(zona_py)

        hoy = ahora_py.date()

        hora_actual = ahora_py.time()

        # =========================
        # PERSONAS
        # =========================
        cur.execute("""
            SELECT
                id,
                nombre,
                ci,
                foto1
            FROM personas
        """)

        personas = cur.fetchall()

        for p in personas:

            id_persona, nombre, ci, foto_db = p

            if not foto_db:
                continue

            try:

                img_bytes = base64.b64decode(
                    foto_db.split(',')[1]
                )

                np_arr_db = np.frombuffer(
                    img_bytes,
                    np.uint8
                )

                img_db = cv2.imdecode(
                    np_arr_db,
                    cv2.IMREAD_COLOR
                )

                rgb_db = cv2.cvtColor(
                    img_db,
                    cv2.COLOR_BGR2RGB
                )

                encodings_db = face_recognition.face_encodings(
                    rgb_db
                )

                if not encodings_db:
                    continue

                resultado_comparacion = face_recognition.compare_faces(
                    [encodings_db[0]],
                    encoding_actual
                )

                # =========================
                # PERSONA RECONOCIDA
                # =========================
                if resultado_comparacion[0]:

                    # =========================
                    # GUARDAR ACCESO
                    # =========================
                    cur.execute("""
                        INSERT INTO accesos(
                            persona_id,
                            nombre_detectado,
                            ci_detectado,
                            fecha_acceso,
                            resultado,
                            similitud
                        )
                        VALUES (
                            %s,
                            %s,
                            %s,
                            NOW(),
                            'permitido',
                            100
                        )
                    """, (
                        id_persona,
                        nombre,
                        ci
                    ))

                    # =========================
                    # BUSCAR ASISTENCIA DE HOY
                    # =========================
                    cur.execute("""
                        SELECT
                            id,
                            hora_entrada,
                            hora_salida
                        FROM asistencias
                        WHERE persona_id = %s
                        AND fecha = %s
                    """, (
                        id_persona,
                        hoy
                    ))

                    asistencia = cur.fetchone()

                    mensaje_asistencia = ""

                    # =========================
                    # SI NO EXISTE -> ENTRADA
                    # =========================
                    if not asistencia:

                        # hora límite
                        entrada_datetime = datetime.combine(
                            hoy,
                            hora_entrada_config
                        )

                        hora_limite = (
                            entrada_datetime +
                            timedelta(minutes=tolerancia)
                        ).time()

                        minutos_tardanza = 0

                        estado = "puntual"

                        # =========================
                        # TARDANZA
                        # =========================
                        if hora_actual > hora_limite:

                            estado = "tardanza"

                            dt_actual = datetime.combine(
                                hoy,
                                hora_actual
                            )

                            diferencia = (
                                dt_actual - entrada_datetime
                            )

                            minutos_tardanza = int(
                                diferencia.total_seconds() / 60
                            )

                        # =========================
                        # INSERTAR ENTRADA
                        # =========================
                        cur.execute("""
                            INSERT INTO asistencias(
                                persona_id,
                                fecha,
                                hora_entrada,
                                estado,
                                minutos_tardanza,
                                observacion
                            )
                            VALUES(
                                %s,
                                %s,
                                %s,
                                %s,
                                %s,
                                %s
                            )
                        """, (
                            id_persona,
                            hoy,
                            hora_actual,
                            estado,
                            minutos_tardanza,
                            estado
                        ))

                        if estado == "tardanza":

                            mensaje_asistencia = (
                                f"Llegada tardía "
                                f"({minutos_tardanza} min)"
                            )

                        else:

                            mensaje_asistencia = (
                                "Entrada registrada"
                            )

                    # =========================
                    # SI YA ENTRÓ -> SALIDA
                    # =========================
                    elif asistencia[1] and not asistencia[2]:

                        entrada_dt = datetime.combine(
                            hoy,
                            asistencia[1]
                        )

                        salida_dt = datetime.combine(
                            hoy,
                            hora_actual
                        )

                        horas = (
                            salida_dt - entrada_dt
                        ).total_seconds() / 3600

                        cur.execute("""
                            UPDATE asistencias
                            SET
                                hora_salida = %s,
                                horas_trabajadas = %s,
                                estado = 'completado'
                            WHERE id = %s
                        """, (
                            hora_actual,
                            round(horas, 2),
                            asistencia[0]
                        ))

                        mensaje_asistencia = (
                            "Salida registrada"
                        )

                    # =========================
                    # YA COMPLETÓ
                    # =========================
                    else:

                        mensaje_asistencia = (
                            "Asistencia ya completada"
                        )

                    conn.commit()

                    cur.close()

                    conn.close()

                    return jsonify({
                        "resultado": "permitido",
                        "nombre": nombre,
                        "ci": ci,
                        "hora": str(hora_actual),
                        "asistencia": mensaje_asistencia
                    })

            except Exception:
                continue

        cur.close()

        conn.close()

        return jsonify({
            "resultado": "denegado"
        })

    except Exception as e:

        return jsonify({
            "resultado": "error",
            "mensaje": str(e)
        })


if __name__ == '__main__':

    app.run(
        host='0.0.0.0',
        port=10000
    )