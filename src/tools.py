import dateparser
import os
import requests
import json
from datetime import datetime, timedelta, timezone
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
    """Agenda o modifica una cita reservando el stock y programa el recordatorio exacto."""
    print(f"\n[Sistema] 📅 Ejecutando agendar_cita: {cliente_nombre} ({telefono}) con fecha {fecha_turno}")
    try:
        try:
            celular_id = int(celular_id) if celular_id and int(celular_id) > 0 else None
            celulares_ids_nuevos = [celular_id] if celular_id else []
        except ValueError:
            celulares_ids_nuevos = []

        try:
            fecha_objetivo = datetime.strptime(fecha_turno, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            settings = {'PREFER_DATES_FROM': 'future', 'TIMEZONE': 'America/Argentina/Buenos_Aires', 'DATE_ORDER': 'DMY'}
            fecha_objetivo = dateparser.parse(fecha_turno, languages=['es'], settings=settings)

        if not fecha_objetivo:
            return f"Error técnico: No se pudo formatear la fecha '{fecha_turno}'."

        fecha_iso = fecha_objetivo.strftime("%Y-%m-%d %H:%M:%S")

        # --- 🌟 NUEVO: Calculamos en qué momento exacto hay que mandar el recordatorio ---
        config_res = supabase.table("configuracion_comercios").select("minutos_anticipacion_recordatorio").eq("comercio_id", int(comercio_id)).execute()
        minutos_anticipacion = 30 # Valor por defecto
        if config_res.data and config_res.data[0].get("minutos_anticipacion_recordatorio") is not None:
            minutos_anticipacion = int(config_res.data[0]["minutos_anticipacion_recordatorio"])
            
        fecha_disparo_recordatorio = fecha_objetivo - timedelta(minutes=minutos_anticipacion)
        # ---------------------------------------------------------------------------------

        turno_existente = supabase.table("turnos_clientes") \
            .select("*") \
            .eq("telefono", telefono) \
            .eq("comercio_id", int(comercio_id)) \
            .execute()

        if turno_existente.data:
            turno_viejo = turno_existente.data[0]
            viejos_ids = turno_viejo.get("celulares_ids") or []
            turno_id = turno_viejo["id"]

            ids_a_liberar = [vid for vid in viejos_ids if vid not in celulares_ids_nuevos]
            ids_a_reservar = [nid for nid in celulares_ids_nuevos if nid not in viejos_ids]

            for vid in ids_a_liberar:
                item = supabase.table("inventario_celulares").select("stock").eq("id", vid).execute()
                if item.data:
                    nuevo_stock = item.data[0]["stock"] + 1
                    supabase.table("inventario_celulares").update({"stock": nuevo_stock}).eq("id", vid).execute()
                
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
            supabase.table("turnos_clientes").update(update_payload).eq("id", turno_id).execute()
            
            # 🌟 GATILLO: Reprogramamos el recordatorio porque cambió la fecha
            _programar_upstash_desde_tools("cita", turno_id, fecha_disparo_recordatorio)

            return f"¡Cita modificada! Quedaste agendado para el {fecha_objetivo.strftime('%A %d/%m a las %H:%M')}."

        else:
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
            res_insert = supabase.table("turnos_clientes").insert(insert_payload).execute()

            # 🌟 GATILLO: Programamos el recordatorio del nuevo turno
            if res_insert.data:
                nuevo_turno_id = res_insert.data[0]["id"]
                _programar_upstash_desde_tools("cita", nuevo_turno_id, fecha_disparo_recordatorio)

            return f"¡Perfecto! Tu cita quedó agendada para el {fecha_objetivo.strftime('%d/%m a las %H:%M')} hs. ¡Te esperamos!"

    except Exception as e:
        print(f"[Falla Crítica] ❌ Error en agendar_cita: {e}")
        solicitar_asistencia_humana(f"Falla al intentar agendar un turno para {cliente_nombre}.", telefono, comercio_id)
        return "SISTEMA_DELAY: Hubo un problema al guardar el turno. Notificamos al local para confirmar la cita a mano."

# --- AGREGAR ESTA FUNCIÓN AL FINAL DE TU ARCHIVO tools.py ---
def _programar_upstash_desde_tools(tipo_evento: str, registro_id: int, fecha_disparo: datetime):
    """
    Función auxiliar para agendar el recordatorio en QStash desde las tools del bot.
    Leyendo credenciales desde las variables de entorno de forma segura.
    """
    # 🌟 Traemos los datos de manera interna sin exponer los tokens
    QSTASH_TOKEN = os.getenv("QSTASH_TOKEN")
    URL_RAILWAY = os.getenv("URL_RAILWAY")
    
    # Control de seguridad por si falta alguna variable
    if not QSTASH_TOKEN or not URL_RAILWAY:
        print("❌ [QStash Tool] Error crítico: QSTASH_TOKEN o URL_RAILWAY no configurados en el entorno.")
        return
    
    url_qstash = f"https://qstash.upstash.io/v2/publish/{URL_RAILWAY}/api/webhooks/disparar-mensaje-programado"
    
    ahora_utc = datetime.now(timezone.utc)
    if fecha_disparo.tzinfo is None:
        fecha_disparo = fecha_disparo.replace(tzinfo=timezone.utc)
        
    diferencia = (fecha_disparo - ahora_utc).total_seconds()
    delay_segundos = max(int(diferencia), 5) # Si ya pasó la hora, que dispare en 5 segs

    headers = {
        "Authorization": f"Bearer {QSTASH_TOKEN}",
        "Content-Type": "application/json",
        "Upstash-Delay": f"{delay_segundos}s" 
    }
    
    payload = {"tipo": tipo_evento, "registro_id": registro_id}
    
    try:
        requests.post(url_qstash, headers=headers, json=payload)
        print(f"✅ [QStash Tool] Cita ID {registro_id} programada para avisar en {delay_segundos} segundos.")
    except Exception as e:
        print(f"❌ [QStash Tool] Error al programar: {e}")
            
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
    
def verificar_numero_excluido(telefono: str, comercio_id: str) -> bool:
    """
    Consulta en Supabase si el número está en la lista negra de ese comercio específico.
    """
    try:
        resultado = supabase.table("numeros_excluidos") \
            .select("id") \
            .eq("comercio_id", comercio_id) \
            .eq("telefono", telefono) \
            .execute()
        
        return len(resultado.data) > 0
    except Exception as e:
        print(f"❌ Error al verificar lista de exclusión: {e}")
        return False

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