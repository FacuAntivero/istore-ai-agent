import os
import requests
from supabase import create_client
from dotenv import load_dotenv
import json  # Importamos para poder formatear la inspección

load_dotenv()

# Configuración (Tus credenciales actualizadas de Railway)
EVOLUTION_URL = "https://evolution-api-production-4b88.up.railway.app" 
INSTANCE_NAME = "istoreBot11"
API_KEY = "74BD7CFB-C38A-4143-833A-FCEA92FBBA21"

# Credenciales de Supabase 
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

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
                "comercio_id": 1
            }).execute()
            count += 1

    print(f"🎯 ¡Sincronización completada! Se guardaron {count} mapeos válidos en Supabase.")

except Exception as e:
    print(f"❌ Error durante la sincronización: {e}")