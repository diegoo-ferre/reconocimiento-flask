from flask import Flask, request, jsonify
from flask_cors import CORS
import base64
import numpy as np
import cv2
import face_recognition
import psycopg2
from datetime import datetime, date, timedelta

app = Flask(__name__)
CORS(app)

def get_connection():
    return psycopg2.connect(
        host="ep-ancient-haze-aca057wp-pooler.sa-east-1.aws.neon.tech",
        database="neondb",
        user="neondb_owner",
        password="npg_6rt8OdayAHcm",
        sslmode="require"
    )

@app.route('/')
def home():
    return "API de reconocimiento facial activa."

@app.route('/reconocer', methods=['POST'])
def reconocer():
    conn = None
    cur = None
    try:
        data = request.get_json()
        if not data or 'foto' not in data:
            return jsonify({"resultado": "error", "mensaje": "Falta la imagen"})
            
        raw_turno_id = data.get('turno_id')
        if not raw_turno_id:
            return jsonify({"resultado": "error", "mensaje": "Debe seleccionar un turno"})
        
        try:
            turno_id = int(raw_turno_id)
        except ValueError:
            return jsonify({"resultado": "error", "mensaje": "Turno inválido"})

        foto_base64 = data['foto'].split(',')[1]
        imagen = base64.b64decode(foto_base64)
        np_arr = np.frombuffer(imagen, np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        rostros = face_recognition.face_locations(rgb)
        if len(rostros) == 0:
            return jsonify({"resultado": "sin_rostro"})

        encoding_actual = face_recognition.face_encodings(rgb, rostros)[0]
        
        conn = get_connection()
        cur = conn.cursor()

        # 1. Consultar el horario oficial del turno seleccionado
        cur.execute("SELECT hora_inicio, hora_fin FROM turnos WHERE id = %s", (turno_id,))
        turno_info = cur.fetchone()
        if not turno_info:
            cur.close()
            conn.close()
            return jsonify({"resultado": "error", "mensaje": "Turno no registrado en la base de datos"})
        
        hora_inicio_turno = turno_info[0]

        # 2. Consultar la tolerancia en minutos configurada globalmente
        cur.execute("SELECT tolerancia_minutos FROM configuracion_horario LIMIT 1")
        tol_info = cur.fetchone()
        tolerancia_minutos = tol_info[0] if tol_info else 10

        cur.execute("SELECT id, nombre, ci, foto1 FROM personas")
        personas = cur.fetchall()

        for p in personas:
            id_persona, nombre, ci, foto_db = p
            if not foto_db: 
                continue
            
            try:
                img_bytes = base64.b64decode(foto_db.split(',')[1])
                np_arr_db = np.frombuffer(img_bytes, np.uint8)
                img_db = cv2.imdecode(np_arr_db, cv2.IMREAD_COLOR)
                rgb_db = cv2.cvtColor(img_db, cv2.COLOR_BGR2RGB)
                encodings_db = face_recognition.face_encodings(rgb_db)
                if not encodings_db: 
                    continue
                
                # Comparación de rostros
                resultado_comparacion = face_recognition.compare_faces([encodings_db[0]], encoding_actual)
                
                if resultado_comparacion[0]:
                    ahora_py = datetime.now() - timedelta(hours=3)
                    hoy = ahora_py.date()
                    hora_actual = ahora_py.time()
                    
                    dt_inicio_oficial = datetime.combine(hoy, hora_inicio_turno)
                    dt_limite_tolerancia = dt_inicio_oficial + timedelta(minutes=tolerancia_minutos)
                    dt_marcacion = datetime.combine(hoy, hora_actual)

                    estado_asistencia_str = "Presente"
                    if dt_marcacion > dt_limite_tolerancia:
                        estado_asistencia_str = "Tardanza"

                    # Registrar acceso en la base de datos
                    cur.execute("""
                        INSERT INTO accesos (persona_id, nombre_detectado, ci_detectado, fecha_acceso, resultado, similitud)
                        VALUES (%s, %s, %s, %s, 'Permitido', 100)
                    """, (id_persona, nombre, ci, ahora_py))
                    
                    # Control de asistencias (asociado al turno_id)
                    cur.execute("SELECT id, hora_entrada, hora_salida FROM asistencias WHERE persona_id = %s AND fecha = %s AND turno_id = %s", (id_persona, hoy, turno_id))
                    asistencia = cur.fetchone()
                    
                    mensaje_asistencia = ""
                    if not asistencia:
                        cur.execute("""
                            INSERT INTO asistencias (persona_id, fecha, hora_entrada, estado, turno_id) 
                            VALUES (%s, %s, %s, %s, %s)
                        """, (id_persona, hoy, hora_actual, estado_asistencia_str, turno_id))
                        mensaje_asistencia = f"Entrada registrada ({estado_asistencia_str})"
                    elif asistencia[1] and not asistencia[2]:
                        entrada_dt = datetime.combine(hoy, asistencia[1])
                        salida_dt = datetime.combine(hoy, hora_actual)
                        horas = (salida_dt - entrada_dt).total_seconds() / 3600
                        cur.execute("UPDATE asistencias SET hora_salida = %s, horas_trabajadas = %s WHERE id = %s", (hora_actual, round(horas, 2), asistencia[0]))
                        mensaje_asistencia = "Salida registrada"
                    else:
                        mensaje_asistencia = "Asistencia ya completada para este turno"

                    conn.commit()
                    cur.close()
                    conn.close()
                    
                    return jsonify({
                        "resultado": "permitido", 
                        "nombre": nombre, 
                        "ci": ci, 
                        "asistencia": mensaje_asistencia, 
                        "hora": str(hora_actual)
                    })
            except Exception as ex:
                print("Error interno procesando persona:", str(ex))
                if conn:
                    conn.rollback()
                continue
        
        if cur: cur.close()
        if conn: conn.close()
        return jsonify({"resultado": "denegado"})

    except Exception as e:
        if conn:
            try:
                conn.rollback()
                conn.close()
            except:
                pass
        return jsonify({"resultado": "error", "mensaje": str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
