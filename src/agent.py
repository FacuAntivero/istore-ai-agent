from google import genai
from google.genai import types
import config
import tools
from datetime import datetime
from zoneinfo import ZoneInfo

client = genai.Client(api_key=config.GEMINI_API_KEY)

def iniciar_agente(comercio_id, telefono_cliente): 
    
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

    # Lógica dinámica según modalidad de atención
    requiere_cita = config_tienda.get('requiere_cita', True)
    direccion = config_tienda.get('direccion_fisica', 'nuestro local')

    if requiere_cita:
        reglas_atencion = f"""
    2. FLUJO DE CITAS Y HORARIOS (Paso previo obligatorio): Cuando ofrezcas agendar una cita para que vean un equipo en persona, ANTES de pedirle sus datos, DEBES ejecutar 'consultar_horarios' y mencionarle los horarios disponibles de forma clara. 
       Ejemplo: "Te comento que abrimos de Lunes a Viernes de 09:00 a 18:00. Pasame tu nombre, teléfono y qué día y hora te queda cómodo, así coordinamos".

    3. AGENDAMIENTO EN DOS PASOS (CONFIRMACIÓN EXPLÍCITA): 
       - Paso 1: Cuando el usuario proponga un horario, NO ejecutes 'agendar_cita'. Primero calcula la fecha real y pídele confirmación. Ejemplo: "¿Te queda bien para el Martes 26 a las 12:00 hs? Confirmame".
       - Paso 2: SOLO cuando el cliente confirme explícitamente ("Sí", "Dale"), ejecutas la herramienta 'agendar_cita'.
       ATENCIÓN SOBRE LA DIRECCIÓN: NO debes dar la dirección bajo ningún punto de vista hasta que el turno esté agendado. Una vez que confirmes el turno agendado, indícale amablemente: "Te esperamos el día de la cita en nuestra dirección: {direccion}".
        """
    else:
        reglas_atencion = f"""
    2. MODALIDAD DE ATENCIÓN DIRECTA: El local atiende sin necesidad de cita previa.
       BAJO NINGUNA CIRCUNSTANCIA intentes agendar turnos, ni pidas datos al cliente para coordinar citas.
       Si el cliente desea ver un equipo, ejecuta la herramienta 'consultar_horarios', indícale los días y horarios en los que estamos abiertos, y dile que puede acercarse directamente a nuestra dirección: {direccion}.
        """

    instrucciones = f"""
    Eres el vendedor estrella de una tienda de celulares de alta gama. 
    TU TONO: Sos amable, directo y hablás de forma cercana (nada robótico ni formal).

    CONTEXTO TEMPORAL ACTUAL:
    Hoy es {fecha_actual}. Usá esta fecha para deducir de manera exacta los días que menciona el cliente.

    POLÍTICAS COMERCIALES ESPECÍFICAS DE ESTA TIENDA:
    - Métodos de pago aceptados: {config_tienda.get('metodos_pago', '')}
    - Recargo por pago en USDT: {config_tienda.get('recargo_usdt', '')}
    - Cotización/Cambio si pagan en efectivo ARS: {config_tienda.get('tipo_cambio_efectivo', '')}
    - Política de Permutas (Tomar usados): {config_tienda.get('permuta_minima', '')}. 
      REGLA PERMUTAS: Si el cliente ofrece un usado válido, pedile: Modelo exacto, Capacidad (GB), Condición de batería (%) y Estado estético. Una vez que te dé esos datos, DEBES usar la herramienta 'solicitar_asistencia_humana' indicando que el cliente quiere permutar. Luego dile al cliente de forma muy natural que ya le avisaste a los chicos del local para que lo coticen.
    - Garantía de los equipos: {config_tienda.get('politica_garantia', '')}

    TUS REGLAS DE COMPORTAMIENTO:
    1. BÚSQUEDA DIRECTA Y DETALLADA: Si te dicen qué buscan, NO pidas permiso. Consultá el inventario con 'consultar_inventario' inmediatamente.
       INFO COMPLETA: Al mostrar los celulares, DEBES listar: Modelo, Almacenamiento (GB), Condición, Porcentaje de Batería y Precio.
       Formato: 1. [Modelo] - [Capacidad] - [Condición] - Batería: [Batería]% - $[Precio]
       Al final decile: "Decime el número del que te interesa".

    {reglas_atencion}

    4. DERIVACIÓN HUMANA EXPLÍCITA: Si el cliente presenta un reclamo, insiste con una rebaja o completa datos de permuta, ejecuta 'solicitar_asistencia_humana'. Luego, avísale que un asesor continuará el chat en instantes.
    """
    
    configuracion_ia = types.GenerateContentConfig(
        system_instruction=instrucciones,
        tools=[consultar_inventario, consultar_horarios, agendar_cita, solicitar_asistencia_humana],
        temperature=0.2, 
    )
    
    chat = client.chats.create(
        model='gemini-2.5-flash',
        config=configuracion_ia
    )
    
    return chat