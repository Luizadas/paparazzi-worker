# poster.py - Módulo de Postagem Automática no TikTok (Mirror-Clips)
# Versão: 1.0
#
# Modos de uso:
#   python poster.py                     # Tenta API, cai para Selenium automaticamente
#   python poster.py --api               # Força uso da API oficial
#   python poster.py --selenium          # Força uso do Selenium
#   python poster.py --file video.mp4    # Posta um vídeo específico
#   python poster.py --queue             # Processa a fila de postagem (fila_postagem.json)

import os
import sys
import json
import math
import time
import argparse
import requests
import traceback
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Carrega as variáveis do .env no mesmo diretório do script
load_dotenv(dotenv_path=Path(__file__).parent / ".env")

# ─────────────────────────────────────────────
#  CONFIGURAÇÕES (via .env)
# ─────────────────────────────────────────────
CLIENT_KEY           = os.getenv("TIKTOK_CLIENT_KEY", "")
CLIENT_SECRET        = os.getenv("TIKTOK_CLIENT_SECRET", "")
ACCESS_TOKEN         = os.getenv("TIKTOK_ACCESS_TOKEN", "")
PRIVACY_LEVEL        = os.getenv("TIKTOK_PRIVACY_LEVEL", "PUBLIC_TO_EVERYONE")
DISABLE_COMMENT      = os.getenv("TIKTOK_DISABLE_COMMENT", "false").lower() == "true"
DISABLE_DUET         = os.getenv("TIKTOK_DISABLE_DUET", "false").lower() == "true"
DISABLE_STITCH       = os.getenv("TIKTOK_DISABLE_STITCH", "false").lower() == "true"
SESSION_ID           = os.getenv("TIKTOK_SESSION_ID", "")
CHROME_BINARY        = os.getenv("CHROME_BINARY_PATH", "")
MIRROR_OUTPUT_DIR    = os.getenv("MIRROR_OUTPUT_DIR", "/mnt/paparazzi/mirror_clips")
DEFAULT_CAPTION      = os.getenv("DEFAULT_CAPTION", "🔥 {titulo} #viral #shorts #trending #fyp")
DEFAULT_HASHTAGS     = os.getenv("DEFAULT_HASHTAGS", "#viral #fyp #shorts #trending")

QUEUE_FILE = Path(__file__).parent / "fila_postagem.json"

TIKTOK_API_BASE = "https://open.tiktokapis.com/v2"

# ─────────────────────────────────────────────
#  UTILITÁRIOS
# ─────────────────────────────────────────────

def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")

def gerar_caption(titulo: str = "") -> str:
    caption = DEFAULT_CAPTION.replace("{titulo}", titulo)
    # Garante que hashtags não estejam duplicadas
    if DEFAULT_HASHTAGS and DEFAULT_HASHTAGS not in caption:
        caption = f"{caption} {DEFAULT_HASHTAGS}"
    return caption[:2200]  # TikTok limita a 2200 chars


# ─────────────────────────────────────────────
#  FILA DE POSTAGEM
# ─────────────────────────────────────────────

