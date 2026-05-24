import dateparser
import os
import requests
from database import supabase

def consultar_inventario(modelo_corregido: str, comercio_id: str) -> str:
    """Busca stock de un celular."""
    print(f"\n[Sistema] 🔍 Buscando en BD: {modelo_corregido} - Comercio: {comercio_id}")
    try:
        response = supabase.table("inventario_celulares") \
            .select("*") \
            .eq("comercio_id", comercio_id) \
            .ilike("modelo", f"%{modelo_corregido}%") \
            .eq("estado_venta", "disponible") \
            .execute()
        datos = response.data
        if not datos:
            return f"No hay stock disponible para: {modelo_corregido}."
        return str(datos)
    except Exception as e:
        return f"Error al consultar la BD: {str(e)}"

def consultar_horarios(comercio_id: str) -> str:
    """Consulta los horarios de atención de la tienda."""
    print(f"\n[Sistema] 🕐 Consultando horarios de atención - Comercio: {comercio_id}")
    try:
        response = supabase.table("horarios_atencion") \
            .select("*") \
            .eq("comercio_id", comercio_id) \
            .eq("activo", True) \
            .order("id") \
            .execute()
        datos = response.data
        if not datos:
            return "No hay horarios de atención configurados."
        resultado = "Nuestros horarios:\n"
        for h in datos:
            apertura = h['hora_apertura'][:5] if h['hora_apertura'] else '—'
            cierre = h['hora_cierre'][:5] if h['hora_cierre'] else '—'
            # Le sacamos el asterisco rebelde
            resultado += f"- {h['dia_semana']}: de {apertura} a {cierre}\n"
        return resultado
    except Exception as e:
        return f"Error al consultar horarios: {str(e)}"

def agendar_cita(cliente_nombre: str, telefono: str, fecha_turno: str, celular_id: int, comercio_id: str) -> str:
    """Agenda o modifica una cita (si el teléfono ya tiene un turno previo)."""
    print(f"\n[Sistema] 📅 Procesando cita: {cliente_nombre} ({telefono}) - Fecha: '{fecha_turno}'")
    try:
        settings = {'PREFER_DATES_FROM': 'future', 'TIMEZONE': 'America/Argentina/Buenos_Aires'}
        fecha_objetivo = dateparser.parse(fecha_turno, languages=['es'], settings=settings)

        if not fecha_objetivo:
            return f"No pude entender bien la fecha '{fecha_turno}'. Por favor decime el día y la hora exactos."

        fecha_iso = fecha_objetivo.strftime("%Y-%m-%d %H:%M:%S")

        # 1. Chequeamos si este cliente (por teléfono) ya tiene un turno en este comercio
        turno_existente = supabase.table("turnos_clientes") \
            .select("*") \
            .eq("telefono", telefono) \
            .eq("comercio_id", comercio_id) \
            .execute()

        if turno_existente.data:
            # 🔄 ES UNA MODIFICACIÓN DE TURNO
            turno_viejo = turno_existente.data[0]
            viejo_celular_id = turno_viejo.get("celular_id")

            # Si el cliente cambió de celular elegido
            if viejo_celular_id and viejo_celular_id != celular_id:
                print(f"[Sistema] 🔄 Liberando celular anterior: ID {viejo_celular_id}")
                supabase.table("inventario_celulares").update({"estado_venta": "disponible"}).eq("id", viejo_celular_id).execute()
                
                if celular_id:
                    print(f"[Sistema] 🔒 Reservando nuevo celular: ID {celular_id}")
                    supabase.table("inventario_celulares").update({"estado_venta": "pendiente"}).eq("id", celular_id).execute()
                        
            # Actualizamos la cita existente con los nuevos datos
            update_payload = {
                "cliente_nombre": cliente_nombre,
                "fecha_turno": fecha_iso,
                "celular_id": celular_id
            }
            supabase.table("turnos_clientes").update(update_payload).eq("id", turno_viejo["id"]).execute()
            print(f"[Sistema] ✏️ Turno ID {turno_viejo['id']} actualizado con éxito.")

            return f"¡Cita modificada! Agendado para el {fecha_objetivo.strftime('%A %d/%m a las %H:%M')}."

        else:
            # 🆕 ES UN TURNO NUEVO
            if celular_id:
                supabase.table("inventario_celulares").update({"estado_venta": "pendiente"}).eq("id", celular_id).execute()

            insert_payload = {
                "comercio_id": comercio_id,
                "celular_id": celular_id,
                "cliente_nombre": cliente_nombre,
                "telefono": telefono,
                "fecha_turno": fecha_iso
            }
            supabase.table("turnos_clientes").insert(insert_payload).execute()
            print(f"[Sistema] 📅 Turno nuevo insertado con éxito.")

            return f"¡Cita agendada! Te esperamos el {fecha_objetivo.strftime('%A %d/%m a las %H:%M')}."

    except Exception as e:
        print(f"[Sistema] ❌ Error en agendar_cita: {str(e)}")
        return f"Error al procesar la cita: {str(e)}"
    
