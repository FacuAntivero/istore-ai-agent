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
        response = supabase.table("inventario_celulares") \
            .select("*") \
            .eq("comercio_id", int(comercio_id)) \
            .ilike("modelo", f"%{modelo_corregido}%") \
            .eq("estado_venta", "disponible") \
            .gt("stock", 0) \
            .execute()
            
        datos = response.data
        if not datos:
            return f"No hay stock disponible para la marca/modelo: {modelo_corregido}"
        
        return json.dumps(datos)
        
    except Exception as e:
        print(f"[Falla Crítica] ❌ Error en inventario: {e}")
        solicitar_asistencia_humana("Falla técnica del servidor al intentar consultar el inventario", telefono_cliente, comercio_id)
        return "SISTEMA_DELAY: Hubo una interrupción temporal de conexión, ya notificamos automáticamente a un asesor humano. Pide disculpas de forma muy amigable sin tecnicismos y dile que un compañero del local continuará el chat en instantes"

def obtener_nombre_comercio(comercio_id: int) -> str:
    """Obtiene el nombre real del comercio desde la tabla principal (comercios)."""
    try:
        response = supabase.table("comercios").select("nombre").eq("id", int(comercio_id)).execute()
        if response.data and response.data[0].get("nombre"):
            return response.data[0]["nombre"]
    except Exception as e:
        print(f"❌ Error al obtener el nombre del comercio: {e}")
    
    return "nuestra tienda"

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
            return "No hay horarios de atención configurados en este momento"
        
        resultado = "Nuestros horarios:\n"
        for h in datos:
            apertura = h['hora_apertura'][:5] if h['hora_apertura'] else '—'
            cierre = h['hora_cierre'][:5] if h['hora_cierre'] else '—'
            resultado += f"- {h['dia_semana']}: de {apertura} a {cierre}\n"
        return resultado.strip()
    except Exception as e:
        print(f"[Falla Crítica] ❌ Error en horarios: {e}")
        solicitar_asistencia_humana("Falla técnica del servidor al intentar consultar los horarios", telefono_cliente, comercio_id)
        return "SISTEMA_DELAY: No se pudieron leer los horarios, ya notificamos automáticamente a un asesor humano. Pide disculpas de forma muy cercana y dile que un compañero lo atenderá enseguida"

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
            return f"Error técnico: No se pudo formatear la fecha {fecha_turno}"

        fecha_iso = fecha_objetivo.strftime("%Y-%m-%d %H:%M:%S")

        # 🌟 NUEVO: Obtenemos configuración de cupos y recordatorios
        config_res = supabase.table("configuracion_comercios").select("minutos_anticipacion_recordatorio, max_citas_por_horario").eq("comercio_id", int(comercio_id)).execute()
        minutos_anticipacion = 30 
        max_citas = 1
        
        if config_res.data:
            conf = config_res.data[0]
            if conf.get("minutos_anticipacion_recordatorio") is not None:
                minutos_anticipacion = int(conf["minutos_anticipacion_recordatorio"])
            if conf.get("max_citas_por_horario") is not None:
                max_citas = int(conf["max_citas_por_horario"])
            
        fecha_disparo_recordatorio = fecha_objetivo - timedelta(minutes=minutos_anticipacion)

        # Buscamos si el cliente ya tiene un turno previo
        turno_existente = supabase.table("turnos_clientes") \
            .select("*") \
            .eq("telefono", telefono) \
            .eq("comercio_id", int(comercio_id)) \
            .execute()
            
        turno_viejo_id = turno_existente.data[0]["id"] if turno_existente.data else None

        # 🌟 NUEVO: VERIFICACIÓN DE CUPOS DISPONIBLES EN ESE HORARIO EXACTO
        query_cupos = supabase.table("turnos_clientes").select("id").eq("comercio_id", int(comercio_id)).eq("fecha_turno", fecha_iso).eq("estado", "pendiente")
        
        # Si ya tiene un turno y solo lo está modificando, no lo contamos como un ocupante extra para ese mismo horario
        if turno_viejo_id:
            query_cupos = query_cupos.neq("id", turno_viejo_id)
            
        turnos_en_horario = query_cupos.execute()
        
        if len(turnos_en_horario.data) >= max_citas:
            return f"El cupo para las {fecha_objetivo.strftime('%H:%M')} hs ya está lleno. Dile al cliente que ese horario se acaba de ocupar y ofrécele amablemente un horario cercano (anterior o posterior)."

        # --- A partir de aquí sigue el flujo normal de guardado ---
        if turno_existente.data:
            turno_viejo = turno_existente.data[0]
            viejos_ids = turno_viejo.get("celulares_ids") or []
            turno_id = turno_viejo["id"]

            ids_a_liberar = [vid for vid in viejos_ids if vid not in celulares_ids_nuevos]
            ids_a_reservar = [nid for nid in celulares_ids_nuevos if nid not in viejos_ids]

            # 🟢 LIBERAMOS STOCK (Vuelve a estar disponible)
            for vid in ids_a_liberar:
                item = supabase.table("inventario_celulares").select("stock").eq("id", vid).execute()
                if item.data:
                    nuevo_stock = item.data[0]["stock"] + 1
                    supabase.table("inventario_celulares").update({
                        "stock": nuevo_stock,
                        "estado_venta": "disponible"
                    }).eq("id", vid).execute()
                
            # 🔴 RESERVAMOS STOCK EN MODIFICACIÓN DE TURNO
            for nid in ids_a_reservar:
                item = supabase.table("inventario_celulares").select("stock").eq("id", nid).execute()
                if item.data and item.data[0]["stock"] > 0:
                    nuevo_stock = item.data[0]["stock"] - 1
                    nuevo_estado = "pendiente" if nuevo_stock == 0 else "disponible"
                    supabase.table("inventario_celulares").update({
                        "stock": nuevo_stock,
                        "estado_venta": nuevo_estado
                    }).eq("id", nid).execute()
                else:
                    return "uh justo acaban de reservar la ultima unidad de ese equipo especifico... podras ofrecerle otra alternativa?"
                        
            update_payload = {
                "cliente_nombre": cliente_nombre,
                "fecha_turno": fecha_iso,
                "celulares_ids": celulares_ids_nuevos
            }
            supabase.table("turnos_clientes").update(update_payload).eq("id", turno_id).execute()
            
            _programar_upstash_desde_tools("cita", turno_id, fecha_disparo_recordatorio)

            return f"cita modificada! quedaste agendado para el {fecha_objetivo.strftime('%A %d/%m a las %H:%M')}"

        else:
            # 🔴 RESERVAMOS STOCK PARA UN TURNO NUEVO
            for nid in celulares_ids_nuevos:
                item = supabase.table("inventario_celulares").select("stock").eq("id", nid).execute()
                if item.data and item.data[0]["stock"] > 0:
                    nuevo_stock = item.data[0]["stock"] - 1
                    nuevo_estado = "pendiente" if nuevo_stock == 0 else "disponible"
                    supabase.table("inventario_celulares").update({
                        "stock": nuevo_stock,
                        "estado_venta": nuevo_estado
                    }).eq("id", nid).execute()
                else:
                    return "uy no queda stock disponible para reservar ese equipo especifico"

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

            if res_insert.data:
                nuevo_turno_id = res_insert.data[0]["id"]
                _programar_upstash_desde_tools("cita", nuevo_turno_id, fecha_disparo_recordatorio)

            return f"buenisimo, tu cita quedo agendada para el {fecha_objetivo.strftime('%d/%m a las %H:%M')} hs, te esperamos"

    except Exception as e:
        print(f"[Falla Crítica] ❌ Error en agendar_cita: {e}")
        solicitar_asistencia_humana(f"Falla al intentar agendar un turno para {cliente_nombre}", telefono, comercio_id)
        return "SISTEMA_DELAY: Hubo un problema al guardar el turno, avisale de forma tranqui que ya notificamos al local para confirmarlo a mano"
    
