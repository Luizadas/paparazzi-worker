# coletor.py - Download e Processamento de Shorts (Mirror-Clips)

import sys
import os
import yt_dlp
from datetime import datetime
import subprocess
import shutil
import whisper

# Integração com módulo de postagem
try:
    from poster import adicionar_na_fila
    POSTER_DISPONIVEL = True
except ImportError:
    POSTER_DISPONIVEL = False

DOWNLOAD_DIR = "/mnt/paparazzi/mirror_clips"

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

        # Enfileira para postagem automática
        if POSTER_DISPONIVEL and os.path.exists(final_output):
            titulo = base_name.replace("_", " ")
            adicionar_na_fila(final_output, titulo)

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
    try:
        model = whisper.load_model("base")
        result = model.transcribe(audio_path, fp16=False)
        
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

        # Enfileira para postagem automática
        if POSTER_DISPONIVEL and os.path.exists(final_output):
            titulo = base_name.replace("_", " ")
            adicionar_na_fila(final_output, titulo)

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
