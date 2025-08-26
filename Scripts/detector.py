# paparazzi-worker: detector.py
# Versão: 3.1 (com Data de Lançamento)
# Data: 17 de Julho de 2025

import os
import sqlite3
from datetime import datetime
from googleapiclient.discovery import build
import isodate

# --- CONFIGURAÇÕES ---
CHANNEL_ID = 'UCPX0gLduKAfgr-HJENa7CFw' # Verifique se este é o ID do canal desejado

# Chave da API do YouTube (substitua pela sua)
API_KEY = 'AIzaSyDXnyQUQuTrXAIuGsi46mzyYp29RlRto5g'

# Nomes dos arquivos de suporte
DB_FILE = 'paparazzi_memory.db'
INFLUENCERS_FILE = '../influenciadores/influenciadores_midia.txt'

# --- Funções Auxiliares ---

def carregar_influencers(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return [line.strip().lower() for line in f if line.strip()]
    except FileNotFoundError:
        print(f"AVISO: Arquivo de influencers '{filepath}' não encontrado.")
        return []

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
        print(f"✅ VÍDEO REGISTRADO PARA OBSERVAÇÃO: '{video_title}'")
    except sqlite3.IntegrityError:
        print(f"ℹ️  Vídeo '{video_title}' já estava na lista de observação.")
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

def verificar_canal_com_api(channel_id):
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Iniciando verificação com API...")
    
    ids_ja_processados = carregar_ids_processados(DB_FILE)
    print(f"ℹ️  Memória carregada com {len(ids_ja_processados)} vídeos já processados.")

    try:
        youtube = build('youtube', 'v3', developerKey=API_KEY)
        # Busca os 50 vídeos mais recentes
        search_response = youtube.search().list(
            channelId=channel_id, part='id', maxResults=50, order='date', type='video'
        ).execute()
        video_ids_api = [item['id']['videoId'] for item in search_response.get('items', [])]
    except Exception as e:
        print(f"ERRO na busca por vídeos: {e}")
        return
        
    ids_a_processar = [vid for vid in video_ids_api if vid not in ids_ja_processados]
    
    if not ids_a_processar:
        print("Nenhum vídeo novo para processar.")
        return
    
    print(f"🔍 Encontrados {len(ids_a_processar)} vídeos novos para análise.")

    try:
        video_details_response = youtube.videos().list(
            part='snippet,contentDetails', id=",".join(ids_a_processar)
        ).execute()
    except Exception as e:
        print(f"ERRO ao buscar detalhes dos vídeos: {e}")
        return
    
    influencers = carregar_influencers(INFLUENCERS_FILE)
    
    for video_item in video_details_response.get('items', []):
        id_recente = video_item['id']
        titulo = video_item['snippet']['title']
        published_at = video_item['snippet']['publishedAt'] # Captura a data de publicação
        duration_iso = video_item['contentDetails']['duration']
        duration_seconds = isodate.parse_duration(duration_iso).total_seconds()

        print(f"\n--- Analisando: {titulo} ---")

        if duration_seconds <= 70:
            print("🚫 Ignorado (Short).")
            marcar_como_processado(DB_FILE, id_recente, published_at)
            continue

        encontrou_influencer = False
        if influencers:
            for influencer in influencers:
                if influencer in titulo.lower():
                    link = f"https://www.youtube.com/watch?v={id_recente}"
                    print(f"🔥 INFLUENCER ENCONTRADO! '{influencer.capitalize()}' está no título.")
                    print(f"🚨 ALERTA: BAIXAR IMEDIATAMENTE! Link: {link}")
                    encontrou_influencer = True
                    break
        
        if not encontrou_influencer:
            salvar_para_observacao(DB_FILE, id_recente, titulo, published_at)

        marcar_como_processado(DB_FILE, id_recente, published_at)

    print("\nVerificação de todos os vídeos novos concluída.")

if __name__ == "__main__":
    configurar_banco(DB_FILE)
    verificar_canal_com_api(CHANNEL_ID)
    