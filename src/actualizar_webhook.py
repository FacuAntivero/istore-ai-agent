import requests

# 1. Tu URL de EVOLUTION API (el servidor de los mensajes, el mismo del código QR)
BASE_URL = "https://evolution-api-production-4b88.up.railway.app"
API_KEY = "74BD7CFB-C38A-4143-833A-FCEA92FBBA21"
INSTANCE_NAME = "istoreBot11"

# 2. La NUEVA URL de tu cerebro Python 
NUEVO_WEBHOOK_URL = "https://istore-ai-agent-production.up.railway.app/webhook" 

headers = {"apikey": API_KEY, "Content-Type": "application/json"}

payload_webhook = {
    "webhook": {
        "enabled": True,
        "url": NUEVO_WEBHOOK_URL,
        "webhookByEvents": False,
        "events": ["MESSAGES_UPSERT"]
    }
}

print(f"🔗 Avisándole a {INSTANCE_NAME} que cambie su ruta...")

# ACÁ ESTÁ LA MAGIA ARREGLADA (/webhook/set/...)
res = requests.post(f"{BASE_URL}/webhook/set/{INSTANCE_NAME}", json=payload_webhook, headers=headers)

if res.status_code in [200, 201]:
    print("✅ ¡Conexión exitosa! El mensajero y el cerebro ya están hablando de nuevo.")
    print("Respuesta del servidor:", res.json())
else:
    print(f"⚠️ Hubo un error: {res.text}")