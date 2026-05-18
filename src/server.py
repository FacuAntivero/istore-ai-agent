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

sesiones_chat = {}
mensajes_procesados = set()
mensajes_pendientes = {}

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
        print(f"[Supabase] ✅ Contacto guardado: {lid} → {numero}")
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

@app.post("/webhook")
async def recibir_mensaje(request: Request):
    datos = await request.json()

    instance_name = datos.get("instance")
    if not instance_name:
        return {"status": "ignorado"}

    comercio = obtener_comercio(instance_name)
    if not comercio:
        print(f"[Sistema] ⚠️ Instancia no registrada: {instance_name}")
        return {"status": "error", "motivo": "Comercio no encontrado"}

    comercio_id = comercio["id"]
    evento = datos.get("event", "")

    # 1. Atrapamos el mapeo real cuando WhatsApp lo revela
    if evento == "contacts.upsert":
        try:
            contactos_data = datos.get("data", [])
            # Evolution puede mandar lista o dict
            if isinstance(contactos_data, dict):
                contactos_data = [contactos_data]
                
            for c in contactos_data:
                c_id = c.get("id", "")
                c_lid = c.get("lid", "")
                c_name = c.get("pushName", "")
                if c_id.endswith("@s.whatsapp.net") and c_lid.endswith("@lid"):
                    guardar_contacto(c_lid, c_id, c_name, comercio_id)
        except Exception as e:
            print(f"[Sistema] Error en contacts.upsert: {e}")
        return {"status": "ok"}

    if evento == "send.message":
        return {"status": "ok"}

    try:
        mensaje_data = datos.get("data", {})
        key = mensaje_data.get("key", {})

        if key.get("fromMe", False):
            return {"status": "ignorado", "motivo": "mensaje del bot"}

        remote_jid = key.get("remoteJid", "")
        id_mensaje = key.get("id", "")
        push_name = mensaje_data.get("pushName", "")

        # Filtro de grupos
        if remote_jid.endswith("@g.us"):
            print(f"[Sistema] 👥 Mensaje de grupo ignorado")
            return {"status": "ignorado", "motivo": "mensaje de grupo"}

        msg_content = mensaje_data.get("message", {})
        if "conversation" in msg_content:
            texto_usuario = msg_content["conversation"]
        elif "extendedTextMessage" in msg_content:
            texto_usuario = msg_content["extendedTextMessage"]["text"]
        else:
            return {"status": "ignorado", "motivo": "no es texto"}

        # Procesar Chats Agendados
        if remote_jid.endswith("@s.whatsapp.net"):
            id_remitente = remote_jid
            if id_mensaje in mensajes_procesados:
                return {"status": "ignorado", "motivo": "duplicado"}
            mensajes_procesados.add(id_mensaje)

        # Procesar Chats NO Agendados (@lid)
        elif remote_jid.endswith("@lid"):
            numero_guardado = obtener_numero_real(remote_jid, comercio_id)
            if numero_guardado:
                id_remitente = numero_guardado
                if id_mensaje in mensajes_procesados:
                    return {"status": "ignorado", "motivo": "duplicado"}
                mensajes_procesados.add(id_mensaje)
            else:
                # Ponemos el mensaje en espera un par de segundos
                mensajes_pendientes[id_mensaje] = {
                    "texto": texto_usuario,
                    "push_name": push_name
                }
                print(f"[Sistema] ⏳ Esperando mapeo de WhatsApp para el nuevo cliente...")
                await asyncio.sleep(2.5)
                
                if id_mensaje in mensajes_pendientes:
                    pendiente = mensajes_pendientes.pop(id_mensaje)
                    numero_real = obtener_numero_real(remote_jid, comercio_id)
                    
                    if numero_real and numero_real != MI_NUMERO:
                        id_remitente = numero_real
                        print(f"[Sistema] ✅ ¡Mapeo completado!: {id_remitente}")
                    else:
                        print(f"[Sistema] ⚠️ No se resolvió. Usando ruta @lid directa.")
                        id_remitente = remote_jid # Evoluton V2 sabe entregar a @lid directos
                        
                    texto_usuario = pendiente["texto"]
                    push_name = pendiente["push_name"]
                    mensajes_procesados.add(id_mensaje)
                else:
                    return {"status": "procesado en paralelo"}
        else:
            id_remitente = remote_jid

        print(f"\n[Comercio: {instance_name}] 📩 Mensaje de {id_remitente} ({push_name}): {texto_usuario}")

    except Exception as e:
        print(f"Error procesando JSON: {e}")
        return {"status": "error"}

    session_key = f"{comercio_id}_{id_remitente}"

    if session_key not in sesiones_chat:
        print(f"[Sistema] 🆕 Nueva sesión: {session_key}")
        sesiones_chat[session_key] = iniciar_agente(comercio_id)

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
        raise HTTPException(status_code=500, detail="Error en servidor")

def enviar_mensaje_whatsapp(numero_destino, texto, instance_name, id_mensaje=None, remote_jid=None):
    url = f"{EVOLUTION_API_URL}/message/sendText/{instance_name}"
    headers = {
        "apikey": API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "number": numero_destino,
        "text": texto
    }
    if id_mensaje and remote_jid:
        payload["options"] = {
            "quoted": {
                "key": {
                    "id": id_mensaje,
                    "remoteJid": remote_jid,
                    "fromMe": False
                }
            }
        }
        
    respuesta = requests.post(url, headers=headers, json=payload)
    if respuesta.status_code in [200, 201]:
        print("✅ Mensaje entregado con éxito por V2")
    else:
        print(f"❌ Error al enviar: {respuesta.text}")