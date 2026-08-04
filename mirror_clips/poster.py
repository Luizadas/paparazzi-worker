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
from dotenv import load_dotenv, find_dotenv

# Carrega as variáveis do .env único na raiz do projeto
load_dotenv(find_dotenv())

# Raiz do repo no path para importar 'comum' (ponte com o banco de controle)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
HEADLESS             = os.getenv("TIKTOK_HEADLESS", "true").lower() == "true"
CHROME_BINARY        = os.getenv("CHROME_BINARY_PATH", "")
MIRROR_OUTPUT_DIR    = os.getenv("MIRROR_OUTPUT_DIR", "/mnt/paparazzi/mirror_clips")
DEFAULT_CAPTION      = os.getenv("DEFAULT_CAPTION", "🔥 {titulo} #viral #shorts #trending #fyp")
DEFAULT_HASHTAGS     = os.getenv("DEFAULT_HASHTAGS", "#viral #fyp #shorts #trending")

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
#  FILA DE POSTAGEM  (fonte única = Postgres, via comum/db_bridge)
# ─────────────────────────────────────────────
# A fila vive no banco (model Post): pendente → postando → publicado/falhou.
# O claim é atômico (SELECT ... FOR UPDATE SKIP LOCKED), então processador e
# postador rodam em paralelo sem duplo-claim — sem arquivo/lock local.

# Sinal criado pelo orquestrador quando o processamento termina; o poster --watch
# drena o restante da fila e então encerra.
STOP_FLAG = Path(__file__).parent / ".processamento_concluido"
# Pausa curta entre postagens (o próximo vídeo já é processado em paralelo).
POST_GAP = int(os.getenv("POST_GAP", "15"))


def adicionar_na_fila(video_path: str, titulo: str = "", caption: str = ""):
    """Chamado pelo coletor.py ao finalizar um vídeo: enfileira no banco (Post
    'pendente'). 'caption' é a legenda gerada por IA."""
    from comum import db_bridge
    ok = db_bridge.enfileirar(str(video_path), titulo=titulo, caption=caption or "")
    if ok:
        log(f"📋 Vídeo enfileirado para postagem (banco): {video_path}")
    else:
        log(f"⚠️  Não consegui enfileirar no banco: {video_path}")


def reivindicar_proximo():
    """Reivindica ATOMICAMENTE o próximo pendente no banco e o marca 'postando'.
    Retorna dict {post_id, video_path, titulo, caption} ou None se a fila vazia."""
    from comum import db_bridge
    return db_bridge.reivindicar_proximo_post()


