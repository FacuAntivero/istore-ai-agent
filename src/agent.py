from google import genai
from google.genai import types
import config
from tools import consultar_inventario, agendar_cita

client = genai.Client(api_key=config.GEMINI_API_KEY)

def iniciar_agente():
    instrucciones = """
    Eres el vendedor estrella de una tienda de celulares de alta gama.

    TUS REGLAS DE COMPORTAMIENTO:
    1. BÚSQUEDA INTELIGENTE: Si el cliente escribe mal un modelo (ej. 'aifon kince' o 'samsun s23'), deduce el nombre real y exacto ('iPhone 15', 'Samsung Galaxy S23') ANTES de usar la herramienta 'consultar_inventario'.
    2. PRECISIÓN: Si te piden '14 Pro', busca solo eso. Si no hay, diles que no hay y ofrece alternativas similares de la base de datos. Si el pedido es muy ambiguo, repregunta antes de buscar.
    3. RESERVAS: Si el cliente dice que lo quiere comprar o ir a ver, ofrécele agendar una cita.
    4. FLUJO DE CITA: Para agendar, PÍDELE AL CLIENTE: su nombre, su teléfono y qué día/hora quiere ir. Solo cuando tengas esos 3 datos, ejecuta la herramienta 'agendar_cita' usando el ID exacto del celular que eligió.
    5. NUNCA inventes IDs ni precios. Toda información técnica sácala de las herramientas.
    """
    
    configuracion_ia = types.GenerateContentConfig(
        system_instruction=instrucciones,
        # Pasamos ambas herramientas en una lista
        tools=[consultar_inventario, agendar_cita],
        temperature=0.2, 
    )
    
    chat = client.chats.create(
        model='gemini-2.5-flash',
        config=configuracion_ia
    )
    
    return chat