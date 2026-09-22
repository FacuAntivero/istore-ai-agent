"""
Cliente de la API oficial de WhatsApp (Meta Cloud API), para la vertical
"consultorio". Es el equivalente de las llamadas a Evolution API que ya
existen para la vertical "celulares", pero con el shape propio de Meta:
distinta URL, autenticación por Bearer token, y un endpoint separado para
mensajes con plantilla (obligatorios para cualquier mensaje que el negocio
inicie sin que el cliente haya escrito primero, como los recordatorios).

access_token y phone_number_id son por tenant (viven en la fila de
`comercios` de cada consultorio), así que se reciben como parámetro en vez
de leerse de config.py.
"""
import hashlib
import hmac
import requests

GRAPH_API_VERSION = "v20.0"


def enviar_mensaje_whatsapp_meta(numero_destino: str, texto: str, phone_number_id: str, access_token: str) -> bool:
    """Manda un mensaje de texto libre. Solo válido como respuesta dentro de
    una conversación que el cliente inició (ventana de 24hs) — para mensajes
    iniciados por el negocio hay que usar enviar_plantilla_whatsapp_meta."""
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": numero_destino,
        "type": "text",
        "text": {"body": texto},
    }

    try:
        respuesta = requests.post(url, headers=headers, json=payload)
        if respuesta.status_code == 200:
            print(f"✅ [Meta] Mensaje enviado a {numero_destino}")
            return True
        print(f"❌ [Meta] Error al enviar mensaje: {respuesta.status_code} {respuesta.text}")
        return False
    except Exception as e:
        print(f"❌ [Meta] Error de red al enviar mensaje: {e}")
        return False


def enviar_plantilla_whatsapp_meta(numero_destino: str, phone_number_id: str, access_token: str,
                                    nombre_plantilla: str, idioma: str = "es_AR", parametros: list = None) -> bool:
    """Manda un mensaje de plantilla pre-aprobada por Meta (recordatorios,
    o cualquier mensaje que el negocio dispara sin que el cliente escribió
    primero). `parametros` es la lista de textos que rellenan las variables
    {{1}}, {{2}}... de la plantilla, en orden."""
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    componentes = []
    if parametros:
        componentes.append({
            "type": "body",
            "parameters": [{"type": "text", "text": str(p)} for p in parametros],
        })

    payload = {
        "messaging_product": "whatsapp",
        "to": numero_destino,
        "type": "template",
        "template": {
            "name": nombre_plantilla,
            "language": {"code": idioma},
            "components": componentes,
        },
    }

    try:
        respuesta = requests.post(url, headers=headers, json=payload)
        if respuesta.status_code == 200:
            print(f"✅ [Meta] Plantilla '{nombre_plantilla}' enviada a {numero_destino}")
            return True
        print(f"❌ [Meta] Error al enviar plantilla: {respuesta.status_code} {respuesta.text}")
        return False
    except Exception as e:
        print(f"❌ [Meta] Error de red al enviar plantilla: {e}")
        return False


def verificar_firma_meta(payload_bytes: bytes, signature_header: str, app_secret: str) -> bool:
    """Valida que un webhook entrante realmente venga de Meta, comparando
    la firma HMAC-SHA256 que manda en el header X-Hub-Signature-256 contra
    una calculada acá con el App Secret. Sin esto, cualquiera que descubra
    la URL del webhook podría mandar mensajes falsos como si fueran de un
    paciente real."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False

    firma_recibida = signature_header.split("sha256=", 1)[1]
    firma_calculada = hmac.new(app_secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(firma_recibida, firma_calculada)
