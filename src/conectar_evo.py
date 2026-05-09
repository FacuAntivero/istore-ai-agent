import requests
import time

BASE_URL = "http://localhost:8080"
API_KEY = "B6D711FCDE4D4FD5936544120E713976"
NGROK_URL = "https://dfdc-152-170-13-129.ngrok-free.app/webhook"
headers = {
    "Content-Type": "application/json",
    "apikey": API_KEY
}

print("🧹 1. Borrando sesión trabada en Docker...")
requests.delete(f"{BASE_URL}/instance/delete/istoreBot", headers=headers)
time.sleep(2) # Esperamos 2 segunditos a que se borre bien

print("✨ 2. Creando la instancia desde cero...")
payload_create = {
    "instanceName": "istoreBot",
    "qrcode": True
}
res_create = requests.post(f"{BASE_URL}/instance/create", json=payload_create, headers=headers)
datos_create = res_create.json()

if "qrcode" in datos_create:
    print("\n✅ ¡NUEVO QR LIMPIO! Cópialo y decodifícalo rápido:\n")
    print(datos_create["qrcode"].get("base64", ""))
else:
    print("\n❌ Algo falló al crear:", datos_create)

print("\n--------------------------------------------------")
print("🔗 3. Volviendo a conectar el Webhook a Ngrok...")
payload_webhook = {
    "enabled": True,
    "url": NGROK_URL,
    "webhookByEvents": False,
    "events": ["MESSAGES_UPSERT"]
}
res_webhook = requests.post(f"{BASE_URL}/webhook/set/istoreBot", json=payload_webhook, headers=headers)
print("✅ Webhook listo:", res_webhook.json().get("webhook", {}).get("enabled", False))