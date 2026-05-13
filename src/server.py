import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, HTTPException, Request
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

# DICCIONARIO DE SESIONES (Clave única: comercio_id + numero_usuario)
sesiones_chat = {}

# SET DE MENSAJES YA PROCESADOS (anti-duplicado)
mensajes_procesados = set()
mensajes_pendientes = {}

# Configuraciones de Evolution API
EVOLUTION_API_URL = "https://evolution-api-production-8717.up.railway.app"
API_KEY = "2977506C-B874-4465-AA51-F92A6F64DAD7"
# La instancia ahora se lee dinámicamente del Webhook

# Supabase
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# Caché de comercios para no consultar la base de datos en CADA mensaje
CACHE_COMERCIOS = {}

def obtener_comercio_id(instancia):
    if instancia in CACHE_COMERCIOS:
        return CACHE_COMERCIOS[instancia]
    try:
        res = supabase.table("comercios").select("id").eq("evolution_instance", instancia).execute()
        if res.data:
            comercio_id = res.data[0]["id"]
            CACHE_COMERCIOS[instancia] = comercio_id
            return comercio_id
    except Exception as e:
        print(f"[Supabase] ❌ Error buscando comercio: {e}")
    return None

TU_NUMERO = "5492494600615@s.whatsapp.net"

def guardar_contacto(lid, numero, nombre):
    try:
        # No guardar si el número es el tuyo propio
        if numero == TU_NUMERO:
            print(f"[Supabase] ⚠️ Ignorando mapeo con tu propio número")
            return
        result = supabase.table("contactos").select("numero").eq("lid", lid).execute()
        if result.data:
            print(f"[Supabase] ℹ️ Contacto ya existe, no se sobreescribe: {lid}")
            return
        supabase.table("contactos").insert({
            "lid": lid,
            "numero": numero,
            "nombre": nombre
        }).execute()
        print(f"[Supabase] ✅ Contacto guardado: {lid} → {numero}")
    except Exception as e:
        print(f"[Supabase] ❌ Error guardando contacto: {e}")

def obtener_numero_real(lid, comercio_id):
    try:
        result = supabase.table("contactos").select("numero").eq("lid", lid).eq("comercio_id", comercio_id).execute()
        if result.data:
            return result.data[0]["numero"]
    except Exception as e:
        pass
    return None

