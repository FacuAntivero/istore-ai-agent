import os
import requests
from datetime import datetime, timedelta
from supabase import create_client
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "https://evolution-api-production-4b88.up.railway.app")
API_KEY = os.getenv("EVOLUTION_API_KEY", "74BD7CFB-C38A-4143-833A-FCEA92FBBA21")

def enviar_whatsapp(numero, texto, instance_name):
    """Envía un mensaje usando la API de Evolution."""
    if not numero.endswith("@s.whatsapp.net"):
        numero = f"{numero}@s.whatsapp.net"
        
    url = f"{EVOLUTION_API_URL}/message/sendText/{instance_name}?checkNumber=false"
    headers = {"apikey": API_KEY, "Content-Type": "application/json"}
    payload = {"number": numero, "text": texto, "checkNumber": False}
    
    try:
        res = requests.post(url, headers=headers, json=payload)
        return res.status_code in [200, 201]
    except Exception as e:
        print(f"❌ Error enviando WS a {numero}: {e}")
        return False

def obtener_instancia_comercio(comercio_id):
    """Busca el nombre de la instancia de WhatsApp de un comercio."""
    try:
        res = supabase.table("comercios").select("evolution_instance").eq("id", comercio_id).execute()
        if res.data:
            return res.data[0]["evolution_instance"]
    except Exception as e:
        print(f"❌ Error buscando instancia para comercio {comercio_id}: {e}")
    return None

def procesar_recordatorios():
    """Busca citas para MAÑANA y envía un recordatorio."""
    print("\n[CRON] 🔍 Buscando recordatorios para mañana...")
    manana = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    
    try:
        turnos = supabase.table("turnos_clientes") \
            .select("*") \
            .eq("tipo_registro", "cita") \
            .eq("estado", "pendiente") \
            .eq("recordatorio_enviado", False) \
            .ilike("fecha_turno", f"%{manana}%") \
            .execute()
            
        if not turnos.data:
            print("[CRON] No hay recordatorios pendientes.")
            return

        for turno in turnos.data:
            instancia = obtener_instancia_comercio(turno["comercio_id"])
            if not instancia: continue

            # Formatear la hora de la cita
            hora_cita = datetime.strptime(turno["fecha_turno"], "%Y-%m-%d %H:%M:%S").strftime("%H:%M")
            nombre = turno.get("cliente_nombre", "Hola")

            mensaje = (
                f"¡Hola {nombre}! 👋 Te escribimos para recordarte que mañana a las *{hora_cita} hs* "
                f"tenés tu cita reservada con nosotros.\n\n"
                f"¿Nos confirmás tu asistencia? ¡Te esperamos! 📱"
            )

            if enviar_whatsapp(turno["telefono"], mensaje, instancia):
                supabase.table("turnos_clientes").update({"recordatorio_enviado": True}).eq("id", turno["id"]).execute()
                print(f"✅ Recordatorio enviado a {nombre} ({turno['telefono']})")

    except Exception as e:
        print(f"[CRON CRÍTICO] Error en recordatorios: {e}")

def procesar_postventa():
    """
    Busca los mensajes de fidelización (CRM) en la cola programados para HOY 
    y los dispara según la estrategia elegida por el comercio.
    """
    print("\n[CRON] 🔍 Buscando mensajes de Post-Venta programados para hoy...")
    fecha_hoy = datetime.now().strftime("%Y-%m-%d")
    
    try:
        # Traemos todos los envíos de la nueva tabla que toquen hoy y estén pendientes
        mensajes = supabase.table("cola_mensajes_postventa") \
            .select("*") \
            .eq("fecha_envio", fecha_hoy) \
            .eq("estado", "pendiente") \
            .execute()
            
        if not mensajes.data:
            print("[CRON] No hay post-ventas programadas para hoy.")
            return

        for msg in mensajes.data:
            instancia = obtener_instancia_comercio(msg["comercio_id"])
            if not instancia: 
                print(f"⚠️ Saltando post-venta ID {msg['id']}: No se encontró instancia de WhatsApp.")
                continue

            nombre = msg.get("cliente_nombre", "")
            equipos = msg.get("equipos_detalle", "equipo")
            estrategia = msg.get("estrategia", "satisfaccion")

            # Redactar el mensaje dinámicamente según la estrategia
            if estrategia == "satisfaccion":
                texto_ws = (
                    f"¡Hola {nombre}! 👋 Te escribimos del local. Hace unos días te llevaste tu {equipos}. "
                    f"Queríamos saber si pudiste pasar toda tu info correctamente y cómo viene funcionando todo. "
                    f"¡Cualquier duda o consulta avisanos! 😊📱"
                )
            elif estrategia == "upselling":
                texto_ws = (
                    f"¡Hola {nombre}! 👋 Esperamos que estés disfrutando a pleno tu {equipos}. "
                    f"Te queríamos contar que nos entraron accesorios nuevos y vidrios templados específicos para tu modelo. "
                    f"¡Por haber comprado el celu tenés un descuento especial en el mostrador! Te esperamos 🛒"
                )
            elif estrategia == "resena":
                texto_ws = (
                    f"¡Hola {nombre}! 👋 ¿Cómo viene ese {equipos}? "
                    f"Si estás conforme con el equipo y nuestra atención, nos ayudarías un montón dejándonos una reseña en Google Maps. "
                    f"¡Gracias por elegirnos y confiar en nosotros! ⭐"
                )
            else:
                texto_ws = (
                    f"¡Hola {nombre}! 👋 Queríamos hacerte un seguimiento rápido para saber cómo estás "
                    f"con tu {equipos}. ¡Estamos a tu disposición por cualquier cosa! ✅"
                )

            # Enviar el WhatsApp y actualizar el estado
            if enviar_whatsapp(msg["telefono"], texto_ws, instancia):
                supabase.table("cola_mensajes_postventa").update({"estado": "enviado"}).eq("id", msg["id"]).execute()
                print(f"✅ Post-Venta ({estrategia}) enviado a {nombre} ({msg['telefono']})")
            else:
                # Si falla el envío (ej: número inválido, instancia caída), marcamos como fallido para que el comercio lo vea en su panel
                supabase.table("cola_mensajes_postventa").update({"estado": "fallido"}).eq("id", msg["id"]).execute()
                print(f"❌ Falló el envío de Post-Venta a {nombre}")

    except Exception as e:
        print(f"[CRON CRÍTICO] Error procesando la cola de post-venta: {e}")