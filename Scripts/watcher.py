# watcher.py - O Analista de Performance do Paparazzi-Worker
# Versão: 2.1 (com Data de Lançamento)

import sqlite3
from googleapiclient.discovery import build
from datetime import datetime, timedelta, timezone
import os
import isodate
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

# --- CONFIGURAÇÕES ---
DB_FILE = 'paparazzi_memory.db'
API_KEY = os.getenv('YOUTUBE_API_KEY', '') # Lida do .env na raiz do projeto
MIN_VIEWS = 200000
MAX_AGE_DAYS = 3

def obter_videos_para_verificar(db_path):
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    # Agora também selecionamos a data de publicação
    cursor.execute("SELECT video_id, published_at FROM watch_list")
    videos = cursor.fetchall()
    conn.close()
    return videos

def remover_video_da_lista(db_path, video_id):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM watch_list WHERE video_id = ?", (video_id,))
    conn.commit()
    conn.close()

def verificar_performance_videos():
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Iniciando verificação da Watch List...")
    
    videos_para_verificar = obter_videos_para_verificar(DB_FILE)
    if not videos_para_verificar:
        print("Watch List está vazia. Nada a fazer.")
        return

    try:
        youtube = build('youtube', 'v3', developerKey=API_KEY)
    except Exception as e:
        print(f"ERRO ao conectar à API do YouTube: {e}")
        return

    ids_dos_videos = [video['video_id'] for video in videos_para_verificar]

    try:
        video_details_response = youtube.videos().list(
            part='statistics', id=",".join(ids_dos_videos)
        ).execute()
    except Exception as e:
        print(f"ERRO ao buscar detalhes dos vídeos: {e}")
        return

    stats_map = {item['id']: item['statistics'] for item in video_details_response.get('items', [])}

    for video in videos_para_verificar:
        video_id = video['video_id']
        
        # Converte a data de publicação (string ISO 8601) para um objeto datetime
        published_at_str = video['published_at']
        published_at = isodate.parse_datetime(published_at_str)

        # 1. Verifica se o vídeo expirou com base na data de PUBLICAÇÃO
        if datetime.now(timezone.utc) - published_at > timedelta(days=MAX_AGE_DAYS):
            print(f"-> EXPIRADO: Vídeo {video_id} (publicado em {published_at.strftime('%d/%m')}) removido.")
            remover_video_da_lista(DB_FILE, video_id)
            continue

        # 2. Verifica a contagem de visualizações
        if video_id in stats_map:
            view_count = int(stats_map[video_id].get('viewCount', 0))
            if view_count >= MIN_VIEWS:
                link = f"https://www.youtube.com/watch?v={video_id}"
                print(f"✅ APROVADO! Vídeo {video_id} atingiu {view_count} views.")
                print(f"🚨 ALERTA: DOWNLOAD APROVADO! Link: {link}")
                remover_video_da_lista(DB_FILE, video_id)

    print("Verificação da Watch List concluída.")

if __name__ == "__main__":
    verificar_performance_videos()