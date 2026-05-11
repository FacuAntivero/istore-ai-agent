import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from google.genai import errors
from supabase import create_client
import requests
import json
import os
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

# DICCIONARIO DE SESIONES
sesiones_chat = {}

# SET DE MENSAJES YA PROCESADOS (anti-duplicado)
mensajes_procesados = set()

# MENSAJES PENDIENTES (esperando el segundo webhook con número real)
mensajes_pendientes = {}

# Configuraciones de Evolution API
EVOLUTION_API_URL = "https://evolution-api-production-8717.up.railway.app"
API_KEY = "2977506C-B874-4465-AA51-F92A6F64DAD7"
INSTANCE_NAME = "istoreBot"

# Supabase
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))


def guardar_contacto(lid, numero, nombre):
    try:
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


def obtener_numero_real(lid):
    try:
        result = supabase.table("contactos").select("numero").eq("lid", lid).execute()
        if result.data:
            return result.data[0]["numero"]
    except Exception as e:
        print(f"[Supabase] ❌ Error buscando contacto: {e}")
    return None


@app.post("/webhook")
async def recibir_mensaje(request: Request):
    datos = await request.json()

    print("\n🔍 --- INSPECCIONANDO EL JSON COMPLETO --- 🔍")
    print(json.dumps(datos, indent=2))
    print("--------------------------------------------\n")

    evento = datos.get("event", "")

    # Capturar automáticamente el número real cuando el bot responde
    if evento == "send.message":
        try:
            msg_data = datos.get("data", {})
            context_info = msg_data.get("contextInfo", {})
            lid = context_info.get("participant", "")
            numero_real = msg_data.get("key", {}).get("remoteJid", "")
            if lid.endswith("@lid") and numero_real.endswith("@s.whatsapp.net"):
                guardar_contacto(lid, numero_real, "")
                print(f"[Sistema] 📝 Mapeado automático: {lid} → {numero_real}")
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

            # Si hay un mensaje pendiente con este mismo id, usamos ese texto
            if id_mensaje in mensajes_pendientes:
                pendiente = mensajes_pendientes.pop(id_mensaje)
                texto_usuario = pendiente["texto"]
                push_name = pendiente["push_name"]
                print(f"[Sistema] ✅ Procesando mensaje pendiente para {id_remitente}")

            # Anti-duplicado
            if id_mensaje in mensajes_procesados:
                print(f"[Sistema] 🔄 Mensaje duplicado ignorado: {id_mensaje}")
                return {"status": "ignorado", "motivo": "mensaje duplicado"}
            mensajes_procesados.add(id_mensaje)

        elif remote_jid.endswith("@lid"):
            numero_guardado = obtener_numero_real(remote_jid)
            if numero_guardado:
                id_remitente = numero_guardado
                print(f"[Sistema] ✅ Número resuelto desde Supabase: {id_remitente}")

                if id_mensaje in mensajes_procesados:
                    print(f"[Sistema] 🔄 Mensaje duplicado ignorado: {id_mensaje}")
                    return {"status": "ignorado", "motivo": "mensaje duplicado"}
                mensajes_procesados.add(id_mensaje)
            else:
                # Primera vez — guardamos pendiente y esperamos 2 segundos
                mensajes_pendientes[id_mensaje] = {
                    "texto": texto_usuario,
                    "push_name": push_name,
                    "lid": remote_jid
                }
                print(f"[Sistema] ⏳ Mensaje pendiente: {id_mensaje}, esperando 2 segundos...")

                await asyncio.sleep(2)

                # Si después de 2 segundos no fue procesado por el segundo webhook
                if id_mensaje in mensajes_pendientes:
                    pendiente = mensajes_pendientes.pop(id_mensaje)
                    id_remitente = sender
                    texto_usuario = pendiente["texto"]
                    push_name = pendiente["push_name"]
                    mensajes_procesados.add(id_mensaje)
                    print(f"[Sistema] ⚠️ Timeout, usando sender como fallback: {id_remitente}")
                else:
                    print(f"[Sistema] ✅ Ya procesado por segundo webhook")
                    return {"status": "procesado por segundo webhook"}
        else:
            id_remitente = remote_jid

        print(f"\n[Red] 📩 Mensaje recibido de {id_remitente} ({push_name}): {texto_usuario}")

    except Exception as e:
        print(f"Error procesando el JSON de WhatsApp: {e}")
        return {"status": "error"}

    if id_remitente not in sesiones_chat:
        print(f"[Sistema] 🆕 Creando nueva sesión para {id_remitente}")
        sesiones_chat[id_remitente] = iniciar_agente()

    chat_actual = sesiones_chat[id_remitente]

    try:
        respuesta = chat_actual.send_message(texto_usuario)
        texto_respuesta = respuesta.text
        print(f"[Agente] 🤖 Respondió: {texto_respuesta}")
        enviar_mensaje_whatsapp(id_remitente, texto_respuesta, id_mensaje, remote_jid)
        return {"status": "success"}

    except errors.APIError as e:
        print(f"[Error API] {e.message}")
        enviar_mensaje_whatsapp(id_remitente, "Disculpa, estoy procesando mucha información. ¿Me repites en unos segundos?", id_mensaje, remote_jid)
        return {"status": "error"}

    except Exception as e:
        print(f"[Error Inesperado] {str(e)}")
        raise HTTPException(status_code=500, detail="Ocurrió un error en el servidor")


def enviar_mensaje_whatsapp(numero_destino, texto, id_mensaje=None, remote_jid=None):
    url = f"{EVOLUTION_API_URL}/message/sendText/{INSTANCE_NAME}"
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
    if respuesta.status_code in [200, 201]:
        print("✅ ¡Mensaje entregado a WhatsApp con éxito!")
    else:
        print(f"❌ Error al enviar mensaje: {respuesta.text}")