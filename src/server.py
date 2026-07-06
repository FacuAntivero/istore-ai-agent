import sys
import os
import time
import random
import base64
import requests
import json
import asyncio
from collections import defaultdict
import mercadopago
import threading
from datetime import datetime, timedelta, timezone  # 🌟 AGREGUÉ timezone PARA QSTASH
from typing import Optional  

sys.path.insert(0, os.path.dirname(__file__))

from tools import verificar_numero_excluido  
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from google.genai import errors, types
from supabase import create_client
from dotenv import load_dotenv
from pydantic import BaseModel
from agent import iniciar_agente

# 🧠 Imports para los recordatorios automáticos
from contextlib import asynccontextmanager
load_dotenv()

# --- PLANIFICADOR DE NOTIFICACIONES INTEGRADO (POST-VENTA) ---
from cron_notificaciones import procesar_postventa

app = FastAPI(title="iStore AI Webhook")

# --- 🚦 SISTEMA DE COLA GLOBAL ANTI-BANEO ---
cola_mensajes = asyncio.Queue()

from redis_client import (
    es_mensaje_procesado, es_pago_procesado, agregar_al_buffer, 
    obtener_y_limpiar_buffer, guardar_cache_comercio, obtener_cache_comercio
)

async def worker_procesador_cola():
    """Trabajador en segundo plano que procesa los mensajes UNO POR UNO."""
    print("👷 Worker de mensajes iniciado y esperando trabajo...")
    while True:
        # Esperamos a que entre un nuevo bloque a la cola
        tarea = await cola_mensajes.get()
        id_remitente_limpio, comercio_id, instance_name, numero_destino, remote_jid_original = tarea
        
        try:
            
            await procesar_bloque_mensajes(id_remitente_limpio, comercio_id, instance_name, numero_destino, remote_jid_original)
            
            # Pequeña pausa extra humana entre clientes (1 a 3 segundos) antes de leer al siguiente
            await asyncio.sleep(random.uniform(1.0, 3.0))
        except Exception as e:
            print(f"❌ [Worker Error] Falló el procesamiento en cola de {id_remitente_limpio}: {e}")
        finally:
            # Le avisamos a la cola que la tarea terminó
            cola_mensajes.task_done()

# Hook de FastAPI para encender el Worker cuando arranca el servidor
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(worker_procesador_cola())
# ----------------------------------------------
    
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://istore-admin.vercel.app",
        "https://www.novva.com.ar"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class PayloadWebhookMensaje(BaseModel):
    tipo: str          # "cita" o "postventa"
    registro_id: int   # El ID en la base de datos

class NgrokHeaderMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["ngrok-skip-browser-warning"] = "true"
        return response
    
class NumeroExcluidoInput(BaseModel):
    comercio_id: int
    telefono: str
    descripcion: Optional[str] = ""
    
app.add_middleware(NgrokHeaderMiddleware)

locks_por_instancia = defaultdict(asyncio.Lock)
_ultimo_envio_timestamp = 0.0

@app.get("/api/numeros-excluidos/{comercio_id}")
async def get_numeros_excluidos(comercio_id: int):
    try:
        # Hacemos la consulta y retornamos explícitamente .data
        res = supabase.table("numeros_excluidos").select("*").eq("comercio_id", comercio_id).execute()
        return res.data
    except Exception as e:
        print(f"Error en GET numeros-excluidos: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/numeros-excluidos")
async def add_numero_excluido(payload: NumeroExcluidoInput):
    try:
        # Armamos el diccionario manualmente para evitar conflictos de versiones en Pydantic
        data_insert = {
            "comercio_id": payload.comercio_id,
            "telefono": payload.telefono,
            "descripcion": payload.descripcion
        }
        res = supabase.table("numeros_excluidos").insert(data_insert).execute()
        return res.data
    except Exception as e:
        print(f"Error en POST numeros-excluidos: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/numeros-excluidos/{id_numero}")
async def delete_numero_excluido(id_numero: int):
    try:
        res = supabase.table("numeros_excluidos").delete().eq("id", id_numero).execute()
        return {"status": "success"}
    except Exception as e:
        print(f"Error en DELETE numeros-excluidos: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/test-recordatorio/{turno_id}")
