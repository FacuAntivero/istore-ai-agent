import sys
import os
import time
import random
sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from starlette.middleware.base import BaseHTTPMiddleware
from google.genai import errors
from supabase import create_client
import requests
import json
import asyncio
from dotenv import load_dotenv

from agent import iniciar_agente

load_dotenv()

app = FastAPI(title="iStore AI Webhook")

class NgrokHeaderMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["ngrok-skip-browser-warning"] = "true"
        return response

app.add_middleware(NgrokHeaderMiddleware)

sesiones_chat = {}
mensajes_procesados = set()

# 🔥 DICCIONARIOS PARA EL DEBOUNCER
buffer_mensajes = {}
timers_debounce = {}

TIEMPO_ESPERA_MENSAJE = float(os.getenv("DEBOUNCE_SECONDS", 3.5))

EVOLUTION_API_URL = "https://evolution-api-production-4b88.up.railway.app"
API_KEY = "74BD7CFB-C38A-4143-833A-FCEA92FBBA21"

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

CACHE_COMERCIOS = {}

def obtener_comercio(instancia):
    if instancia in CACHE_COMERCIOS:
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
        
        # 1. Intentar buscar en la base de datos local (Supabase)
        result = supabase.table("contactos").select("numero", "comercio_id", "nombre").eq("lid", lid).execute()
        
        if result.data:
            for contacto in result.data:
                if contacto.get("comercio_id") == id_limpio:
                    return contacto["numero"]
            
            # Si está en la DB pero con otro comercio, lo vinculamos a este
            numero_real = result.data[0]["numero"]
            nombre_cliente = nombre_push if nombre_push else result.data[0].get("nombre", "Cliente")
            print(f"[SaaS Link] 🔗 LID detectado en la red global. Vinculando número {numero_real} al comercio {id_limpio}")
            guardar_contacto(lid, numero_real, nombre_cliente, id_limpio)
            return numero_real
            
        # 2. 🔥 RED DE SEGURIDAD: Consultar a Evolution API en tiempo real
        print(f"🔍 [Motor de Búsqueda] {lid} no encontrado en DB. Consultando a Evolution API...")
        
        url_profile = f"{EVOLUTION_API_URL}/chat/fetchProfile/{instance_name}"
        headers = {"apikey": API_KEY, "Content-Type": "application/json"}
        payload = {"number": lid}
        
        respuesta = requests.post(url_profile, headers=headers, json=payload, timeout=5)
        
        if respuesta.status_code in [200, 201]:
            res_data = respuesta.json()
            # Evolution puede devolver una lista o un objeto directo
            data_obj = res_data[0] if isinstance(res_data, list) and len(res_data) > 0 else res_data
            
            id_real = None
            if isinstance(data_obj, dict):
                id_real = data_obj.get("id") or data_obj.get("jid") or data_obj.get("number")
                
            if id_real and id_real.endswith("@s.whatsapp.net"):
                print(f"🎯 [Evolution API] ¡Éxito! Encontrado número real en tiempo real: {id_real}")
                nombre_cliente = nombre_push if nombre_push else "Cliente"
                guardar_contacto(lid, id_real, nombre_cliente, id_limpio)
                return id_real
            else:
                print(f"⚠️ [Evolution API] No se pudo extraer el ID real. Respuesta: {res_data}")
        else:
            print(f"❌ [Evolution API] Error en fetchProfile: {respuesta.text}")

    except Exception as e:
        print(f"[Supabase/Evolution] ❌ Error en la red de seguridad global: {e}")
    return None

def simular_escribiendo(numero_destino, instance_name, encendido=True):
    url = f"{EVOLUTION_API_URL}/chat/sendPresence/{instance_name}"
    headers = {"apikey": API_KEY, "Content-Type": "application/json"}
    payload = {
        "number": numero_destino,
        "presence": "composing" if encendido else "available",
        "delay": 0
    }
    try:
        requests.post(url, headers=headers, json=payload)
    except Exception as e:
        pass

def enviar_mensaje_whatsapp(numero_destino, texto, instance_name, id_mensaje=None, remote_jid=None):
    # 🔥 EL COMBO GANADOR: URL con checkNumber=false
    url = f"{EVOLUTION_API_URL}/message/sendText/{instance_name}?checkNumber=false"
    headers = {
        "apikey": API_KEY,
        "Content-Type": "application/json"
    }
    
    # Enviamos el destino exacto que recibimos (con su @lid)
    payload = {
        "number": numero_destino,
        "text": texto
    }
    
    options = {
        "delay": 0
    }
    
    if id_mensaje and remote_jid:
        options["quoted"] = {
            "key": {"id": id_mensaje, "remoteJid": remote_jid, "fromMe": False}
        }

    payload["options"] = options
        
    try:
        respuesta = requests.post(url, headers=headers, json=payload)
        if respuesta.status_code in [200, 201]:
            print("✅ Mensaje entregado a Evolution y despachado a WhatsApp")
        else:
            print(f"❌ Error al enviar: {respuesta.text}")
    except Exception as e:
        print(f"❌ Error crítico de red: {e}")

