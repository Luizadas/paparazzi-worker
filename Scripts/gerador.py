# gerador.py

import json
import subprocess
import os
from datetime import timedelta

def formatar_tempo_ffmpeg(segundos: float) -> str:
    """Converte segundos para o formato HH:MM:SS.ms, ideal para o FFmpeg."""
    delta = timedelta(seconds=segundos)
    return str(delta)

def gerar_cortes_do_roteiro(caminho_video_original: str, roteiro_json: list):
    """
    Lê uma lista de clipes de um roteiro JSON e usa o FFmpeg para cortar o vídeo original.
    """
    print("--- Iniciando Módulo 4: Geração de Clipes ---")
    
    if not os.path.exists(caminho_video_original):
        print(f"ERRO: Vídeo original não encontrado em '{caminho_video_original}'")
        return

    # Cria uma pasta para salvar os clipes, se não existir
    pasta_saida = "cortes_gerados"
    os.makedirs(pasta_saida, exist_ok=True)
    print(f"Clipes serão salvos em: '{pasta_saida}/'")

    for i, clipe in enumerate(roteiro_json):
        num_clipe = i + 1
        start_time = clipe['start']
        end_time = clipe['end']
        justificativa = clipe['justificativa'].replace(" ", "_").lower() # Cria um nome de arquivo amigável

        tempo_inicial_formatado = formatar_tempo_ffmpeg(start_time)
        tempo_final_formatado = formatar_tempo_ffmpeg(end_time)
        
        # Gera um nome de arquivo descritivo
        nome_arquivo_saida = f"{pasta_saida}/clipe_{num_clipe:02d}_{justificativa[:30]}.mp4"
        
        print(f"\n[Clip {num_clipe}/{len(roteiro_json)}] Gerando corte...")
        print(f"  Roteiro: De {tempo_inicial_formatado} até {tempo_final_formatado}")
        print(f"  Motivo: {clipe['justificativa']}")

        comando_ffmpeg = [
            "ffmpeg",
            "-i", caminho_video_original,
            "-ss", tempo_inicial_formatado,
            "-to", tempo_final_formatado,
            "-c", "copy", # A mágica da eficiência!
            "-y", # Sobrescreve o arquivo de saída se ele já existir
            nome_arquivo_saida
        ]
        
        try:
            # Usamos capture_output=True para não poluir o console com o output do ffmpeg
            resultado = subprocess.run(comando_ffmpeg, check=True, capture_output=True, text=True)
            print(f"  SUCESSO: Clipe salvo como '{nome_arquivo_saida}'")
        except FileNotFoundError:
            print("  ERRO: FFmpeg não encontrado. Ele está instalado e no PATH do sistema?")
            break # Interrompe o loop se o FFmpeg não for encontrado
        except subprocess.CalledProcessError as e:
            print(f"  ERRO ao executar o FFmpeg para o clipe {num_clipe}:")
            print(f"  Comando: {' '.join(comando_ffmpeg)}")
            print(f"  Erro stderr: {e.stderr}")

# --- Bloco de Teste Independente ---
# Permite rodar este script sozinho para testar a lógica de corte
if __name__ == '__main__':
    # Este é um exemplo do JSON que o roteirizador.py vai gerar
    roteiro_exemplo = [
        {
            "start": 10.5,
            "end": 45.0,
            "justificativa": "Gancho forte com pergunta polemica"
        },
        {
            "start": 123.2,
            "end": 181.8,
            "justificativa": "Explicacao de conceito complexo"
        },
        {
            "start": 305.0,
            "end": 330.1,
            "justificativa": "Pico de emocao e risada"
        }
    ]

    video_teste = "/mnt/paparazzi/RICHARD RASMUSSEN FINALMENTE REALIZOU A SUA OPERAÇÃO ! - 9pvLdumWiCs_20250717_175031.mp4" # Use o mesmo vídeo de teste do outro script

    if not os.path.exists(video_teste):
        print(f"AVISO: Arquivo de teste '{video_teste}' não encontrado.")
        print("Crie um ou coloque um vídeo com este nome na pasta do projeto para testar.")
    else:
        gerar_cortes_do_roteiro(video_teste, roteiro_exemplo)