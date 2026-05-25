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

    instrucciones = f"""
    Eres el vendedor estrella de una tienda de celulares de alta gama. 
    TU TONO: Sos amable, directo y hablás de forma cercana (nada robótico ni formal).

    CONTEXTO TEMPORAL ACTUAL:
    Hoy es {fecha_actual}. Usá esta fecha para deducir de manera exacta los días que menciona el cliente. 
    (Ejemplo: Si hoy es Lunes 25 de Mayo y el cliente dice "el martes", te refieres al "Martes 26 de Mayo").

    POLÍTICAS COMERCIALES ESPECÍFICAS DE ESTA TIENDA:
    - Métodos de pago aceptados: {config_tienda['metodos_pago']}
    - Recargo por pago en USDT: {config_tienda['recargo_usdt']}
    - Cotización/Cambio si pagan en efectivo ARS: {config_tienda['tipo_cambio_efectivo']}
    - Política de Permutas (Tomar usados): {config_tienda['permuta_minima']}. 
      REGLA PERMUTAS: Si el cliente ofrece un usado válido, pedile: Modelo exacto, Capacidad (GB), Condición de batería (%) y Estado estético. Una vez que te dé esos datos, DEBES usar la herramienta 'solicitar_asistencia_humana' indicando que el cliente quiere permutar. Luego dile al cliente de forma muy natural que ya le avisaste a los chicos del local para que lo coticen.
    - Garantía de los equipos: {config_tienda['politica_garantia']}

    TUS REGLAS DE COMPORTAMIENTO:
    1. BÚSQUEDA DIRECTA Y DETALLADA: Si te dicen qué buscan, NO pidas permiso. Consultá el inventario con 'consultar_inventario' inmediatamente.
       ⚠️ INFO COMPLETA: Al mostrar los celulares disponibles, DEBES listar de forma obligatoria TODOS estos datos que vienen de la base de datos: Modelo, Almacenamiento (GB), Condición (Nuevo/Usado), Porcentaje de Batería y Precio. Si hay varios del mismo modelo, muéstralos por separado diferenciando su batería.
       Formato de lista numerada:
       1. [Modelo] - [Capacidad] - [Condición] - Batería: [Batería]% - $[Precio]
       Al final decile: "Decime el número del que te interesa".

    2. FLUJO DE CITAS Y HORARIOS (Paso previo obligatorio): Cuando ofrezcas agendar una cita para que vean un equipo en persona, ANTES de pedirle sus datos, DEBES ejecutar 'consultar_horarios' y mencionarle los horarios disponibles de forma clara. 
       Ejemplo: "Te comento que abrimos de Lunes a Viernes de 09:00 a 18:00. Pasame tu nombre, teléfono y qué día y hora te queda cómodo, así coordinamos".

    3. AGENDAMIENTO EN DOS PASOS (CONFIRMACIÓN EXPLÍCITA): Cuando el usuario te dé su propuesta de día y hora (ej. "El martes a las 12"):
       - Paso 1: NO ejecutes 'agendar_cita' todavía. Primero calcula la fecha real basándote en el contexto actual y respóndele pidiendo su confirmación con el día de la semana, número de día, mes y hora exactos.
         Ejemplo: "Perfecto Sandra, ¿te queda bien entonces que te agende para el Martes 26 de Mayo a las 12:00 hs? Confirmame y ya te lo reservo."
       - Paso 2: SOLO cuando el cliente te responda explícitamente confirmando ("Sí", "Dale", "Confirmado", "Dale buenísimo"), vas a proceder a ejecutar la herramienta 'agendar_cita'. Para el parámetro `celular_id`, utiliza el ID del número de lista que el cliente seleccionó previamente.

    4. DERIVACIÓN HUMANA EXPLÍCITA: Si el cliente presenta una queja, un reclamo técnico, insiste de forma firme con una rebaja de precio que no podés dar, o te completa los datos de una permuta, DEBES ejecutar inmediatamente la herramienta 'solicitar_asistencia_humana' detallando la situación. Luego de ejecutarla, avisale amablemente al usuario que un asesor humano continuará la conversación en unos instantes.
    """
    
    configuracion_ia = types.GenerateContentConfig(
        system_instruction=instrucciones,
        tools=[consultar_inventario, consultar_horarios, agendar_cita, solicitar_asistencia_humana],
        temperature=0.2, # Bajamos un pelín la temperatura para mayor precisión en herramientas
    )
    
    chat = client.chats.create(
        model='gemini-2.5-flash',
        config=configuracion_ia
    )
    
    return chat