# --- paparazzi-worker: Módulo 1 (Detecção) ---
# Versão 0.9-selenium: Web Scraping - Prova de Conceito com Selenium

import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

# --- 1. CONFIGURAÇÃO ---
URL_ALVO_TIKTOK = "https://www.tiktok.com/@neymarjr"

# --- 2. LÓGICA DE SCRAPING COM SELENIUM ---

def fazer_primeiro_contato_selenium():
    """
    Usa o Selenium para acessar uma página do TikTok, tirar um screenshot
    e extrair o conteúdo HTML inicial.
    """
    print("✅ Iniciando o Selenium para o primeiro contato com o TikTok...")

    try:
        # Configurações do Chrome para rodar em modo "headless" (sem janela)
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        
        print("  -> Configurando o WebDriver do Chrome...")
        # Usa o webdriver-manager para baixar e configurar o chromedriver automaticamente
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        print(f"  -> Navegando para: {URL_ALVO_TIKTOK}")
        driver.get(URL_ALVO_TIKTOK)
        
        # Selenium pode precisar de um tempo explícito para a página carregar o JavaScript
        print("  -> Aguardando 10 segundos para a página carregar completamente...")
        time.sleep(10)
        
        # Tira um screenshot da página para verificação visual.
        screenshot_path = "tiktok_pagina_selenium.png"
        driver.save_screenshot(screenshot_path)
        print(f"📸 Screenshot salvo em: {screenshot_path}")

        # Extrai o conteúdo HTML completo da página.
        html_content = driver.page_source
        print("\n✔️ Conteúdo HTML extraído com sucesso!")
        print("-" * 50)
        print("500 primeiros caracteres do HTML:")
        print(html_content[:500])
        print("-" * 50)

    except Exception as e:
        print(f"\n❌ Ocorreu um erro durante a operação do Selenium.")
        print(f"   Detalhe do erro: {e}")
    
    finally:
        # Garante que o navegador seja fechado no final, mesmo se ocorrer um erro.
        if 'driver' in locals():
            driver.quit()
            print("\n✅ Navegador encerrado. Operação concluída.")

# --- 3. EXECUÇÃO ---
if __name__ == "__main__":
    fazer_primeiro_contato_selenium()