from google.genai import errors
from agent import iniciar_agente

# 💡 ID de tu comercio para pruebas en la terminal
COMERCIO_TEST_ID = "85d583ba-9421-4225-b337-52cef3c66d9f"

def main():
    # Le pasamos el ID al inicializar el agente
    chat = iniciar_agente(COMERCIO_TEST_ID)
    
    print("🤖 Agente de Ventas iniciado. Escribe 'salir' para terminar.")
    
    while True:
        usuario = input("\nTú: ")
        if usuario.lower() == 'salir':
            break
            
        try:
            respuesta = chat.send_message(usuario)
            print(f"\nAgente: {respuesta.text}")
            
        except errors.APIError as e:
            if e.code == 503:
                print("\n[Sistema] ⚠️ Los servidores de IA están saturados en este momento. Por favor, intenta de nuevo en unos segundos.")
            elif e.code == 429:
                print("\n[Sistema] ⚠️ Alcanzamos el límite de peticiones de la API gratuita. Espera un minuto.")
            else:
                print(f"\n[Sistema] ❌ Error de la API de Google: {e.message}")
                
        except Exception as e:
            print(f"\n[Sistema] ❌ Ocurrió un error inesperado: {str(e)}")

if __name__ == "__main__":
    main()