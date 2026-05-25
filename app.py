from flask import Flask, request, jsonify
from flask_cors import CORS
import base64
import numpy as np
import cv2
import face_recognition
import psycopg2
from datetime import datetime, date

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

@app.route('/reconocer', methods=['POST'])
def reconocer():
    try:
        data = request.get_json()
        if 'foto' not in data:
            return jsonify({"resultado": "error", "mensaje": "Falta la imagen"})
            
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
        cur.execute("SELECT id, nombre, ci, foto1 FROM personas")
        personas = cur.fetchall()

        for p in personas:
            id_persona, nombre, ci, foto_db = p
            if not foto_db: continue
            
            try:
                img_bytes = base64.b64decode(foto_db.split(',')[1])
                np_arr_db = np.frombuffer(img_bytes, np.uint8)
                img_db = cv2.imdecode(np_arr_db, cv2.IMREAD_COLOR)
                rgb_db = cv2.cvtColor(img_db, cv2.COLOR_BGR2RGB)
                encodings_db = face_recognition.face_encodings(rgb_db)
                if not encodings_db: continue
                
                # Comparación
                resultado_comparacion = face_recognition.compare_faces([encodings_db[0]], encoding_actual)
                
                if resultado_comparacion[0]:
                    hoy = date.today()
                    ahora = datetime.now().time()
                    
                    # --- CORRECCIÓN IMPORTANTE ---
                    # Cambié 'Exitoso' por 'Permitido' para evitar conflictos con el CHECK constraint
                    cur.execute("""
                        INSERT INTO accesos (persona_id, nombre_detectado, ci_detectado, fecha_acceso, resultado, similitud)
                        VALUES (%s, %s, %s, %s, 'Permitido', 100)
                    """, (id_persona, nombre, ci, hoy))
                    
                    cur.execute("SELECT id, hora_entrada, hora_salida FROM asistencias WHERE persona_id = %s AND fecha = %s", (id_persona, hoy))
                    asistencia = cur.fetchone()
                    
                    mensaje_asistencia = ""
                    if not asistencia:
                        cur.execute("INSERT INTO asistencias (persona_id, fecha, hora_entrada, estado) VALUES (%s, %s, %s, 'En curso')", (id_persona, hoy, ahora))
                        mensaje_asistencia = "Entrada registrada"
                    elif asistencia[1] and not asistencia[2]:
                        entrada_dt = datetime.combine(hoy, asistencia[1])
                        salida_dt = datetime.now()
                        horas = (salida_dt - entrada_dt).total_seconds() / 3600
                        cur.execute("UPDATE asistencias SET hora_salida = %s, horas_trabajadas = %s, estado = 'Completado' WHERE id = %s", (ahora, round(horas, 2), asistencia[0]))
                        mensaje_asistencia = "Salida registrada"
                    else:
                        mensaje_asistencia = "Asistencia ya completada"

                    conn.commit()
                    cur.close()
                    conn.close()
                    
                    return jsonify({"resultado": "permitido", "nombre": nombre, "asistencia": mensaje_asistencia})
            except Exception:
                continue
        
        cur.close()
        conn.close()
        return jsonify({"resultado": "denegado"})

    except Exception as e:
        return jsonify({"resultado": "error", "mensaje": str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
