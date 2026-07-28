# watcher.py - Monitoramento de Performance de Shorts (Mirror-Clips)
# Verifica se os Shorts atingiram a meta de 100k views para serem copiados.

import sqlite3
from googleapiclient.discovery import build
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import isodate
import subprocess
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

# Raiz do projeto (uma pasta acima de mirror_clips/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --- CONFIGURAÇÕES ---
DB_FILE = str(PROJECT_ROOT / 'data' / 'mirror_memory.db')
API_KEY = os.getenv('YOUTUBE_API_KEY', '') # Lida do .env na raiz do projeto
MIN_VIEWS = 30000   # Meta para Shorts virais
MAX_AGE_DAYS = 7    # Dá mais tempo para o Short viralizar

def obter_shorts_para_verificar(db_path):
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT video_id, published_at FROM watch_list")
    videos = cursor.fetchall()
    conn.close()
    return videos

def remover_short_da_lista(db_path, video_id):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM watch_list WHERE video_id = ?", (video_id,))
    conn.commit()
    conn.close()

def verificar_performance_shorts():
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Iniciando verificação de Shorts na Watch List...")
    
    shorts_para_verificar = obter_shorts_para_verificar(DB_FILE)
    if not shorts_para_verificar:
        print("Watch List está vazia. Nenhum Short em observação.")
        return

    try:
        youtube = build('youtube', 'v3', developerKey=API_KEY)
    except Exception as e:
        print(f"ERRO ao conectar à API do YouTube: {e}")
        return

    ids_dos_shorts = [video['video_id'] for video in shorts_para_verificar]

    try:
        # Pede em blocos de no máximo 50 se a lista for grande, mas aqui assume-se <= 50 do detector
        video_details_response = youtube.videos().list(
            part='statistics', id=",".join(ids_dos_shorts[:50])
        ).execute()
    except Exception as e:
        print(f"ERRO ao buscar detalhes dos Shorts: {e}")
        return

    stats_map = {item['id']: item['statistics'] for item in video_details_response.get('items', [])}

    for video in shorts_para_verificar:
        video_id = video['video_id']
        
        published_at_str = video['published_at']
        published_at = isodate.parse_datetime(published_at_str)

        # 1. Verifica se o short expirou (passou o tempo máximo de observação sem viralizar)
        if datetime.now(timezone.utc) - published_at > timedelta(days=MAX_AGE_DAYS):
            print(f"-> EXPIRADO: Short {video_id} removido por tempo.")
            remover_short_da_lista(DB_FILE, video_id)
            continue

        # 2. Verifica a contagem de visualizações
        if video_id in stats_map:
            view_count = int(stats_map[video_id].get('viewCount', 0))
            if view_count >= MIN_VIEWS:
                link = f"https://www.youtube.com/watch?v={video_id}"
                print(f"✅ VIRALIZOU! Short {video_id} atingiu {view_count} views (Meta: {MIN_VIEWS}).")
                print(f"📥 Disparando download e processamento para: {link}")
                remover_short_da_lista(DB_FILE, video_id)
                
                # Chama o coletor e AGUARDA terminar (download + processamento + enfileira).
                # Bloqueante de propósito: assim, quando o watcher termina, a fila de
                # postagem já está pronta para o poster.py --queue.
                try:
                    import sys, os
                    script_dir = os.path.dirname(os.path.abspath(__file__))
                    subprocess.run(
                        [sys.executable, 'coletor.py', link],
                        cwd=script_dir, check=False
                    )
                except Exception as e:
                    print(f"Erro ao disparar coletor.py: {e}")

    print("Verificação da Watch List de Shorts concluída.")

if __name__ == "__main__":
    verificar_performance_shorts()
