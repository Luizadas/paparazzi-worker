import subprocess
import os
import sys
import requests

def check_ffmpeg():
    print("[*] Verificando FFmpeg...")
    try:
        resultado = subprocess.run(["ffmpeg", "-version"], check=True, capture_output=True, text=True)
        print("  - [OK] FFmpeg encontrado no PATH do sistema.")
    except FileNotFoundError:
        print("  - [ERRO] Comando 'ffmpeg' não encontrado! Por favor, instale o FFmpeg e adicione-o às variáveis de ambiente (PATH) do Windows.")
        print("  - Dica: Você pode baixar em https://www.gyan.dev/ffmpeg/builds/ ou usar o winget: winget install ffmpeg")
    except Exception as e:
        print(f"  - [ERRO] Ocorreu um erro ao verificar o FFmpeg: {e}")

def check_whisper():
    print("\n[*] Verificando OpenAI Whisper...")
    try:
        import whisper
        print("  - [OK] Biblioteca 'whisper' importada com sucesso da sua instalação Python.")
    except ImportError:
        print("  - [ERRO] Import 'whisper' falhou. Não foi possível encontrar a biblioteca 'openai-whisper'.")
        print("  - Dica: Instale rodando: pip install openai-whisper")
        
def check_ollama():
    print("\n[*] Verificando serviço do Ollama local...")
    # Ollama padrão usa a porta 11434.
    url = "http://localhost:11434/api/tags"
    try:
        resposta = requests.get(url, timeout=3)
        resposta.raise_for_status()
        modelos = resposta.json().get("models", [])
        if modelos:
            nomes = [m['name'] for m in modelos]
            print(f"  - [OK] Ollama está rolando perfeitamente! Modelos encontrados localmente: {', '.join(nomes)}")
        else:
            print("  - [OK] Instância do Ollama está ativa, MAS nenhum modelo foi encontrado baixado.")
            print("  - Dica: Rode 'ollama run deepseek-coder-v2' (ou 'deepseek-v3') no terminal.")
            
    except requests.exceptions.ConnectionError:
        print("  - [ERRO] Não foi possível conectar ao Ollama! O aplicativo do Ollama está rodando na bandeja do sistema?")
    except requests.exceptions.Timeout:
        print("  - [ERRO] Tempo excedido ao tentar conectar ao Ollama. Verifique se a API local está acessível.")
    except Exception as e:
        print(f"  - [ERRO] Ocorreu um erro ao acessar a API do Ollama: {e}")

if __name__ == "__main__":
    print("=" * 50)
    print("   Papazarri Worker - Diagnóstico de Ambiente    ")
    print("=" * 50)
    print(f"System Python Executable: {sys.executable}")
    check_ffmpeg()
    check_whisper()
    check_ollama()
    print("=" * 50)
