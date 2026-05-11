from database import supabase

def consultar_inventario(modelo_corregido: str) -> str:
    """
    Busca stock de un celular. Solo trae los que están en estado 'disponible'.
    Args:
        modelo_corregido: El nombre oficial del celular (ej. 'iPhone 14 Pro').
    """
    print(f"\n[Sistema] 🔍 Buscando en BD: {modelo_corregido} (Solo disponibles)")
    
    try:
        response = supabase.table("inventario_celulares") \
            .select("*") \
            .ilike("modelo", f"%{modelo_corregido}%") \
            .eq("estado_venta", "disponible") \
            .execute()
        
        datos = response.data
        if not datos:
            return f"No hay stock disponible para: {modelo_corregido}."
        
        return str(datos)
        
    except Exception as e:
        return f"Error al consultar la BD: {str(e)}"


def consultar_horarios() -> str:
    """
    Consulta los horarios de atención de la tienda.
    Usar esta herramienta cuando el cliente pregunta cuándo puede venir o quiere agendar una cita.
    """
    print(f"\n[Sistema] 🕐 Consultando horarios de atención")
    
    try:
        response = supabase.table("horarios_atencion") \
            .select("*") \
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


def agendar_cita(celular_id: int, cliente_nombre: str, telefono: str, fecha_turno: str) -> str:
    """
    Agenda una cita para un cliente, guardando sus datos y cambiando el estado del celular a 'pendiente'.
    Args:
        celular_id: El ID (número) del celular que el cliente quiere reservar.
        cliente_nombre: Nombre del cliente.
        telefono: Teléfono de contacto.
        fecha_turno: Día y horario en el que el cliente irá a la tienda. Debe estar dentro del horario de atención.
    """
    print(f"\n[Sistema] 📅 Agendando cita para {cliente_nombre} por el celular ID {celular_id}")
    
    try:
        supabase.table("inventario_celulares") \
            .update({"estado_venta": "pendiente"}) \
            .eq("id", celular_id) \
            .execute()
            
        supabase.table("turnos_clientes") \
            .insert({
                "celular_id": celular_id,
                "cliente_nombre": cliente_nombre,
                "telefono": telefono,
                "fecha_turno": fecha_turno
            }).execute()
            
        return f"Cita agendada con éxito para {cliente_nombre} el {fecha_turno}. El celular queda reservado como 'pendiente'."
        
    except Exception as e:
        return f"Error al agendar la cita en la base de datos: {str(e)}"
