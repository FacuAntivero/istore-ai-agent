import sys
import os
import time
import random
sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from google.genai import errors
from supabase import create_client
import requests
import json
import asyncio
import mercadopago
from dotenv import load_dotenv
from datetime import datetime, timedelta
from agent import iniciar_agente

load_dotenv()

app = FastAPI(title="iStore AI Webhook")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
        # Pedimos el dato fresco de DB
        res_comercio = supabase.table("comercios").select("mensajes_disponibles", "plan_actual", "telefono_dueno").eq("id", comercio_id).execute()
        if res_comercio.data:
            comercio_db = res_comercio.data[0]
            saldo = comercio_db.get("mensajes_disponibles", 0)
            tel_dueno = comercio_db.get("telefono_dueno")

            if saldo <= 0:
                print(f"🚫 [SaaS] Comercio {comercio_id} no tiene mensajes disponibles. Bot frenado.")
                buffer_mensajes.pop(id_remitente_limpio, None)
                return
            
            # Descontamos el saldo
            nuevo_saldo = saldo - 1
            supabase.table("comercios").update({"mensajes_disponibles": nuevo_saldo}).eq("id", comercio_id).execute()
            print(f"📉 [SaaS] Crédito consumido para {comercio_id}. Restantes: {nuevo_saldo}")

            # Alertas de consumo dinámicas (Asumiendo 3500 max, ajustalo a tu plan real)
            plan_actual = str(comercio_db.get("plan_actual", "")).lower()
            limite_plan = 3500 if "negocio" in plan_actual else 1000 
            
            umbral_80 = int(limite_plan * 0.20)
            umbral_95 = int(limite_plan * 0.05)
            
            if nuevo_saldo == umbral_80:
                alertar_consumo_dueno(tel_dueno, 80, nuevo_saldo, instance_name)
            elif nuevo_saldo == umbral_95:
                alertar_consumo_dueno(tel_dueno, 95, nuevo_saldo, instance_name)

    except Exception as e:
        print(f"❌ [SaaS] Error verificando suscripción: {e}")
    # ---------------------------------------------------------------

    mensajes = buffer_mensajes.pop(id_remitente_limpio)
    texto_completo = ". ".join([m["texto"] for m in mensajes])
    ultimo_id_mensaje = mensajes[-1]["id_mensaje"]

    print(f"\n[Procesando Bloque 📦] {id_remitente_limpio}: {texto_completo}")

    session_key = f"{comercio_id}_{id_remitente_limpio}"
    if session_key not in sesiones_chat:
        sesiones_chat[session_key] = iniciar_agente(comercio_id, numero_destino)
    chat_actual = sesiones_chat[session_key]

    try:
        respuesta = chat_actual.send_message(texto_completo)
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

        # Lógica original intacta de alertas al dueño
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
        if key.get("fromMe", False): return {"status": "ignorado"}

        remote_jid = key.get("remoteJid", "")
        if remote_jid.endswith("@g.us") or remote_jid == "status@broadcast":
            return {"status": "ignorado"}

        # Resolvemos número destino
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
        id_mensaje = key.get("id", "")

        if id_mensaje in mensajes_procesados: return {"status": "ignorado"}
        mensajes_procesados.add(id_mensaje)

        # 🛡️ 1. CHECK ANTI-TROLL
        if es_troll(id_remitente_limpio):
            print(f"🚷 [Anti-Troll] Bloqueando ráfaga de mensajes de {id_remitente_limpio}")
            return {"status": "bloqueado_rate_limit"}

        # 🛑 2. EXTRAER Y FILTRAR TEXTO/MULTIMEDIA
        texto_usuario, tipo_mensaje = extraer_texto_y_tipo(msg_content)

        if tipo_mensaje in ["sticker", "image"]:
            print("🖼️ Mensaje visual ignorado.")
            return {"status": "multimedia_ignorado"}
            
        elif tipo_mensaje == "audio":
            permite_audio = comercio.get("permitir_audios", False)
            if not permite_audio:
                enviar_mensaje_whatsapp(numero_destino, "Perdoná, por el momento solo puedo leer textos. Por favor, escribime tu consulta 😊", instance_name, id_mensaje, key.get("remoteJid"))
                return {"status": "audio_rechazado"}
            else:
                enviar_mensaje_whatsapp(numero_destino, "Estoy aprendiendo a escuchar audios, pronto podré responderte. ¡Escribime porfa!", instance_name)
                return {"status": "audio_en_desarrollo"}

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
        tipo_plan = body.get("tipo_plan", "pro").lower() # Por defecto 'pro' si no envían nada
        
        if not comercio_id:
            raise HTTPException(status_code=400, detail="Falta el comercio_id")

        if not mp_access_token:
            raise HTTPException(status_code=500, detail="SDK de MercadoPago no configurado en el servidor")

        # 1. Definimos los precios y títulos dinámicos
        planes = {
            "basico": {"precio": 15000.00, "titulo": "iStore Admin - Plan Básico"},
            "pro": {"precio": 35000.00, "titulo": "iStore Admin - Plan Pro"},
            "premium": {"precio": 85000.00, "titulo": "iStore Admin - Plan Premium"}
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
            # 2. EL TRUCO: Guardamos el ID y el Plan separados por un "|" (Ej: "5|premium")
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
                
                # 3. Extraemos el ID y el plan del texto (Ej: "5|premium" -> id: 5, plan: premium)
                partes = external_reference.split("|")
                comercio_id = partes[0]
                tipo_plan = partes[1] if len(partes) > 1 else "pro"
                
                # 4. Asignamos los mensajes según el plan comprado
                mensajes_por_plan = {
                    "basico": 1000,
                    "pro": 3500,
                    "premium": 10000
                }
                mensajes_a_cargar = mensajes_por_plan.get(tipo_plan, 3500)
                
                # 5. Calculamos la fecha de vencimiento (30 días exactos desde hoy)
                fecha_vencimiento = (datetime.utcnow() + timedelta(days=30)).isoformat()
                
                # 6. Actualizamos toda la info del usuario en la base de datos
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