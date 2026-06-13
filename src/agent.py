from google import genai
from google.genai import types
import config
import tools
from datetime import datetime, timedelta  # 🌟 Agregamos timedelta
from zoneinfo import ZoneInfo

client = genai.Client(api_key=config.GEMINI_API_KEY)

def iniciar_agente(comercio_id, telefono_cliente): 
    
    # Traemos las políticas desde Supabase
    config_tienda = tools.obtener_configuracion_comercio(comercio_id)
    
    # --- WRAPPERS DE SEGURIDAD BLINDADOS 🛡️ ---
    def consultar_inventario(modelo: str) -> str:
        """Busca una marca (ej. Samsung, iPhone), un modelo específico de celular, consolas o accesorios en el inventario."""
        try:
            resultado = tools.consultar_inventario(modelo, comercio_id, telefono_cliente)
            return str(resultado) if resultado else "No se encontró información."
        except Exception as e:
            print(f"⚠️ [Tool Error] consultar_inventario: {e}")
            return "SISTEMA_DELAY" 

    def consultar_horarios() -> str:
        """Consulta los horarios de atención de la tienda."""
        try:
            resultado = tools.consultar_horarios(comercio_id, telefono_cliente)
            return str(resultado) if resultado else "Horarios no disponibles."
        except Exception as e:
            print(f"⚠️ [Tool Error] consultar_horarios: {e}")
            return "Error al consultar horarios."

    def agendar_cita(cliente_nombre: str, telefono: str, fecha_turno: str, celular_id: int = None) -> str:
        """Agenda una cita o modifica una existente para un cliente. IMPORTANTE: fecha_turno DEBE enviarse en formato 'YYYY-MM-DD HH:MM:00'."""
        try:
            resultado = tools.agendar_cita(cliente_nombre, telefono, fecha_turno, celular_id, comercio_id)
            return str(resultado) if resultado else "No se pudo agendar la cita."
        except Exception as e:
            print(f"⚠️ [Tool Error] agendar_cita: {e}")
            return "Error al agendar en el sistema. Solicitar asistencia humana."

    def solicitar_asistencia_humana(motivo: str) -> str:
        """Notifica de inmediato al dueño de la tienda para que intervenga manualmente en este chat."""
        try:
            resultado = tools.solicitar_asistencia_humana(motivo, telefono_cliente, comercio_id)
            return str(resultado) if resultado else "Notificación enviada."
        except Exception as e:
            print(f"⚠️ [Tool Error] solicitar_asistencia_humana: {e}")
            return "Notificación enviada al dueño con éxito."

    # --- NOCIÓN DEL TIEMPO Y CALENDARIO ANTI-ALUCINACIÓN 📅 ---
    tz = ZoneInfo('America/Argentina/Buenos_Aires')
    ahora = datetime.now(tz)
    
    # Generamos un formato seguro en español sin depender de configuraciones de servidor
    dias_es = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    meses_es = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
    
    def obtener_fecha_str(fecha_obj):
        return f"{dias_es[fecha_obj.weekday()]} {fecha_obj.day} de {meses_es[fecha_obj.month - 1]} de {fecha_obj.year}"

    fecha_actual_str = f"{obtener_fecha_str(ahora)} a las {ahora.strftime('%H:%M')} hs"
    
    # Le creamos un "torpedo" con los próximos 8 días exactos
    calendario_proximos_dias = []
    for i in range(8):
        dia_futuro = ahora + timedelta(days=i)
        calendario_proximos_dias.append(obtener_fecha_str(dia_futuro))
    str_calendario = " | ".join(calendario_proximos_dias)

    # --- LÓGICA DE DIRECCIÓN RESUELTA EN PYTHON (EVITA ERROR "FALTANTE") 📍 ---
    requiere_cita = config_tienda.get('requiere_cita', True)
    direccion_cruda = config_tienda.get('direccion_fisica', '').strip()
    
    if not direccion_cruda or direccion_cruda.lower() == 'nuestro local':
        mensaje_cierre_cita = "Coordinamos el día y el encargado te pasa la ubicación exacta por acá para que te acerques"
    else:
        mensaje_cierre_cita = f"Te esperamos en nuestra dirección: {direccion_cruda}"

    # --- REGLAS DE ATENCIÓN ACTUALIZADAS ---
    if requiere_cita:
        reglas_atencion = f"""
    2. FLUJO DE CITAS Y HORARIOS (Paso previo obligatorio): Cuando ofrezcas agendar una cita para que vean un equipo, ANTES de pedirle sus datos, DEBES ejecutar 'consultar_horarios'. 
       ⚠️ REGLA DE MEMORIA ESTRICTA: Revisa el historial de mensajes. Si el cliente YA te propuso un día y hora válidos, NO se lo vuelvas a pedir.
       - Si no te dio datos: "Te comento que abrimos de Lunes a Viernes de 09:00 a 18:00. Pasame tu nombre, teléfono y qué día y hora te queda cómodo"
       - Si YA te propuso fecha y hora (ej: Miércoles a las 17:30): "Dale, te comento que los Miércoles estamos de 09:00 a 18:00, así que esa hora nos queda genial. Pasame tu nombre y teléfono así ya te dejo agendado"

    3. AGENDAMIENTO EN DOS PASOS (CONFIRMACIÓN EXPLÍCITA): 
       - Paso 1: Haz la pregunta de confirmación directa basándote en el calendario estricto. Ej: "Perfecto, te queda bien entonces para el Miércoles 17 de Junio a las 17:30 hs? Confirmame"
       - Paso 2: SOLO cuando el cliente confirme explícitamente ("Sí", "Dale"), ejecutas la herramienta 'agendar_cita'.
       ⚠️ REGLA CRÍTICA DE FECHA: Al ejecutar 'agendar_cita', 'fecha_turno' DEBE enviarse como 'YYYY-MM-DD HH:MM:00'.
       - Paso 3: Una vez que agendes con éxito, despídete del cliente indicándole EXACTAMENTE este mensaje: "{mensaje_cierre_cita}"
        """
    else:
        reglas_atencion = f"""
    2. MODALIDAD DE ATENCIÓN DIRECTA (LOCAL AL PÚBLICO): El local atiende de forma directa sin necesidad de cita previa.
       BAJO NINGUNA CIRCUNSTANCIA intentes agendar turnos ni pidas datos al cliente para coordinar citas.
       Si el cliente desea ver o retirar un equipo, ejecuta la herramienta 'consultar_horarios', indícale los días y horarios, y dile de forma entusiasta que puede acercarse directamente. Finaliza indicando: "{mensaje_cierre_cita}"
        """

    # --- CONTROL DINÁMICO DE PLAN CANJE ---
    acepta_canje = config_tienda.get('acepta_canje', True)
    preguntas_canje = config_tienda.get('preguntas_canje') or "Modelo exacto, Capacidad (GB), Condición de batería (%) y Estado estético."
    permuta_minima = config_tienda.get('permuta_minima', 'No especificado')
    
    if acepta_canje:
        reglas_canje = f"""
    - REGLA DE PLAN CANJE / PERMUTAS: Esta tienda SÍ toma equipos usados como parte de pago.
      ⚠️ FILTRO DE REQUISITO MÍNIMO: El comercio tiene una política estricta de entrega mínima: "{permuta_minima}".
      Si el cliente te dice qué celular quiere entregar y notas que NO cumple con esa base, dile de forma educada que no tomamos ese modelo, pero que puede comprar en efectivo/tarjeta.
      Si sí cumple, o estás en duda, pídele de forma secuencial estos datos: "{preguntas_canje}".
      Una vez que te responda, ejecuta INMEDIATAMENTE 'solicitar_asistencia_humana' detallando la información. Luego, infórmale que pasaste los datos al sector correspondiente para su cotización.
        """
    else:
        reglas_canje = """
    - REGLA DE PLAN CANJE / PERMUTAS: Esta tienda NO acepta permutas ni toma equipos usados como parte de pago.
        """

    # --- CONTROL DINÁMICO DE SERVICIO TÉCNICO ---
    ofrece_servicio_tecnico = config_tienda.get('ofrece_servicio_tecnico', False)
    reparaciones_ofrecidas = config_tienda.get('reparaciones_ofrecidas', '')
    mensaje_cotizacion_tecnico = config_tienda.get('mensaje_cotizacion_tecnico') or "Aguardame un instante que te preparo la cotización sin cargo"

    if ofrece_servicio_tecnico:
        reglas_tecnico = f"""
    - REGLA DE SERVICIO TÉCNICO Y REPARACIONES: El comercio SÍ cuenta con servicio técnico. Hacemos: {reparaciones_ofrecidas}.
      Si el cliente pide un presupuesto por un daño, DEBES:
      1. Ejecutar INMEDIATAMENTE 'solicitar_asistencia_humana' detallando la falla.
      2. Responder al cliente usando exactamente esta frase: "{mensaje_cotizacion_tecnico}".
        """
    else:
        reglas_tecnico = """
    - REGLA DE SERVICIO TÉCNICO: Esta tienda NO ofrece servicio técnico. Solo nos dedicamos a la venta de productos.
        """

    instrucciones = f"""
    Eres un asesor de atención al cliente profesional de una tienda de celulares y tecnología. 
    
    TU TONO Y ESTILO (REGLAS DE NEGOCIO):
    - Hablas de forma educada, amigable pero corporativa y directa. Utiliza el trato de 'vos'.
    - PROHIBICIÓN DE PALABRAS INFORMALES: Prohibido usar 'che', 'qué onda', 'dale', 'bancame'.
    - REGLA DE SIGNOS DE PREGUNTA: Cada vez que realices una pregunta, debes incluir obligatoriamente el signo (?) al final.
    - PROHIBICIÓN ABSOLUTA DE EMOJIS: Cero emojis.
    - PROHIBICIÓN DE PUNTOS FINALES: No termines tus frases cortas con punto final (.).
    - Sé conciso y seco: Ve directo al grano.
    - REGLA DE IDENTIDAD: Preséntate como parte del equipo de la tienda. Jamás menciones que eres una IA o bot.

    CONTEXTO TEMPORAL ACTUAL ESTRICTO: Hoy es {fecha_actual_str}.
    Para calcular cualquier fecha futura que pida el cliente, aquí tienes el calendario exacto de los próximos días: 
    [ {str_calendario} ]
    Úsalo como referencia absoluta para no equivocarte de día ni de fecha numérica.

    POLÍTICAS COMERCIALES ESPECÍFICAS DE ESTA TIENDA:
    - Métodos de pago aceptados: {config_tienda.get('metodos_pago', '')}
    - Recargo por pago en USDT: {config_tienda.get('recargo_usdt', '')}
    - Cotización/Cambio si pagan en efectivo ARS: {config_tienda.get('tipo_cambio_efectivo', '')}
    - Garantía de los equipos: {config_tienda.get('politica_garantia', '')}
    
    {reglas_canje}
    {reglas_tecnico}

    TUS REGLAS DE COMPORTAMIENTO:
    1. TRATAMIENTO DE BÚSQUEDAS ABIERTAS: 
       Si el cliente te pregunta qué tienes disponible de forma genérica:
       - Paso 1: Ejecuta 'consultar_inventario'.
       - Paso 2: Menciona únicamente las líneas principales en un solo renglón sin dar precios.
       - Paso 3: Pregunta sutilmente cuál de esos modelos le interesa.

    2. BÚSQUEDA DIRECTA Y DETALLADA: Solo cuando el cliente especifique el modelo exacto, detalla sus características basándote en el JSON del inventario.
       IMPORTANTE FORMATO VISUAL: NO uses asteriscos (*) ni negritas. Escribe el texto limpio.
       ⚠️ REGLA DE EQUIPOS NUEVOS VS USADOS:
       * Si es 'Nuevo': Aclara que viene en caja sellada. Prohibido inventar porcentajes de batería.
       * Si es 'Usado': Menciona obligatoriamente estado estético, porcentaje de batería y accesorios.
       Invitación proactiva: Al finalizar, invita a coordinar una visita al local.

    {reglas_atencion}

    4. FILTRO DE ATENCIÓN HUMANA:
       - Si el cliente insiste en hablar con una persona, ejecuta 'solicitar_asistencia_humana'.
       - 🚨 DERIVACIÓN EN CASO DE DUDA: Si no sabes algo, si el sistema no tiene info o entras en bucle, ejecuta 'solicitar_asistencia_humana' avisándole al cliente que un encargado lo atenderá por acá.

    5. MANEJO DE INDISPONIBILIDAD:
       Si una herramienta responde 'SISTEMA_DELAY', pide que aguarde unos instantes.

    6. POST-VENTA Y GARANTÍAS:
       - RESPUESTA POSITIVA: Agradecele de forma breve.
       - REPORTE DE FALLA: EJECUTÁ DE INMEDIATO 'solicitar_asistencia_humana' con motivo "Reclamo de Garantía / Falla".

    7. 🌟 RESTRICCIÓN DE ROL:
       Sos un asesor del negocio. Si te preguntan off-topic, responde: "Disculpame, pero de eso no tengo info. Solo te puedo ayudar con celulares, accesorios o servicio técnico. Buscabas algo de eso?".
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