async def test_recordatorio(turno_id: int):
    print(f"🧪 Iniciando prueba de recordatorio para ID: {turno_id}")
    try:
        # Forzamos la ejecución de la función de envío directamente
        procesar_envio_inmediato("cita", turno_id)
        return {"status": "disparo_ejecutado"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    
def programar_evento_futuro(tipo_evento: str, registro_id: int, fecha_disparo: datetime):
    """
    Se comunica con Upstash QStash para agendar un webhook en el futuro.
    Leyendo las credenciales de forma segura desde las variables de entorno.
    """
    # 🌟 Leemos las variables del archivo .env de forma segura
    QSTASH_TOKEN = os.getenv("QSTASH_TOKEN")
    URL_RAILWAY = os.getenv("URL_RAILWAY")
    
    # Validación de seguridad por si te olvidás de configurarlas
    if not QSTASH_TOKEN or not URL_RAILWAY:
        print("❌ [QStash] Error crítico: QSTASH_TOKEN o URL_RAILWAY no están configurados en el entorno.")
        return
    
    url_qstash = f"https://qstash.upstash.io/v2/publish/{URL_RAILWAY}/api/webhooks/disparar-mensaje-programado"
    
    # Calculamos cuántos segundos faltan desde AHORA hasta la fecha de disparo
    ahora_utc = datetime.now(timezone.utc)
    
    # Si la fecha de disparo viene sin zona horaria, asumimos UTC para evitar errores
    if fecha_disparo.tzinfo is None:
        fecha_disparo = fecha_disparo.replace(tzinfo=timezone.utc)
        
    diferencia = (fecha_disparo - ahora_utc).total_seconds()
    
    # Si la fecha ya pasó o es ahora mismo, que dispare en 5 segundos
    delay_segundos = max(int(diferencia), 5)

    headers = {
        "Authorization": f"Bearer {QSTASH_TOKEN}",
        "Content-Type": "application/json",
        "Upstash-Delay": f"{delay_segundos}s" 
    }
    
    payload = {
        "tipo": tipo_evento,
        "registro_id": registro_id
    }
    
    try:
        res = requests.post(url_qstash, headers=headers, json=payload)
        if res.status_code == 201:
            print(f"✅ [QStash] Evento programado: {tipo_evento} ID {registro_id} en {delay_segundos} segundos.")
        else:
            print(f"❌ [QStash] Error al programar: {res.text}")
    except Exception as e:
        print(f"❌ [QStash] Error de red: {e}")

# 3. EL RECEPTOR DEL WEBHOOK (El que ejecuta el disparo final)
@app.post("/api/webhooks/disparar-mensaje-programado")
async def webhook_disparar_mensaje_programado(data: PayloadWebhookMensaje, background_tasks: BackgroundTasks):
    print(f"🚀 [Webhook] Recibido desde QStash para {data.tipo} ID: {data.registro_id}")
    
    # Lo pasamos a segundo plano para que QStash reciba un "200 OK" al instante
    background_tasks.add_task(procesar_envio_inmediato, data.tipo, data.registro_id)
    return {"status": "accepted"}

# 4. EL EJECUTOR (Lee la DB por ID y despacha)
def procesar_envio_inmediato(tipo: str, registro_id: int):
    try:
        if tipo == "cita":
            # 1. ATÓMICO: Marcamos como 'procesando' solo si estaba 'pendiente'
            lock = supabase.table("turnos_clientes")\
                .update({"estado": "procesando"})\
                .eq("id", registro_id)\
                .eq("estado", "pendiente")\
                .eq("recordatorio_enviado", False)\
                .execute()
            
            if not lock.data:
                print(f"⚠️ [Ejecutor] Cita ID {registro_id} ignorada: ya fue procesada por otro hilo.")
                return
            
            # 2. Obtenemos el registro bloqueado
            res = supabase.table("turnos_clientes")\
                .select("*, comercio:comercio_id(evolution_instance)")\
                .eq("id", registro_id)\
                .execute()
            
            turno = res.data[0]
            instance_name = turno.get("comercio", {}).get("evolution_instance")
            
            # 3. Procesamiento
            fecha_obj = datetime.fromisoformat(turno["fecha_turno"].replace("Z", ""))
            hora_formateada = fecha_obj.strftime("%H:%M")
            mensaje = f"¡Hola {turno.get('cliente_nombre', 'Cliente')}! 👋\n\nTe recordamos tu cita a las *{hora_formateada} hs*.\n\n¡Te esperamos!"
            
            enviar_mensaje_whatsapp(turno["telefono"], mensaje, instance_name)
            
            # 4. Finalización atómica
            supabase.table("turnos_clientes")\
                .update({"recordatorio_enviado": True, "estado": "completado"})\
                .eq("id", registro_id)\
                .execute()
            print(f"✅ Recordatorio enviado: {turno.get('cliente_nombre')}")

        elif tipo == "postventa":
            # 1. ATÓMICO: Lock para postventa
            lock = supabase.table("cola_mensajes_postventa")\
                .update({"estado": "procesando"})\
                .eq("id", registro_id)\
                .eq("estado", "pendiente")\
                .execute()
            
            if not lock.data:
                print(f"⚠️ [Ejecutor] Postventa ID {registro_id} ignorada: ya fue procesada.")
                return
            
            res = supabase.table("cola_mensajes_postventa").select("*, comercio:comercio_id(evolution_instance)").eq("id", registro_id).execute()
            msg = res.data[0]
            instance_name = msg.get("comercio", {}).get("evolution_instance")
            
            # 2. Lógica de envío
            texto_ws = msg.get("mensaje_texto") or f"¡Hola {msg.get('cliente_nombre', '')}! Gracias por tu compra de {msg.get('equipos_detalle', 'equipo')}. ¡Estamos a tu disposición!"
                
            enviar_mensaje_whatsapp(msg["telefono"], texto_ws, instance_name)
            
            # 3. Finalización
            supabase.table("cola_mensajes_postventa").update({"estado": "enviado"}).eq("id", registro_id).execute()
            print(f"✅ Post-Venta enviada: {msg.get('cliente_nombre')}")

    except Exception as e:
        print(f"❌ [Ejecutor] Error crítico en ID {registro_id}: {e}")
        
class EditarPostVentaInput(BaseModel):
    mensaje_texto: str
    fecha_envio: str

class PlantillaPostVentaInput(BaseModel):
    comercio_id: int
    nombre: str
    dias_espera: int
    texto: str

@app.get("/api/plantillas/{comercio_id}")
async def obtener_plantillas(comercio_id: int):
    """Trae las plantillas de un comercio. Si no tiene ninguna, le siembra las 3 por defecto."""
    try:
        res = supabase.table("plantillas_postventa") \
            .select("*") \
            .eq("comercio_id", comercio_id) \
            .execute()
        
        # 🌟 MAGIA: Si la lista está vacía, creamos los 3 ejemplos por defecto
        if not res.data:
            plantillas_defecto = [
                {
                    "comercio_id": comercio_id,
                    "nombre": "Control de Satisfacción",
                    "dias_espera": 3,
                    "texto": "¡Hola {nombre}! 😊 Te escribimos del local. Queríamos saber cómo te estás sintiendo con tu {equipo}. ¿Salió todo bien? Cualquier duda que necesites, ¡estamos acá!"
                },
                {
                    "comercio_id": comercio_id,
                    "nombre": "Venta de Accesorios / Descuentos",
                    "dias_espera": 7,
                    "texto": "¡Hola {nombre}! 🛒 Esperamos que disfrutes a pleno tu {equipo}. Te dejamos un mimo: por haber comprado tu equipo con nosotros, tenés un *20% OFF* en accesorios esta semana."
                },
                {
                    "comercio_id": comercio_id,
                    "nombre": "Fidelización y Reseña de Google",
                    "dias_espera": 15,
                    "texto": "¡Hola {nombre}! ⭐ Pasaron unos días desde que te llevaste tu {equipo}. Nos ayudarías un montón dejando una breve reseña en Google. ¡Muchas gracias por elegirnos!"
                }
            ]
            # Las insertamos en la base de datos para que ya queden guardadas
            res_insert = supabase.table("plantillas_postventa").insert(plantillas_defecto).execute()
            return sorted(res_insert.data, key=lambda x: x.get('created_at', ''))

        # Si ya tenía plantillas (propias o las por defecto modificadas), las devolvemos
        return sorted(res.data, key=lambda x: x.get('created_at', ''))
        
    except Exception as e:
        print(f"❌ ERROR EN GET PLANTILLAS: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    
@app.put("/api/plantillas/{plantilla_id}")
async def actualizar_plantilla(plantilla_id: int, datos: PlantillaPostVentaInput):
    """Actualiza una plantilla existente en la base de datos."""
    try:
        # Preparamos los datos que vamos a actualizar
        payload = {
            "nombre": datos.nombre,
            "dias_espera": datos.dias_espera,
            "texto": datos.texto
        }
        
        # Ejecutamos el update en Supabase filtrando por el ID de la plantilla
        res = supabase.table("plantillas_postventa").update(payload).eq("id", plantilla_id).execute()
        
        return {"status": "success", "data": res.data}
    except Exception as e:
        print(f"❌ ERROR EN PUT PLANTILLAS: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/api/plantillas")
async def crear_plantilla(datos: PlantillaPostVentaInput):
    """Guarda una nueva plantilla en la base de datos."""
    try:
        payload = {
            "comercio_id": datos.comercio_id,
            "nombre": datos.nombre,
            "dias_espera": datos.dias_espera,
            "texto": datos.texto
        }
        res = supabase.table("plantillas_postventa").insert(payload).execute()
        return {"status": "success", "data": res.data}
    except Exception as e:
        print(f"❌ ERROR EN POST PLANTILLAS: {str(e)}")  # Revisar en logs de Railway
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/plantillas/{plantilla_id}")
async def eliminar_plantilla(plantilla_id: int):
    """Elimina una plantilla específica."""
    try:
        res = supabase.table("plantillas_postventa").delete().eq("id", plantilla_id).execute()
        return {"status": "success", "message": "Plantilla eliminada"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
# --- INICIALIZACIÓN MERCADOPAGO ---
mp_access_token = os.getenv("MERCADOPAGO_ACCESS_TOKEN")
if mp_access_token:
    mp = mercadopago.SDK(mp_access_token)
else:
    print("⚠️ ADVERTENCIA: No se encontró MERCADOPAGO_ACCESS_TOKEN en el .env")

sesiones_chat = {}

# --- DICCIONARIOS PARA EL DEBOUNCER Y ANTI-TROLL ---
timers_debounce = {}
rate_limiter = {} # Guarda: { "numero_remitente": [timestamp1, timestamp2...] }
ultimo_aviso_audio = {} # Anti-Spam: Guarda { "id_remitente": timestamp_ultimo_aviso }

TIEMPO_ESPERA_MENSAJE = float(os.getenv("DEBOUNCE_SECONDS", 60))

EVOLUTION_API_URL = "https://evolution-api-production-4b88.up.railway.app"
API_KEY = "74BD7CFB-C38A-4143-833A-FCEA92FBBA21"

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))


async def obtener_comercio(instancia, forzar_actualizacion=False):
    # 1. Si no forzamos, revisamos Redis primero
    if not forzar_actualizacion:
        comercio_cache = await obtener_cache_comercio(instancia)
        if comercio_cache:
            return comercio_cache
    
    try:
        print(f"🔄 [BD] Buscando comercio en Supabase para instancia: {instancia}")
        # Hacemos la consulta asíncrona usando to_thread si la librería de Supabase es síncrona
        res = await asyncio.to_thread(
            lambda: supabase.table("comercios").select("*").ilike("evolution_instance", instancia).execute()
        )
        
        if res.data:
            comercio_actualizado = res.data[0]
            # 2. Guardamos en Redis para la próxima vez
            await guardar_cache_comercio(instancia, comercio_actualizado)
            return comercio_actualizado
            
    except Exception as e:
        print(f"[Supabase] ❌ Error crítico buscando comercio: {e}")
    
    return None

MI_NUMERO = os.getenv("MI_NUMERO", "5492494600615@s.whatsapp.net")

def descargar_audio_evolution(instance_name: str, mensaje_data: dict) -> bytes:
    """
    Pide a Evolution API que descifre el mensaje multimedia y devuelve los bytes reales del audio.
    """
    url = f"https://evolution-api-production-4b88.up.railway.app/chat/getBase64FromMediaMessage/{instance_name}"
    headers = {
        "apikey": API_KEY,  # Mantenemos tu variable sin comillas
        "Content-Type": "application/json"
    }
    payload = {"message": mensaje_data}
    
    try:
        respuesta = requests.post(url, json=payload, headers=headers)
        
        # 🚨 CAMBIO CLAVE: Aceptamos tanto 200 (OK) como 201 (Created)
        if respuesta.status_code in [200, 201]:
            datos = respuesta.json()
            
            # Buscamos el base64 sin importar dónde lo haya puesto Evolution API
            base64_string = datos.get("base64")
            
            # Algunas versiones de Evolution meten el base64 dentro de un objeto 'media'
            if not base64_string and "media" in datos:
                base64_string = datos["media"].get("base64")

            if base64_string:
                # Limpiamos el prefijo si viene con formato Data URI o con coma
                if "base64," in base64_string:
                    base64_string = base64_string.split("base64,")[1]
                elif "," in base64_string:
                     base64_string = base64_string.split(",")[1]
                
                return base64.b64decode(base64_string)
            else:
                print(f"❌ Evolution API no devolvió el campo 'base64'. Respuesta: {datos}")
        else:
            print(f"❌ Error de Evolution API HTTP {respuesta.status_code}: {respuesta.text}")
    except Exception as e:
        print(f"❌ Error en proceso de descarga/decodificación de audio: {e}")
        
    return None

_ultimo_envio_instancia = defaultdict(float)
async def enviar_mensaje_whatsapp(numero_destino, texto, instance_name, id_mensaje=None, remote_jid=None):
    # 1. CALCULAR DELAY DE ESCRITURA
    delay_milisegundos = min(max(len(texto) * 30 + random.randint(800, 1500), 1200), 3500)
    
    url = f"{EVOLUTION_API_URL}/message/sendText/{instance_name}?checkNumber=false"
    headers = {"apikey": API_KEY, "Content-Type": "application/json"}
    
    payload = {
        "number": numero_destino,
        "text": texto,
        "checkNumber": False,       
        "verifyNumber": False,      
        "options": {
            "delay": delay_milisegundos,
            "checkNumber": False    
        }
    }
    
    if id_mensaje and remote_jid:
        payload["options"]["quoted"] = {
            "key": {"id": id_mensaje, "remoteJid": remote_jid, "fromMe": False}
        }
        
    # 2. PROTEGER EL ENVÍO (Ahora sí, 100% por instancia)
    async with locks_por_instancia[instance_name]:
        ahora = time.time()
        tiempo_transcurrido = ahora - _ultimo_envio_instancia[instance_name]
        
        if tiempo_transcurrido < 1.5:
            tiempo_espera = 1.5 - tiempo_transcurrido
            print(f"⏳ [Anti-Ban] La instancia {instance_name} espera {tiempo_espera:.2f}s...")
            await asyncio.sleep(tiempo_espera) 
        
        # Actualizamos el tiempo SOLO para esta instancia
        _ultimo_envio_instancia[instance_name] = time.time()

        try:
            respuesta = await asyncio.to_thread(requests.post, url, headers=headers, json=payload)
            
            if respuesta.status_code in [200, 201]:
                print(f"✅ Mensaje a {numero_destino} (Delay: {delay_milisegundos}ms) desde {instance_name}")
            else:
                print(f"❌ Error WhatsApp: {respuesta.text}")
        except Exception as e:
            print(f"❌ Error crítico de red: {e}")

# --- FUNCIÓN DE ALERTA AL DUEÑO (PREVIA) ---
async def alertar_consumo_dueno(telefono_dueno, porcentaje, mensajes_restantes, instance_name):
    if not telefono_dueno: 
        return
        
    tel_dueno_limpio = telefono_dueno.replace("+", "").replace(" ", "").strip()
    
    if not tel_dueno_limpio.endswith("@s.whatsapp.net"): 
        tel_dueno_jid = f"{tel_dueno_limpio}@s.whatsapp.net"
    else:
        tel_dueno_jid = tel_dueno_limpio
    
    emoji = "⚠️" if porcentaje <= 80 else "🚨"
    
    mensaje = (
        f"{emoji} *Aviso de Consumo del Bot*\n\n"
        f"Tu bot ha consumido el *{porcentaje}%* de los mensajes de tu plan actual.\n"
        f"Te quedan: *{mensajes_restantes} mensajes*.\n\n"
        f"Por favor, renová tu plan pronto desde el panel para evitar que el bot se detenga."
    )
    
    # 🌟 Agregamos await
    await enviar_mensaje_whatsapp(tel_dueno_jid, mensaje, instance_name)
    print(f"📢 [ALERTA] Aviso de {porcentaje}% enviado al dueño ({tel_dueno_jid})")

# --- NUEVA FUNCIÓN DE ALERTA POR SUSPENSIÓN DE SERVICIO (SALDO 0) ---
async def alertar_suspension_dueno(telefono_dueno, instance_name):
    if not telefono_dueno: return
    
    tel_dueno_limpio = telefono_dueno.replace("+", "").replace(" ", "").strip()
    if not tel_dueno_limpio.endswith("@s.whatsapp.net"):
        tel_dueno_jid = f"{tel_dueno_limpio}@s.whatsapp.net"
    else:
        tel_dueno_jid = tel_dueno_limpio

    mensaje = (
        "🛑 *BOT PAUSADO - ACCIÓN REQUERIDA*\n\n"
        f"Tu asistente virtual de la instancia *{instance_name}* se ha quedado sin mensajes disponibles y ha dejado de responder a tus clientes.\n\n"
        "Para reactivar el servicio de inmediato y no perder ventas, por favor ingresá a tu panel y realizá la recarga o renovación de tu plan.\n\n"
        "👉 _Tus clientes seguirán escribiendo, pero el bot no intervendrá hasta que restaures el saldo._"
    )
    
    # 🌟 Agregamos await
    await enviar_mensaje_whatsapp(tel_dueno_jid, mensaje, instance_name)
    print(f"🛑 [SaaS - SUSPENSIÓN] Se notificó al dueño ({tel_dueno_jid}) que el bot se quedó en 0.")
    
async def procesar_bloque_mensajes(id_remitente_limpio, comercio_id, instance_name, numero_destino, remote_jid_original):
    # 1. Filtro de seguridad inicial
    if verificar_numero_excluido(id_remitente_limpio, comercio_id):
        print(f"🤫 [Filtro Blacklist] Mensaje de {id_remitente_limpio} ignorado.")
        await obtener_y_limpiar_buffer(id_remitente_limpio)
        return

    # 2. Extraemos y limpiamos la lista de mensajes DIRECTO de Redis
    mensajes = await obtener_y_limpiar_buffer(id_remitente_limpio)
    if not mensajes:
        return

    # Limpieza de memoria: Quitamos el timer apenas empieza el proceso
    timers_debounce.pop(id_remitente_limpio, None)

    try:
        # 3. Obtención de datos FUERZANDO actualización
        comercio_db = await obtener_comercio(instance_name, forzar_actualizacion=True)
        if comercio_db:
            estado = str(comercio_db.get("estado_suscripcion", "trial")).lower().strip()
            plan_actual_db = str(comercio_db.get("plan_actual", "basico")).lower().strip()
            plan_actual = plan_actual_db.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
            
            tel_dueno = comercio_db.get("telefono_dueno")
            creditos_demo = comercio_db.get("creditos_demo", 0)
            saldo_mensajes = comercio_db.get("mensajes_disponibles", 0)

            es_trial = (estado == "trial")
            saldo_actual = creditos_demo if es_trial else saldo_mensajes

            if saldo_actual <= 0:
                print(f"🚫 [SaaS] Comercio {comercio_id} sin créditos. Bloqueando respuesta silenciosamente.")
                try:
                    await alertar_suspension_dueno(tel_dueno, instance_name)
                except Exception as e:
                    print(f"⚠️ No se pudo enviar la alerta de suspensión al dueño: {e}")
                return

            # Realizar el descuento
            if es_trial:
                nuevo_saldo = creditos_demo - 1
                supabase.table("comercios").update({"creditos_demo": nuevo_saldo}).eq("id", comercio_id).execute()
                if nuevo_saldo in [10, 2]: 
                    await alertar_consumo_dueno(tel_dueno, 90 if nuevo_saldo == 10 else 95, nuevo_saldo, instance_name)
            else:
                nuevo_saldo = saldo_mensajes - 1
                supabase.table("comercios").update({"mensajes_disponibles": nuevo_saldo}).eq("id", comercio_id).execute()
                topes = {"basico": 1000, "pro": 3500, "premium": 10000}
                limite = topes.get(plan_actual, 1000)
                if nuevo_saldo == int(limite * 0.20) or nuevo_saldo == int(limite * 0.05):
                    await alertar_consumo_dueno(tel_dueno, 80 if nuevo_saldo > (limite * 0.1) else 95, nuevo_saldo, instance_name)
        else:
            return
    except Exception as e:
        print(f"❌ [SaaS] Error crítico en facturación: {e}")

    # 4. Procesamiento del mensaje (Gemini)
    elementos_prompt = []
    textos_del_bloque = []

    for m in mensajes:
        textos_del_bloque.append(m["texto"])
        if "audio_b64" in m and m["audio_b64"]:
            audio_bytes_reales = base64.b64decode(m["audio_b64"])
            parte_audio = types.Part.from_bytes(data=audio_bytes_reales, mime_type="audio/ogg")
            elementos_prompt.append(parte_audio)

    texto_completo = ". ".join(textos_del_bloque)
    elementos_prompt.append(texto_completo)
    ultimo_id_mensaje = mensajes[-1]["id_mensaje"]
    
    # 🧠 INTEGRACIÓN DE MEMORIA HISTÓRICA A LARGO PLAZO
    session_key = f"{comercio_id}_{id_remitente_limpio}"
    if session_key not in sesiones_chat:
        try:
            # Consultamos los últimos 20 mensajes de este cliente en Supabase (en un hilo para no bloquear descargas)
            res_historial = await asyncio.to_thread(
                lambda: supabase.table("historial_chat_ia")
                .select("rol", "contenido")
                .eq("telefono_cliente", id_remitente_limpio)
                .order("created_at", descending=False)
                .limit(20)
                .execute()
            )
            historial_previo = res_historial.data if res_historial.data else []
        except Exception as e:
            print(f"⚠️ [Supabase Error] No se pudo recuperar el historial para {id_remitente_limpio}: {e}")
            historial_previo = []
            
        # Inicializamos el agente pasándole la lista de diccionarios cruda de la BD
        sesiones_chat[session_key] = iniciar_agente(comercio_id, numero_destino, historial_base=historial_previo)
        
    chat_actual = sesiones_chat[session_key]

    # 5. Envío a Gemini con Captura de Errores de Cuota de Emergencia
    try:
        respuesta = chat_actual.send_message(elementos_prompt)
        texto_respuesta = respuesta.text or "Aguardame un segundo que reviso el sistema..."
            
        await enviar_mensaje_whatsapp(numero_destino, texto_respuesta, instance_name, ultimo_id_mensaje, remote_jid_original)
        
        # 💾 GUARDADO ATÓMICO EN LA MEMORIA DE SUPABASE
        try:
            await asyncio.to_thread(
                lambda: supabase.table("historial_chat_ia").insert([
                    {"telefono_cliente": id_remitente_limpio, "rol": "user", "contenido": texto_completo},
                    {"telefono_cliente": id_remitente_limpio, "rol": "model", "contenido": texto_respuesta}
                ]).execute()
            )
        except Exception as db_err:
            print(f"❌ [Supabase Error] No se pudo guardar el nuevo bloque en el historial: {db_err}")
            
    except Exception as e:
        error_str = str(e)
        print(f"[Error Procesamiento] {error_str}")
        
        if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
            print(f"⚠️ [Cuota Excedida] Enviando mensaje de contingencia a {numero_destino} por saturación de Gemini.")
            msg_ocupado = "En este momento todos nuestros asesores están ocupados atendiendo a otros clientes. Por favor, aguardanos unos minutitos y volvé a escribirnos. ¡Gracias!"
            await enviar_mensaje_whatsapp(numero_destino, msg_ocupado, instance_name, ultimo_id_mensaje, remote_jid_original)
            
# --- DETECCIÓN DE TIPOS DE MENSAJE MULTIMEDIA ---
def extraer_texto_y_tipo(msg_object):
    if not isinstance(msg_object, dict): return None, "text"
    
    if "stickerMessage" in msg_object: return None, "sticker"
    if "imageMessage" in msg_object: return None, "image"
    if "audioMessage" in msg_object: return None, "audio"

    if "conversation" in msg_object and isinstance(msg_object["conversation"], str):
        return msg_object["conversation"], "text"
    if "extendedTextMessage" in msg_object and "text" in msg_object["extendedTextMessage"]:
        return msg_object["extendedTextMessage"]["text"], "text"
    
    for key, value in msg_object.items():
        if isinstance(value, dict):
            resultado, tipo = extraer_texto_y_tipo(value)
            if resultado or tipo != "text": return resultado, tipo
            
    return None, "text"

# --- RATE LIMITER (ANTI-TROLL) ---
def es_troll(id_remitente):
    ahora = time.time()
    if id_remitente not in rate_limiter:
        rate_limiter[id_remitente] = []
    
    rate_limiter[id_remitente] = [ts for ts in rate_limiter[id_remitente] if ahora - ts < 15]
    rate_limiter[id_remitente].append(ahora)
    
    if len(rate_limiter[id_remitente]) > 5:
        return True
    return False

# --- ENDPOINTS WEBHOOK WHATSAPP ---
@app.post("/webhook")
async def recibir_mensaje(request: Request, background_tasks: BackgroundTasks):
    datos = await request.json()
    
    evento_actual = datos.get("event", "SIN_EVENTO")
    instance_name = datos.get("instance")
    
    if not instance_name:
        return {"status": "ignorado"}

    comercio = await obtener_comercio(instance_name)
    if not comercio:
        return {"status": "error"}
    comercio_id = comercio["id"]

    if evento_actual not in ["messages.upsert", "messages.update"]:
        return {"status": "ok"}

    try:
        mensaje_data = datos.get("data", {})
        if isinstance(mensaje_data, dict) and "message" in mensaje_data and isinstance(mensaje_data["message"], dict) and "message" in mensaje_data["message"]:
             msg_content = mensaje_data["message"]["message"]
        elif "message" in mensaje_data:
             msg_content = mensaje_data["message"]
        else:
             msg_content = mensaje_data

        key = mensaje_data.get("key", {})
        remote_jid = key.get("remoteJid", "")
        
        if remote_jid.endswith("@g.us") or remote_jid == "status@broadcast":
            return {"status": "ignorado"}

        push_name = mensaje_data.get("pushName", "Usuario")
        sender = mensaje_data.get("sender", "")
        participant = key.get("participant", "")

        numero_destino = remote_jid 
        if remote_jid.endswith("@lid"):
            if sender and sender.endswith("@s.whatsapp.net"):
                numero_destino = sender
            elif participant and participant.endswith("@s.whatsapp.net"):
                numero_destino = participant

        id_remitente_limpio = numero_destino.split("@")[0]

        # --- 🧠 MANEJO DE CONTEXTO ---
        if key.get("fromMe", False):
            texto_saliente, tipo = extraer_texto_y_tipo(msg_content)
            
            if texto_saliente and tipo == "text":
                session_key = f"{comercio_id}_{id_remitente_limpio}"
                
                # 1. Guardamos en Supabase para la memoria eterna
                try:
                    await asyncio.to_thread(
                        lambda: supabase.table("historial_chat_ia").insert({
                            "telefono_cliente": id_remitente_limpio,
                            "rol": "model",
                            "contenido": texto_saliente
                        }).execute()
                    )
                except Exception as db_err:
                    print(f"❌ [Supabase Context Error] No se pudo guardar el mensaje saliente humano: {db_err}")

                # 2. ⚡ TRUCO: Borramos de la RAM para forzar la reconstrucción desde DB en el próximo mensaje
                sesiones_chat.pop(session_key, None)
                print(f"🧠 [Contexto Híbrido] Mensaje humano guardado en DB y sesión RAM reseteada para {id_remitente_limpio}")
            return {"status": "contexto_guardado"}

        id_mensaje = key.get("id", "")

        if await es_mensaje_procesado(id_mensaje):
            return {"status": "ignorado"}

        if es_troll(id_remitente_limpio):
            print(f"🚷 [Anti-Troll] Bloqueando ráfaga de mensajes de {id_remitente_limpio}")
            return {"status": "bloqueado_rate_limit"}

        texto_usuario, tipo_mensaje = extraer_texto_y_tipo(msg_content)

        if tipo_mensaje in ["sticker", "image"]:
            print("🖼️ Mensaje visual ignorado.")
            return {"status": "multimedia_ignorado"}
            
        elif tipo_mensaje == "audio":
            plan_actual = str(comercio.get("plan_actual", "trial")).lower()
            if plan_actual == "basico":
                ahora = time.time()
                ultimo_ts = ultimo_aviso_audio.get(id_remitente_limpio, 0)
                
                if ahora - ultimo_ts > 300: 
                    ultimo_aviso_audio[id_remitente_limpio] = ahora
                    msg_escribime = "Hola, no puedo escuchar audios, por favor escribime tu consulta así te puedo ayudar"
                    await enviar_mensaje_whatsapp(numero_destino, msg_escribime, instance_name, id_mensaje, key.get("remoteJid"))
                    return {"status": "audio_denegado_plan_basico"}
                
                print(f"🔇 [Anti-Spam Audio] Audio de {id_remitente_limpio} ignorado silenciosamente.")
                return {"status": "audio_ignorado_por_spam"}
            
            permite_audio = comercio.get("permitir_audios", False)
            if not permite_audio:
                await enviar_mensaje_whatsapp(numero_destino, "Disculpa, por el momento solo puedo leer textos, por favor escribime tu consulta", instance_name, id_mensaje, key.get("remoteJid"))
                return {"status": "audio_rechazado"}
            
            audio_bytes = descargar_audio_evolution(instance_name, mensaje_data)
            if not audio_bytes:
                await enviar_mensaje_whatsapp(numero_destino, "Tuve un problema al escuchar tu audío, me la podés escribir por texto por favor", instance_name, id_mensaje, key.get("remoteJid"))
                return {"status": "error_descarga_audio"}

            # 🌟 REDIS: Codificamos a texto seguro para JSON
            audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
            
            await agregar_al_buffer(id_remitente_limpio, {
                "texto": "[El usuario envió una nota de voz/audio]",
                "audio_b64": audio_b64,
                "id_mensaje": id_mensaje
            })

            if id_remitente_limpio in timers_debounce and not timers_debounce[id_remitente_limpio].done():
                timers_debounce[id_remitente_limpio].cancel()

            async def timer_task_audio():
                await asyncio.sleep(TIEMPO_ESPERA_MENSAJE)
                await cola_mensajes.put((id_remitente_limpio, comercio_id, instance_name, numero_destino, remote_jid))

            timers_debounce[id_remitente_limpio] = asyncio.create_task(timer_task_audio())
            return {"status": "audio_en_espera"}

        if not texto_usuario: return {"status": "ignorado"}

        # 🌟 REDIS: Guardamos el texto directo al buffer
        await agregar_al_buffer(id_remitente_limpio, {
            "texto": texto_usuario,
            "id_mensaje": id_mensaje
        })

        if id_remitente_limpio in timers_debounce and not timers_debounce[id_remitente_limpio].done():
            timers_debounce[id_remitente_limpio].cancel()

        async def timer_task():
            await asyncio.sleep(TIEMPO_ESPERA_MENSAJE)
            await cola_mensajes.put((id_remitente_limpio, comercio_id, instance_name, numero_destino, remote_jid))

        timers_debounce[id_remitente_limpio] = asyncio.create_task(timer_task())
        return {"status": "en_espera"}

    except Exception as e:
        print(f"Error procesando mensaje: {e}")
        return {"status": "error"}

# --- ENDPOINTS MERCADOPAGO ---
@app.post("/api/checkout/crear-preferencia")
async def crear_preferencia(request: Request):
    try:
        body = await request.json()
        comercio_id = body.get("comercio_id")
        tipo_plan = body.get("tipo_plan", "pro").lower()
        
        if not comercio_id:
            raise HTTPException(status_code=400, detail="Falta el comercio_id")

        if not mp_access_token:
            raise HTTPException(status_code=500, detail="SDK de MercadoPago no configurado en el servidor")

        planes = {
            "basico": {"precio": 15000.00, "titulo": "Novva - Plan Básico"},
            "pro": {"precio": 35000.00, "titulo": "Novva - Plan Pro"},
            "premium": {"precio": 85000.00, "titulo": "Novva - Plan Premium"}
        }
        
        plan_seleccionado = planes.get(tipo_plan, planes["pro"])

        preference_data = {
            "items": [
                {
                    "title": plan_seleccionado["titulo"],
                    "quantity": 1,
                    "unit_price": plan_seleccionado["precio"],
                    "currency_id": "ARS"
                }
            ],
            "external_reference": f"{comercio_id}|{tipo_plan}",
            "back_urls": {
                "success": "https://istore-ai-agent-production.up.railway.app/?pago=exitoso",
                "failure": "https://istore-ai-agent-production.up.railway.app/?pago=fallido",
                "pending": "https://istore-ai-agent-production.up.railway.app/?pago=pendiente"
            },
            "auto_return": "approved"
        }

        preference_response = mp.preference().create(preference_data)
        print(f"🔍 PREFERENCIA CREADA: {preference_response.get('response', {}).get('id')}")

        if preference_response.get("status") not in [200, 201]:
            error_detalle = preference_response.get("response", "Error desconocido")
            raise Exception(f"MercadoPago rechazó la petición: {error_detalle}")
        
        preference = preference_response["response"]
        return {"init_point": preference["init_point"]}

    except Exception as e:
        print(f"❌ Error creando preferencia de MP: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/webhook/mercadopago")
async def webhook_mercadopago(request: Request):
    try:
        params = dict(request.query_params)
        
        if params.get("type") == "payment":
            payment_id = str(params.get("data.id"))
            
            # 1. BARRERA REDIS: Evita ráfagas y sobrevive a reinicios del servidor
            if await es_pago_procesado(payment_id):
                print(f"🛡️ [MercadoPago] Webhook duplicado atajado en Redis: {payment_id}")
                return {"status": "ignored", "message": "Pago ya procesado recientemente"}
            
            payment_info = mp.payment().get(payment_id)
            payment_data = payment_info.get("response", {})
            
            status = payment_data.get("status")
            external_reference = payment_data.get("external_reference")

            if status == "approved" and external_reference:
                partes = external_reference.split("|")
                comercio_id = partes[0]
                tipo_plan = partes[1] if len(partes) > 1 else "pro"

                # 2. BARRERA DE BASE DE DATOS: Verificamos si este pago ya se aplicó
                comercio_res = supabase.table("comercios").select("ultimo_pago_id").eq("id", int(comercio_id)).execute()
                
                if comercio_res.data:
                    ultimo_pago_guardado = str(comercio_res.data[0].get("ultimo_pago_id"))
                    if ultimo_pago_guardado == payment_id:
                        print(f"⚠️ [MercadoPago] Pago {payment_id} ya fue acreditado previamente en la DB.")
                        return {"status": "success", "message": "Acreditación ya existente"}

                print(f"💰 [MercadoPago] ¡Pago NUEVO APROBADO! ID Pago: {payment_id}")
                
                mensajes_por_plan = {"basico": 1000, "pro": 3500, "premium": 10000}
                mensajes_a_cargar = mensajes_por_plan.get(tipo_plan, 3500)
                
                fecha_vencimiento = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
                
                # 3. ACTUALIZACIÓN ATÓMICA CON SELLO DE PAGO
                # Actualizamos el plan y sellamos el comercio con este payment_id
                supabase.table("comercios").update({
                    "estado_suscripcion": "activa",
                    "plan_actual": tipo_plan,
                    "mensajes_disponibles": mensajes_a_cargar,
                    "plan_vence_el": fecha_vencimiento,
                    "ultimo_pago_id": payment_id  # 🔒 Este es tu nuevo candado
                }).eq("id", int(comercio_id)).execute()
                
                # Eliminada la vieja línea de pagos_procesados_cache.add(payment_id)
                
                print(f"✅ Comercio {comercio_id} actualizado a {tipo_plan.upper()} con {mensajes_a_cargar} mensajes.")
                return {"status": "success", "message": "Comercio activado y saldo recargado"}
                
        return {"status": "received"}
        
    except Exception as e:
        print(f"❌ Error procesando Webhook de MP: {e}")
        return {"status": "error", "detail": str(e)}
    
async def agendar_postventa(comercio_id: int, cliente_nombre: str, telefono: str, celulares_ids: list, estrategia_o_plantilla):
    print(f"\n--- 🚀 INICIANDO POST-VENTA PARA: {cliente_nombre} ---")
    try:
        if not telefono:
            print("⚠️ Omitido: No hay teléfono.")
            return False

        # 1. Validar Plan
        comercio_res = supabase.table("comercios").select("plan_actual").eq("id", comercio_id).execute()
        if not comercio_res.data:
            print("❌ Falla: Comercio no encontrado.")
            return False
            
        plan_bruto = comercio_res.data[0].get("plan_actual")
        plan_comercio = plan_bruto.lower() if plan_bruto else "basico"
        
        if plan_comercio not in ["pro", "vip"]:
            print(f"ℹ️ Post-venta omitido: Plan {plan_comercio.upper()}.")
            return False  # ❌ Retorna False si no le da el plan

        # 2. Obtener Equipos
        detalles_equipos = []
        if celulares_ids:
            ids_limpios = [int(nid) for nid in celulares_ids]
            equipos_res = supabase.table("inventario_celulares").select("modelo, capacidad").in_("id", ids_limpios).execute()
            for eq in equipos_res.data:
                cap = f" ({eq['capacidad']})" if eq.get("capacidad") else ""
                detalles_equipos.append(f"{eq['modelo']}{cap}")
        
        equipos_string = ", ".join(detalles_equipos) if detalles_equipos else "tu nuevo equipo"

        # 3. Calcular días de delay y armar la PLANTILLA
        primer_nombre = cliente_nombre.split()[0] if cliente_nombre else "Cliente"
        dias_delay = 3
        texto_campana = ""
        nombre_estrategia = "Post-Venta"

        # MAGIA: Detectar si recibimos un ID de plantilla creada por el usuario o un texto viejo
        es_plantilla_personalizada = False
        
        if str(estrategia_o_plantilla).isdigit():
            plantilla_id = int(estrategia_o_plantilla)
            plantilla_res = supabase.table("plantillas_postventa").eq("id", plantilla_id).execute()
            
            if plantilla_res.data:
                plantilla = plantilla_res.data[0]
                dias_delay = plantilla["dias_espera"]
                nombre_estrategia = plantilla["nombre"]
                
                # Reemplazo dinámico de variables
                texto_crudo = plantilla["texto"]
                texto_campana = texto_crudo.replace("{nombre}", primer_nombre).replace("{equipo}", equipos_string)
                es_plantilla_personalizada = True

        # Fallback de seguridad (Mantiene vivas las plantillas genéricas por si la personalizada falla)
        if not es_plantilla_personalizada:
            nombre_estrategia = str(estrategia_o_plantilla)
            if nombre_estrategia == "satisfaccion":
                dias_delay = 3
                texto_campana = f"¡Hola {primer_nombre}! 😊 Te escribimos del local. Queríamos saber cómo te estás sintiendo con tu {equipos_string}. ¿Salió todo bien? Cualquier duda que necesites, ¡estamos acá!"
            elif nombre_estrategia == "upselling":
                dias_delay = 7
                texto_campana = f"¡Hola {primer_nombre}! 🛒 Esperamos que disfrutes a pleno tu {equipos_string}. Te dejamos un mimo: por haber comprado tu equipo con nosotros, tenés un *20% OFF* en accesorios esta semana."
            elif nombre_estrategia == "resena":
                dias_delay = 15
                texto_campana = f"¡Hola {primer_nombre}! ⭐ Pasaron unos días desde que te llevaste tu {equipos_string}. Nos ayudarías un montón dejando una breve reseña en Google. ¡Muchas gracias por elegirnos!"
            else:
                dias_delay = 3
                texto_campana = f"¡Hola {primer_nombre}! Gracias por tu compra de {equipos_string} con nosotros. ¡Estamos a tu disposición!"

        # 4. 🌟 CAMBIO CLAVE: Calculamos la fecha y HORA EXACTA del disparo
        fecha_disparo_dt = datetime.now(timezone.utc) + timedelta(days=dias_delay)
        
        payload_postventa = {
            "comercio_id": comercio_id,
            "cliente_nombre": cliente_nombre,
            "telefono": telefono,
            "equipos_detalle": equipos_string,
            "estrategia": nombre_estrategia, 
            "fecha_envio": fecha_disparo_dt.isoformat(),  # Guardamos con hora, minuto y segundo
            "estado": "pendiente",
            "mensaje_texto": texto_campana  
        }
        
        # 5. INSERCIÓN SEGURA: Dejamos que Supabase maneje la unicidad
        res_insert = supabase.table("cola_mensajes_postventa").insert(payload_postventa).execute()
        
        # 🌟 EL GATILLO DE UPSTASH: Solo se programa si se insertó un registro nuevo realmente
        if res_insert.data:
            nuevo_id = res_insert.data[0]["id"]
            programar_evento_futuro("postventa", nuevo_id, fecha_disparo_dt)
            print(f"🎉 ¡Post-Venta Agendado con precisión de minutos! Estrategia usada: {nombre_estrategia}")
            
        return True  # ✅ Retorna True porque se agendó con éxito

    except Exception as e:
        error_msg = str(e).lower()
        # 🛡️ BLINDAJE: Si el error es por nuestro CONSTRAINT UNIQUE, lo capturamos
        if "unique constraint" in error_msg or "duplicate key" in error_msg or "unique_postventa_por_cliente" in error_msg:
            print(f"⚠️ [Blindaje] La estrategia '{nombre_estrategia}' ya estaba agendada para {telefono}. Evitando duplicidad exitosamente.")
            return True # Retornamos True para que el frontend/proceso crea que todo salió bien, porque en efecto, el mensaje ya está asegurado.
        else:
            print(f"❌ Error crítico en agendar_postventa: {str(e)}")
            return False

@app.post("/api/ventas/directa")
async def registrar_venta_directa(request: Request):
    try:
        datos = await request.json()
        comercio_id = datos.get("comercio_id")
        cliente_nombre = datos.get("cliente_nombre", "Cliente Local")
        telefono = datos.get("telefono")
        celulares_ids = datos.get("celulares_ids", [])
        
        estrategia_o_plantilla = datos.get("plantilla_id") or datos.get("estrategia", "satisfaccion")

        if not comercio_id or not celulares_ids:
            raise HTTPException(status_code=400, detail="Faltan datos obligatorios")

        for nid in celulares_ids:
            supabase.table("inventario_celulares").update({
                "estado_venta": "vendido"
            }).eq("id", int(nid)).execute()

        fecha_hoy = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        insert_payload = {
            "comercio_id": int(comercio_id),
            "cliente_nombre": cliente_nombre,
            "telefono": telefono,
            "celulares_ids": celulares_ids,
            "tipo_registro": "venta_directa",
            "estado": "completado",
            "fecha_turno": fecha_hoy
        }
        supabase.table("turnos_clientes").insert(insert_payload).execute()

        # Capturamos si se agendó o no
        postventa_agendada = await agendar_postventa(int(comercio_id), cliente_nombre, telefono, celulares_ids, estrategia_o_plantilla)

        return {
            "status": "success", 
            "message": "Venta registrada.",
            "postventa_agendada": postventa_agendada # Se lo mandamos a React
        }
        
    except Exception as e:
        print(f"❌ Error en registro de venta directa: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/turnos/{turno_id}/completar")
async def completar_turno(turno_id: int, estrategia: str = None, plantilla_id: int = None):
    try:
        # 1. BLOQUEO ATÓMICO: Solo completamos si el turno estaba PENDIENTE
        # Esto evita que dos clics simultáneos disparen el proceso dos veces.
        lock = supabase.table("turnos_clientes")\
            .update({"estado": "completado"})\
            .eq("id", turno_id)\
            .eq("estado", "pendiente")\
            .execute()
        
        if not lock.data:
            # Si no hay data, es porque el turno ya no estaba pendiente o no existe
            raise HTTPException(status_code=400, detail="El turno ya fue procesado o no existe.")

        # 2. Obtenemos datos del turno (ahora que sabemos que es nuestro)
        turno = lock.data[0]
        comercio_id = turno.get("comercio_id")
        cliente_nombre = turno.get("cliente_nombre")
        telefono = turno.get("telefono")
        celulares_ids = turno.get("celulares_ids", [])

        # 3. Actualización de inventario
        if celulares_ids:
            for nid in celulares_ids:
                supabase.table("inventario_celulares").update({
                    "estado_venta": "vendido",
                    "stock": 0 
                }).eq("id", int(nid)).execute()
        
        # 4. Agendamiento de postventa
        estrategia_final = plantilla_id if plantilla_id else (estrategia or "satisfaccion")
        postventa_agendada = False
        
        if comercio_id:
            # Como agendar_postventa ya tiene su blindaje de UNIQUE constraint, 
            # podemos llamarla con confianza.
            postventa_agendada = await agendar_postventa(int(comercio_id), cliente_nombre, telefono, celulares_ids, estrategia_final)
        
        return {
            "status": "success", 
            "postventa_agendada": postventa_agendada
        }
        
    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"❌ Error crítico al completar turno {turno_id}: {e}")
        # En una arquitectura profesional, si algo falla después de completar el turno,
        # deberías considerar un "rollback" o una alerta de error grave.
        raise HTTPException(status_code=500, detail=str(e))
    
