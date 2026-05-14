import dateparser
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
        resultado = "Horarios de atención:\n"
        for h in datos:
            apertura = h['hora_apertura'][:5] if h['hora_apertura'] else '—'
            cierre = h['hora_cierre'][:5] if h['hora_cierre'] else '—'
            resultado += f"- {h['dia_semana']}: {apertura} a {cierre}\n"
        return resultado
    except Exception as e:
        return f"Error al consultar horarios: {str(e)}"

def agendar_cita(cliente_nombre: str, telefono: str, fecha_turno: str, celular_id: int, comercio_id: str) -> str:
    """Agenda una cita para un cliente en la tienda asociando el ID del celular."""
    print(f"\n[Sistema] 📅 Agendando cita: {cliente_nombre} - Fecha: '{fecha_turno}' - Celular ID: {celular_id}")
    try:
        settings = {'PREFER_DATES_FROM': 'future', 'TIMEZONE': 'America/Argentina/Buenos_Aires'}
        fecha_objetivo = dateparser.parse(fecha_turno, languages=['es'], settings=settings)

        if not fecha_objetivo:
            return f"No entendí la fecha '{fecha_turno}'. Intentá algo como 'Mañana a las 10:00'."

        fecha_iso = fecha_objetivo.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[Sistema] 📅 Fecha parseada: {fecha_iso}")

        update_res = supabase.table("inventario_celulares") \
            .update({"estado_venta": "pendiente"}) \
            .eq("id", celular_id) \
            .eq("comercio_id", comercio_id) \
            .execute()
        print(f"[Sistema] 📅 Update inventario: {update_res.data}")

        insert_payload = {
            "comercio_id": comercio_id,
            "celular_id": celular_id,
            "cliente_nombre": cliente_nombre,
            "telefono": telefono,
            "fecha_turno": fecha_iso
        }
        print(f"[Sistema] 📅 Insertando turno: {insert_payload}")
        
        insert_res = supabase.table("turnos_clientes").insert(insert_payload).execute()
        print(f"[Sistema] 📅 Resultado insert: {insert_res.data}")

        return f"¡Cita agendada! Para {cliente_nombre} el día {fecha_objetivo.strftime('%d/%m a las %H:%M')}."
    except Exception as e:
        print(f"[Sistema] ❌ Error agendar_cita: {str(e)}")
        return f"Error al agendar la cita: {str(e)}"