def marcar_fila_status(video_path: str, status: str, post_id: int = None):
    """Atualiza o status do post no banco (publicado/falhou/postando)."""
    from comum import db_bridge
    db_bridge.marcar_post_status(str(video_path), status, post_id=post_id)

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

    def postar(self, video_path: str, titulo: str = "", caption: str = "") -> bool:
        if not self._verificar_token():
            return False

        # Legenda: prioriza a gerada por IA; senão, template padrão.
        titulo = caption.strip() if caption else titulo

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
            if HEADLESS:
                opts.add_argument("--headless=new")
                log("🖥️  Selenium: modo headless ativado (sem janela).")
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

        log("🍪 Selenium: Injetando cookie de sessão (via CDP)...")

        # Abre um contexto tiktok.com (necessário para o CDP setar no domínio certo)
        self.driver.get("https://www.tiktok.com")
        time.sleep(2)

        # Injeta o cookie via Chrome DevTools Protocol. Ao contrário de add_cookie,
        # o Network.setCookie atua na camada de rede e aceita domínio com ponto
        # inicial sem exigir estar exatamente na página — elimina o erro
        # intermitente "invalid cookie domain" no headless.
        try:
            self.driver.execute_cdp_cmd("Network.enable", {})
            self.driver.execute_cdp_cmd("Network.setCookie", {
                "name": "sessionid",
                "value": self.session_id,
                "domain": ".tiktok.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
            })
        except Exception as e:
            # Fallback: método clássico do Selenium
            log(f"   (CDP falhou: {e} — tentando add_cookie clássico)")
            self.driver.add_cookie({
                "name": "sessionid",
                "value": self.session_id,
                "path": "/",
                "secure": True,
            })
        log("✅ Selenium: Cookie injetado.")

        # Recarrega já autenticado para confirmar o login
        self.driver.get("https://www.tiktok.com")
        time.sleep(2)

    def _definir_privacidade(self, wait) -> bool:
        """
        Ajusta a visibilidade do post conforme PRIVACY_LEVEL antes de publicar.
        Retorna True se aplicou (ou se não havia nada a fazer), False se falhou.
        A UI do TikTok Studio muda com frequência — por isso é best-effort e
        tenta textos em inglês e português.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys

        textos_por_nivel = {
            "PUBLIC_TO_EVERYONE": ["Everyone", "Todos"],
            "MUTUAL_FOLLOW_FRIENDS": ["Friends", "Amigos"],
            "FOLLOWER_OF_CREATOR": ["Followers", "Seguidores"],
            "SELF_ONLY": ["Only you", "Only me", "Somente você", "Private", "Privado"],
        }
        alvos = textos_por_nivel.get(PRIVACY_LEVEL)
        if not alvos:
            return True  # nível desconhecido: deixa o padrão da conta

        log(f"🔒 Selenium: Ajustando visibilidade para '{PRIVACY_LEVEL}'...")
        time.sleep(2)

        # 0. Fecha popups/modais que possam sobrepor e bloquear cliques
        try:
            self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.5)
        except Exception:
            pass

        # 1. Abre o dropdown "Who can see this post" clicando na CAIXA de valor
        #    atual (ex: "Everyone"), ancorada logo após o rótulo.
        labels = ["Who can see this post", "Who can view this post",
                  "Who can watch this video", "Quem pode ver esta publicação",
                  "Quem pode ver este post"]
        valores_atuais = ["Everyone", "Friends", "Followers", "Only you", "Only me",
                          "Todos", "Amigos", "Seguidores", "Somente você"]
        aberto = False
        for label in labels:
            try:
                lbl = self.driver.find_element(
                    By.XPATH, f"//*[contains(normalize-space(text()), '{label}')]"
                )
                self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", lbl)
                time.sleep(0.5)
                # a caixa de valor atual = primeiro elemento após o rótulo cujo
                # texto é um dos valores possíveis do dropdown
                cond = " or ".join([f"normalize-space(text())='{v}'" for v in valores_atuais])
                caixa = lbl.find_element(By.XPATH, f"following::*[{cond}][1]")
                try:
                    caixa.click()               # clique nativo abre o dropdown
                except Exception:
                    self.driver.execute_script("arguments[0].click();", caixa)
                aberto = True
                time.sleep(1.5)
                break
            except Exception:
                continue

        if not aberto:
            log("⚠️  Selenium: não localizei o dropdown de visibilidade.")
            return False

        # Debug: salva o estado com o dropdown (esperado) aberto
        try:
            self.driver.save_screenshot(str(Path(__file__).parent / "debug_privacy_open.png"))
        except Exception:
            pass

        # 2. Seleciona a opção desejada. Após abrir, há DUAS ocorrências do texto
        #    (a caixa + a opção na lista) — pegamos a ÚLTIMA (a opção da lista).
        for texto in alvos:
            try:
                opcoes = self.driver.find_elements(
                    By.XPATH, f"//*[normalize-space(text())='{texto}']"
                )
                if not opcoes:
                    continue
                alvo = opcoes[-1]
                try:
                    alvo.click()
                except Exception:
                    self.driver.execute_script("arguments[0].click();", alvo)
                log(f"✅ Selenium: Visibilidade definida como '{texto}'.")
                time.sleep(1)
                return True
            except Exception:
                continue

        log(f"⚠️  Selenium: NÃO foi possível selecionar a opção para '{PRIVACY_LEVEL}'.")
        return False

    def _fechar_tour(self):
        """Remove o tour/tutorial (react-joyride) e overlays que interceptam cliques."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        # 1) tenta um botão de fechar/pular, se existir
        for xp in ["//button[contains(., 'Skip')]", "//button[contains(., 'Got it')]",
                   "//button[contains(., 'Pular')]", "//button[contains(., 'Entendi')]",
                   "//button[@aria-label='Close']"]:
            try:
                els = self.driver.find_elements(By.XPATH, xp)
                if els:
                    els[0].click()
                    time.sleep(0.3)
            except Exception:
                pass
        # 2) remove à força qualquer elemento do joyride/overlay via JS
        try:
            self.driver.execute_script(
                "document.querySelectorAll("
                "'.react-joyride__overlay,[data-test-id=\"overlay\"],"
                ".react-joyride__spotlight,.react-joyride__tooltip')"
                ".forEach(function(e){e.remove();});"
            )
        except Exception:
            pass
        # 3) ESC como reforço
        try:
            self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        except Exception:
            pass

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

        # Fecha o tour/tutorial (react-joyride) que sobrepõe a página e intercepta
        # cliques (ex: popup "New editing features added").
        self._fechar_tour()

        # Preenche a legenda: limpa o texto auto-preenchido (nome do arquivo) e
        # digita a legenda gerada por IA. O editor é um contenteditable (DraftJS).
        try:
            from selenium.webdriver.common.keys import Keys
            caption_field = wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "div[contenteditable='true']")
                )
            )
            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", caption_field)
            self._fechar_tour()  # garante que nada esteja sobrepondo antes do clique
            try:
                caption_field.click()
            except Exception:
                self.driver.execute_script("arguments[0].click();", caption_field)
            time.sleep(1)

            # Seleciona tudo e apaga o conteúdo pré-preenchido pelo TikTok
            caption_field.send_keys(Keys.CONTROL, "a")
            time.sleep(0.3)
            caption_field.send_keys(Keys.DELETE)
            time.sleep(0.3)

            # Digita a legenda. Hashtags disparam um menu de sugestões; um espaço
            # após cada palavra fecha o menu e evita seleção acidental.
            caption_field.send_keys(caption)
            time.sleep(0.5)
            caption_field.send_keys(" ")
            log(f"✅ Selenium: Legenda preenchida: {caption[:80]}...")
        except Exception as e:
            log(f"⚠️  Selenium: Não foi possível preencher a legenda automaticamente: {e}")

        # Ajusta a visibilidade do post (público/amigos/privado) antes de publicar
        privacidade_ok = self._definir_privacidade(wait)
        if PRIVACY_LEVEL == "SELF_ONLY" and not privacidade_ok:
            log("🛑 Selenium: Abortando — não foi possível garantir visibilidade PRIVADA.")
            self.driver.save_screenshot(str(Path(__file__).parent / "selenium_debug.png"))
            return False

        # Localiza e clica no botão de publicar. O botão fica no rodapé (fora da
        # tela) e só habilita quando o upload/processamento termina.
        time.sleep(3)

        def _achar_botao_post():
            # 1) seletor específico do TikTok Studio
            for sel in ["button[data-e2e='post_video_button']",
                        "[data-e2e='post_video_button']"]:
                els = self.driver.find_elements(By.CSS_SELECTOR, sel)
                if els:
                    return els[0]
            # 2) fallback por texto (evita 'Upload' pra não pegar outros botões)
            for xp in ["//button[normalize-space()='Post']",
                       "//button[normalize-space()='Publicar']",
                       "//button[.//div[normalize-space()='Post']]",
                       "//button[contains(., 'Post') or contains(., 'Publicar')]"]:
                els = self.driver.find_elements(By.XPATH, xp)
                if els:
                    return els[0]
            return None

        try:
            # Espera o botão aparecer E ficar habilitado (upload concluído),
            # até ~90s (processamento do vídeo pode demorar).
            btn = None
            for _ in range(45):
                btn = _achar_botao_post()
                if btn and btn.is_enabled() and btn.get_attribute("aria-disabled") != "true":
                    break
                time.sleep(2)

            if not btn:
                raise RuntimeError("Botão de publicar não encontrado.")

            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            time.sleep(1)
            try:
                btn.click()
            except Exception:
                self.driver.execute_script("arguments[0].click();", btn)
            log("✅ Selenium: Botão de publicar clicado!")

            # O TikTok pode abrir um modal "Continue to post?" quando as checagens
            # (copyright / content check) ainda estão rodando. Confirmamos com "Post now".
            time.sleep(2)
            for xp in ["//button[normalize-space()='Post now']",
                       "//button[contains(., 'Post now')]",
                       "//button[normalize-space()='Publicar agora']",
                       "//button[contains(., 'Publicar agora')]"]:
                modais = self.driver.find_elements(By.XPATH, xp)
                if modais:
                    try:
                        modais[0].click()
                    except Exception:
                        self.driver.execute_script("arguments[0].click();", modais[0])
                    log("✅ Selenium: Confirmado 'Post now' (checagem ainda em andamento).")
                    break

            # Confirma a publicação: TikTok mostra um modal de sucesso / redireciona
            time.sleep(8)
            sucesso_txt = ["Your video has been", "was posted", "foi publicad",
                           "Manage your posts", "posted to"]
            page = self.driver.page_source.lower()
            if any(t.lower() in page for t in sucesso_txt) or "content" in (self.driver.current_url or ""):
                log("🎉 Selenium: Publicação confirmada!")
            else:
                log("ℹ️  Selenium: Botão clicado; confirmação não detectada explicitamente (verifique a conta).")
            self.driver.save_screenshot(str(Path(__file__).parent / "post_result.png"))
            return True
        except Exception as e:
            log(f"❌ Selenium: Não foi possível clicar em Publicar: {e}")
            log("   Tirando screenshot de debug: selenium_debug.png")
            self.driver.save_screenshot(str(Path(__file__).parent / "selenium_debug.png"))
            return False

    def postar(self, video_path: str, titulo: str = "", caption: str = "") -> bool:
        try:
            self._iniciar_driver()
            self._injetar_cookies()
            # Usa a legenda gerada por IA se houver; senão, cai no template padrão.
            caption_final = caption.strip() if caption else gerar_caption(titulo)
            resultado = self._fazer_upload(video_path, caption_final)
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

