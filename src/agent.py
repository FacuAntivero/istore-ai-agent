from google import genai
from google.genai import types
import config
import tools # ⬅️ Cambiamos la forma de importar para poder envolver las funciones

client = genai.Client(api_key=config.GEMINI_API_KEY)

def iniciar_agente(comercio_id): # ⬅️ AHORA RECIBE EL ID
    
    # --- WRAPPERS (ENVOLTORIOS) DE SEGURIDAD ---
    # Gemini usará estas funciones locales, y nosotros le inyectamos 
    # el comercio_id a las funciones reales de tools.py por detrás.
    
    def consultar_inventario(modelo: str = "") -> str:
        """Busca un modelo de celular en el inventario de la tienda."""
        return tools.consultar_inventario(modelo, comercio_id)

    def consultar_horarios() -> str:
        """Consulta los horarios de atención de la tienda."""
        return tools.consultar_horarios(comercio_id)

    def agendar_cita(cliente_nombre: str, telefono: str, fecha_turno: str, celular_id: int) -> str:
        """Agenda una cita para un cliente en la tienda asociando el ID del celular."""
        return tools.agendar_cita(cliente_nombre, telefono, fecha_turno, celular_id, comercio_id)


    instrucciones = """
    Eres el vendedor estrella de una tienda de celulares de alta gama.

    TUS REGLAS DE COMPORTAMIENTO:
    1. BÚSQUEDA INTELIGENTE: Si el cliente escribe mal un modelo (ej. 'aifon kince' o 'samsun s23'), deduce el nombre real y exacto ('iPhone 15', 'Samsung Galaxy S23') ANTES de usar la herramienta 'consultar_inventario'.
    2. PRECISIÓN: Si te piden '14 Pro', busca solo eso. Si no hay, diles que no hay y ofrece alternativas similares. Si el pedido es muy ambiguo, repregunta antes de buscar.
    3. RESERVAS: Si el cliente dice que lo quiere comprar o ir a ver, ofrécele agendar una cita.
    4. HORARIOS: Antes de agendar una cita, SIEMPRE consultá los horarios con 'consultar_horarios' e informale al cliente cuándo puede venir. Verificá que la fecha y hora que propone el cliente esté dentro del horario de atención.
    5. FLUJO DE CITA: Para agendar, PÍDELE AL CLIENTE: su nombre, su teléfono y qué día/hora quiere ir. Solo cuando tengas esos 3 datos Y hayas verificado que el horario es válido, ejecutá 'agendar_cita' con el ID exacto del celular elegido.
    6. NUNCA inventes IDs ni precios. Toda información técnica sácala de las herramientas.
    """
    
    configuracion_ia = types.GenerateContentConfig(
        system_instruction=instrucciones,
        tools=[consultar_inventario, consultar_horarios, agendar_cita], # ⬅️ Pasamos las funciones locales
        temperature=0.2, 
    )
    
    chat = client.chats.create(
        model='gemini-2.5-flash',
        config=configuracion_ia
    )
    
    return chat