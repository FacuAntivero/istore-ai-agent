import os
import json
import redis.asyncio as redis
from dotenv import load_dotenv
# Importamos el cliente oficial de Supabase
from supabase import create_client, Client

load_dotenv()

# ==========================================
# 🔌 INICIALIZACIÓN DE CONEXIONES (Redis & Supabase)
# ==========================================

# Conexión a Upstash Redis (Para buffer, anti-duplicados y caché)
redis_db = redis.from_url(os.getenv("UPSTASH_REDIS_URL"), decode_responses=True)

# Conexión a Supabase (Para memoria a largo plazo e historial persistente)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ==========================================
# 🛡️ ESCUDOS ANTI-DUPLICADOS (TTL: 24 horas)
# ==========================================

async def es_mensaje_procesado(id_mensaje: str) -> bool:
    """Verifica si el mensaje ya existe y lo guarda. Devuelve True si ya existía."""
    fue_creado = await redis_db.setnx(f"msg_procesado:{id_mensaje}", "1")
    if fue_creado:
        await redis_db.expire(f"msg_procesado:{id_mensaje}", 86400)
        return False
    return True

async def es_pago_procesado(payment_id: str) -> bool:
    """Misma lógica que los mensajes, pero para los webhooks de MercadoPago."""
    fue_creado = await redis_db.setnx(f"pago_procesado:{payment_id}", "1")
    if fue_creado:
        await redis_db.expire(f"pago_processed:{payment_id}", 86400)
        return False
    return True


# ==========================================
# 📦 BUFFER DE MENSAJES Y AUDIOS (La Cola)
# ==========================================

async def agregar_al_buffer(id_remitente: str, datos_mensaje: dict):
    """Guarda un mensaje en la lista de espera del usuario."""
    mensaje_json = json.dumps(datos_mensaje)
    key = f"buffer:{id_remitente}"
    await redis_db.rpush(key, mensaje_json)
    await redis_db.expire(key, 300)

async def obtener_y_limpiar_buffer(id_remitente: str) -> list:
    """Saca todos los mensajes agrupados del usuario y vacía la cola."""
    key = f"buffer:{id_remitente}"
    mensajes_crudos = await redis_db.lrange(key, 0, -1)
    if mensajes_crudos:
        await redis_db.delete(key)
    return [json.loads(m) for m in mensajes_crudos]


# ==========================================
# 🧠 MEMORIA DE GEMINI (Historial con Supabase)
# ==========================================

async def guardar_historial_chat(session_key: str, historial_dict: list):
    """
    Guarda los NUEVOS mensajes en la base de datos de Supabase.
    Recibe la lista completa actual, pero solo inserta los registros que no existan
    o simplemente añade las nuevas interacciones de forma limpia.
    Nota: Para evitar duplicados en la base de datos al enviar toda la lista,
    lo ideal es pasarle solo los últimos mensajes o limpiar/guardar según tu lógica en agent.py o server.py.
    Esta función toma el último par (o mensaje) y lo inserta.
    """
    if not historial_dict:
        return

    # Usualmente Gemini añade el mensaje del usuario y del modelo. 
    # Tomamos el último mensaje generado en la sesión para insertarlo de forma atómica.
    ultimo_mensaje = historial_dict[-1]
    
    # Adaptamos los campos a la tabla que creamos
    datos_insercion = {
        "telefono_cliente": str(session_key),
        "rol": ultimo_mensaje.get("role", "user"), # Puede venir como 'role' o 'rol' según tu agent.py
        "contenido": ultimo_mensaje.get("parts", [""])[0] if isinstance(ultimo_mensaje.get("parts"), list) else ultimo_mensaje.get("parts", "")
    }
    
    # Insertamos en Supabase de forma sincrónica (la librería de supabase ejecuta bloqueante por defecto)
    try:
        supabase_client.table("historial_chat_ia").insert(datos_insercion).execute()
    except Exception as e:
        print(f"❌ Error al guardar historial en Supabase: {e}")


async def obtener_historial_chat(session_key: str) -> list:
    """Recupera los últimos 20 mensajes de Supabase para inyectárselos a Gemini."""
    try:
        # Buscamos los mensajes del número de teléfono, ordenados por fecha ascendente
        respuesta = (
            supabase_client.table("historial_chat_ia")
            .select("rol", "contenido")
            .eq("telefono_cliente", str(session_key))
            .order("created_at", descending=False)
            .limit(20) # Traemos los últimos 20 para no saturar el contexto de Gemini
            .execute()
        )
        
        registros = respuesta.data
        historial_gemini = []
        
        # Mapeamos los datos al formato exacto que espera tu script/Gemini
        for reg in registros:
            historial_gemini.append({
                "role": reg["rol"],
                "parts": [reg["contenido"]]
            })
            
        return historial_gemini
        
    except Exception as e:
        print(f"❌ Error al obtener historial de Supabase: {e}")
        return []


# ==========================================
# 🏪 CACHÉ DE COMERCIOS (TTL: 5 minutos)
# ==========================================

async def guardar_cache_comercio(instance_name: str, datos_comercio: dict):
    key = f"cache_comercio:{instance_name}"
    await redis_db.setex(key, 300, json.dumps(datos_comercio))

async def obtener_cache_comercio(instance_name: str):
    key = f"cache_comercio:{instance_name}"
    datos_json = await redis_db.get(key)
    if datos_json:
        return json.loads(datos_json)
    return None