def postar_video(video_path: str, titulo: str = "", modo: str = "auto", caption: str = "") -> bool:
    """
    Posta um vídeo no TikTok.
    modo: 'auto' | 'api' | 'selenium'
    caption: legenda gerada por IA (se vazia, usa o template padrão).
    """
    log(f"\n{'='*50}")
    log(f"🚀 Iniciando postagem: {os.path.basename(video_path)}")
    log(f"   Modo: {modo.upper()}")

    if modo in ("auto", "api"):
        log("--- Tentando API Oficial ---")
        poster = TikTokAPIposter()
        sucesso = poster.postar(video_path, titulo, caption)
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
        return poster.postar(video_path, titulo, caption)

    return False


def processar_fila(modo: str = "auto"):
    """Posta todos os pendentes da fila (banco), de forma serial."""
    from comum import db_bridge
    n = db_bridge.contar_pendentes()
    if not n:
        log("📋 Fila de postagem vazia. Nada a fazer.")
        return
    log(f"📋 {n} vídeo(s) na fila para postar.")

    while True:
        item = reivindicar_proximo()
        if not item:
            break
        video_path = item["video_path"]
        sucesso = postar_video(video_path, item.get("titulo", ""), modo, item.get("caption", ""))
        marcar_fila_status(video_path, "publicado" if sucesso else "falhou",
                           post_id=item.get("post_id"))
        time.sleep(30)   # evita sobrecarga entre postagens

    log("\n✅ Processamento da fila concluído.")


