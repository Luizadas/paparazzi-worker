# coletor.py - Download e Processamento de Shorts (Mirror-Clips)

import sys
import os
import re
import requests
import yt_dlp
from datetime import datetime
import subprocess
import shutil
import whisper
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

# Integração com módulo de postagem
try:
    from poster import adicionar_na_fila
    POSTER_DISPONIVEL = True
except ImportError:
    POSTER_DISPONIVEL = False

DOWNLOAD_DIR = "/mnt/paparazzi/mirror_clips"

# LLM local (Ollama) para gerar a legenda do post
LLM_API_URL = os.getenv("LLM_API_URL", "http://localhost:11434/api/generate")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-r1:latest")


def gerar_legenda_ia(texto_transcricao: str, titulo_original: str = "") -> str:
    """
    Gera a legenda do TikTok via LLM local (Ollama) com BASE no conteúdo do
    próprio vídeo (transcrição do Whisper + título original), orientando o LLM
    com técnicas de viralização no TikTok.
    Retorna string vazia se o LLM falhar (o poster cai no template padrão).
    """
    texto = (texto_transcricao or "").strip()
    if not texto:
        return ""

    contexto_titulo = f"\nTÍTULO ORIGINAL DO VÍDEO: {titulo_original}\n" if titulo_original else ""

    prompt = f"""Você é um especialista em crescimento e viralização no TikTok no Brasil,
com anos escrevendo legendas que geram milhões de views. Sua tarefa é criar a
LEGENDA de um post baseada NO CONTEÚDO DO PRÓPRIO VÍDEO (a transcrição abaixo).

Aplique as melhores práticas de viralização no TikTok:
- GANCHO nos primeiros segundos: comece com algo que gera curiosidade, choque,
  polêmica ou identificação imediata (ex: pergunta, afirmação forte, "ninguém te conta que...").
- Crie CURIOSITY GAP: insinue algo sem entregar tudo, pra fazer o usuário assistir até o fim.
- Gatilhos emocionais (surpresa, humor, indignação, inspiração) e linguagem coloquial brasileira.
- Um CTA sutil quando fizer sentido (ex: "marca alguém", "concorda?", "comenta aí").
- Termine com 4 a 6 HASHTAGS de alto alcance, misturando amplas (#fyp #viral #foryou)
  com específicas do tema do vídeo.

Regras de saída (OBRIGATÓRIAS):
- Escreva EXCLUSIVAMENTE em PORTUGUÊS DO BRASIL.
- NÃO use caracteres chineses, japoneses, coreanos, russos ou de qualquer outro
  idioma. As hashtags devem estar apenas em português ou inglês.
- Legenda curta e escaneável (idealmente até 150 caracteres antes das hashtags).
- Pode usar 1 ou 2 emojis se combinar.
- Responda APENAS com a legenda final (texto + hashtags) em UMA linha. Sem aspas,
  sem títulos, sem explicações, sem "Legenda:".
{contexto_titulo}
--- TRANSCRIÇÃO DO VÍDEO ---
{texto[:2500]}
"""

    payload = {
        "model": LLM_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.7, "num_predict": 200},
    }
    try:
        print("🧠 Gerando legenda com IA (LLM)...")
        resp = requests.post(LLM_API_URL, json=payload, timeout=180)
        resp.raise_for_status()
        saida = resp.json().get("response", "") or ""

        legenda = _sanitizar_legenda(saida)
        if legenda:
            print(f"✅ Legenda IA: {legenda}")
        return legenda[:2200]
    except Exception as e:
        print(f"⚠️ Falha ao gerar legenda com IA: {e}. Usarei o template padrão.")
        return ""


def _sanitizar_legenda(saida: str) -> str:
    """Limpa a resposta do LLM: remove blocos de raciocínio, caracteres CJK/estrangeiros
    e prefixos, deixando apenas a legenda em português + hashtags."""
    if not saida:
        return ""
    # Remove blocos <think>...</think> de modelos de raciocínio
    saida = re.sub(r"<think>.*?</think>", "", saida, flags=re.DOTALL)
    # Remove caracteres CJK (chinês/japonês/coreano) e formas de largura total
    saida = re.sub(
        r"[　-〿぀-ヿㇰ-ㇿ㐀-䶿"
        r"一-鿿ꥠ-꥿가-퟿豈-﫿＀-￯]",
        "", saida,
    )
    # Junta em uma linha, remove aspas e prefixos comuns
    linhas = [l.strip().strip('"').strip() for l in saida.splitlines() if l.strip()]
    legenda = " ".join(linhas).strip()
    legenda = re.sub(r"^(legenda|caption)\s*:\s*", "", legenda, flags=re.IGNORECASE)
    # Colapsa espaços múltiplos criados pela remoção de caracteres
    legenda = re.sub(r"\s{2,}", " ", legenda).strip()
    return legenda

