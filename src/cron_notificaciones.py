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
    """Busca ventas/citas de hace 14 días y pide feedback."""
    print("\n[CRON] 🔍 Buscando ventas de hace 14 días para Post-Venta...")
    hace_14_dias = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    
    try:
        ventas = supabase.table("turnos_clientes") \
            .select("*") \
            .eq("estado", "completado") \
            .eq("postventa_enviado", False) \
            .ilike("fecha_turno", f"%{hace_14_dias}%") \
            .execute()
            
        if not ventas.data:
            print("[CRON] No hay post-ventas pendientes para hoy.")
            return

        for venta in ventas.data:
            estado_pv = venta.get("estado_postventa", "ok")
            nombre = venta.get("cliente_nombre", "")
            
            # 🚨 Si el equipo está en revisión por garantía, LO SALTAMOS
            if estado_pv == "en_revision":
                print(f"⚠️ Saltando a {nombre}: Equipo en garantía/revisión.")
                # Lo marcamos como enviado para que no quede en bucle infinito
                supabase.table("turnos_clientes").update({"postventa_enviado": True}).eq("id", venta["id"]).execute()
                continue

            instancia = obtener_instancia_comercio(venta["comercio_id"])
            if not instancia: continue

            # Generar mensaje según el contexto
            if estado_pv == "ok":
                mensaje = (
                    f"¡Hola {nombre}! 👋 Pasaron un par de semanas desde que te llevaste tu equipo. "
                    f"¿Cómo viene funcionando todo? ¡Esperamos que lo estés súper disfrutando! 😊📱"
                )
            elif estado_pv == "solucionado":
                mensaje = (
                    f"¡Hola {nombre}! 👋 Queríamos hacer un seguimiento de tu caso y saber si, "
                    f"luego del cambio/revisión que hicimos, el equipo quedó funcionando al 100%. "
                    f"¡Estamos a tu disposición! 🛠️✅"
                )

            if enviar_whatsapp(venta["telefono"], mensaje, instancia):
                supabase.table("turnos_clientes").update({"postventa_enviado": True}).eq("id", venta["id"]).execute()
                print(f"✅ Post-Venta enviado a {nombre} ({venta['telefono']})")

    except Exception as e:
        print(f"[CRON CRÍTICO] Error en post-venta: {e}")

