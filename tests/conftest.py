"""
Hace que los módulos de src/ (config, database, tools, auth, ...) se puedan
importar de forma "plana" (import tools, no from src import tools) desde los
tests, igual que los importa el resto de la app.

También fuerza variables de entorno dummy ANTES de que nada importe config.py,
para que los tests corran sin necesitar un .env real ni credenciales de
verdad — así funcionan igual en tu máquina, en la de otra persona, o en CI.
"""
import os
import sys

os.environ.setdefault("SUPABASE_URL", "https://dummy.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "dummy-key")
os.environ.setdefault("GEMINI_API_KEY", "dummy-key")
os.environ.setdefault("EVOLUTION_API_URL", "https://dummy.example.com")
os.environ.setdefault("EVOLUTION_API_KEY", "dummy-key")
os.environ.setdefault("META_APP_SECRET", "dummy-secret")
os.environ.setdefault("META_VERIFY_TOKEN", "dummy-verify-token")
os.environ.setdefault("UPSTASH_REDIS_URL", "rediss://dummy")
os.environ.setdefault("QSTASH_TOKEN", "dummy-token")
os.environ.setdefault("URL_RAILWAY", "https://dummy.example.com")

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC_DIR))
