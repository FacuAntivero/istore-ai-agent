import requests
import time

# ACORDATE DE PONER TU ENLACE NUEVO ACÁ ABAJO
BASE_URL = "https://evolution-api-production-4b88.up.railway.app" 
API_KEY = "74BD7CFB-C38A-4143-833A-FCEA92FBBA21"
INSTANCE_NAME = "istoreBot10"
NUMERO = "5492494600615"
WEBHOOK_URL = "https://web-production-cadf4.up.railway.app/webhook"

headers = {"apikey": API_KEY, "Content-Type": "application/json"}

print(f"✨ 1. Creando la instancia {INSTANCE_NAME}...")

# Paso 1: Le mandamos TODOS los datos desde el principio, incluyendo el número.
payload_create = {
    "instanceName": INSTANCE_NAME,
    "integration": "WHATSAPP-BAILEYS",
    "qrcode": False,
    "number": NUMERO
}

res_create = requests.post(f"{BASE_URL}/instance/create", json=payload_create, headers=headers)
print(f"   Respuesta del servidor al crear: {res_create.json()}")

print("\n⏳ 2. Dándole 5 segundos al servidor para generar el código...")
time.sleep(5)

print("\n📲 3. Solicitando código de emparejamiento...")
res_connect = requests.get(f"{BASE_URL}/instance/connect/{INSTANCE_NAME}?number={NUMERO}", headers=headers)
datos = res_connect.json()

if "pairingCode" in datos and datos["pairingCode"]:
    print("\n✅ ¡CÓDIGO OBTENIDO CON ÉXITO!\n")
    print(f"👉 TU CÓDIGO PARA WHATSAPP ES:  {datos['pairingCode']}\n")
    
    print("🔗 4. Conectando el Webhook...")
    payload_webhook = {
        "webhook": {
            "enabled": True,
            "url": WEBHOOK_URL,
            "webhookByEvents": False,
            "events": ["MESSAGES_UPSERT"]
        }
    }
    requests.post(f"{BASE_URL}/webhook/instance/{INSTANCE_NAME}", json=payload_webhook, headers=headers)
    print("✅ ¡Webhook configurado!")
else:
    print("\n⚠️ El servidor no mandó el código. Esta es la respuesta exacta:")
    print(datos)