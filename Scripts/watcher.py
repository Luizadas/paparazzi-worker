# watcher.py - O Analista de Performance do Paparazzi-Worker

import sqlite3
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import os

# --- CONFIGURAÇÕES ---
DB_FILE = 'watch_list.db'

# IMPORTANTE: Cole aqui a sua Chave da API que você gerou no Google Cloud.
# Em um projeto real, NUNCA deixe a chave direto no código.
# Use variáveis de ambiente (os.environ.get('API_KEY')) ou um arquivo .env.
API_KEY = 'AIzaSyDXnyQUQuTrXAIuGsi46mzyYp29RlRto5g'
YOUTUBE_API_SERVICE_NAME = 'youtube'
YOUTUBE_API_VERSION = 'v3'

# Limites para aprovação
MIN_VIEWS = 200000
MAX_AGE_DAYS = 3


def obter_videos_para_verificar(db_path):
    """Obtém a lista de vídeos da watch_list no banco de dados."""
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    # Garante que possamos ler a data corretamente
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT video_id, added_at FROM watch_list")
    videos = cursor.fetchall()
    conn.close()
    return videos


def remover_video_da_lista(db_path, video_id):
    """Remove um vídeo da watch_list, seja por aprovação ou por expirar."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM watch_list WHERE video_id = ?", (video_id,))
    conn.commit()
    conn.close()


def verificar_performance_videos():
    """Função principal que verifica a performance dos vídeos em observação."""
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Iniciando verificação da Watch List...")

    videos_para_verificar = obter_videos_para_verificar(DB_FILE)
    if not videos_para_verificar:
        print("Watch List está vazia. Nada a fazer.")
        return

    # --- DEBUG 1: VER O QUE VEIO DO BANCO DE DADOS ---
    ids_dos_videos = [video['video_id'] for video in videos_para_verificar]
    print(f"DEBUG: IDs encontrados no banco de dados: {ids_dos_videos}")

    if not ids_dos_videos:
        print("Verificação concluída. Nenhum ID para processar.")
        return

    try:
        youtube = build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION, developerKey=API_KEY)
    except Exception as e:
        print(f"ERRO: Não foi possível conectar à API do YouTube. Verifique sua chave. Erro: {e}")
        return

    request = youtube.videos().list(
        part="snippet,statistics",
        id=",".join(ids_dos_videos)
    )
    response = request.execute()

    # --- DEBUG 2: VER A RESPOSTA BRUTA DA API ---
    print(f"DEBUG: Resposta completa da API: {response}")

    stats_map = {item['id']: item['statistics'] for item in response.get('items', [])}

    # --- DEBUG 3: VER O MAPA DE STATS QUE CRIAMOS ---
    print(f"DEBUG: Mapa de estatísticas criado: {stats_map}")

    for video in videos_para_verificar:
        video_id = video['video_id']
        added_at_str = video['added_at']
        # Usando o formato corrigido
        added_at = datetime.strptime(added_at_str, '%Y-%m-%d %H:%M:%S')

        if datetime.now() - added_at > timedelta(days=MAX_AGE_DAYS):
            print(f" EXPIRADO: Vídeo {video_id} tem mais de {MAX_AGE_DAYS} dias. Removendo.")
            remover_video_da_lista(DB_FILE, video_id)
            continue

        if video_id in stats_map:
            view_count = int(stats_map[video_id].get('viewCount', 0))
            print(f"  - Verificando '{video_id}': {view_count} visualizações.")

            if view_count >= MIN_VIEWS:
                print(f"✅ APROVADO! Vídeo    {video_id} atingiu {view_count} views.")
                print(f"🚨 ALERTA: DOWNLOAD APROVADO! Link: https://www.youtube.com/watch?v={video_id}")
                remover_video_da_lista(DB_FILE, video_id)
        else:
            # --- DEBUG 4: INFORMAR SE UM ID DO NOSSO BANCO NÃO FOI ENCONTRADO NA RESPOSTA DA API ---
            print(f"AVISO: O video_id '{video_id}' do nosso banco de dados não foi encontrado na resposta da API.")

    print("Verificação da Watch List concluída.")


# --- PONTO DE ENTRADA DO SCRIPT ---
if __name__ == "__main__":
    verificar_performance_videos()