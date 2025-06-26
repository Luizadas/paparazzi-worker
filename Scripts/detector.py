# detector.py v2.0 - O Vigia com "Super Visão" via API

import os
import sqlite3
from datetime import datetime
from googleapiclient.discovery import build
import isodate  # Biblioteca para parser a duração do vídeo

# --- CONFIGURAÇÕES ---
# Cole aqui o ID do canal que você quer monitorar
CHANNEL_ID = 'UCPX0gLduKAfgr-HJENa7CFw'  # Exemplo: ID que você encontrou

# IMPORTANTE: Cole aqui a sua Chave da API
API_KEY = 'AIzaSyDXnyQUQuTrXAIuGsi46mzyYp29RlRto5g'

DB_FILE = 'watch_list.db'
INFLUENCERS_FILE = 'influencers.txt'
LAST_VIDEO_FILE = 'last_video.txt'


# --- Funções Auxiliares (a maioria permanece a mesma) ---

def carregar_influencers(filepath):
    """Lê o arquivo de influenciadores."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return [line.strip().lower() for line in f if line.strip()]
    except FileNotFoundError:
        print(f"AVISO: Arquivo '{filepath}' não encontrado.")
        return []


def configurar_banco(db_path):
    """Cria e configura a tabela no banco de dados SQLite."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS watch_list (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id TEXT NOT NULL UNIQUE,
        video_title TEXT NOT NULL,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    conn.commit()
    conn.close()


def salvar_para_observacao(db_path, video_id, video_title):
    """Salva um vídeo na lista de observação."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO watch_list (video_id, video_title) VALUES (?, ?)", (video_id, video_title))
        conn.commit()
        print(f"✅ VÍDEO REGISTRADO PARA OBSERVAÇÃO: '{video_title}'")
    except sqlite3.IntegrityError:
        print(f"ℹ️  Vídeo '{video_title}' já estava na lista de observação.")
    finally:
        conn.close()


def ler_ultimo_video_visto(filepath):
    """Lê o ID do último vídeo processado."""
    try:
        with open(filepath, 'r') as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


def salvar_ultimo_video_visto(filepath, video_id):
    """Salva o ID do último vídeo processado."""
    with open(filepath, 'w') as f:
        f.write(video_id)


# --- Lógica Principal com API ---

def verificar_canal_com_api(channel_id):
    """Função principal que usa a API para encontrar o vídeo longo mais recente."""
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Iniciando verificação com API...")

    try:
        # Forma correta, 'y' minúsculo, sem parênteses
        Youtube = build('youtube', 'v3', developerKey=API_KEY)
    except Exception as e:
        print(f"ERRO: Falha ao conectar na API do YouTube. Verifique a chave. Erro: {e}")
        return

    # 1. Busca os IDs dos 15 vídeos mais recentes do canal
    try:
        # Forma correta: Youtube().list()
        search_response = Youtube().list(
            channelId=channel_id,
            part='id',  # Só precisamos do ID por enquanto, mais eficiente
            maxResults=15,
            order='date',
            type='video'
        ).execute()

        video_ids = [item['id']['videoId'] for item in search_response.get('items', [])]
        if not video_ids:
            print("Nenhum vídeo encontrado na busca recente.")
            return

    except Exception as e:
        print(f"ERRO: Falha na busca por vídeos. Verifique o CHANNEL_ID e a API_KEY. Erro: {e}")
        return

    # 2. Busca os detalhes (duração e título) de todos os vídeos de uma só vez
    try:
        video_details_response = Youtube.videos().list(
            part='snippet,contentDetails',
            id=",".join(video_ids)
        ).execute()
    except Exception as e:
        print(f"ERRO: Falha ao buscar detalhes dos vídeos. Erro: {e}")
        return

    video_longo_recente = None

    # 3. Itera pelos vídeos para encontrar o primeiro que não é um Short
    for video_item in video_details_response.get('items', []):
        duration_iso = video_item['contentDetails']['duration']
        duration_seconds = isodate.parse_duration(duration_iso).total_seconds()

        if duration_seconds > 70:
            video_longo_recente = video_item
            print(f"ℹ️  Vídeo longo mais recente encontrado: {video_longo_recente['snippet']['title']}")
            break

    if not video_longo_recente:
        print("Nenhum vídeo longo encontrado nas publicações recentes.")
        return

    # 4. Compara com o último vídeo processado
    id_recente = video_longo_recente['id']
    ultimo_visto = ler_ultimo_video_visto(LAST_VIDEO_FILE)

    if id_recente == ultimo_visto:
        print("Nenhum vídeo longo NOVO encontrado.")
        return

    titulo = video_longo_recente['snippet']['title']
    link = f"https://www.youtube.com/watch?v={id_recente}"
    print(f"🚀 NOVO VÍDEO LONGO DETECTADO: {titulo}")

    # 5. Aplica a lógica de classificação por influencer
    influencers = carregar_influencers(INFLUENCERS_FILE)
    encontrou_influencer = False
    if influencers:
        for influencer in influencers:
            if influencer in titulo.lower():
                print(f"🔥 INFLUENCER ENCONTRADO! '{influencer.capitalize()}' está no título.")
                print(f"🚨 ALERTA: BAIXAR IMEDIATAMENTE! Link: {link}")
                encontrou_influencer = True
                break

    if not encontrou_influencer:
        salvar_para_observacao(DB_FILE, id_recente, titulo)

    salvar_ultimo_video_visto(LAST_VIDEO_FILE, id_recente)
    print("Verificação concluída.")


if __name__ == "__main__":
    # Instale a biblioteca isodate: pip install isodate
    configurar_banco(DB_FILE)
    verificar_canal_com_api(CHANNEL_ID)