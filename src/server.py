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

# ⏱️ CONFIGURACIÓN DINÁMICA DESDE EL .ENV
TIEMPO_ESPERA_MENSAJE = float(os.getenv("DEBOUNCE_SECONDS", 3.5))

EVOLUTION_API_URL = "https://evolution-api-production-4b88.up.railway.app"
API_KEY = "74BD7CFB-C38A-4143-833A-FCEA92FBBA21"

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

CACHE_COMERCIOS = {}

def obtener_comercio(instancia):
    if instancia in CACHE_COMERCIOS:
        return CACHE_COMERCIOS[instancia]
    try:
        res = supabase.table("comercios").select("*").eq("evolution_instance", instancia).execute()
        if res.data:
            CACHE_COMERCIOS[instancia] = res.data[0]
            return res.data[0]
    except Exception as e:
        print(f"[Supabase] ❌ Error buscando comercio: {e}")
    return None

MI_NUMERO = os.getenv("MI_NUMERO", "5492494600615@s.whatsapp.net")

def guardar_contacto(lid, numero, nombre, comercio_id):
    try:
        if numero == MI_NUMERO:
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
        print(f"[Supabase] ✅ Contacto guardado con éxito: {lid} → {numero}")
    except Exception as e:
        print(f"[Supabase] ❌ Error guardando contacto: {e}")

def obtener_numero_real(lid, comercio_id):
    try:
        result = supabase.table("contactos").select("numero").eq("lid", lid).eq("comercio_id", comercio_id).execute()
        if result.data:
            return result.data[0]["numero"]
    except Exception as e:
        print(f"[Supabase] ❌ Error buscando contacto: {e}")
    return None

def simular_escribiendo(numero_destino, instance_name, encendido=True):
    """Envia el estado 'escribiendo...' (composing) a Evolution API."""
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
        print(f"⚠️ No se pudo cambiar estado escribiendo: {e}")

def enviar_mensaje_whatsapp(numero_destino, texto, instance_name, id_mensaje=None, remote_jid=None):
    url = f"{EVOLUTION_API_URL}/message/sendText/{instance_name}?checkNumber=false"
    headers = {
        "apikey": API_KEY,
        "Content-Type": "application/json"
    }
    options = {"checkNumber": False}
    
    if id_mensaje and remote_jid:
        options["quoted"] = {
            "key": {"id": id_mensaje, "remoteJid": remote_jid, "fromMe": False}
        }

    payload = {
        "number": numero_destino,
        "text": texto,
        "checkNumber": False,
        "options": options
    }
        
    respuesta = requests.post(url, headers=headers, json=payload)
    if respuesta.status_code in [200, 201]:
        print("✅ Mensaje entregado con éxito")
    else:
        print(f"❌ Error al enviar: {respuesta.text}")

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

        # 🔥 LÓGICA SIMULADOR HUMANO DESDE EL .ENV
        activar_delay_humano = os.getenv("SIMULATE_HUMAN_DELAY", "true").lower() == "true"
        
        if activar_delay_humano:
            simular_escribiendo(id_remitente, instance_name, encendido=True)
            
            tiempo_lectura = 1.5
            tiempo_tipeo = min(len(texto_respuesta) * 0.02, 6.0) 
            delay_total = round(random.uniform(tiempo_lectura + tiempo_tipeo, (tiempo_lectura + tiempo_tipeo) + 1.5), 1)
            
            print(f"⏳ Esperando {delay_total} segundos (simulando escritura humana...)")
            await asyncio.sleep(delay_total)
            
            simular_escribiendo(id_remitente, instance_name, encendido=False)
        else:
            print("⚡ Modo test activo: Ignorando delay de escritura humana.")

        enviar_mensaje_whatsapp(id_remitente, texto_respuesta, instance_name, ultimo_id_mensaje, remote_jid)

    except errors.APIError as e:
        print(f"[Error API] {e.message}")
        enviar_mensaje_whatsapp(id_remitente, "Disculpa, estoy procesando mucha información. ¿Me repites en unos segundos?", instance_name, ultimo_id_mensaje, remote_jid)
    except Exception as e:
        print(f"[Error Inesperado] {str(e)}")

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
                c_id = c.get("id", "")
                c_lid = c.get("lid", "")
                c_name = c.get("pushName", "")
                if c_id.endswith("@s.whatsapp.net") and c_lid.endswith("@lid"):
                    guardar_contacto(c_lid, c_id, c_name, comercio_id)
        except Exception as e:
            pass
        return {"status": "ok"}

    if evento_actual == "send.message":
        return {"status": "ok"}

    try:
        mensaje_data = datos.get("data", {})
        key = mensaje_data.get("key", {})

        if key.get("fromMe", False):
            return {"status": "ignorado"}

        remote_jid = key.get("remoteJid", "")
        id_mensaje = key.get("id", "")
        push_name = mensaje_data.get("pushName", "")
        sender = mensaje_data.get("sender", "")

        if remote_jid.endswith("@g.us"):
            return {"status": "ignorado"}

        msg_content = mensaje_data.get("message", {})
        if "conversation" in msg_content:
            texto_usuario = msg_content["conversation"]
        elif "extendedTextMessage" in msg_content:
            texto_usuario = msg_content["extendedTextMessage"]["text"]
        else:
            return {"status": "ignorado"}

        if remote_jid.endswith("@lid") and sender.endswith("@s.whatsapp.net"):
            guardar_contacto(remote_jid, sender, push_name, comercio_id)

        if id_mensaje in mensajes_procesados:
            return {"status": "ignorado"}
        mensajes_procesados.add(id_mensaje)

        id_remitente = remote_jid
        if remote_jid.endswith("@lid"):
            numero_guardado = obtener_numero_real(remote_jid, comercio_id)
            if numero_guardado: id_remitente = numero_guardado

        # ==========================================================
        # ⏱️ LÓGICA DE DEBOUNCE O COLECTOR DE MENSAJES
        # ==========================================================
        if id_remitente not in buffer_mensajes:
            buffer_mensajes[id_remitente] = []
        
        buffer_mensajes[id_remitente].append({
            "texto": texto_usuario,
            "id_mensaje": id_mensaje
        })

        if id_remitente in timers_debounce and not timers_debounce[id_remitente].done():
            timers_debounce[id_remitente].cancel()

        async def timer_task():
            # Utiliza la variable leída dinámicamente del .env
            await asyncio.sleep(TIEMPO_ESPERA_MENSAJE)
            await procesar_bloque_mensajes(id_remitente, comercio_id, instance_name, remote_jid)

        timers_debounce[id_remitente] = asyncio.create_task(timer_task())
        
        return {"status": "en_espera"}

    except Exception as e:
        print(f"Error procesando: {e}")
        return {"status": "error"}