def _programar_upstash_desde_tools(tipo_evento: str, registro_id: int, fecha_disparo: datetime):
    """Función auxiliar para agendar el recordatorio en QStash desde las tools del bot."""
    from zoneinfo import ZoneInfo
    
    QSTASH_TOKEN = os.getenv("QSTASH_TOKEN")
    URL_RAILWAY = os.getenv("URL_RAILWAY")
    
    if not QSTASH_TOKEN or not URL_RAILWAY:
        print("❌ [QStash Tool] Error crítico: QSTASH_TOKEN o URL_RAILWAY no configurados en el entorno.")
        return
    
    url_base_limpia = URL_RAILWAY.strip("/")
    url_qstash = f"https://qstash.upstash.io/v2/publish/{url_base_limpia}/api/webhooks/disparar-mensaje-programado"
    
    tz_local = ZoneInfo('America/Argentina/Buenos_Aires')
    if fecha_disparo.tzinfo is None:
        fecha_disparo = fecha_disparo.replace(tzinfo=tz_local)
        
    ahora_local = datetime.now(tz_local)
    diferencia = (fecha_disparo - ahora_local).total_seconds()
    delay_segundos = max(int(diferencia), 5)

    headers = {
        "Authorization": f"Bearer {QSTASH_TOKEN}",
        "Content-Type": "application/json",
        "Upstash-Delay": f"{delay_segundos}s" 
    }
    
    payload = {"tipo": tipo_evento, "registro_id": registro_id}
    
    try:
        res = requests.post(url_qstash, headers=headers, json=payload)
        
        if res.status_code in [200, 201, 202]:
            print(f"✅ [QStash Tool] ¡Conexión Exitosa! Cita ID {registro_id} programada en Upstash para dentro de {delay_segundos}s.")
        else:
            print(f"❌ [QStash Tool] Upstash rechazó la petición (Código {res.status_code}). Respuesta: {res.text}")
            print(f"🔗 URL intentada: {url_qstash}")
            
    except Exception as e:
        print(f"❌ [QStash Tool] Error crítico de red al intentar conectar con Upstash: {e}")
            
