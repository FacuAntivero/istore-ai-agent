import sys
import os
import time
import random
sys.path.insert(0, os.path.dirname(__file__))
import base64
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from google.genai import errors, types
from supabase import create_client
import requests
import json
import asyncio
import mercadopago
from dotenv import load_dotenv
from datetime import datetime, timedelta
from agent import iniciar_agente
from pydantic import BaseModel

load_dotenv()

app = FastAPI(title="iStore AI Webhook")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://istore-admin.vercel.app",
        "https://www.novva.com.ar"  # ✅ DOMINIO AGREGADO AQUÍ PARA SOLUCIONAR EL CORS
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class NgrokHeaderMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["ngrok-skip-browser-warning"] = "true"
        return response

app.add_middleware(NgrokHeaderMiddleware)

class EditarPostVentaInput(BaseModel):
    mensaje_texto: str
    fecha_envio: str

# Modelo para recibir la nueva plantilla desde React
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
mensajes_procesados = set()

# --- DICCIONARIOS PARA EL DEBOUNCER Y ANTI-TROLL ---
buffer_mensajes = {}
timers_debounce = {}
rate_limiter = {} # Guarda: { "numero_remitente": [timestamp1, timestamp2...] }
ultimo_aviso_audio = {} # Anti-Spam: Guarda { "id_remitente": timestamp_ultimo_aviso }

TIEMPO_ESPERA_MENSAJE = float(os.getenv("DEBOUNCE_SECONDS", 3.5))

EVOLUTION_API_URL = "https://evolution-api-production-4b88.up.railway.app"
API_KEY = "74BD7CFB-C38A-4143-833A-FCEA92FBBA21"

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

CACHE_COMERCIOS = {}

def obtener_comercio(instancia, forzar_actualizacion=False):
    if not forzar_actualizacion and instancia in CACHE_COMERCIOS:
        return CACHE_COMERCIOS[instancia]
    try:
        res = supabase.table("comercios").select("*").ilike("evolution_instance", instancia).execute()
        if res.data:
            CACHE_COMERCIOS[instancia] = res.data[0]
            return res.data[0]
    except Exception as e:
        print(f"[Supabase] ❌ Error buscando comercio: {e}")
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

def guardar_contacto(lid, numero, nombre, comercio_id):
    try:
        if numero == MI_NUMERO or numero.endswith("@lid"):
            return
        result = supabase.table("contactos").select("numero").eq("lid", lid).eq("comercio_id", comercio_id).execute()
        if result.data:
            return
        supabase.table("contactos").insert({
            "lid": lid,
            "numero": numero,
            "nombre": nombre,
            "comercio_id": comercio_id
        }).execute()
        print(f"[Supabase] ✅ Contacto guardado con éxito: {lid} → {numero} (Comercio ID: {comercio_id})")
    except Exception as e:
        print(f"[Supabase] ❌ Error guardando contacto: {e}")

def obtener_numero_real(lid, comercio_id, nombre_push, instance_name):
    try:
        id_limpio = int(comercio_id) if comercio_id is not None else None
        result = supabase.table("contactos").select("numero", "comercio_id", "nombre").eq("lid", lid).execute()
        
        if result.data:
            for contacto in result.data:
                if contacto.get("comercio_id") == id_limpio:
                    return contacto["numero"]
            
            numero_real = result.data[0]["numero"]
            nombre_cliente = nombre_push if nombre_push else result.data[0].get("nombre", "Cliente")
            print(f"[SaaS Link] 🔗 LID detectado en la red global. Vinculando número {numero_real} al comercio {id_limpio}")
            guardar_contacto(lid, numero_real, nombre_cliente, id_limpio)
            return numero_real
            
        if lid.endswith("@lid"):
            print(f"⚠️ [Motor de Búsqueda] {lid} es un ID enmascarado nuevo. Saltando fetchProfile para evitar bloqueo 400 de Evolution API.")
            return None
            
        print(f"🔍 [Motor de Búsqueda] {lid} no encontrado en DB. Consultando a Evolution API...")
        url_profile = f"{EVOLUTION_API_URL}/chat/fetchProfile/{instance_name}"
        headers = {"apikey": API_KEY, "Content-Type": "application/json"}
        payload = {"number": lid}
        
        respuesta = requests.post(url_profile, headers=headers, json=payload, timeout=5)
        
        if respuesta.status_code in [200, 201]:
            res_data = respuesta.json()
            data_obj = res_data[0] if isinstance(res_data, list) and len(res_data) > 0 else res_data
            id_real = None
            if isinstance(data_obj, dict):
                id_real = data_obj.get("id") or data_obj.get("jid") or data_obj.get("number")
                
            if id_real and id_real.endswith("@s.whatsapp.net"):
                print(f"🎯 [Evolution API] Encontrado número real: {id_real}")
                nombre_cliente = nombre_push if nombre_push else "Cliente"
                guardar_contacto(lid, id_real, nombre_cliente, id_limpio)
                return id_real
    except Exception as e:
        print(f"[Supabase/Evolution] ❌ Error en la red de seguridad global: {e}")
    return None

