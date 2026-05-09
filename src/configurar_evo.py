import requests

BASE_URL = "http://localhost:8080"
# Usamos la API Key que Evolution API tenía en tus logs anteriores
API_KEY = "ADAC8995-41C5-45CF-8BFD-DCC0D4CAD67C" 
NGROK_URL = "https://dfdc-152-170-13-129.ngrok-free.app/webhook"

headers = {
    "Content-Type": "application/json",
    "apikey": API_KEY
}

print("1️⃣ Solicitando creación de instancia 'istoreBot'...")
payload_instancia = {
    "instanceName": "istoreBot",
    "qrcode": True
}
response = requests.post(f"{BASE_URL}/instance/create", json=payload_instancia, headers=headers)
datos_instancia = response.json()

if "base64" in datos_instancia or "qrcode" in datos_instancia:
    qr_data = datos_instancia.get("base64") or datos_instancia.get("qrcode", {}).get("base64")
    print("\n✅ ¡QR Generado con éxito! Aquí tienes el código:\n")
    print(qr_data)
    print("\n👉 INSTRUCCIÓN: Copia todo ese texto (desde 'data:image/png;base64...'), pégalo en la barra de direcciones de tu navegador (Chrome o Safari) y presiona Enter. ¡Aparecerá el código QR para que lo escanees con tu WhatsApp!")
else:
    print("\n⚠️ No se pudo generar el QR. Respuesta del servidor:")
    print(datos_instancia)

print("\n--------------------------------------------------")

print("\n2️⃣ Configurando el Webhook hacia Ngrok...")
payload_webhook = {
    "webhook": {
        "enabled": True,
        "url": NGROK_URL,
        "events": ["MESSAGES_UPSERT"]
    }
}
# Nota: Si falla, a veces la estructura en versiones viejas es sin la llave "webhook" principal.
res_webhook = requests.post(f"{BASE_URL}/webhook/set/istoreBot", json=payload_webhook, headers=headers)
print("\n✅ Respuesta del Webhook:", res_webhook.json())