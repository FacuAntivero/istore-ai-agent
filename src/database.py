from supabase import create_client, Client
import config

# Inicializamos el cliente usando las credenciales de config.py
supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)