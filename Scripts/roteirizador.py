# roteirizador.py

import whisper
import subprocess
import os
import json
import requests
from datetime import timedelta

# --- A função que já tínhamos, sem alterações ---
def transcrever_video_localmente(caminho_video: str, modelo_whisper: str = "base") -> dict:
    # ... (código da função anterior exatamente como estava) ...
    """
    Extrai o áudio de um vídeo, transcreve usando o Whisper local e limpa os arquivos temporários.
    """
    print(f"Iniciando o processo de transcrição para: {caminho_video}")

    if not os.path.exists(caminho_video):
        raise FileNotFoundError(f"O arquivo de vídeo não foi encontrado em: {caminho_video}")

    caminho_audio_temporario = "temp_audio.wav"

    try:
        print("Passo 1/3: Extraindo áudio com FFmpeg...")
        comando_ffmpeg = [
            "ffmpeg", "-i", caminho_video, "-vn", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1", caminho_audio_temporario, "-y"
        ]
        subprocess.run(comando_ffmpeg, check=True, capture_output=True, text=True)
        print("Áudio extraído com sucesso.")
    except subprocess.CalledProcessError as e:
        print(f"Erro ao executar o FFmpeg: {e.stderr}")
        raise
    except FileNotFoundError:
        print("Erro: FFmpeg não encontrado. Ele está instalado e no PATH do sistema?")
        raise

    try:
        print(f"Passo 2/3: Carregando o modelo '{modelo_whisper}' do Whisper...")
        model = whisper.load_model(modelo_whisper)
        
        print("Iniciando a transcrição (isso pode levar um tempo)...")
        resultado = model.transcribe(caminho_audio_temporario, fp16=False)
        print("Transcrição concluída.")

    finally:
        print("Passo 3/3: Limpando arquivo de áudio temporário...")
        if os.path.exists(caminho_audio_temporario):
            os.remove(caminho_audio_temporario)
            print("Arquivo temporário removido.")

    return resultado

# --- NOVA FUNÇÃO ---
def analisar_transcricao_com_llm(transcricao_result: dict, llm_api_url: str) -> list:
    """
    Envia a transcrição para um LLM local e pede para identificar clipes virais.
    """
    print("\nPasso 4/4: Analisando transcrição com o LLM...")
    
    # Formatando a transcrição para ser enviada
    texto_formatado = ""
    for segment in transcricao_result['segments']:
        start = int(segment['start'])
        texto = segment['text']
        texto_formatado += f"[{start}s] {texto.strip()}\n"

    # Nosso prompt estratégico V1
    prompt = f"""Você é um editor de vídeos experiente no mercado focado em do tiktok , especialista em identificar e extrair clipes virais de conteúdo longo para plataformas como TikTok, Reels e YouTube Shorts. Você receberá a transcrição de um vídeo, com timestamps para cada segmento de fala. Sua tarefa é analisar a transcrição e identificar de 3 a 5 momentos com o maior potencial de viralização. Um bom clipe deve ter entre 60 e 90 segundos. Critérios para um clipe de alto potencial: - **Gancho Forte:** Começa com uma pergunta intrigante, uma afirmação polêmica ou uma promessa de valor clara. - **Emoção:** Contém picos de emoção como risadas, surpresa, raiva ou paixão. - **Conteúdo de Valor:** Oferece uma dica prática, um insight profundo ou uma explicação clara sobre um tópico complexo. - **Narrativa Concisa:** Possui um início, meio e fim claros dentro do segmento. Para cada clipe identificado, você deve retornar APENAS um objeto JSON em um formato de lista. Não adicione nenhuma explicação ou texto fora do JSON. O formato deve ser o seguinte: [{{ "start": <timestamp inicial em segundos>, "end": <timestamp final em segundos>, "justificativa": "<uma frase curta explicando por que este clipe tem alto potencial viral, baseada nos critérios acima>" }}]

--- TRANSCRIÇÃO DO VÍDEO ---
{texto_formatado}
"""

    # Corpo da requisição para a API do LLM (ex: Ollama)
    payload = {
        "model": "deepseek-coder-v2", # Mude para o modelo que você está usando localmente
        "prompt": prompt,
        "stream": False, # Queremos a resposta completa de uma vez
        "format": "json" # Pedindo para a API já formatar a saída como JSON
    }

    try:
        print("Enviando para a API do LLM. Aguardando a análise...")
        response = requests.post(llm_api_url, json=payload, timeout=300) # Timeout de 5 minutos
        response.raise_for_status() # Lança um erro para respostas 4xx/5xx

        # A resposta da API do Ollama vem com o JSON dentro de uma chave 'response'
        response_json_str = response.json().get("response", "{}")
        pacote_de_cortes = json.loads(response_json_str)
        print("Pacote de cortes recebido e analisado com sucesso!")
        return pacote_de_cortes

    except requests.exceptions.RequestException as e:
        print(f"Erro ao se comunicar com a API do LLM: {e}")
        return None
    except json.JSONDecodeError:
        print("Erro: O LLM não retornou um JSON válido. Resposta recebida:")
        print(response.text)
        return None


# --- Bloco de teste ATUALIZADO ---
if __name__ == '__main__':
    # URL da sua API do LLM local. Se usa Ollama, provavelmente é esta.
    LLM_API_URL = "http://localhost:11434/api/generate"
    
    DOWNLOAD_DIR = "/mnt/paparazzi"
    
    print(f"[*] Buscando vídeos na pasta: {DOWNLOAD_DIR}")
    arquivo_de_teste = None
    
    if os.path.exists(DOWNLOAD_DIR):
        for file in os.listdir(DOWNLOAD_DIR):
            if file.endswith(".mp4"):
                arquivo_de_teste = os.path.join(DOWNLOAD_DIR, file)
                break
                
    if not arquivo_de_teste:
        print(f"AVISO: Nenhum arquivo de vídeo (.mp4) encontrado na pasta '{DOWNLOAD_DIR}'.")
        print("Dica: Use o coletor.py para baixar um vídeo primeiro!")
    else:
        print(f"[*] Vídeo selecionado para teste: {arquivo_de_teste}")
        try:
            # Etapa 1: Transcrição
            transcricao_completa = transcrever_video_localmente(arquivo_de_teste, modelo_whisper="base")
            
            # Etapa 2: Análise com LLM
            if transcricao_completa:
                pacote_final = analisar_transcricao_com_llm(transcricao_completa, LLM_API_URL)
                
                if pacote_final:
                    print("\n--- PACOTE DE CORTES FINAL (JSON) ---")
                    # Usamos json.dumps para imprimir o JSON de forma legível (pretty print)
                    print(json.dumps(pacote_final, indent=2, ensure_ascii=False))

        except Exception as e:
            print(f"\nOcorreu um erro durante a execução do pipeline: {e}")