def obtener_configuracion_comercio(comercio_id: int) -> dict:
    """Trae las políticas personalizadas del comercio desde la base de datos o las crea si no existen (On-Demand)."""
    try:
        # 1. Buscamos si ya existe la configuración
        response = supabase.table("configuracion_comercios") \
            .select("*") \
            .eq("comercio_id", int(comercio_id)) \
            .execute()
        
        if response.data:
            return response.data[0]
            
        # 2. Si NO existe (comercio nuevo sin configurar), la creamos on-the-fly
        print(f"⚠️ [Sistema] Comercio ID {comercio_id} no tiene configuración. Creando fila por defecto...")
        
        tel_dueno = None
        # Intentamos recuperar el teléfono del dueño de la tabla principal
        try:
            res_comercio = supabase.table("comercios").select("telefono_dueno").eq("id", int(comercio_id)).execute()
            if res_comercio.data:
                tel_dueno = res_comercio.data[0].get("telefono_dueno")
        except Exception as e_tel:
            print(f"[Sistema] ⚠️ No se pudo obtener el teléfono del dueño para la config: {e_tel}")

        # Armamos el diccionario de configuración seguro por defecto
        config_default = {
            "comercio_id": int(comercio_id),
            "metodos_pago": "Efectivo",
            "recargo_usdt": "0%",
            "tipo_cambio_efectivo": "Dólar blue vendedor del día",
            "permuta_minima": "No especificado",
            "politica_garantia": "Sin garantía",
            "telefono_dueno": tel_dueno, 
            "requiere_cita": False, 
            "direccion_fisica": "NUESTRO_LOCAL",
            "acepta_canje": False,
            "preguntas_canje": "Qué modelo es? Cuantos gb tiene?",
            "ofrece_servicio_tecnico": False,
            "reparaciones_ofrecidas": "",
            "mensaje_cotizacion_tecnico": "Aguardame un instante que te preparo la cotización sin cargo",
            "minutos_anticipacion_recordatorio": 15,
            "intervalo_citas_minutos": 30,  # 🌟 NUEVO
            "max_citas_por_horario": 1      # 🌟 NUEVO
        }
        
        # 3. La insertamos para que quede guardada y lista para el frontend
        supabase.table("configuracion_comercios").insert(config_default).execute()
        print(f"✅ [Sistema] Configuración inicializada y guardada para Comercio ID: {comercio_id}")
        
        return config_default

    except Exception as e:
        print(f"[Sistema] ❌ Error leyendo/creando configuración del comercio: {e}")
        # Retorno de emergencia hiperbásico por si falla la inserción
        return {
            "metodos_pago": "Efectivo",
            "requiere_cita": False, 
            "telefono_dueno": None
        }
    
def verificar_numero_excluido(telefono: str, comercio_id: str) -> bool:
    """Consulta en Supabase si el número está en la lista negra de ese comercio específico."""
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
            return "No se pudo alertar al dueño porque no tiene configurado un teléfono de soporte"
        
        if not comercio_res.data:
            return "Error: No se encontró la instancia de WhatsApp de este comercio"

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
            return "Éxito: El dueño ha sido notificado"
        else:
            return "No se pudo enviar la notificación por problemas de API"

    except Exception as e:
        return f"Error interno al procesar la asistencia: {str(e)}"