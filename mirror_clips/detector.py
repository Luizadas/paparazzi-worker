# paparazzi-worker: detector.py (Mirror-Clips Module)
# Foco: Encontrar Shorts (<= 70s) e monitorar.

import os
import sqlite3
from pathlib import Path
from datetime import datetime
from googleapiclient.discovery import build
import isodate
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

# Raiz do projeto (uma pasta acima de mirror_clips/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
(PROJECT_ROOT / 'data').mkdir(exist_ok=True)

# --- CONFIGURAÇÕES ---
CHANNEL_ID = 'UCPX0gLduKAfgr-HJENa7CFw' # Canal alvo (pode ser ajustado ou alterado para busca global)

# Chave da API do YouTube (lida do .env na raiz do projeto)
API_KEY = os.getenv('YOUTUBE_API_KEY', '')

# Banco de dados específico para o mirror-clips (em data/ na raiz)
DB_FILE = str(PROJECT_ROOT / 'data' / 'mirror_memory.db')

# --- Funções Auxiliares ---

def configurar_banco(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS watch_list (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id TEXT NOT NULL UNIQUE,
        video_title TEXT NOT NULL,
        published_at TIMESTAMP,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS processed_videos (
        video_id TEXT PRIMARY KEY,
        published_at TIMESTAMP,
        processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    conn.commit()
    conn.close()

def salvar_para_observacao(db_path, video_id, video_title, published_at):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO watch_list (video_id, video_title, published_at) VALUES (?, ?, ?)", 
                       (video_id, video_title, published_at))
        conn.commit()
        print(f"✅ SHORT REGISTRADO PARA OBSERVAÇÃO: '{video_title}'")
    except sqlite3.IntegrityError:
        print(f"ℹ️  Short '{video_title}' já estava na lista de observação.")
    finally:
        conn.close()

def carregar_ids_processados(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT video_id FROM processed_videos")
    ids = {row[0] for row in cursor.fetchall()}
    conn.close()
    return ids

def marcar_como_processado(db_path, video_id, published_at):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO processed_videos (video_id, published_at) VALUES (?, ?)", 
                   (video_id, published_at))
    conn.commit()
    conn.close()

# --- Lógica Principal ---

_CACHE_UPLOADS = {}


def _playlist_de_uploads(youtube, channel_id):
    """ID da playlist de uploads do canal. Consultado uma vez por processo (1
    unidade de cota) e guardado — não muda."""
    if channel_id not in _CACHE_UPLOADS:
        resp = youtube.channels().list(part="contentDetails", id=channel_id).execute()
        itens = resp.get("items", [])
        if not itens:
            raise RuntimeError(f"canal {channel_id} não encontrado na API")
        _CACHE_UPLOADS[channel_id] = (
            itens[0]["contentDetails"]["relatedPlaylists"]["uploads"])
    return _CACHE_UPLOADS[channel_id]


def verificar_canal_com_api(channel_id):
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Iniciando verificação de Shorts com API...")
    
    ids_ja_processados = carregar_ids_processados(DB_FILE)
    print(f"ℹ️  Memória carregada com {len(ids_ja_processados)} shorts já analisados inicialmente.")

    try:
        youtube = build('youtube', 'v3', developerKey=API_KEY)
        # Os 50 mais recentes pela playlist de UPLOADS do canal, e não por
        # search.list: dá a mesma lista, mas custa 1 unidade de cota em vez de
        # 100. Rodando de 5 em 5 min seriam 28.800 unidades/dia com search
        # (a cota padrão é 10.000 — cegava o detector depois de ~8h por dia);
        # por aqui são 288.
        playlist_uploads = _playlist_de_uploads(youtube, channel_id)
        resp = youtube.playlistItems().list(
            playlistId=playlist_uploads, part='contentDetails', maxResults=50
        ).execute()
        video_ids_api = [item['contentDetails']['videoId']
                         for item in resp.get('items', [])]
    except Exception as e:
        print(f"ERRO na busca por shorts: {e}")
        return
        
    ids_a_processar = [vid for vid in video_ids_api if vid not in ids_ja_processados]
    
    if not ids_a_processar:
        print("Nenhum short novo para processar.")
        return
    
    print(f"🔍 Encontrados {len(ids_a_processar)} vídeos novos para análise.")

    try:
        video_details_response = youtube.videos().list(
            part='snippet,contentDetails', id=",".join(ids_a_processar)
        ).execute()
    except Exception as e:
        print(f"ERRO ao buscar detalhes dos vídeos: {e}")
        return
    
    for video_item in video_details_response.get('items', []):
        id_recente = video_item['id']
        titulo = video_item['snippet']['title']
        published_at = video_item['snippet']['publishedAt'] # Captura a data de publicação
        duration_iso = video_item['contentDetails']['duration']
        duration_seconds = isodate.parse_duration(duration_iso).total_seconds()

        print(f"\n--- Analisando: {titulo} ---")

        if duration_seconds > 70:
            print("🚫 Ignorado (Vídeo Longo).")
            marcar_como_processado(DB_FILE, id_recente, published_at)
            continue

        # É um Short (<= 70s). Salvamos para observação.
        salvar_para_observacao(DB_FILE, id_recente, titulo, published_at)
        marcar_como_processado(DB_FILE, id_recente, published_at)

    print("\nVerificação de Shorts novos concluída.")

def buscar_shorts_virais_api():
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Iniciando busca global de Shorts (Copa/Futebol)...")
    
    ids_ja_processados = carregar_ids_processados(DB_FILE)
    print(f"ℹ️  Memória carregada com {len(ids_ja_processados)} shorts já analisados inicialmente.")

    try:
        youtube = build('youtube', 'v3', developerKey=API_KEY)
        # Busca os 50 vídeos globais mais relevantes
        search_response = youtube.search().list(
            q='copa OR futebol OR world cup',
            part='id', 
            maxResults=50, 
            order='viewCount', # Busca os mais visualizados / em alta
            type='video',
            videoDuration='short' # Força a busca por vídeos curtos
        ).execute()
        video_ids_api = [item['id']['videoId'] for item in search_response.get('items', [])]
    except Exception as e:
        print(f"ERRO na busca por shorts: {e}")
        return
        
    ids_a_processar = [vid for vid in video_ids_api if vid not in ids_ja_processados]
    
    if not ids_a_processar:
        print("Nenhum short novo para processar.")
        return
    
    print(f"🔍 Encontrados {len(ids_a_processar)} vídeos novos para análise.")

    try:
        video_details_response = youtube.videos().list(
            part='snippet,contentDetails', id=",".join(ids_a_processar)
        ).execute()
    except Exception as e:
        print(f"ERRO ao buscar detalhes dos vídeos: {e}")
        return
    
    for video_item in video_details_response.get('items', []):
        id_recente = video_item['id']
        titulo = video_item['snippet']['title']
        published_at = video_item['snippet']['publishedAt'] # Captura a data de publicação
        duration_iso = video_item['contentDetails']['duration']
        duration_seconds = isodate.parse_duration(duration_iso).total_seconds()

        print(f"\n--- Analisando: {titulo} ---")

        if duration_seconds > 70:
            print("🚫 Ignorado (Vídeo Longo).")
            marcar_como_processado(DB_FILE, id_recente, published_at)
            continue

        # É um Short (<= 70s). Salvamos para observação.
        salvar_para_observacao(DB_FILE, id_recente, titulo, published_at)
        marcar_como_processado(DB_FILE, id_recente, published_at)

    print("\nVerificação de Shorts novos concluída.")

if __name__ == "__main__":
    configurar_banco(DB_FILE)
    verificar_canal_com_api(CHANNEL_ID)
    # Busca global (copa/futebol) DESATIVADA: mantém o mirror focado no canal fixo.
    # buscar_shorts_virais_api()
