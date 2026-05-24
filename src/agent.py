from google import genai
from google.genai import types
import config
import tools
from datetime import datetime
from zoneinfo import ZoneInfo

client = genai.Client(api_key=config.GEMINI_API_KEY)

def iniciar_agente(comercio_id, telefono_cliente): # ⬅️ AHORA TAMBIÉN RECIBE EL TELÉFONO DEL CLIENTE
    
    # Traemos las políticas desde Supabase
    config_tienda = tools.obtener_configuracion_comercio(comercio_id)
    
    # --- WRAPPERS DE SEGURIDAD ---
    def consultar_inventario(modelo: str = "") -> str:
        """Busca un modelo de celular en el inventario de la tienda."""
        return tools.consultar_inventario(modelo, comercio_id)

    def consultar_horarios() -> str:
        """Consulta los horarios de atención de la tienda."""
        return tools.consultar_horarios(comercio_id)

    def agendar_cita(cliente_nombre: str, telefono: str, fecha_turno: str, celular_id: int) -> str:
        """Agenda una cita o modifica una existente para un cliente."""
        return tools.agendar_cita(cliente_nombre, telefono, fecha_turno, celular_id, comercio_id)

    def solicitar_asistencia_humana(motivo: str) -> str:
        """Notifica de inmediato al dueño de la tienda para que intervenga manualmente en este chat."""
        return tools.solicitar_asistencia_humana(motivo, telefono_cliente, comercio_id)

    # Noción del tiempo
    tz = ZoneInfo('America/Argentina/Buenos_Aires')
    ahora = datetime.now(tz)
    fecha_actual = ahora.strftime("%A, %d de %B de %Y - %H:%M")

    instrucciones = f"""
    Eres el vendedor estrella de una tienda de celulares de alta gama. 
    TU TONO: Sos amable, directo y hablás de forma cercana (nada robótico ni formal).

    CONTEXTO TEMPORAL ACTUAL:
    Hoy es {fecha_actual}. Usá esta fecha para deducir los días automáticamente.

    POLÍTICAS COMERCIALES ESPECÍFICAS DE ESTA TIENDA:
    - Métodos de pago aceptados: {config_tienda['metodos_pago']}
    - Recargo por pago en USDT: {config_tienda['recargo_usdt']}
    - Cotización/Cambio si pagan en efectivo ARS: {config_tienda['tipo_cambio_efectivo']}
    - Política de Permutas (Tomar usados): {config_tienda['permuta_minima']}. 
      REGLA PERMUTAS: Si el cliente ofrece un usado válido, pedile: Modelo exacto, Capacidad (GB), Condición de batería (%) y Estado estético. Una vez que te dé esos datos, DEBES usar la herramienta 'solicitar_asistencia_humana' indicando que el cliente quiere permutar. Luego dile al cliente de forma muy natural que ya le avisaste a los chicos del local para que lo coticen.
    - Garantía de los equipos: {config_tienda['politica_garantia']}

    TUS REGLAS DE COMPORTAMIENTO:
    1. BÚSQUEDA DIRECTA: Si te dicen qué buscan, NO pidas permiso. Consultá el inventario con 'consultar_inventario' inmediatamente y listalos.
    2. FORMATO DE LISTA: Mostrá los celulares disponibles en una lista numerada: 
       1. [Modelo] - [Capacidad] - [Precio]
       Decile al final: "Decime el número del que te interesa".
    3. TURNOS Y CAMBIOS: Si quieren ir a ver un equipo, pediles Nombre, Teléfono y Día/Hora. Valida con 'consultar_horarios' y luego ejecuta 'agendar_cita'.
    4. DERIVACIÓN HUMANA EXPLICITA: Si el cliente presenta una queja, un reclamo técnico, insiste de forma firme con una rebaja de precio que no podés dar, o te completa los datos de una permuta, DEBES ejecutar inmediatamente la herramienta 'solicitar_asistencia_humana' detallando la situación. Luego de ejecutarla, avisale amablemente al usuario que un asesor humano continuará la conversación en unos instantes.
    """
    
    configuracion_ia = types.GenerateContentConfig(
        system_instruction=instrucciones,
        # Agregamos la nueva función local a las herramientas de Gemini
        tools=[consultar_inventario, consultar_horarios, agendar_cita, solicitar_asistencia_humana],
        temperature=0.3, 
    )
    
    chat = client.chats.create(
        model='gemini-2.5-flash',
        config=configuracion_ia
    )
    
    return chat