def baixar_short(url):
    """
    Usa a biblioteca yt-dlp para baixar um vídeo de uma URL.
    """
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    print(f"[{timestamp}] 📥 Iniciando download de: {url}")

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    
    # Extraindo apenas ID e Titulo para formar o nome de saída
    out_tmpl = os.path.join(DOWNLOAD_DIR, f'%(id)s_{timestamp}.%(ext)s')
    
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': out_tmpl,
        'noplaylist': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info_dict)
            print(f"✅ Download concluído: {filename}")
            return filename
    except Exception as e:
        print(f"❌ ERRO ao tentar baixar o vídeo. Motivo: {e}")
        return None

def processar_video(video_path):
    print(f"\n⚙️ Iniciando processamento de: {video_path}")
    base_dir = os.path.dirname(video_path)
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    
    srt_path = os.path.join(base_dir, f"{base_name}.srt")
    audio_path = os.path.join(base_dir, f"{base_name}_audio.wav")
    final_output = os.path.join(base_dir, f"{base_name}_final.mp4")

    # Passo 1: Extrair áudio para o Whisper
    print("Passo 1/3: Extraindo áudio para transcrição...")
    try:
        subprocess.run([
            "ffmpeg", "-i", video_path, "-vn", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1", audio_path, "-y"
        ], check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f"❌ Erro ao extrair áudio: {e.stderr.decode('utf-8', errors='ignore')}")
        return

    # Passo 2: Gerar Legendas (SRT) usando o CLI do Whisper
    print("Passo 2/3: Gerando legendas com Whisper...")
    try:
        # Chama o whisper localmente pedindo formato SRT
        subprocess.run([
            "whisper", audio_path, "--model", "base", "--output_format", "srt", "--output_dir", base_dir
        ], check=True)
    except subprocess.CalledProcessError as e:
        print(f"❌ Erro ao rodar whisper: {e}")
        return
    except FileNotFoundError:
        print("❌ Whisper não encontrado no PATH. Certifique-se de que está instalado.")
        return

    # Verificar se o srt foi gerado. O whisper pode salvar como .wav.srt dependendo de como é chamado, ou com o mesmo nome do áudio.
    whisper_srt_output = os.path.join(base_dir, f"{base_name}_audio.srt")
    if os.path.exists(whisper_srt_output):
        os.rename(whisper_srt_output, srt_path)
    elif not os.path.exists(srt_path):
        print("❌ Arquivo de legenda não encontrado após a execução do Whisper.")
        return

    # Extrai o texto do SRT para servir de base à legenda gerada por IA
    texto_transcricao = ""
    try:
        with open(srt_path, encoding="utf-8") as f:
            linhas = [l.strip() for l in f
                      if l.strip() and not l.strip().isdigit() and "-->" not in l]
        texto_transcricao = " ".join(linhas)
    except Exception:
        pass

    # Passo 3: Espelhar o vídeo e queimar as legendas
    print("Passo 3/3: Espelhando o vídeo e adicionando legendas...")
    
    # É preciso formatar o caminho do SRT para o FFmpeg (escapar caminhos absolutos no windows/linux)
    # FFmpeg subtitles filter tem uma sintaxe chata para caminhos absolutos, então vamos rodar no mesmo diretório
    try:
        srt_basename = os.path.basename(srt_path)
        video_basename = os.path.basename(video_path)
        final_basename = os.path.basename(final_output)

        # Filtro: hflip para espelhar, subtitles para legendar. 
        # Configuração básica de fonte para ficar visível (amarelo com borda)
        vf_filter = f"hflip,subtitles={srt_basename}:force_style='FontSize=24,PrimaryColour=&H00FFFF,Outline=1'"

        subprocess.run([
            "ffmpeg", "-i", video_basename, "-vf", vf_filter, 
            "-c:a", "copy", final_basename, "-y"
        ], cwd=base_dir, check=True, capture_output=True)
        print(f"✅ Processamento concluído! Vídeo final salvo em: {final_output}")

        # Enfileira para postagem automática (com legenda gerada por IA)
        if POSTER_DISPONIVEL and os.path.exists(final_output):
            titulo = base_name.replace("_", " ")
            legenda_ia = gerar_legenda_ia(texto_transcricao, titulo_original="")
            adicionar_na_fila(final_output, titulo, caption=legenda_ia)

    except subprocess.CalledProcessError as e:
        print(f"❌ Erro ao processar FFmpeg final: {e.stderr.decode('utf-8', errors='ignore')}")

    # Limpeza
    for temp_file in [audio_path, srt_path, video_path]:
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except:
                pass


