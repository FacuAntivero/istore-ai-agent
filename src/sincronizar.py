import sys
import requests
import json  # Importamos para poder formatear la inspección
import config
from database import supabase

# Configuración
EVOLUTION_URL = config.EVOLUTION_API_URL
API_KEY = config.EVOLUTION_API_KEY

# Antes estos dos valores estaban hardcodeados (INSTANCE_NAME = "istoreBot11" y
# comercio_id 1 fijo más abajo). En un sistema multi-tenant eso significa que
# correr este script para sincronizar los contactos de OTRO comercio los
# hubiera guardado igual como si fueran del comercio 1. Ahora son argumentos
# obligatorios para que sea imposible correrlo "por las dudas" sin pensarlo.
if len(sys.argv) != 3:
    sys.exit(
        "Uso: python sincronizar.py <INSTANCE_NAME> <COMERCIO_ID>\n"
        "Ejemplo: python sincronizar.py istoreBot11 3"
    )

INSTANCE_NAME = sys.argv[1]
COMERCIO_ID = int(sys.argv[2])

headers = {
    "apiKey": API_KEY,
    "Content-Type": "application/json"
}

print("📥 Buscando contactos en Evolution API (v2)...")
try:
    url = f"{EVOLUTION_URL}/chat/findContacts/{INSTANCE_NAME}"
    payload = {"where": {}}
    
    response = requests.post(url, headers=headers, json=payload)
    contacts = response.json()
    
    # Manejo de la estructura de respuesta de Evolution v2
    if isinstance(contacts, dict):
        contacts = contacts.get("contacts", contacts.get("response", []))
        
    if not isinstance(contacts, list):
        if isinstance(response.json(), list):
            contacts = response.json()
        else:
            contacts = []

    # 🔬 BLOQUE DE INSPECCIÓN DE DATOS
    # Esto nos va a mostrar en la terminal exactamente qué campos trae un contacto
    if isinstance(contacts, list) and len(contacts) > 0:
        print("\n🔬 INSPECCIÓN DEL PRIMER CONTACTO COMPLETO:")
        print(json.dumps(contacts[0], indent=2))
        print("-" * 50 + "\n")

    print(f"📋 Se encontraron {len(contacts)} registros en la respuesta. Sincronizando...")
    
    count = 0
    for c in contacts:
        lid = c.get("lid")
        # Sumamos 'remoteJid' al mapeo que es el que vimos en tu log anterior
        id_jid = c.get("remoteJid") or c.get("id") or c.get("wuid") or c.get("jid")
        name = c.get("pushName") or c.get("name") or "Cliente antiguo"
        
        # Validación estricta original
        if lid and id_jid and "@lid" in lid and "@s.whatsapp.net" in id_jid:
            supabase.table("contactos").upsert({
                "lid": lid,
                "numero": id_jid,
                "nombre": name,
                "comercio_id": COMERCIO_ID
            }).execute()
            count += 1

    print(f"🎯 ¡Sincronización completada! Se guardaron {count} mapeos válidos en Supabase.")

except Exception as e:
    print(f"❌ Error durante la sincronización: {e}")