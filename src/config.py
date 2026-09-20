"""
Punto único de configuración del proyecto.

Todas las variables de entorno que necesita la app se leen ACÁ, una sola vez,
y el resto del código las importa desde este módulo en lugar de llamar a
os.getenv() por su cuenta. Esto evita que un secreto quede hardcodeado o
repetido en varios archivos, y hace evidente (con un vistazo a este archivo)
qué necesita configurado un entorno nuevo para levantar el proyecto.

Ver .env.example para la lista de variables con una breve descripción de cada una.
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()


def _requerida(nombre: str) -> str:
    """Lee una variable de entorno obligatoria. Si falta, corta la ejecución
    con un mensaje claro en vez de dejar que el programa arranque a medias
    y falle más adelante de forma confusa."""
    valor = os.getenv(nombre)
    if not valor:
        sys.exit(f"❌ Falta configurar la variable de entorno obligatoria: {nombre}")
    return valor


def _opcional(nombre: str, default: str = None) -> str:
    return os.getenv(nombre, default)


# --- Supabase ---
SUPABASE_URL = _requerida("SUPABASE_URL")
SUPABASE_KEY = _requerida("SUPABASE_KEY")

# --- Google Gemini (agente de IA) ---
GEMINI_API_KEY = _requerida("GEMINI_API_KEY")

# --- Evolution API (gateway de WhatsApp) ---
EVOLUTION_API_URL = _requerida("EVOLUTION_API_URL")
EVOLUTION_API_KEY = _requerida("EVOLUTION_API_KEY")

# --- Redis / Upstash (buffer de mensajes, anti-duplicados, caché) ---
UPSTASH_REDIS_URL = _requerida("UPSTASH_REDIS_URL")

# --- Upstash QStash (recordatorios y mensajes programados) ---
QSTASH_TOKEN = _requerida("QSTASH_TOKEN")
URL_RAILWAY = _requerida("URL_RAILWAY")  # URL pública de este mismo servicio, para que QStash le pegue de vuelta
QSTASH_URL = _opcional("QSTASH_URL", "https://qstash-us-east-1.upstash.io/v2/publish")

# --- MercadoPago (cobro de suscripciones) ---
# Opcional: si falta, el servidor arranca igual pero el checkout queda deshabilitado.
MERCADOPAGO_ACCESS_TOKEN = _opcional("MERCADOPAGO_ACCESS_TOKEN")

# --- Varios ---
DEBOUNCE_SECONDS = float(_opcional("DEBOUNCE_SECONDS", "60"))
MI_NUMERO = _opcional("MI_NUMERO", "5492494600615@s.whatsapp.net")