def processar_video_whisper_nativo(video_path):
    print(f"\n⚙️ Iniciando processamento de: {video_path}")
    base_dir = os.path.dirname(video_path)
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    
    srt_path = os.path.join(base_dir, f"{base_name}.srt")
    audio_path = os.path.join(base_dir, f"{base_name}_audio.wav")
    final_output = os.path.join(base_dir, f"{base_name}_final.mp4")

    # Passo 1: Extrair áudio para o Whisper
    print("Passo 1/3: Extraindo áudio para transcrição...")
    try:
        subprocess.run([
            "ffmpeg", "-i", video_path, "-vn", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1", audio_path, "-y"
        ], check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f"❌ Erro ao extrair áudio: {e.stderr.decode('utf-8', errors='ignore')}")
        return

    # Passo 2: Gerar Legendas (SRT) usando biblioteca Whisper do Python
    print("Passo 2/3: Gerando legendas com Whisper (Nativo)...")
    texto_transcricao = ""
    try:
        model = whisper.load_model("base")
        result = model.transcribe(audio_path, fp16=False)
        texto_transcricao = (result.get("text") or "").strip()  # base para a legenda IA

        # Função auxiliar para formatar timestamp do SRT
        def format_timestamp(seconds: float):
            milliseconds = round(seconds * 1000.0)
            hours = milliseconds // 3_600_000
            milliseconds -= hours * 3_600_000
            minutes = milliseconds // 60_000
            milliseconds -= minutes * 60_000
            sec = milliseconds // 1_000
            milliseconds -= sec * 1_000
            return f"{hours:02d}:{minutes:02d}:{sec:02d},{milliseconds:03d}"

        # Escrever SRT manualmente para garantir compatibilidade
        with open(srt_path, "w", encoding="utf-8") as srt_file:
            for i, segment in enumerate(result["segments"], start=1):
                srt_file.write(f"{i}\n")
                srt_file.write(f"{format_timestamp(segment['start'])} --> {format_timestamp(segment['end'])}\n")
                srt_file.write(f"{segment['text'].strip()}\n\n")
                
    except Exception as e:
        print(f"❌ Erro ao rodar whisper internamente: {e}")
        return

    if not os.path.exists(srt_path):
        print("❌ Arquivo de legenda não foi gerado.")
        return

    # Passo 3: Espelhar o vídeo e queimar as legendas
    print("Passo 3/3: Espelhando o vídeo e adicionando legendas...")
    
    # É preciso formatar o caminho do SRT para o FFmpeg (escapar caminhos absolutos no windows/linux)
    # FFmpeg subtitles filter tem uma sintaxe chata para caminhos absolutos, então vamos rodar no mesmo diretório
    try:
        srt_basename = os.path.basename(srt_path)
        video_basename = os.path.basename(video_path)
        final_basename = os.path.basename(final_output)

        # Filtro: hflip para espelhar, subtitles para legendar. 
        # Configuração básica de fonte para ficar visível (amarelo com borda)
        vf_filter = f"hflip,subtitles={srt_basename}:force_style='FontSize=24,PrimaryColour=&H00FFFF,Outline=1'"

        subprocess.run([
            "ffmpeg", "-i", video_basename, "-vf", vf_filter, 
            "-c:a", "copy", final_basename, "-y"
        ], cwd=base_dir, check=True, capture_output=True)
        print(f"✅ Processamento concluído! Vídeo final salvo em: {final_output}")

        # Enfileira para postagem automática (com legenda gerada por IA)
        if POSTER_DISPONIVEL and os.path.exists(final_output):
            titulo = base_name.replace("_", " ")
            legenda_ia = gerar_legenda_ia(texto_transcricao, titulo_original="")
            adicionar_na_fila(final_output, titulo, caption=legenda_ia)

    except subprocess.CalledProcessError as e:
        print(f"❌ Erro ao processar FFmpeg final: {e.stderr.decode('utf-8', errors='ignore')}")

    # Limpeza
    for temp_file in [audio_path, srt_path, video_path]:
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except:
                pass


if __name__ == "__main__":
    if len(sys.argv) > 1:
        video_url = sys.argv[1]
        caminho_baixado = baixar_short(video_url)
        if caminho_baixado:
            # Mantendo a chamada original ou a nova
            # processar_video(caminho_baixado) # original via CLI
            processar_video_whisper_nativo(caminho_baixado) # nova via biblioteca nativa
    else:
        print("ERRO: Nenhuma URL de vídeo fornecida.")
        print("Uso: python coletor.py 'URL_DO_SHORT'")