def modo_watch(modo: str = "auto"):
    """
    PIPELINE CONCORRENTE: fica observando a fila e postando os vídeos assim que
    ficam prontos, EM PARALELO com o processador (que edita o próximo vídeo).
    Encerra quando o processamento sinaliza conclusão (STOP_FLAG) e não há mais
    pendentes/em-postagem. Assim, enquanto um vídeo posta, o outro é processado.
    """
    log("👀 Poster em modo WATCH — postando conforme a fila enche (pipeline concorrente).")
    # Enquanto o poster está vivo, ele usa a LLM (caption). Registra como consumidor
    # para o shutdown gracioso: o Ollama só encerra quando o poster também soltar.
    try:
        from comum import db_bridge
        db_bridge.llm_adquirir("poster")
    except Exception:
        pass
    try:
        while True:
            item = reivindicar_proximo()
            if item:
                video_path = item["video_path"]
                titulo = item.get("titulo", "")
                caption = item.get("caption", "")
                sucesso = postar_video(video_path, titulo, modo, caption)
                marcar_fila_status(video_path, "publicado" if sucesso else "falhou",
                                   post_id=item.get("post_id"))
                time.sleep(POST_GAP)   # pausa anti-spam (o próximo já processa em paralelo)
            else:
                # Nada pendente agora
                if STOP_FLAG.exists():
                    log("✅ Processamento concluído e fila drenada — encerrando o poster.")
                    break
                time.sleep(3)   # aguarda o processador produzir o próximo
    finally:
        try:
            from comum import db_bridge
            db_bridge.llm_liberar("poster")   # encerra Ollama se ninguém mais usa
        except Exception:
            pass