async def procesar_bloque_mensajes(id_remitente, comercio_id, instance_name, remote_jid):
    if id_remitente not in buffer_mensajes or not buffer_mensajes[id_remitente]:
        return

    mensajes = buffer_mensajes.pop(id_remitente)
    texto_completo = ". ".join([m["texto"] for m in mensajes])
    ultimo_id_mensaje = mensajes[-1]["id_mensaje"]

    print(f"\n[Procesando Bloque 📦] {id_remitente}: {texto_completo}")

    session_key = f"{comercio_id}_{id_remitente}"
    if session_key not in sesiones_chat:
        sesiones_chat[session_key] = iniciar_agente(comercio_id, id_remitente)
    chat_actual = sesiones_chat[session_key]

    try:
        respuesta = chat_actual.send_message(texto_completo)
        texto_respuesta = respuesta.text
        print(f"[Agente] 🤖 Respuesta lista: {texto_respuesta}")

        activar_delay_humano = os.getenv("SIMULATE_HUMAN_DELAY", "true").lower() == "true"
        
        if activar_delay_humano:
            # 🔥 USAMOS remote_jid PARA QUE EVOLUTION SEPA QUE ES UN @lid
            simular_escribiendo(remote_jid, instance_name, encendido=True)
            
            tiempo_lectura = random.uniform(2.0, 4.0)
            tiempo_tipeo = max(len(texto_respuesta) * 0.03, 3.5)
            delay_total = round(tiempo_lectura + tiempo_tipeo, 1)
            delay_total = min(delay_total, 10.0) 
            
            await asyncio.sleep(delay_total)
            simular_escribiendo(remote_jid, instance_name, encendido=False)

        # 🔥 CAMBIO AQUÍ: Enviamos a remote_jid en lugar de id_remitente
        enviar_mensaje_whatsapp(remote_jid, texto_respuesta, instance_name, ultimo_id_mensaje, remote_jid)

    except errors.APIError as e:
        print(f"[Error API Gemini] {e.message}")
        # 🔥 CAMBIO AQUÍ TAMBIÉN
        enviar_mensaje_whatsapp(remote_jid, "Disculpa, estoy procesando mucha información. ¿Me repites en unos segundos?", instance_name, ultimo_id_mensaje, remote_jid)
    except Exception as e:
        print(f"[Error Inesperado] {str(e)}")


def extraer_texto_mensaje(msg_object):
    if not isinstance(msg_object, dict):
        return None
    
    if "conversation" in msg_object and isinstance(msg_object["conversation"], str):
        return msg_object["conversation"]
        
    if "extendedTextMessage" in msg_object and "text" in msg_object["extendedTextMessage"]:
        return msg_object["extendedTextMessage"]["text"]
        
    for key, value in msg_object.items():
        if isinstance(value, dict):
            resultado = extraer_texto_mensaje(value)
            if resultado: return resultado
            
    return None

@app.post("/webhook")
async def recibir_mensaje(request: Request, background_tasks: BackgroundTasks):
    datos = await request.json()
    
    # 🔥 EL DEBUG DEFINITIVO
    print("\n--- INICIO JSON WEBHOOK ---")
    print(json.dumps(datos, indent=2))
    print("--- FIN JSON WEBHOOK ---\n")
    
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
                c_id = c.get("id", "")
                c_lid = c.get("lid", "")
                c_name = c.get("pushName", "")
                if c_id.endswith("@s.whatsapp.net") and c_lid.endswith("@lid"):
                    guardar_contacto(c_lid, c_id, c_name, comercio_id)
        except Exception as e:
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

        if key.get("fromMe", False):
            return {"status": "ignorado"}

        remote_jid = key.get("remoteJid", "")
        id_mensaje = key.get("id", "")
        push_name = mensaje_data.get("pushName", "")
        
        # 🔥 EL CAMBIO CLAVE ESTÁ AQUÍ. Buscamos el sender principal en la raíz.
        sender = mensaje_data.get("sender", "")
        participant = key.get("participant", "")

        if remote_jid.endswith("@g.us") or remote_jid == "status@broadcast":
            return {"status": "ignorado"}

        texto_usuario = extraer_texto_mensaje(msg_content)
        
        if not texto_usuario:
            return {"status": "ignorado"}

        if remote_jid.endswith("@lid") and sender and sender.endswith("@s.whatsapp.net"):
            guardar_contacto(remote_jid, sender, push_name, comercio_id)

        if id_mensaje in mensajes_procesados:
            return {"status": "ignorado"}
        mensajes_procesados.add(id_mensaje)

        # 🔥 BÚSQUEDA AGRESIVA DEL NÚMERO REAL
        # Usamos el número real para la base de datos (Gemini/Supabase) pero conservamos el remote_jid para enviar el mensaje.
        id_remitente = remote_jid 
        
        if remote_jid.endswith("@lid"):
            if sender and sender.endswith("@s.whatsapp.net"):
                id_remitente = sender
            elif participant and participant.endswith("@s.whatsapp.net"):
                id_remitente = participant
            else:
                numero_guardado = obtener_numero_real(remote_jid, comercio_id, push_name, instance_name)
                if numero_guardado: 
                    id_remitente = numero_guardado

        # Limpiamos el número para que sea más fácil guardarlo y buscarlo
        id_remitente_limpio = id_remitente.split("@")[0]

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
            # Pasamos tanto la ID limpia (para Gemini/DB) como el remote_jid (para enviar el WhatsApp)
            await procesar_bloque_mensajes(id_remitente_limpio, comercio_id, instance_name, remote_jid)

        timers_debounce[id_remitente_limpio] = asyncio.create_task(timer_task())
        
        return {"status": "en_espera"}

    except Exception as e:
        print(f"Error procesando mensaje: {e}")
        return {"status": "error"}