import requests
import time
import base64
import os

# 1. URL de la API (Correcta)
BASE_URL = "https://evolution-api-production-4b88.up.railway.app"

# 2. Tu API Key
API_KEY = "74BD7CFB-C38A-4143-833A-FCEA92FBBA21"

# 3. ¡NUEVO NOMBRE PARA ROMPER EL CACHÉ ZOMBI!
INSTANCE_NAME = "istoreBot13" 

# 4. URL del webhook (Corregida a la que aparecía en tus logs, confirmala)
WEBHOOK_URL = "https://istore-ai-agent-production.up.railway.app/webhook"

headers = {"apikey": API_KEY, "Content-Type": "application/json"}

print(f"✨ 1. Creando la instancia {INSTANCE_NAME} en modo QR...")

payload_create = {
    "instanceName": INSTANCE_NAME,
    "integration": "WHATSAPP-BAILEYS",
    "qrcode": True 
}
requests.post(f"{BASE_URL}/instance/create", json=payload_create, headers=headers)

print("⏳ 2. Dándole unos segundos al servidor para generar el lienzo...")
time.sleep(3)

print("📲 3. Solicitando el código QR a WhatsApp...")
res_connect = requests.get(f"{BASE_URL}/instance/connect/{INSTANCE_NAME}", headers=headers)
datos = res_connect.json()

if "base64" in datos:
    print("\n✅ ¡QR OBTENIDO CON ÉXITO!")
    
    # Separamos la imagen real de la cabecera del base64
    base64_data = datos["base64"].split(",")[1]
    
    # Guardamos el QR como archivo de imagen
    with open("qr_whatsapp.png", "wb") as f:
        f.write(base64.b64decode(base64_data))
        
    print("📸 Abriendo la imagen del QR en tu Mac...")
    os.system("open qr_whatsapp.png") 
    
    print("\n🔗 4. Conectando el Webhook en segundo plano...")
    # Aseguramos la ruta correcta para setear el webhook (en Evolution suele ser /webhook/set/nombre)
    # Si tu ruta anterior te funcionaba dejala, pero esta es la estándar:
    webhook_endpoint = f"{BASE_URL}/webhook/set/{INSTANCE_NAME}" 
    payload_webhook = {
        "webhook": {
            "enabled": True,
            "url": WEBHOOK_URL,
            "webhookByEvents": False,
            "events": ["MESSAGES_UPSERT"]
        }
    }
    requests.post(webhook_endpoint, json=payload_webhook, headers=headers)
    print("✅ ¡Webhook configurado! Esperando que escanees...")
else:
    print("\n⚠️ No se pudo obtener el QR. El servidor dijo:")
    print(datos)