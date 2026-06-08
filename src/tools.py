import dateparser
import os
import requests
import json
from datetime import datetime
from database import supabase

def consultar_inventario(modelo_corregido: str, comercio_id: int, telefono_cliente: str) -> str:
    """Busca stock de un celular devolviendo todos sus atributos útiles y cantidad."""
    print(f"\n[Sistema] 🔍 Buscando en BD: {modelo_corregido} - Comercio: {comercio_id}")
    try:
        # CAMBIO ACA: Select * para traer absolutamente toda la fila (incluyendo accesorios, notas, etc)
        response = supabase.table("inventario_celulares") \
            .select("*") \
            .eq("comercio_id", int(comercio_id)) \
            .ilike("modelo", f"%{modelo_corregido}%") \
            .gt("stock", 0) \
            .execute()
            
        datos = response.data
        if not datos:
            return f"No hay stock disponible para la marca/modelo: {modelo_corregido}."
        
        return json.dumps(datos)
        
    except Exception as e:
        print(f"[Falla Crítica] ❌ Error en inventario: {e}")
        solicitar_asistencia_humana("Falla técnica del servidor al intentar consultar el inventario.", telefono_cliente, comercio_id)
        return "SISTEMA_DELAY: Hubo una interrupción temporal de conexión. Ya notificamos automáticamente a un asesor humano. Pídele disculpas al cliente de forma muy amable, sin tecnicismos, y dile que un compañero del local continuará el chat en instantes."

def consultar_horarios(comercio_id: int, telefono_cliente: str) -> str:
    """Consulta los horarios de atención de la tienda."""
    print(f"\n[Sistema] 🕐 Consultando horarios de atención - Comercio: {comercio_id}")
    try:
        response = supabase.table("horarios_atencion") \
            .select("*") \
            .eq("comercio_id", int(comercio_id)) \
            .eq("activo", True) \
            .order("id") \
            .execute()
        datos = response.data
        if not datos:
            return "No hay horarios de atención configurados en este momento."
        
        resultado = "Nuestros horarios:\n"
        for h in datos:
            apertura = h['hora_apertura'][:5] if h['hora_apertura'] else '—'
            cierre = h['hora_cierre'][:5] if h['hora_cierre'] else '—'
            resultado += f"- {h['dia_semana']}: de {apertura} a {cierre}\n"
        return resultado
    except Exception as e:
        print(f"[Falla Crítica] ❌ Error en horarios: {e}")
        solicitar_asistencia_humana("Falla técnica del servidor al intentar consultar los horarios.", telefono_cliente, comercio_id)
        return "SISTEMA_DELAY: No se pudieron leer los horarios. Ya notificamos automáticamente a un asesor humano. Pídele disculpas al cliente de forma muy cercana y dile que un compañero del local lo atenderá enseguida."

