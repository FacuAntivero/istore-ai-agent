from fastapi import FastAPI, HTTPException, Request
from google.genai import errors
from supabase import create_client
import requests
import json
import os
from dotenv import load_dotenv

from agent import iniciar_agente

load_dotenv()

app = FastAPI(title="iStore AI Webhook")

# DICCIONARIO DE SESIONES
sesiones_chat = {}

# Configuraciones de Evolution API
EVOLUTION_API_URL = "http://localhost:8080"
API_KEY = "B6D711FCDE4D4FD5936544120E713976"
INSTANCE_NAME = "istoreBot"

# Supabase
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))


def guardar_contacto(lid, numero, nombre):
    """Guarda el mapeo lid → número real solo si no existe todavía"""
    try:
        # Primero verificamos si ya existe
        result = supabase.table("contactos").select("numero").eq("lid", lid).execute()
        if result.data:
            print(f"[Supabase] ℹ️ Contacto ya existe, no se sobreescribe: {lid}")
            return
        # Solo insertamos si no existe
        supabase.table("contactos").insert({
            "lid": lid,
            "numero": numero,
            "nombre": nombre
        }).execute()
        print(f"[Supabase] ✅ Contacto guardado: {lid} → {numero}")
    except Exception as e:
        print(f"[Supabase] ❌ Error guardando contacto: {e}")


def obtener_numero_real(lid):
    """Busca el número real asociado a un @lid en Supabase"""
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

    try:
        mensaje_data = datos.get("data", {})
        key = mensaje_data.get("key", {})

        # ANTI-BUCLE
        if key.get("fromMe", False):
            return {"status": "ignorado", "motivo": "mensaje enviado por el bot"}

        remote_jid = key.get("remoteJid", "")
        id_mensaje = key.get("id", "")
        sender = datos.get("sender", "")
        push_name = mensaje_data.get("pushName", "")

        if remote_jid.endswith("@s.whatsapp.net"):
            # Número real directo, no hay problema
            id_remitente = remote_jid

        elif remote_jid.endswith("@lid"):
            # Guardamos el mapeo lid → sender en Supabase (siempre actualizamos)
            guardar_contacto(remote_jid, sender, push_name)

            # Buscamos si ya teníamos un número guardado previamente
            numero_guardado = obtener_numero_real(remote_jid)
            if numero_guardado:
                id_remitente = numero_guardado
                print(f"[Sistema] ✅ Número resuelto desde Supabase: {id_remitente}")
            else:
                # Fallback al sender (que en este caso es tu número, no ideal)
                id_remitente = sender
                print(f"[Sistema] ⚠️ Usando sender como fallback: {id_remitente}")
        else:
            id_remitente = remote_jid

        msg_content = mensaje_data.get("message", {})
        if "conversation" in msg_content:
            texto_usuario = msg_content["conversation"]
        elif "extendedTextMessage" in msg_content:
            texto_usuario = msg_content["extendedTextMessage"]["text"]
        else:
            return {"status": "ignorado", "motivo": "no es un mensaje de texto"}

        print(f"\n[Red] 📩 Mensaje recibido de {id_remitente} ({push_name}): {texto_usuario}")

    except Exception as e:
        print(f"Error procesando el JSON de WhatsApp: {e}")
        return {"status": "error"}

    # Lógica de la IA
    if id_remitente not in sesiones_chat:
        print(f"[Sistema] 🆕 Creando nueva sesión para {id_remitente}")
        sesiones_chat[id_remitente] = iniciar_agente()

    chat_actual = sesiones_chat[id_remitente]

    try:
        respuesta = chat_actual.send_message(texto_usuario)
        texto_respuesta = respuesta.text
        print(f"[Agente] 🤖 Respondió: {texto_respuesta}")

        enviar_mensaje_whatsapp(id_remitente, texto_respuesta, id_mensaje)

        return {"status": "success"}

    except errors.APIError as e:
        print(f"[Error API] {e.message}")
        enviar_mensaje_whatsapp(id_remitente, "Disculpa, estoy procesando mucha información. ¿Me repites en unos segundos?", id_mensaje)
        return {"status": "error"}

    except Exception as e:
        print(f"[Error Inesperado] {str(e)}")
        raise HTTPException(status_code=500, detail="Ocurrió un error en el servidor")


def enviar_mensaje_whatsapp(numero_destino, texto, id_mensaje=None):
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

    if id_mensaje:
        payload["options"]["quoted"] = {
            "key": {
                "id": id_mensaje,
                "remoteJid": numero_destino,
                "fromMe": False
            }
        }

    respuesta = requests.post(url, headers=headers, json=payload)
    if respuesta.status_code in [200, 201]:
        print("✅ ¡Mensaje entregado a WhatsApp con éxito!")
    else:
        print(f"❌ Error al enviar mensaje: {respuesta.text}")