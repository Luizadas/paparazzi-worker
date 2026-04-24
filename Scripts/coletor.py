# coletor.py - O Baixador do Paparazzi-Worker

import sys
import os
import yt_dlp
from datetime import datetime

def baixar_video(url):
    """
    Usa a biblioteca yt-dlp para baixar um vídeo de uma URL.
    """
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    print(f"[{timestamp}] 📥 Iniciando download de: {url}")

    # Diretório padrão para salvar os vídeos especificado pelo usuário
    DOWNLOAD_DIR = "/mnt/paparazzi"
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    
    # Opções de download para o yt-dlp
    # Estamos especificando para baixar o melhor formato de vídeo MP4 disponível
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': os.path.join(DOWNLOAD_DIR, f'%(title)s - %(id)s_{timestamp}.%(ext)s'), # Salva em uma pasta multi-plataforma
        'noplaylist': True, # Garante que não baixe a playlist inteira se o link for de uma
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        print(f"✅ Download concluído com sucesso!")
    except Exception as e:
        print(f"❌ ERRO ao tentar baixar o vídeo. Motivo: {e}")

if __name__ == "__main__":
    # O script espera receber a URL como um argumento da linha de comando
    if len(sys.argv) > 1:
        video_url = sys.argv[1]
        baixar_video(video_url)
    else:
        print("ERRO: Nenhuma URL de vídeo fornecida.")
        print("Uso: python coletor.py 'URL_DO_VIDEO'")