def obtener_configuracion_comercio(comercio_id: int) -> dict:
    """Trae las políticas personalizadas del comercio desde la base de datos."""
    try:
        response = supabase.table("configuracion_comercios") \
            .select("*") \
            .eq("comercio_id", comercio_id) \
            .execute()
        if response.data:
            return response.data[0]
    except Exception as e:
        print(f"[Sistema] ❌ Error leyendo configuración del comercio: {e}")
    
    return {
        "metodos_pago": "Efectivo",
        "recargo_usdt": "A consultar",
        "tipo_cambio_efectivo": "A coordinar el día de la cita",
        "permuta_minima": "No especificado",
        "politica_garantia": "Garantía estándar de la tienda",
        "telefono_dueno": None
    }

def solicitar_asistencia_humana(motivo: str, telefono_cliente: str, comercio_id: int) -> str:
    """Envía un WhatsApp de alerta al dueño del comercio notificándole que se requiere su atención."""
    print(f"\n[Sistema] 🚨 Solicitando asistencia humana para Comercio ID: {comercio_id}. Motivo: {motivo}")
    try:
        # 1. Obtenemos los datos de configuración de la tienda y su instancia de Evolution
        config_res = supabase.table("configuracion_comercios").select("telefono_dueno").eq("comercio_id", comercio_id).execute()
        comercio_res = supabase.table("comercios").select("evolution_instance").eq("id", comercio_id).execute()
        
        if not config_res.data or not config_res.data[0].get("telefono_dueno"):
            return "No se pudo alertar al dueño porque no tiene configurado un teléfono de soporte en el panel."
        
        if not comercio_res.data:
            return "Error: No se encontró la instancia de WhatsApp de este comercio."

        telefono_dueno = config_res.data[0]["telefono_dueno"]
        instance_name = comercio_res.data[0]["evolution_instance"]
        
        # Limpiamos el JID del cliente para dejar solo el número
        cliente_numero_limpio = telefono_cliente.split("@")[0]

        # 2. Diseñamos el mensaje de alerta para el dueño (Formato directo y conciso)
        texto_alerta = (
            f"🚨 *Intervención requerida*\n\n"
            f"📱 *Número:* {cliente_numero_limpio}\n"
            f"📌 *Motivo:* {motivo}"
        )

        # 3. Enviamos el mensaje usando la Evolution API configurada
        EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "https://evolution-api-production-4b88.up.railway.app")
        API_KEY = os.getenv("EVOLUTION_API_KEY", "74BD7CFB-C38A-4143-833A-FCEA92FBBA21")
        
        url = f"{EVOLUTION_API_URL}/message/sendText/{instance_name}?checkNumber=false"
        headers = {
            "apikey": API_KEY,
            "Content-Type": "application/json"
        }
        payload = {
            "number": telefono_dueno,
            "text": texto_alerta,
            "checkNumber": False
        }
        
        res = requests.post(url, headers=headers, json=payload)
        if res.status_code in [200, 201]:
            print(f"[Sistema] ✅ WhatsApp de alerta enviado con éxito al dueño ({telefono_dueno})")
            return "Éxito: El dueño ha sido notificado por WhatsApp y se unirá al chat a la brevedad."
        else:
            print(f"[Sistema] ❌ Error Evolution API al alertar al dueño: {res.text}")
            return "No se pudo enviar la notificación debido a un problema con el gateway de WhatsApp."

    except Exception as e:
        print(f"[Sistema] ❌ Error general en solicitar_asistencia_humana: {e}")
        return f"Error interno al procesar la asistencia: {str(e)}"