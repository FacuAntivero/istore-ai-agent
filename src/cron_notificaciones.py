import requests
from datetime import datetime
import config
from database import supabase

def enviar_whatsapp(numero, texto, instance_name):
    """Envía un mensaje usando la API de Evolution."""
    if not numero.endswith("@s.whatsapp.net"):
        numero = f"{numero}@s.whatsapp.net"

    url = f"{config.EVOLUTION_API_URL}/message/sendText/{instance_name}?checkNumber=false"
    headers = {"apikey": config.EVOLUTION_API_KEY, "Content-Type": "application/json"}
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

def procesar_postventa():
    """
    Busca los mensajes de fidelización (CRM) en la cola programados para HOY 
    y los dispara leyendo el mensaje que ya fue generado en la base de datos.
    """
    print("\n[CRON] 🔍 Buscando mensajes de Post-Venta programados para hoy...")
    fecha_hoy = datetime.now().strftime("%Y-%m-%d")
    
    try:
        # Usamos .lte (Less Than or Equal) para atrapar lo de hoy y lo atrasado
        mensajes = supabase.table("cola_mensajes_postventa") \
            .select("*") \
            .lte("fecha_envio", fecha_hoy) \
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
            telefono = msg["telefono"]
            
            # Tomamos el texto que ya redactó el servidor el día de la venta
            texto_ws = msg.get("mensaje_texto")
            
            # Fallback de seguridad por si hay algún registro viejo sin texto
            if not texto_ws:
                equipos = msg.get("equipos_detalle", "equipo")
                texto_ws = f"¡Hola {nombre}! Gracias por tu compra de {equipos} en nuestro local. ¡Estamos a disposición!"

            # Enviar el WhatsApp y actualizar el estado
            print(f"📱 Intentando enviar mensaje a {nombre} ({telefono})...")
            if enviar_whatsapp(telefono, texto_ws, instancia):
                supabase.table("cola_mensajes_postventa").update({"estado": "enviado"}).eq("id", msg["id"]).execute()
                print(f"✅ ¡ÉXITO! Post-Venta enviado y archivado para {nombre}.")
            else:
                supabase.table("cola_mensajes_postventa").update({"estado": "fallido"}).eq("id", msg["id"]).execute()
                print(f"❌ Falló el envío a {nombre}. Marcado como 'fallido'.")

    except Exception as e:
        print(f"[CRON CRÍTICO] Error procesando la cola de post-venta: {e}")