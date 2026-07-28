# main.py - Orquestrador do Sistema Paparazzi (gera vídeos novos)
#
# Fluxo: roteirizador.py (Whisper + LLM acha cortes) -> gerador.py (corta com FFmpeg)

import subprocess
import sys
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent

def executar_script(nome_script):
    """Executa um script Python (na pasta do sistema) e verifica se houve erros."""
    print(f"\n=========================================")
    print(f" EXECUTANDO: {nome_script}")
    print(f"=========================================")

    # Garante que estamos usando o mesmo executável Python que está rodando o main.py
    # Isso é crucial para ambientes virtuais!
    python_executable = sys.executable

    resultado = subprocess.run([python_executable, str(BASE_DIR / nome_script)], cwd=str(BASE_DIR))
    
    if resultado.returncode != 0:
        print(f"\nERRO: O script {nome_script} terminou com um erro (código: {resultado.returncode}). Abortando.")
        return False
    
    print(f"\nSUCESSO: O script {nome_script} foi concluído.")
    return True

if __name__ == '__main__':
    VIDEO_ALVO = "video_teste.mp4" # O vídeo que queremos processar

    # Verifica se o vídeo existe antes de começar
    if not os.path.exists(VIDEO_ALVO):
        print(f"ERRO CRÍTICO: O vídeo alvo '{VIDEO_ALVO}' não foi encontrado!")
        print("Coloque o vídeo na pasta do projeto antes de continuar.")
    else:
        # Etapa 1: Executar o roteirizador para gerar o JSON
        if executar_script("roteirizador.py"):
            # Etapa 2: Se o roteirizador funcionou, executar o gerador de cortes
            executar_script("gerador.py")

        print("\n=========================================")
        print(" Pipeline Paparazzi-Worker concluído!")
        print(" Seus clipes estão na pasta 'cortes_gerados'.")
        print("=========================================")