@app.put("/api/postventa/{id_registro}")
async def editar_mensaje_postventa(id_registro: int, datos: EditarPostVentaInput):
    """Permite al comerciante modificar el texto y la fecha de un mensaje programado."""
    try:
        # 1. Actualizamos en la base de datos
        res = supabase.table("cola_mensajes_postventa") \
            .update({
                "mensaje_texto": datos.mensaje_texto,
                "fecha_envio": datos.fecha_envio,
                "estado": "pendiente" # Por si estaba fallido y lo corrigen
            }) \
            .eq("id", id_registro) \
            .execute()
            
        if not res.data:
            raise HTTPException(status_code=404, detail="Registro no encontrado")
            
        # 2. 🌟 REPROGRAMAMOS EL GATILLO CON LA NUEVA FECHA
        try:
            # Detectamos si el front nos manda fecha con hora (ISO) o solo el día (YYYY-MM-DD)
            if "T" in datos.fecha_envio or " " in datos.fecha_envio:
                # Si trae hora, la respetamos exacta
                nueva_fecha_str = datos.fecha_envio.replace("Z", "").replace(" ", "T")
                nueva_fecha_obj = datetime.fromisoformat(nueva_fecha_str)
            else:
                # Si solo trae el día, le ponemos las 12:00 del mediodía por defecto para no enviar de madrugada
                nueva_fecha_obj = datetime.strptime(datos.fecha_envio, "%Y-%m-%d").replace(hour=12, minute=0)
            
            # Avisamos a Upstash del nuevo horario
            programar_evento_futuro("postventa", id_registro, nueva_fecha_obj)
            print(f"🔄 Gatillo de Post-Venta ID {id_registro} reprogramado para {nueva_fecha_obj}")
            
        except Exception as e:
            print(f"⚠️ Error al reprogramar QStash (el registro en BD se actualizó igual): {e}")

        return {"status": "success", "message": "Mensaje actualizado correctamente", "data": res.data}
        
    except Exception as e:
        print(f"❌ Error al editar postventa: {e}")
        raise HTTPException(status_code=500, detail=str(e))