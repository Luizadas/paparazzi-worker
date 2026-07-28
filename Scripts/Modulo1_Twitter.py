# --- paparazzi-worker: Módulo 1 (Detecção) ---
# Versão 0.8: Monitor Paciente (Anti-Burst-Limit)

import os
import tweepy
import time
from datetime import datetime, timezone
import math
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

# --- 1. CONFIGURAÇÃO ---
BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN", "")

LISTA_DE_ALVOS = ["whindersson", "felipeneto", "Virginia", "g1"]
LISTA_DE_PALAVRAS_CHAVE = [
    "treta", "polêmica", "cancelado", "processo",
    "namorando", "separou", "traição", "gravida"
]

TAMANHO_DO_LOTE = 3

# PARÂMETROS DE PACIÊNCIA: Aumentamos drasticamente os tempos para respeitar
# os limites de "burst" implícitos do plano Basic.

# Pausa entre cada lote de palavras-chave para o MESMO influenciador.
# Saímos de 5s para mais de 1 minuto.
PAUSA_ENTRE_LOTES_SEGUNDOS = 75 

# Intervalo geral entre os ciclos de verificação de TODOS os influenciadores.
# Saímos de 15min para 30min.
INTERVALO_EM_MINUTOS = 30

PERCENTUAL_DE_ALERTA = 0.0005

# --- O RESTANTE DO CÓDIGO PERMANECE O MESMO DA v0.7 ---

def dividir_em_lotes(lista, tamanho_lote):
    for i in range(0, len(lista), tamanho_lote):
        yield lista[i:i + tamanho_lote]

def monitorar_com_paciencia():
    print(f"✅ Monitor v0.8 (Paciente) iniciado. Pausa entre lotes: {PAUSA_ENTRE_LOTES_SEGUNDOS}s. Intervalo geral: {INTERVALO_EM_MINUTOS}min.")
    client = tweepy.Client(BEARER_TOKEN)

    lotes_de_palavras = list(dividir_em_lotes(LISTA_DE_PALAVRAS_CHAVE, TAMANHO_DO_LOTE))

    while True:
        print("-" * 70)
        print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}] Iniciando novo ciclo...")

        for influenciador in LISTA_DE_ALVOS:
            contagem_acumulada = 0
            try:
                print(f"\nVerificando @{influenciador}...")
                user_response = client.get_user(username=influenciador, user_fields=["public_metrics"])
                if user_response.data is None: continue

                num_seguidores = user_response.data.public_metrics['followers_count']
                limite_dinamico = math.ceil(num_seguidores * PERCENTUAL_DE_ALERTA)
                print(f"  -> Limite dinâmico: {limite_dinamico} menções.")

                for i, lote in enumerate(lotes_de_palavras):
                    or_clause = " OR ".join([f'"{palavra}"' for palavra in lote])
                    query = f'"{influenciador}" ({or_clause}) -is:retweet'
                    
                    print(f"  -> Buscando Lote {i+1}/{len(lotes_de_palavras)}: {lote}")
                    
                    response = client.get_recent_tweets_count(query=query)
                    contagem_do_lote = response.meta['total_tweet_count']
                    contagem_acumulada += contagem_do_lote
                    
                    print(f"     -> Resultado do lote: {contagem_do_lote} tweets.")
                    # Pausa CRÍTICA e AUMENTADA
                    print(f"     -> Aguardando {PAUSA_ENTRE_LOTES_SEGUNDOS} segundos...")
                    time.sleep(PAUSA_ENTRE_LOTES_SEGUNDOS)
                
                print(f"  -> Volume total de buzz acumulado: {contagem_acumulada} tweets.")

                if contagem_acumulada > limite_dinamico:
                    print(f"\n🔥🔥🔥 ALERTA DE TENDÊNCIA PARA @{influenciador}! 🔥🔥🔥\n")

            except Exception as e:
                print(f"  -> ❌ AVISO: Ocorreu um erro ao processar @{influenciador}: {e}")
                if "429" in str(e):
                    print("     -> Rate Limit. Interrompendo verificação para este alvo.")
                    # Não precisamos mais do 'break' geral, pois o erro deve ser por alvo.
                    # A pausa longa deve prevenir que o erro aconteça.

        print(f"\n🏁 Ciclo de verificação completo. Próximo ciclo em {INTERVALO_EM_MINUTOS} minutos.")
        time.sleep(INTERVALO_EM_MINUTOS * 60)

if __name__ == "__main__":
    if BEARER_TOKEN == "SEU_BEARER_TOKEN_AQUI":
        print("🛑 ATENÇÃO: Você precisa configurar seu BEARER_TOKEN.")
    else:
        monitorar_com_paciencia()