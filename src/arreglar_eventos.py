import requests

BASE_URL = "https://evolution-api-production-4b88.up.railway.app"
API_KEY = "74BD7CFB-C38A-4143-833A-FCEA92FBBA21"
INSTANCE_NAME = "istoreBot11"
# Tu URL del cerebro en Railway
NUEVO_WEBHOOK_URL = "https://istore-ai-agent-production.up.railway.app/webhook" 

headers = {"apikey": API_KEY, "Content-Type": "application/json"}

# ¡Acá está la clave! Agregamos CONTACTS_UPSERT y SEND_MESSAGE
payload_webhook = {
    "webhook": {
        "enabled": True,
        "url": NUEVO_WEBHOOK_URL,
        "webhookByEvents": False,
        "events": [
            "MESSAGES_UPSERT", 
            "CONTACTS_UPSERT", 
            "SEND_MESSAGE"
        ]
    }
}

print("🔗 Actualizando los permisos del webhook en Evolution...")

res = requests.post(f"{BASE_URL}/webhook/set/{INSTANCE_NAME}", json=payload_webhook, headers=headers)

if res.status_code in [200, 201]:
    print("✅ ¡Permisos actualizados! Ahora el bot va a recibir los números reales.")
    print("Respuesta:", res.json())
else:
    print(f"⚠️ Error: {res.text}")