def modo_daemon(modo: str = "auto", deve_parar=lambda: False):
    """
    Poster como SERVIÇO (systemd): fica vivo postando a fila conforme ela enche,
    e — diferente do modo_watch — NÃO encerra quando a fila esvazia; só sai quando
    `deve_parar()` fica True (botão Desligar / SIGTERM).

    Parada graciosa: `deve_parar` é checado ENTRE posts — o post em andamento
    termina normalmente. Segura a LLM ('poster') apenas ENQUANTO há trabalho e a
    libera quando a fila fica ociosa, para o Ollama poder ser encerrado.
    """
    log("📮 Poster em modo DAEMON — postando a fila (fica vivo aguardando novos).")
    segurando_llm = False

    def _adquirir():
        nonlocal segurando_llm
        if not segurando_llm:
            try:
                from comum import db_bridge
                db_bridge.llm_adquirir("poster", garantir=False)
            except Exception:
                pass
            segurando_llm = True

    def _liberar():
        nonlocal segurando_llm
        if segurando_llm:
            try:
                from comum import db_bridge
                db_bridge.llm_liberar("poster")
            except Exception:
                pass
            segurando_llm = False

    # REGRA DE PARADA: desligar é imediato. A fila NÃO segura o desligamento —
    # itens pendentes ficam para a próxima vez. O ÚNICO caso que aguarda é uma
    # postagem em andamento (o selenium do post atual roda até o fim; SIGTERM não
    # o interrompe). Depois de terminar o post atual, para na hora.
    try:
        while not deve_parar():
            item = reivindicar_proximo()
            if item:
                _adquirir()
                video_path = item["video_path"]
                titulo = item.get("titulo", "")
                caption = item.get("caption", "")
                sucesso = postar_video(video_path, titulo, modo, caption)  # roda até o fim
                marcar_fila_status(video_path, "publicado" if sucesso else "falhou",
                                   post_id=item.get("post_id"))
                if deve_parar():
                    break                # parada pedida → não pega novo item (fila fica pendente)
                for _ in range(POST_GAP):    # pausa anti-spam, interrompível
                    if deve_parar():
                        break
                    time.sleep(1)
            else:
                _liberar()               # fila ociosa → solta a LLM
                for _ in range(3):       # aguarda novos, respondendo rápido ao desligar
                    if deve_parar():
                        break
                    time.sleep(1)
    finally:
        _liberar()
        log("👋 Poster daemon encerrado.")


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
    parser.add_argument("--queue", action="store_true", help="Posta toda a fila de uma vez (modo serial)")
    parser.add_argument("--watch", action="store_true",
                        help="Fica postando conforme a fila enche (pipeline concorrente)")
    parser.add_argument("--daemon", action="store_true",
                        help="Serviço: fica vivo postando a fila (não encerra ao esvaziar)")

    args = parser.parse_args()

    # Determina o modo
    if args.api:
        modo = "api"
    elif args.selenium:
        modo = "selenium"
    else:
        modo = "auto"

    if args.daemon:
        modo_daemon(modo)
    elif args.watch:
        modo_watch(modo)
    elif args.queue:
        processar_fila(modo)
    elif args.file:
        sucesso = postar_video(args.file, args.titulo, modo)
        sys.exit(0 if sucesso else 1)
    else:
        # Comportamento padrão: processa a fila
        processar_fila(modo)