def simular_escribiendo(numero_destino, instance_name, encendido=True):
    url = f"{EVOLUTION_API_URL}/chat/sendPresence/{instance_name}?checkNumber=false"
    headers = {"apikey": API_KEY, "Content-Type": "application/json"}
    payload = {
        "number": numero_destino,
        "presence": "composing" if encendido else "available",
        "delay": 0,
        "checkNumber": False
    }
    try:
        requests.post(url, headers=headers, json=payload)
    except Exception:
        pass

def enviar_mensaje_whatsapp(numero_destino, texto, instance_name, id_mensaje=None, remote_jid=None):
    url = f"{EVOLUTION_API_URL}/message/sendText/{instance_name}?checkNumber=false"
    headers = {"apikey": API_KEY, "Content-Type": "application/json"}
    
    payload = {
        "number": numero_destino,
        "text": texto,
        "checkNumber": False,       
        "verifyNumber": False,      
        "options": {
            "delay": 0,
            "checkNumber": False    
        }
    }
    
    if id_mensaje and remote_jid:
        payload["options"]["quoted"] = {
            "key": {"id": id_mensaje, "remoteJid": remote_jid, "fromMe": False}
        }
        
    try:
        respuesta = requests.post(url, headers=headers, json=payload)
        if respuesta.status_code in [200, 201]:
            print(f"✅ Mensaje despachado con éxito a {numero_destino}")
        else:
            print(f"❌ Error al enviar a WhatsApp: {respuesta.text}")
    except Exception as e:
        print(f"❌ Error crítico de red: {e}")

# --- FUNCIÓN DE ALERTA AL DUEÑO ---
def alertar_consumo_dueno(telefono_dueno, porcentaje, mensajes_restantes, instance_name):
    if not telefono_dueno: return
    tel_dueno_jid = telefono_dueno.strip()
    if not tel_dueno_jid.endswith("@s.whatsapp.net"): tel_dueno_jid = f"{tel_dueno_jid}@s.whatsapp.net"
    
    emoji = "⚠️" if porcentaje == 80 else "🚨"
    mensaje = (
        f"{emoji} *Aviso de Consumo del Bot*\n\n"
        f"Tu bot ha consumido el *{porcentaje}%* de los mensajes de tu plan actual.\n"
        f"Te quedan: *{mensajes_restantes} mensajes*.\n\n"
        f"Por favor, renová tu plan pronto desde el panel para evitar que el bot se detenga."
    )
    enviar_mensaje_whatsapp(tel_dueno_jid, mensaje, instance_name)
    print(f"📢 [ALERTA] Aviso de {porcentaje}% enviado al dueño ({tel_dueno_jid})")

