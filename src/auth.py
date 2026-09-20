"""
Verificación de que quien llama a la API es dueño del comercio que está
pidiendo/modificando.

Antes, ningún endpoint de /api/... chequeaba esto: cualquiera que supiera
(o adivinara, son enteros secuenciales) el id de OTRO comercio podía leer o
escribir sus plantillas, números excluidos, turnos, etc. Como el panel admin
(istore-admin) ya loguea a cada dueño con Supabase Auth, reusamos ese mismo
token: el frontend lo manda como "Authorization: Bearer <access_token>" y acá
lo validamos contra Supabase y confirmamos que el comercio pedido sea
realmente suyo (comercios.owner_id == user.id).
"""
import asyncio
from fastapi import Header, HTTPException
from database import supabase


async def obtener_usuario_autenticado(authorization: str = Header(None)) -> str:
    """Valida el JWT de Supabase Auth y devuelve el user_id. 401 si falta o es inválido."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Falta el header Authorization (Bearer token)")

    token = authorization.split(" ", 1)[1].strip()
    try:
        user_response = await asyncio.to_thread(supabase.auth.get_user, token)
    except Exception:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")

    user = getattr(user_response, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")

    return user.id


async def verificar_dueno_de_comercio(comercio_id: int, user_id: str) -> None:
    """403 si el usuario autenticado no es el dueño (owner_id) de ese comercio_id."""
    res = await asyncio.to_thread(
        lambda: supabase.table("comercios").select("id").eq("id", comercio_id).eq("owner_id", user_id).execute()
    )
    if not res.data:
        raise HTTPException(status_code=403, detail="No tenés permiso sobre este comercio")


async def verificar_dueno_de_registro(tabla: str, id_registro, user_id: str) -> int:
    """
    Para endpoints que reciben el id de un SUB-registro (una plantilla, un
    turno, un mensaje de post-venta) en vez del comercio_id directamente:
    busca a qué comercio pertenece ese registro y verifica que el usuario
    autenticado sea su dueño. Devuelve el comercio_id (por si el endpoint
    lo necesita) y responde 404 si el registro no existe.
    """
    res = await asyncio.to_thread(
        lambda: supabase.table(tabla).select("comercio_id").eq("id", id_registro).execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Registro no encontrado")

    comercio_id = res.data[0]["comercio_id"]
    await verificar_dueno_de_comercio(comercio_id, user_id)
    return comercio_id