def agendar_cita(cliente_nombre: str, telefono: str, fecha_turno: str, celular_id: int = None, comercio_id: int = None) -> str:
    """Agenda o modifica una cita reservando el stock numérico del inventario."""
    print(f"\n[Sistema] 📅 Ejecutando agendar_cita: {cliente_nombre} ({telefono}) con fecha {fecha_turno}")
    try:
        try:
            celular_id = int(celular_id) if celular_id and int(celular_id) > 0 else None
            celulares_ids_nuevos = [celular_id] if celular_id else []
        except ValueError:
            celulares_ids_nuevos = []

        # CAMBIO ACA: Priorizamos el formato estricto ISO que le pedimos a Gemini. Si falla, cae en dateparser forzando DMY (Día/Mes/Año)
        try:
            fecha_objetivo = datetime.strptime(fecha_turno, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            settings = {'PREFER_DATES_FROM': 'future', 'TIMEZONE': 'America/Argentina/Buenos_Aires', 'DATE_ORDER': 'DMY'}
            fecha_objetivo = dateparser.parse(fecha_turno, languages=['es'], settings=settings)

        if not fecha_objetivo:
            return f"Error técnico: No se pudo formatear la fecha '{fecha_turno}'."

        fecha_iso = fecha_objetivo.strftime("%Y-%m-%d %H:%M:%S")

        turno_existente = supabase.table("turnos_clientes") \
            .select("*") \
            .eq("telefono", telefono) \
            .eq("comercio_id", int(comercio_id)) \
            .execute()

        if turno_existente.data:
            turno_viejo = turno_existente.data[0]
            viejos_ids = turno_viejo.get("celulares_ids") or []

            ids_a_liberar = [vid for vid in viejos_ids if vid not in celulares_ids_nuevos]
            ids_a_reservar = [nid for nid in celulares_ids_nuevos if nid not in viejos_ids]

            # 1. Devolver el stock de los equipos que ya no quiere
            for vid in ids_a_liberar:
                item = supabase.table("inventario_celulares").select("stock").eq("id", vid).execute()
                if item.data:
                    nuevo_stock = item.data[0]["stock"] + 1
                    supabase.table("inventario_celulares").update({"stock": nuevo_stock}).eq("id", vid).execute()
                
            # 2. Restar el stock de los nuevos equipos reservados
            for nid in ids_a_reservar:
                item = supabase.table("inventario_celulares").select("stock").eq("id", nid).execute()
                if item.data and item.data[0]["stock"] > 0:
                    nuevo_stock = item.data[0]["stock"] - 1
                    supabase.table("inventario_celulares").update({"stock": nuevo_stock}).eq("id", nid).execute()
                else:
                    return "Lamentablemente, justo acaban de reservar la última unidad de ese equipo específico. ¿Podrías ofrecerle otra alternativa?"
                        
            update_payload = {
                "cliente_nombre": cliente_nombre,
                "fecha_turno": fecha_iso,
                "celulares_ids": celulares_ids_nuevos
            }
            supabase.table("turnos_clientes").update(update_payload).eq("id", turno_viejo["id"]).execute()

            return f"¡Cita modificada! Quedaste agendado para el {fecha_objetivo.strftime('%A %d/%m a las %H:%M')}."

        else:
            # 1. Restar el stock del equipo reservado (Nuevo turno)
            for nid in celulares_ids_nuevos:
                item = supabase.table("inventario_celulares").select("stock").eq("id", nid).execute()
                if item.data and item.data[0]["stock"] > 0:
                    nuevo_stock = item.data[0]["stock"] - 1
                    supabase.table("inventario_celulares").update({"stock": nuevo_stock}).eq("id", nid).execute()
                else:
                    return "Lamentablemente, no queda stock disponible para reservar ese equipo específico."

            insert_payload = {
                "comercio_id": int(comercio_id),
                "celulares_ids": celulares_ids_nuevos,
                "cliente_nombre": cliente_nombre,
                "telefono": telefono,
                "fecha_turno": fecha_iso,
                "tipo_registro": "cita",
                "estado": "pendiente"
            }
            supabase.table("turnos_clientes").insert(insert_payload).execute()

            return f"¡Perfecto! Tu cita quedó agendada para el {fecha_objetivo.strftime('%d/%m a las %H:%M')} hs. ¡Te esperamos!"

    except Exception as e:
        print(f"[Falla Crítica] ❌ Error en agendar_cita: {e}")
        solicitar_asistencia_humana(f"Falla al intentar agendar un turno para {cliente_nombre}.", telefono, comercio_id)
        return "SISTEMA_DELAY: Hubo un problema al guardar el turno. Notificamos al local para confirmar la cita a mano."
    
def obtener_configuracion_comercio(comercio_id: int) -> dict:
    """Trae las políticas personalizadas del comercio desde la base de datos."""
    try:
        response = supabase.table("configuracion_comercios") \
            .select("*") \
            .eq("comercio_id", int(comercio_id)) \
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
        config_res = supabase.table("configuracion_comercios").select("telefono_dueno").eq("comercio_id", int(comercio_id)).execute()
        comercio_res = supabase.table("comercios").select("evolution_instance").eq("id", int(comercio_id)).execute()
        
        if not config_res.data or not config_res.data[0].get("telefono_dueno"):
            return "No se pudo alertar al dueño porque no tiene configurado un teléfono de soporte."
        
        if not comercio_res.data:
            return "Error: No se encontró la instancia de WhatsApp de este comercio."

        telefono_dueno = config_res.data[0]["telefono_dueno"]
        instance_name = comercio_res.data[0]["evolution_instance"]
        cliente_numero_limpio = telefono_cliente.split("@")[0]

        texto_alerta = (
            f"🚨 *Intervención requerida*\n\n"
            f"📱 *Número del Cliente:* {cliente_numero_limpio}\n"
            f"📌 *Motivo:* {motivo}"
        )

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
            return "Éxito: El dueño ha sido notificado."
        else:
            return "No se pudo enviar la notificación por problemas de API."

    except Exception as e:
        return f"Error interno al procesar la asistencia: {str(e)}"