async def procesar_bloque_mensajes(id_remitente_limpio, comercio_id, instance_name, numero_destino, remote_jid_original):
    if id_remitente_limpio not in buffer_mensajes or not buffer_mensajes[id_remitente_limpio]:
        return

    # --- 🛡️ BARRERA SAAS: VALIDACIÓN DE SUSCRIPCIÓN Y SALDO ---
    try:
        res_comercio = supabase.table("comercios").select("mensajes_disponibles", "plan_actual", "telefono_dueno").eq("id", comercio_id).execute()
        if res_comercio.data:
            comercio_db = res_comercio.data[0]
            saldo = comercio_db.get("mensajes_disponibles", 0)
            tel_dueno = comercio_db.get("telefono_dueno")

            if saldo <= 0:
                print(f"🚫 [SaaS] Comercio {comercio_id} no tiene mensajes disponibles. Bot frenado.")
                buffer_mensajes.pop(id_remitente_limpio, None)
                return
            
            nuevo_saldo = saldo - 1
            supabase.table("comercios").update({"mensajes_disponibles": nuevo_saldo}).eq("id", comercio_id).execute()
            print(f"📉 [SaaS] Crédito consumido para {comercio_id}. Restantes: {nuevo_saldo}")

            # Alertas de consumo dinámicas adaptadas a los límites oficiales de tus planes
            plan_actual = str(comercio_db.get("plan_actual", "trial")).lower()
            topes_planes = {"trial": 50, "basico": 1000, "pro": 3500, "premium": 10000}
            limite_plan = topes_planes.get(plan_actual, 1000)
            
            umbral_80 = int(limite_plan * 0.20)  # Queda el 20% disponible (80% consumido)
            umbral_95 = int(limite_plan * 0.05)  # Queda el 5% disponible (95% consumido)
            
            if nuevo_saldo == umbral_80:
                alertar_consumo_dueno(tel_dueno, 80, nuevo_saldo, instance_name)
            elif nuevo_saldo == umbral_95:
                alertar_consumo_dueno(tel_dueno, 95, nuevo_saldo, instance_name)

    except Exception as e:
        print(f"❌ [SaaS] Error verificando suscripción: {e}")
    # ---------------------------------------------------------------

    mensajes = buffer_mensajes.pop(id_remitente_limpio)
    
    # Preparamos el contenido estructurado que recibirá Gemini
    elementos_prompt = []
    textos_del_bloque = []

    for m in mensajes:
        textos_del_bloque.append(m["texto"])
        # Si el elemento trae contenido de audio adjunto, lo estructuramos para la SDK de Google
        if "audio_bytes" in m and m["audio_bytes"]:
            # ✅ ESTE ES EL CAMBIO CLAVE: Usamos types.Part.from_bytes
            parte_audio = types.Part.from_bytes(
                data=m["audio_bytes"],
                mime_type="audio/ogg"
            )
            elementos_prompt.append(parte_audio)

    texto_completo = ". ".join(textos_del_bloque)
    elementos_prompt.append(texto_completo)
    
    ultimo_id_mensaje = mensajes[-1]["id_mensaje"]

    print(f"\n[Procesando Bloque 📦] {id_remitente_limpio}: {texto_completo} (Contiene audios: {len(elementos_prompt) > 1})")

    session_key = f"{comercio_id}_{id_remitente_limpio}"
    if session_key not in sesiones_chat:
        sesiones_chat[session_key] = iniciar_agente(comercio_id, numero_destino)
    chat_actual = sesiones_chat[session_key]

    try:
        # Pasamos la lista conteniendo tanto el texto acumulado como los objetos Part de los audios
        respuesta = chat_actual.send_message(elementos_prompt)
        texto_respuesta = respuesta.text
        print(f"[Agente] 🤖 Respuesta lista: {texto_respuesta}")

        activar_delay_humano = os.getenv("SIMULATE_HUMAN_DELAY", "true").lower() == "true"
        
        if activar_delay_humano:
            simular_escribiendo(numero_destino, instance_name, encendido=True)
            tiempo_lectura = random.uniform(2.0, 4.0)
            tiempo_tipeo = max(len(texto_respuesta) * 0.03, 3.5)
            delay_total = min(round(tiempo_lectura + tiempo_tipeo, 1), 10.0)
            
            await asyncio.sleep(delay_total)
            simular_escribiendo(numero_destino, instance_name, encendido=False)

        enviar_mensaje_whatsapp(numero_destino, texto_respuesta, instance_name, ultimo_id_mensaje, remote_jid_original)

        try:
            res_conf = supabase.table("configuracion_comercios").select("telefono_dueno", "mensaje_cotizacion_tecnico").eq("comercio_id", comercio_id).execute()
            if res_conf.data:
                conf = res_conf.data[0]
                tel_dueno = conf.get("telefono_dueno")
                msg_tecnico_cfg = conf.get("mensaje_cotizacion_tecnico") or "Aguardame un instante que te preparo la cotización sin cargo 🛠"
                
                debe_notificar = False
                motivo_alerta = "Intervención Solicitada"
                
                if msg_tecnico_cfg in texto_respuesta:
                    debe_notificar = True
                    motivo_alerta = "Presupuesto de Servicio Técnico 🛠️"
                elif "asesor" in texto_respuesta.lower() and ("continuará" in texto_respuesta.lower() or "derivo" in texto_respuesta.lower() or "atendiendo" in texto_respuesta.lower()):
                    debe_notificar = True
                    motivo_alerta = "Plan Canje / Solicitud de Humano 👤"
                
                if debe_notificar and tel_dueno:
                    tel_dueno_jid = tel_dueno.strip()
                    if not tel_dueno_jid.endswith("@s.whatsapp.net"):
                        tel_dueno_jid = f"{tel_dueno_jid}@s.whatsapp.net"
                        
                    mensaje_alerta = (
                        f"⚠️ *[ALERTA AGENTE - {motivo_alerta}]*\n\n"
                        f"El cliente *{id_remitente_limpio}* requiere atención humana urgente.\n"
                        f"💬 *Historial del bloque:* {texto_completo}\n\n"
                        f"👉 Entrá a tu chat para responderle."
                    )
                    print(f"📢 [Notificación] Enviando alerta de soporte al dueño ({tel_dueno_jid})")
                    enviar_mensaje_whatsapp(tel_dueno_jid, mensaje_alerta, instance_name)
        except Exception as e:
            print(f"❌ [Notificación] Error al intentar alertar al dueño: {e}")

    except errors.APIError as e:
        print(f"[Error API Gemini] {e.message}")
        enviar_mensaje_whatsapp(numero_destino, "Disculpa, estoy procesando mucha información. ¿Me repites en unos segundos?", instance_name, ultimo_id_mensaje, remote_jid_original)
    except Exception as e:
        print(f"[Error Inesperado] {str(e)}")

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

    comercio = obtener_comercio(instance_name)
    if not comercio:
        return {"status": "error"}
    comercio_id = comercio["id"]

    if evento_actual == "contacts.upsert":
        try:
            contactos_data = datos.get("data", [])
            if isinstance(contactos_data, dict): contactos_data = [contactos_data]
            for c in contactos_data:
                if c.get("id", "").endswith("@s.whatsapp.net") and c.get("lid", "").endswith("@lid"):
                    guardar_contacto(c.get("lid"), c.get("id"), c.get("pushName", ""), comercio_id)
        except Exception:
            pass
        return {"status": "ok"}

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
            else:
                numero_guardado = obtener_numero_real(remote_jid, comercio_id, push_name, instance_name)
                if numero_guardado: numero_destino = numero_guardado

        id_remitente_limpio = numero_destino.split("@")[0]

        # --- 🧠 MANEJO DE CONTEXTO PARA MENSAJES ENVIADOS POR EL SISTEMA ---
        if key.get("fromMe", False):
            texto_saliente, tipo = extraer_texto_y_tipo(msg_content)
            
            if texto_saliente and tipo == "text":
                session_key = f"{comercio_id}_{id_remitente_limpio}"
                
                # Instanciamos el agente si no estaba en memoria
                if session_key not in sesiones_chat:
                    sesiones_chat[session_key] = iniciar_agente(comercio_id, numero_destino)
                
                # Le inyectamos al historial de la IA lo que el sistema/humano acaba de enviar
                sesiones_chat[session_key].history.append(
                    types.Content(
                        role="model", 
                        parts=[types.Part.from_text(text=texto_saliente)]
                    )
                )
                print(f"🧠 [Contexto Inyectado] Gemini ahora sabe que le dijimos: {texto_saliente[:30]}...")
            
            return {"status": "contexto_guardado"}
        # -------------------------------------------------------------------

        id_mensaje = key.get("id", "")

        if id_mensaje in mensajes_procesados: return {"status": "ignorado"}
        mensajes_procesados.add(id_mensaje)

        if es_troll(id_remitente_limpio):
            print(f"🚷 [Anti-Troll] Bloqueando ráfaga de mensajes de {id_remitente_limpio}")
            return {"status": "bloqueado_rate_limit"}

        texto_usuario, tipo_mensaje = extraer_texto_y_tipo(msg_content)

        if tipo_mensaje in ["sticker", "image"]:
            print("🖼️ Mensaje visual ignorado.")
            return {"status": "multimedia_ignorado"}
            
        elif tipo_mensaje == "audio":
            plan_actual = str(comercio.get("plan_actual", "trial")).lower()
            
            # 🛑 LIMITACIÓN PLAN BÁSICO (Rechazo automático + Anti-Spam de 5 minutos)
            if plan_actual == "basico":
                ahora = time.time()
                ultimo_ts = ultimo_aviso_audio.get(id_remitente_limpio, 0)
                
                if ahora - ultimo_ts > 300:  # Pasaron más de 300 segundos (5 min)
                    ultimo_aviso_audio[id_remitente_limpio] = ahora
                    msg_escribime = "¡Hola! Por el momento mi plan no me permite escuchar notas de voz. 🎙️❌\n\nPor favor, *escribime tu consulta por texto* para que pueda ayudarte de inmediato. 😊"
                    enviar_mensaje_whatsapp(numero_destino, msg_escribime, instance_name, id_mensaje, key.get("remoteJid"))
                    return {"status": "audio_denegado_plan_basico"}
                
                print(f"🔇 [Anti-Spam Audio] Audio de {id_remitente_limpio} ignorado silenciosamente.")
                return {"status": "audio_ignorado_por_spam"}
            
            # Lógica normal para el resto de los planes habilitados (Trial, Pro, Premium)
            permite_audio = comercio.get("permitir_audios", False)
            if not permite_audio:
                enviar_mensaje_whatsapp(numero_destino, "Perdoná, por el momento solo puedo leer textos. Por favor, escribime tu consulta 😊", instance_name, id_mensaje, key.get("remoteJid"))
                return {"status": "audio_rechazado"}
            
            # 🌟 [PLAN PRO/PREMIUM VALIDADOS] -> Descarga asíncrona/directa del audio
            audio_bytes = descargar_audio_evolution(instance_name, mensaje_data)
            if not audio_bytes:
                enviar_mensaje_whatsapp(numero_destino, "Pucha, tuve un problema al descargar tu nota de voz. 😥 ¿Me la podés repetir o escribir por texto porfa?", instance_name, id_mensaje, key.get("remoteJid"))
                return {"status": "error_descarga_audio"}

            if id_remitente_limpio not in buffer_mensajes:
                buffer_mensajes[id_remitente_limpio] = []
            
            # Almacenamos los bytes directamente dentro de la lista de pendientes
            buffer_mensajes[id_remitente_limpio].append({
                "texto": "[El usuario envió una nota de voz/audio]",
                "audio_bytes": audio_bytes,
                "id_mensaje": id_mensaje
            })

            if id_remitente_limpio in timers_debounce and not timers_debounce[id_remitente_limpio].done():
                timers_debounce[id_remitente_limpio].cancel()

            async def timer_task_audio():
                await asyncio.sleep(TIEMPO_ESPERA_MENSAJE)
                await procesar_bloque_mensajes(id_remitente_limpio, comercio_id, instance_name, numero_destino, remote_jid)

            timers_debounce[id_remitente_limpio] = asyncio.create_task(timer_task_audio())
            return {"status": "audio_en_espera"}

        if not texto_usuario: return {"status": "ignorado"}

        if remote_jid.endswith("@lid") and numero_destino.endswith("@s.whatsapp.net"):
            guardar_contacto(remote_jid, numero_destino, push_name, comercio_id)

        if id_remitente_limpio not in buffer_mensajes:
            buffer_mensajes[id_remitente_limpio] = []
        
        buffer_mensajes[id_remitente_limpio].append({
            "texto": texto_usuario,
            "id_mensaje": id_mensaje
        })

        if id_remitente_limpio in timers_debounce and not timers_debounce[id_remitente_limpio].done():
            timers_debounce[id_remitente_limpio].cancel()

        async def timer_task():
            await asyncio.sleep(TIEMPO_ESPERA_MENSAJE)
            await procesar_bloque_mensajes(id_remitente_limpio, comercio_id, instance_name, numero_destino, remote_jid)

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
            payment_id = params.get("data.id")
            
            payment_info = mp.payment().get(payment_id)
            payment_data = payment_info["response"]
            
            status = payment_data.get("status")
            external_reference = payment_data.get("external_reference")

            if status == "approved" and external_reference:
                print(f"💰 [MercadoPago] ¡Pago APROBADO! ID Pago: {payment_id}")
                
                partes = external_reference.split("|")
                comercio_id = partes[0]
                tipo_plan = partes[1] if len(partes) > 1 else "pro"
                
                mensajes_por_plan = {
                    "basico": 1000,
                    "pro": 3500,
                    "premium": 10000
                }
                mensajes_a_cargar = mensajes_por_plan.get(tipo_plan, 3500)
                
                fecha_vencimiento = (datetime.utcnow() + timedelta(days=30)).isoformat()
                
                supabase.table("comercios").update({
                    "estado_suscripcion": "activa",
                    "plan_actual": tipo_plan,
                    "mensajes_disponibles": mensajes_a_cargar,
                    "plan_vence_el": fecha_vencimiento
                }).eq("id", int(comercio_id)).execute()
                
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

        # 4. Insertar en la cola con la fecha correcta
        fecha_disparo = (datetime.now() + timedelta(days=dias_delay)).strftime("%Y-%m-%d")

        payload_postventa = {
            "comercio_id": comercio_id,
            "cliente_nombre": cliente_nombre,
            "telefono": telefono,
            "equipos_detalle": equipos_string,
            "estrategia": nombre_estrategia, # Guarda el nombre custom o el legacy
            "fecha_envio": fecha_disparo,
            "estado": "pendiente",
            "mensaje_texto": texto_campana  
        }
        
        res_insert = supabase.table("cola_mensajes_postventa").insert(payload_postventa).execute()
        print(f"🎉 ¡Post-Venta Agendado! Estrategia usada: {nombre_estrategia}")
        return True  # ✅ Retorna True porque se agendó con éxito

    except Exception as e:
        print(f"❌ Error en agendar_postventa: {str(e)}")
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
        turno_res = supabase.table("turnos_clientes").select("id, comercio_id, cliente_nombre, telefono, celulares_ids").eq("id", turno_id).execute()
        if not turno_res.data:
            raise HTTPException(status_code=404, detail="Turno no encontrado")

        turno = turno_res.data[0]
        comercio_id = turno.get("comercio_id")
        cliente_nombre = turno.get("cliente_nombre")
        telefono = turno.get("telefono")
        celulares_ids = turno.get("celulares_ids", [])

        supabase.table("turnos_clientes").update({"estado": "completado"}).eq("id", turno_id).execute()
        
        if celulares_ids:
            for nid in celulares_ids:
                supabase.table("inventario_celulares").update({
                    "estado_venta": "vendido"
                }).eq("id", int(nid)).execute()
        
        estrategia_final = plantilla_id if plantilla_id else (estrategia or "satisfaccion")

        # Capturamos si se agendó o no
        postventa_agendada = False
        if comercio_id:
            postventa_agendada = await agendar_postventa(int(comercio_id), cliente_nombre, telefono, celulares_ids, estrategia_final)
        
        return {
            "status": "success", 
            "postventa_agendada": postventa_agendada # Se lo mandamos a React
        }
        
    except Exception as e:
        print(f"❌ Error al completar turno {turno_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- PLANIFICADOR DE NOTIFICACIONES INTEGRADO (CRON INTERNO) ---
from cron_notificaciones import procesar_recordatorios, procesar_postventa

async def planificador_interno():
    """Bucle infinito que escanea de forma horaria la DB para despachar recordatorios y post-ventas."""
    print("[Planificador] 🚀 Motor de notificaciones automáticas iniciado en segundo plano.")
    while True:
        try:
            print("[Planificador] ⏰ Ejecutando escaneo automático de campañas listas...")
            
            # Ejecuta las sub-rutinas modulares de cron_notificaciones.py
            # Al usar internamente .lte("fecha_envio", fecha_hoy), se enviará todo lo que
            # corresponda al día actual sin importar la hora exacta en la que se reprogramó.
            procesar_recordatorios()
            procesar_postventa()
            
            # Duerme 1 hora exacta (3600 segundos) para optimizar el consumo de la base de datos
            await asyncio.sleep(3600)
                
        except Exception as e:
            print(f"[Planificador] ❌ Error crítico en el loop de notificaciones: {e}")
            # Si hay un error de conexión, espera 1 minuto y vuelve a intentar para no romper el servicio
            await asyncio.sleep(60)

@app.on_event("startup")
async def arrancar_planificador_en_segundo_plano():
    """Le dice a FastAPI que encienda el reloj apenas el servidor se ponga en marcha."""
    asyncio.create_task(planificador_interno())
    
@app.get("/api/admin/forzar-cron-postventa")
async def forzar_cron_postventa_endpoint():
    """Endpoint de desarrollo para ejecutar el cron sin esperar a las 11 AM."""
    try:
        procesar_postventa()
        return {"status": "success", "message": "Cron forzado ejecutado. Revisá la terminal para ver los resultados."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.put("/api/postventa/{id_registro}")
async def editar_mensaje_postventa(id_registro: int, datos: EditarPostVentaInput):
    """Permite al comerciante modificar el texto y la fecha de un mensaje programado."""
    try:
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
            
        return {"status": "success", "message": "Mensaje actualizado correctamente", "data": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))