def carregar_fila() -> list:
    if not QUEUE_FILE.exists():
        return []
    with open(QUEUE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def salvar_fila(fila: list):
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(fila, f, ensure_ascii=False, indent=2)

def adicionar_na_fila(video_path: str, titulo: str = ""):
    """Chamado pelo coletor.py ao finalizar um vídeo processado."""
    fila = carregar_fila()
    entrada = {
        "video_path": str(video_path),
        "titulo": titulo,
        "adicionado_em": datetime.now().isoformat(),
        "status": "pendente"
    }
    fila.append(entrada)
    salvar_fila(fila)
    log(f"📋 Vídeo enfileirado para postagem: {video_path}")

def marcar_fila_status(video_path: str, status: str):
    fila = carregar_fila()
    for item in fila:
        if item["video_path"] == str(video_path):
            item["status"] = status
            item["atualizado_em"] = datetime.now().isoformat()
    salvar_fila(fila)


# ─────────────────────────────────────────────
#  MODO 1: TIKTOK CONTENT POSTING API OFICIAL
# ─────────────────────────────────────────────

class TikTokAPIposter:
    """
    Posta vídeos usando a TikTok Content Posting API v2.
    Documentação: https://developers.tiktok.com/doc/content-posting-api-get-started/
    
    Requer: TIKTOK_ACCESS_TOKEN configurado no .env
    Para obter o access_token, siga o fluxo OAuth em:
    https://developers.tiktok.com/doc/oauth-user-access-token-management/
    """

    CHUNK_SIZE = 10 * 1024 * 1024  # 10MB por chunk (mín: 5MB, máx: 64MB)

    def __init__(self):
        self.access_token = ACCESS_TOKEN
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    def _verificar_token(self) -> bool:
        if not self.access_token:
            log("❌ API: TIKTOK_ACCESS_TOKEN não configurado no .env")
            return False
        return True

    def _init_upload(self, file_size: int) -> dict | None:
        """Inicializa o upload e obtém a URL de envio."""
        total_chunks = math.ceil(file_size / self.CHUNK_SIZE)
        
        payload = {
            "post_info": {
                "title": "",           # Preenchido na etapa de publicação
                "privacy_level": PRIVACY_LEVEL,
                "disable_comment": DISABLE_COMMENT,
                "disable_duet": DISABLE_DUET,
                "disable_stitch": DISABLE_STITCH,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": file_size,
                "chunk_size": self.CHUNK_SIZE,
                "total_chunk_count": total_chunks,
            }
        }

        url = f"{TIKTOK_API_BASE}/post/publish/inbox/video/init/"
        try:
            resp = requests.post(url, json=payload, headers=self.headers, timeout=30)
            data = resp.json()
            if resp.status_code != 200 or data.get("error", {}).get("code") != "ok":
                log(f"❌ API Init Error: {data}")
                return None
            log(f"✅ API: Upload inicializado. publish_id={data['data']['publish_id']}")
            return data["data"]
        except Exception as e:
            log(f"❌ API: Erro ao inicializar upload: {e}")
            return None

    def _upload_chunks(self, upload_url: str, video_path: str) -> bool:
        """Envia o vídeo em chunks via PUT."""
        file_size = os.path.getsize(video_path)
        total_chunks = math.ceil(file_size / self.CHUNK_SIZE)

        log(f"📤 API: Enviando vídeo em {total_chunks} chunk(s)...")

        with open(video_path, "rb") as f:
            for i in range(total_chunks):
                chunk = f.read(self.CHUNK_SIZE)
                start = i * self.CHUNK_SIZE
                end = start + len(chunk) - 1

                put_headers = {
                    "Content-Type": "video/mp4",
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Content-Length": str(len(chunk)),
                }

                try:
                    resp = requests.put(upload_url, data=chunk, headers=put_headers, timeout=120)
                    if resp.status_code not in [200, 206]:
                        log(f"❌ API: Erro no chunk {i+1}/{total_chunks}: HTTP {resp.status_code}")
                        return False
                    log(f"   Chunk {i+1}/{total_chunks} enviado ✅")
                except Exception as e:
                    log(f"❌ API: Erro ao enviar chunk {i+1}: {e}")
                    return False

        return True

    def _verificar_status_publish(self, publish_id: str, titulo: str) -> bool:
        """Aguarda o processamento e confirma a publicação."""
        log("⏳ API: Aguardando processamento do TikTok...")
        url = f"{TIKTOK_API_BASE}/post/publish/status/fetch/"
        
        for tentativa in range(10):
            time.sleep(5)
            try:
                resp = requests.post(
                    url,
                    json={"publish_id": publish_id},
                    headers=self.headers,
                    timeout=30
                )
                data = resp.json()
                status = data.get("data", {}).get("status", "UNKNOWN")
                log(f"   Status ({tentativa+1}/10): {status}")

                if status == "PUBLISH_COMPLETE":
                    log(f"🎉 API: Vídeo publicado com sucesso no TikTok!")
                    return True
                elif status in ["FAILED", "SEND_TO_USER_INBOX"]:
                    log(f"❌ API: Publicação falhou. Status: {status}. Detalhes: {data}")
                    return False
            except Exception as e:
                log(f"   Erro ao verificar status: {e}")

        log("❌ API: Timeout ao aguardar publicação.")
        return False

    def postar(self, video_path: str, titulo: str = "") -> bool:
        if not self._verificar_token():
            return False

        if not os.path.exists(video_path):
            log(f"❌ API: Arquivo não encontrado: {video_path}")
            return False

        file_size = os.path.getsize(video_path)
        log(f"📁 API: Postando '{os.path.basename(video_path)}' ({file_size / 1024 / 1024:.1f} MB)")

        # Etapa 1: Inicializar upload
        init_data = self._init_upload(file_size)
        if not init_data:
            return False

        upload_url = init_data["upload_url"]
        publish_id = init_data["publish_id"]

        # Etapa 2: Enviar chunks
        if not self._upload_chunks(upload_url, video_path):
            return False

        # Etapa 3: Aguardar e confirmar publicação
        return self._verificar_status_publish(publish_id, titulo)


# ─────────────────────────────────────────────
#  MODO 2: SELENIUM (FALLBACK)
# ─────────────────────────────────────────────

class TikTokSeleniumPoster:
    """
    Posta vídeos no TikTok usando automação de navegador via Selenium.
    Usa o cookie sessionid para autenticação sem precisar de login manual.
    
    Como obter o sessionid:
    1. Faça login no TikTok pelo Chrome
    2. Abra DevTools (F12) → Application → Cookies → tiktok.com
    3. Copie o valor do cookie 'sessionid'
    4. Cole em TIKTOK_SESSION_ID no .env
    """

    UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?lang=en"

    def __init__(self):
        self.session_id = SESSION_ID
        self.driver = None

    def _iniciar_driver(self):
        """Inicializa o Chrome WebDriver."""
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.service import Service
            from selenium.webdriver.chrome.options import Options
            from webdriver_manager.chrome import ChromeDriverManager

            opts = Options()
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            opts.add_argument("--disable-blink-features=AutomationControlled")
            opts.add_experimental_option("excludeSwitches", ["enable-automation"])
            opts.add_experimental_option("useAutomationExtension", False)
            opts.add_argument("--window-size=1280,900")
            opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

            if CHROME_BINARY:
                opts.binary_location = CHROME_BINARY

            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=opts)
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            log("✅ Selenium: Chrome iniciado.")
        except ImportError:
            log("❌ Selenium: 'selenium' ou 'webdriver-manager' não instalados.")
            log("   Execute: pip install selenium webdriver-manager")
            raise
        except Exception as e:
            log(f"❌ Selenium: Erro ao iniciar driver: {e}")
            raise

    def _injetar_cookies(self):
        """Injeta o sessionid do TikTok para autenticar sem login."""
        from selenium.webdriver.support.ui import WebDriverWait

        if not self.session_id:
            log("❌ Selenium: TIKTOK_SESSION_ID não configurado no .env")
            raise ValueError("Session ID não configurado")

        log("🍪 Selenium: Injetando cookie de sessão...")
        self.driver.get("https://www.tiktok.com")
        time.sleep(3)

        self.driver.add_cookie({
            "name": "sessionid",
            "value": self.session_id,
            "domain": ".tiktok.com",
            "path": "/",
            "secure": True,
        })
        log("✅ Selenium: Cookie injetado.")

    def _fazer_upload(self, video_path: str, caption: str):
        """Navega até a página de upload e faz o envio do vídeo."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        video_path_abs = str(Path(video_path).resolve())
        log(f"🌐 Selenium: Navegando para página de upload...")
        self.driver.get(self.UPLOAD_URL)

        wait = WebDriverWait(self.driver, 30)

        # Aguarda o input de arquivo aparecer
        log("⏳ Selenium: Aguardando área de upload...")
        input_file = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='file']"))
        )

        # Envia o arquivo
        log(f"📤 Selenium: Enviando arquivo: {video_path_abs}")
        input_file.send_keys(video_path_abs)

        # Aguarda o processamento do upload (barra de progresso desaparecer)
        log("⏳ Selenium: Aguardando processamento do vídeo pelo TikTok...")
        time.sleep(10)

        # Tenta preencher a legenda
        try:
            caption_field = wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "div[contenteditable='true']")
                )
            )
            caption_field.click()
            time.sleep(1)
            # Limpa o campo e digita a legenda
            caption_field.send_keys(caption)
            log(f"✅ Selenium: Legenda preenchida.")
        except Exception:
            log("⚠️  Selenium: Não foi possível preencher a legenda automaticamente.")

        # Aguarda um pouco e clica em Publicar
        time.sleep(5)
        try:
            btn_publicar = wait.until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//button[contains(., 'Post') or contains(., 'Publicar') or contains(., 'Upload')]")
                )
            )
            btn_publicar.click()
            log("✅ Selenium: Botão de publicar clicado!")
            time.sleep(8)
            return True
        except Exception as e:
            log(f"❌ Selenium: Não foi possível clicar em Publicar: {e}")
            log("   Tirando screenshot de debug: selenium_debug.png")
            self.driver.save_screenshot(str(Path(__file__).parent / "selenium_debug.png"))
            return False

    def postar(self, video_path: str, titulo: str = "") -> bool:
        try:
            self._iniciar_driver()
            self._injetar_cookies()
            caption = gerar_caption(titulo)
            resultado = self._fazer_upload(video_path, caption)
            return resultado
        except Exception as e:
            log(f"❌ Selenium: Erro geral: {e}")
            if self.driver:
                try:
                    debug_path = str(Path(__file__).parent / "selenium_debug.png")
                    self.driver.save_screenshot(debug_path)
                    log(f"   Tirando screenshot de debug: {debug_path}")
                except Exception as ex:
                    log(f"   Erro ao tirar screenshot: {ex}")
            traceback.print_exc()
            return False
        finally:
            if self.driver:
                time.sleep(3)
                self.driver.quit()
                log("🔒 Selenium: Navegador fechado.")


# ─────────────────────────────────────────────
#  LÓGICA PRINCIPAL
# ─────────────────────────────────────────────

def postar_video(video_path: str, titulo: str = "", modo: str = "auto") -> bool:
    """
    Posta um vídeo no TikTok.
    modo: 'auto' | 'api' | 'selenium'
    """
    log(f"\n{'='*50}")
    log(f"🚀 Iniciando postagem: {os.path.basename(video_path)}")
    log(f"   Modo: {modo.upper()}")

    if modo in ("auto", "api"):
        log("--- Tentando API Oficial ---")
        poster = TikTokAPIposter()
        sucesso = poster.postar(video_path, titulo)
        if sucesso:
            return True
        elif modo == "api":
            log("❌ API falhou e modo forçado como 'api'. Abortando.")
            return False
        else:
            log("⚠️  API não disponível. Ativando fallback Selenium...")

    if modo in ("auto", "selenium"):
        log("--- Tentando Selenium ---")
        poster = TikTokSeleniumPoster()
        return poster.postar(video_path, titulo)

    return False


def processar_fila(modo: str = "auto"):
    """Processa todos os vídeos pendentes na fila de postagem."""
    fila = carregar_fila()
    pendentes = [item for item in fila if item.get("status") == "pendente"]

    if not pendentes:
        log("📋 Fila de postagem vazia. Nada a fazer.")
        return

    log(f"📋 {len(pendentes)} vídeo(s) na fila para postar.")

    for item in pendentes:
        video_path = item["video_path"]
        titulo = item.get("titulo", "")

        marcar_fila_status(video_path, "processando")
        sucesso = postar_video(video_path, titulo, modo)

        if sucesso:
            marcar_fila_status(video_path, "publicado")
        else:
            marcar_fila_status(video_path, "falhou")

        # Aguarda entre postagens para não sobrecarregar
        time.sleep(30)

    log("\n✅ Processamento da fila concluído.")


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="🎬 TikTok Auto Poster — Mirror-Clips Module"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--api", action="store_true", help="Força uso da API oficial do TikTok")
    group.add_argument("--selenium", action="store_true", help="Força uso do Selenium")

    parser.add_argument("--file", type=str, help="Caminho para um vídeo específico a ser postado")
    parser.add_argument("--titulo", type=str, default="", help="Título/legenda do vídeo")
    parser.add_argument("--queue", action="store_true", help="Processa a fila de postagem completa")

    args = parser.parse_args()

    # Determina o modo
    if args.api:
        modo = "api"
    elif args.selenium:
        modo = "selenium"
    else:
        modo = "auto"

    if args.queue:
        processar_fila(modo)
    elif args.file:
        sucesso = postar_video(args.file, args.titulo, modo)
        sys.exit(0 if sucesso else 1)
    else:
        # Comportamento padrão: processa a fila
        processar_fila(modo)
