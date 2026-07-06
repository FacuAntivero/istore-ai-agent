import os
import json
import redis.asyncio as redis
from dotenv import load_dotenv

load_dotenv()

# Inicializamos la conexión a Upstash
# decode_responses=True hace que Redis nos devuelva strings de Python en lugar de bytes puros
redis_db = redis.from_url(os.getenv("UPSTASH_REDIS_URL"), decode_responses=True)

# ==========================================
# 🛡️ ESCUDOS ANTI-DUPLICADOS (TTL: 24 horas)
# ==========================================

async def es_mensaje_procesado(id_mensaje: str) -> bool:
    """Verifica si el mensaje ya existe y lo guarda. Devuelve True si ya existía."""
    # setnx (Set if Not eXists) devuelve 1 si lo creó, 0 si ya existía
    fue_creado = await redis_db.setnx(f"msg_procesado:{id_mensaje}", "1")
    if fue_creado:
        # Si es nuevo, le ponemos fecha de vencimiento de 24 hs (86400 segundos)
        await redis_db.expire(f"msg_procesado:{id_mensaje}", 86400)
        return False
    return True

async def es_pago_procesado(payment_id: str) -> bool:
    """Misma lógica que los mensajes, pero para los webhooks de MercadoPago."""
    fue_creado = await redis_db.setnx(f"pago_procesado:{payment_id}", "1")
    if fue_creado:
        await redis_db.expire(f"pago_procesado:{payment_id}", 86400)
        return False
    return True

# ==========================================
# 📦 BUFFER DE MENSAJES Y AUDIOS (La Cola)
# ==========================================

async def agregar_al_buffer(id_remitente: str, datos_mensaje: dict):
    """Guarda un mensaje en la lista de espera del usuario."""
    # Convertimos el diccionario a JSON string
    mensaje_json = json.dumps(datos_mensaje)
    key = f"buffer:{id_remitente}"
    
    # Lo empujamos al final de la lista de Redis
    await redis_db.rpush(key, mensaje_json)
    # Le damos un TTL corto (ej. 5 minutos) por si el servidor crashea y el timer no se ejecuta
    await redis_db.expire(key, 300)

async def obtener_y_limpiar_buffer(id_remitente: str) -> list:
    """Saca todos los mensajes agrupados del usuario y vacía la cola."""
    key = f"buffer:{id_remitente}"
    
    # Obtiene todos los elementos (de 0 a -1 significa "todos")
    mensajes_crudos = await redis_db.lrange(key, 0, -1)
    
    # Si hay mensajes, borramos la llave para dejarla limpia para la próxima
    if mensajes_crudos:
        await redis_db.delete(key)
        
    # Volvemos a convertir los JSON strings a diccionarios de Python
    return [json.loads(m) for m in mensajes_crudos]

# ==========================================
# 🧠 MEMORIA DE GEMINI (Historial de Chat)
# ==========================================

async def guardar_historial_chat(session_key: str, historial_dict: list):
    """
    Guarda el contexto del chat. 
    Nota: historial_dict debe ser una lista de diccionarios que representen los roles y textos.
    """
    key = f"chat_history:{session_key}"
    historial_json = json.dumps(historial_dict)
    
    # Guardamos el historial con una expiración de 24 horas de inactividad
    await redis_db.setex(key, 86400, historial_json)

async def obtener_historial_chat(session_key: str) -> list:
    """Recupera el historial para inyectárselo a un nuevo agente de Gemini al vuelo."""
    key = f"chat_history:{session_key}"
    historial_json = await redis_db.get(key)
    
    if historial_json:
        return json.loads(historial_json)
    return []

# ==========================================
# 🏪 CACHÉ DE COMERCIOS (TTL: 5 minutos)
# ==========================================

async def guardar_cache_comercio(instance_name: str, datos_comercio: dict):
    key = f"cache_comercio:{instance_name}"
    # Guardamos por 300 segundos (5 minutos)
    await redis_db.setex(key, 300, json.dumps(datos_comercio))

async def obtener_cache_comercio(instance_name: str):
    key = f"cache_comercio:{instance_name}"
    datos_json = await redis_db.get(key)
    if datos_json:
        return json.loads(datos_json)
    return None