@app.post("/webhook")
async def recibir_mensaje(request: Request):
    datos = await request.json()

    # 1. Identificar a qué comercio pertenece este mensaje
    instance_name = datos.get("instance")
    if not instance_name:
        return {"status": "ignorado", "motivo": "Falta parámetro instance en webhook"}

    comercio_id = obtener_comercio_id(instance_name)
    if not comercio_id:
        print(f"[Sistema] ⚠️ Instancia ignorada o no registrada: {instance_name}")
        return {"status": "error", "motivo": "Comercio no encontrado"}

    evento = datos.get("event", "")

    # Capturar automáticamente el número real cuando el bot responde
    if evento == "send.message":
        try:
            msg_data = datos.get("data", {})
            context_info = msg_data.get("contextInfo", {})
            lid = context_info.get("participant", "")
            numero_real = msg_data.get("key", {}).get("remoteJid", "")
            if lid.endswith("@lid") and numero_real.endswith("@s.whatsapp.net"):
                guardar_contacto(lid, numero_real, "", comercio_id)
        except Exception as e:
            print(f"[Sistema] Error en mapeo automático: {e}")
        return {"status": "ok"}

    try:
        mensaje_data = datos.get("data", {})
        key = mensaje_data.get("key", {})

        if key.get("fromMe", False):
            return {"status": "ignorado", "motivo": "mensaje enviado por el bot"}

        remote_jid = key.get("remoteJid", "")
        id_mensaje = key.get("id", "")
        sender = datos.get("sender", "")
        push_name = mensaje_data.get("pushName", "")

        msg_content = mensaje_data.get("message", {})
        if "conversation" in msg_content:
            texto_usuario = msg_content["conversation"]
        elif "extendedTextMessage" in msg_content:
            texto_usuario = msg_content["extendedTextMessage"]["text"]
        else:
            return {"status": "ignorado", "motivo": "no es un mensaje de texto"}

        if remote_jid.endswith("@s.whatsapp.net"):
            id_remitente = remote_jid
            if id_mensaje in mensajes_pendientes:
                pendiente = mensajes_pendientes.pop(id_mensaje)
                texto_usuario = pendiente["texto"]
                push_name = pendiente["push_name"]

            if id_mensaje in mensajes_procesados:
                return {"status": "ignorado", "motivo": "mensaje duplicado"}
            mensajes_procesados.add(id_mensaje)

        elif remote_jid.endswith("@lid"):
            numero_guardado = obtener_numero_real(remote_jid, comercio_id)
            if numero_guardado:
                id_remitente = numero_guardado
                if id_mensaje in mensajes_procesados:
                    return {"status": "ignorado", "motivo": "mensaje duplicado"}
                mensajes_procesados.add(id_mensaje)
            else:
                mensajes_pendientes[id_mensaje] = {
                    "texto": texto_usuario,
                    "push_name": push_name,
                    "lid": remote_jid
                }
                await asyncio.sleep(2)
                if id_mensaje in mensajes_pendientes:
                    pendiente = mensajes_pendientes.pop(id_mensaje)
                    id_remitente = sender
                    texto_usuario = pendiente["texto"]
                    push_name = pendiente["push_name"]
                    mensajes_procesados.add(id_mensaje)
                else:
                    return {"status": "procesado por segundo webhook"}
        else:
            id_remitente = remote_jid

        print(f"\n[Comercio: {instance_name}] 📩 Mensaje de {id_remitente}: {texto_usuario}")

    except Exception as e:
        print(f"Error procesando JSON de WhatsApp: {e}")
        return {"status": "error"}

    # Generamos una ID de sesión combinada (Comercio + Remitente)
    session_key = f"{comercio_id}_{id_remitente}"

    if session_key not in sesiones_chat:
        print(f"[Sistema] 🆕 Creando agente Gemini para la sesión {session_key}")
        sesiones_chat[session_key] = iniciar_agente(comercio_id) # ¡AQUÍ PASAMOS EL COMERCIO!

    chat_actual = sesiones_chat[session_key]

    try:
        respuesta = chat_actual.send_message(texto_usuario)
        texto_respuesta = respuesta.text
        print(f"[Agente] 🤖 Respondió: {texto_respuesta}")
        enviar_mensaje_whatsapp(id_remitente, texto_respuesta, instance_name, id_mensaje, remote_jid)
        return {"status": "success"}

    except errors.APIError as e:
        print(f"[Error API] {e.message}")
        enviar_mensaje_whatsapp(id_remitente, "Disculpa, estoy procesando mucha información. ¿Me repites en unos segundos?", instance_name, id_mensaje, remote_jid)
        return {"status": "error"}

    except Exception as e:
        print(f"[Error Inesperado] {str(e)}")
        raise HTTPException(status_code=500, detail="Ocurrió un error en el servidor")


def enviar_mensaje_whatsapp(numero_destino, texto, instance_name, id_mensaje=None, remote_jid=None):
    # La URL ahora usa la instancia dinámica
    url = f"{EVOLUTION_API_URL}/message/sendText/{instance_name}"
    headers = {
        "apikey": API_KEY,
        "Content-Type": "application/json"
    }

    payload = {
        "number": numero_destino,
        "textMessage": {
            "text": texto
        },
        "options": {}
    }

    if id_mensaje and remote_jid:
        payload["options"]["quoted"] = {
            "key": {
                "id": id_mensaje,
                "remoteJid": remote_jid,
                "fromMe": False
            }
        }

    respuesta = requests.post(url, headers=headers, json=payload)
    if respuesta.status_code not in [200, 201]:
        print(f"❌ Error al enviar mensaje: {respuesta.text}")