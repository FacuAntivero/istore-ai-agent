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

def agendar_cita(celular_id: int, cliente_nombre: str, telefono: str, fecha_turno: str) -> str:
    """
    Agenda una cita para un cliente, guardando sus datos y cambiando el estado del celular a 'pendiente'.
    Args:
        celular_id: El ID (número) del celular que el cliente quiere reservar.
        cliente_nombre: Nombre del cliente.
        telefono: Teléfono de contacto.
        fecha_turno: Día y horario en el que el cliente irá a la tienda.
    """
    print(f"\n[Sistema] 📅 Agendando cita para {cliente_nombre} por el celular ID {celular_id}")
    
    try:
        # 1. Cambiar el estado del celular a 'pendiente'
        supabase.table("inventario_celulares") \
            .update({"estado_venta": "pendiente"}) \
            .eq("id", celular_id) \
            .execute()
            
        # 2. Guardar el turno del cliente
        supabase.table("turnos_clientes") \
            .insert({
                "celular_id": celular_id,
                "cliente_nombre": cliente_nombre,
                "telefono": telefono,
                "fecha_turno": fecha_turno
            }).execute()
            
        return f"Cita agendada con éxito para {cliente_nombre}. El celular {celular_id} ahora está 'pendiente'."
        
    except Exception as e:
        return f"Error al agendar la cita en la base de datos: {str(e)}"