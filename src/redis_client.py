import json
import redis.asyncio as redis
import config

# ==========================================
# 🔌 INICIALIZACIÓN DE CONEXIONES (Redis)
# ==========================================

# Conexión a Upstash Redis (Para buffer, anti-duplicados y caché)
redis_db = redis.from_url(config.UPSTASH_REDIS_URL, decode_responses=True)


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
        # Antes decía "pago_processed" (inglés) acá, una clave que nunca se creó,
        # así que expire() no hacía nada y estas claves quedaban para siempre en Redis.
        await redis_db.expire(f"pago_procesado:{payment_id}", 86400)
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