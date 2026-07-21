import base64, csv, html, os, re
from io import BytesIO, StringIO
from pathlib import Path
import qrcode, requests, streamlit as st
from streamlit_autorefresh import st_autorefresh

try:
    import gspread
    from google.oauth2.service_account import Credentials
except Exception:
    gspread = None
    Credentials = None

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / '.last_followers_count'

def secret(name, default=''):
    """Read Streamlit Secrets first and environment variables second."""
    try:
        if name in st.secrets:
            return str(st.secrets[name]).strip()
    except Exception:
        pass
    return str(os.getenv(name, default)).strip()
SHEET_ID = secret('LIIVV_SHEET_ID', '1M98FNCa83Y5V9grSlWazoY_xthp_cs32s5xuheBNM0o')
PUBLICITY_SHEET = secret('PUBLICITY_SHEET', 'Publicidades')
CONFIG_SHEET = secret('CONFIG_SHEET', 'Configuracoes')
USER_ACCESS_TOKEN = secret('USER_ACCESS_TOKEN')
IG_BUSINESS_ID = secret('IG_BUSINESS_ID')
GRAPH_VERSION = secret('GRAPH_VERSION', 'v24.0')
REFRESH_SECONDS = int(secret('REFRESH_SECONDS', '5'))
SHEET_CACHE_SECONDS = int(secret('SHEET_CACHE_SECONDS', '5'))
MEDIA_CACHE_SECONDS = int(secret('MEDIA_CACHE_SECONDS', '15'))
MOCK_FOLLOWERS_START = int(secret('MOCK_FOLLOWERS_START', '0'))

DEFAULT_CONFIG = {
    'BRAND_NAME':'LIIVV BEAUTY','INSTAGRAM_USERNAME':'liivv_beauty',
    'PROFILE_URL':'https://www.instagram.com/liivv_beauty/',
    'WELCOME_MESSAGE':'Bem-vinda à comunidade LIIVV Beauty',
    'CTA_TITLE':'Siga a LIIVV no Instagram',
    'CTA_SUBTITLE':'Aponte a câmera e descubra beleza, praticidade e cuidado',
    'FOOTER_TEXT':'Rochaverá • Bridge Tower • São Paulo',
    'BOOKING_URL':'https://www.instagram.com/liivv_beauty/',
    'SECONDARY_URL':'https://www.instagram.com/liivv_beauty/'
}

def esc(v): return html.escape(str(v or ''))

def drive_image_url(url):
    text = str(url or '').strip()
    for pattern in [r'/file/d/([\w-]+)', r'[?&]id=([\w-]+)', r'/d/([\w-]+)']:
        m = re.search(pattern, text)
        if m: return f'https://drive.google.com/thumbnail?id={m.group(1)}&sz=w1800'
    return text

def qr_data_uri(url):
    img = qrcode.make(url); bio = BytesIO(); img.save(bio, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(bio.getvalue()).decode()

@st.cache_resource(show_spinner=False)
def google_client():
    if not gspread or not Credentials:
        raise RuntimeError('Bibliotecas gspread/google-auth não instaladas')

    if 'google_service_account' in st.secrets:
        data = dict(st.secrets['google_service_account'])
    elif 'gcp_service_account' in st.secrets:
        data = dict(st.secrets['gcp_service_account'])
    else:
        raise RuntimeError('Seção [gcp_service_account] ou [google_service_account] ausente nos Secrets')

    private_key = str(data.get('private_key', '')).replace('\\n', '\n').strip()
    if private_key:
        data['private_key'] = private_key

    required = ['client_email', 'private_key', 'token_uri']
    missing = [field for field in required if not data.get(field)]
    if missing:
        raise RuntimeError('Campos ausentes na conta de serviço: ' + ', '.join(missing))

    scopes = [
        'https://www.googleapis.com/auth/spreadsheets.readonly',
        'https://www.googleapis.com/auth/drive.readonly'
    ]
    creds = Credentials.from_service_account_info(data, scopes=scopes)
    return gspread.authorize(creds)

def sheet_rows(name):
    errors = []

    # Caminho principal: conta de serviço
    try:
        client = google_client()
        worksheet = client.open_by_key(SHEET_ID).worksheet(name)
        return worksheet.get_all_records(), ''
    except Exception as exc:
        errors.append(f'Conta de serviço: {type(exc).__name__}: {exc}')

    # Fallback igual ao YVORA: planilha publicada/pública em CSV
    try:
        url = (
            f'https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq'
            f'?tqx=out:csv&sheet={requests.utils.quote(name)}'
        )
        response = requests.get(
            url,
            timeout=8,
            headers={'Cache-Control': 'no-cache', 'Pragma': 'no-cache'}
        )
        response.raise_for_status()
        rows = list(csv.DictReader(StringIO(response.text)))
        if rows:
            return rows, ''
        errors.append('CSV público retornou zero linhas')
    except Exception as exc:
        errors.append(f'CSV público: {type(exc).__name__}: {exc}')

    return [], ' | '.join(errors)

@st.cache_data(ttl=SHEET_CACHE_SECONDS, show_spinner=False)
def get_config():
    cfg = DEFAULT_CONFIG.copy()
    rows, error = sheet_rows(CONFIG_SHEET)
    for row in rows:
        key = str(row.get('Chave') or row.get('chave') or '').strip()
        value = str(row.get('Valor') or row.get('valor') or '').strip()
        if key and value:
            cfg[key] = value
    return cfg, error

@st.cache_data(ttl=SHEET_CACHE_SECONDS, show_spinner=False)
def get_publicities():
    rows, error = sheet_rows(PUBLICITY_SHEET)
    out = []

    for row in rows:
        active = str(row.get('Ativo') or row.get('ativo') or '0').strip().lower()
        title = str(row.get('Publicidade') or row.get('publicidade') or '').strip()
        image = str(row.get('URL') or row.get('url') or '').strip()
        link = str(row.get('Link') or row.get('link') or '#').strip()
        category = str(row.get('Categoria') or row.get('categoria') or 'LIIVV').strip()

        if active not in {'1', 'true', 'sim', 'yes'}:
            continue
        if not title or not image:
            continue

        try:
            order = int(float(str(row.get('Ordem') or row.get('ordem') or '999').replace(',', '.')))
        except Exception:
            order = 999

        out.append({
            'title': title,
            'image': drive_image_url(image),
            'link': link or '#',
            'category': category,
            'order': order
        })

    return sorted(out, key=lambda item: (item['order'], item['title'].lower())), error

def graph_get(path, params=None):
    if not USER_ACCESS_TOKEN or not IG_BUSINESS_ID: raise RuntimeError('Meta API não configurada')
    q = dict(params or {}); q['access_token'] = USER_ACCESS_TOKEN
    r = requests.get(f'https://graph.facebook.com/{GRAPH_VERSION}/{path.lstrip("/")}', params=q, timeout=15)
    r.raise_for_status(); return r.json()

@st.cache_data(ttl=MEDIA_CACHE_SECONDS, show_spinner=False)
def instagram_data(username, profile_url):
    try:
        s = graph_get(IG_BUSINESS_ID, {'fields':'username,followers_count,media_count'})
        m = graph_get(f'{IG_BUSINESS_ID}/media', {'fields':'caption,media_type,media_url,thumbnail_url,permalink,like_count,comments_count','limit':'12'})
        items=[]
        for i in m.get('data',[]):
            image = i.get('thumbnail_url') if i.get('media_type')=='VIDEO' else i.get('media_url')
            if not image: continue
            likes=int(i.get('like_count') or 0); comments=int(i.get('comments_count') or 0)
            items.append({'image':image,'permalink':i.get('permalink') or profile_url,'caption':(i.get('caption') or '')[:140],'likes':likes,'comments':comments,'score':likes+comments*3,'type':i.get('media_type')})
        return {
            'followers': int(s.get('followers_count', 0)),
            'media_count': int(s.get('media_count', 0)),
            'username': s.get('username', username),
            'items': items,
            'error': ''
        }
    except Exception as exc:
        return {
            'followers': MOCK_FOLLOWERS_START,
            'media_count': 0,
            'username': username,
            'items': [],
            'error': f'{type(exc).__name__}: {exc}'
        }

def read_prev():
    try: return int(STATE_FILE.read_text()) if STATE_FILE.exists() else None
    except Exception: return None

def write_prev(v):
    try: STATE_FILE.write_text(str(v))
    except Exception: pass

def campaign_html(items):
    if not items:
        return '<div class="campaign-empty">Nenhuma publicidade ativa foi carregada.</div>'

    cards = ''.join(
        f'<a class="campaign-card" href="{esc(item["link"])}" target="_blank">'
        f'<img src="{esc(item["image"])}" alt="{esc(item["title"])}">'
        f'<div class="overlay"><small>{esc(item["category"])}</small>'
        f'<b>{esc(item["title"])}</b></div></a>'
        for item in items
    )

    if len(items) == 1:
        return (
            '<div class="campaign-strip campaign-single">'
            '<div class="kicker">Destaque LIIVV</div>'
            f'<div class="single-campaign">{cards}</div></div>'
        )

    return (
        '<div class="campaign-strip"><div class="kicker">Destaques LIIVV</div>'
        f'<div class="marquee"><div class="track">{cards + cards}</div></div></div>'
    )

def post_html(i,label):
    kind='Reel' if i.get('type')=='VIDEO' else 'Post'
    return f'<a class="post" href="{esc(i["permalink"])}" target="_blank"><img src="{esc(i["image"])}"><div class="post-body"><div class="post-label">{label} · {kind}</div><div class="post-stats">♥ {i["likes"]} · 💬 {i["comments"]}</div><div class="post-caption">{esc(i["caption"] or "Conteúdo LIIVV Beauty")}</div></div></a>'

def render():
    st.set_page_config(page_title='LIIVV Beauty', page_icon='✨', layout='wide', initial_sidebar_state='collapsed')
    st_autorefresh(interval=REFRESH_SECONDS*1000, key='liivv_refresh')
    cfg, config_error = get_config(); pubs, publicity_error = get_publicities(); username=cfg['INSTAGRAM_USERNAME'].replace('@','')
    data=instagram_data(username,cfg['PROFILE_URL'])
    if data.get('error'):
        st.error('Falha na Meta API: ' + data['error'])
    if publicity_error:
        st.warning('Falha ao carregar Publicidades: ' + publicity_error)
    elif not pubs:
        st.info('A planilha foi lida, mas não há linhas ativas e válidas na aba Publicidades.')
    if config_error:
        st.caption('Configurações padrão em uso: ' + config_error)
    prev=read_prev(); delta=0 if prev is None else data['followers']-prev; write_prev(data['followers'])
    latest=data['items'][:4]; top=sorted(data['items'], key=lambda x:x['score'], reverse=True)[:4]
    latest_html=''.join(post_html(i,'Último post') for i in latest) or '<div class="empty">Conecte a Meta API para carregar os posts.</div>'
    top_html=''.join(post_html(i,'Maior interação') for i in top) or '<div class="empty">As métricas aparecerão após a conexão da Meta API.</div>'
    change=f'<div class="change">+{delta} novas seguidoras</div>' if delta>1 else ('<div class="change">+1 nova seguidora</div>' if delta==1 else '')
    toast=f'<div class="toast"><small>Nova seguidora</small><b>{esc(cfg["WELCOME_MESSAGE"])}</b><span>✨ ♡ ✨</span></div>' if delta>0 else ''
    css='''<style>@import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700;800&family=Playfair+Display:wght@500;600&display=swap');#MainMenu,footer,header{visibility:hidden}.stApp{background:#f4efeb;color:#4c3540;font-family:Montserrat}.block-container{padding:22px 30px;max-width:100%}.shell{max-width:1500px;margin:auto}.header{display:flex;gap:22px;align-items:center;margin-bottom:20px}.brand{display:flex;gap:16px;align-items:center}.logo{width:104px;height:104px;border-radius:26px;background:#fffafb;border:1px solid #dfd3d6;display:flex;align-items:center;justify-content:center;text-align:center}.logo strong{font:600 28px 'Playfair Display';letter-spacing:4px}.logo span{display:block;font-size:9px;letter-spacing:5px;margin-top:8px;color:#a27484}.title{font:600 42px 'Playfair Display'}.subtitle{color:#8b727b}.campaign-strip{height:110px;flex:1;min-width:520px;background:#fffafb;border:1px solid #dfd3d6;border-radius:26px;padding:10px 16px;overflow:hidden}.kicker{font-size:11px;letter-spacing:2px;text-transform:uppercase;color:#a27484;font-weight:800}.marquee{overflow:hidden;margin-top:8px}.track{display:flex;gap:12px;width:max-content;animation:scroll 32s linear infinite}.campaign-card{width:245px;height:70px;position:relative;overflow:hidden;border-radius:17px;flex-shrink:0}.campaign-single{display:grid;grid-template-rows:20px 1fr}.single-campaign{height:74px;margin-top:4px}.single-campaign .campaign-card{width:100%;height:74px}.single-campaign .campaign-card img{object-fit:cover}.campaign-card img{width:100%;height:100%;object-fit:cover}.overlay{position:absolute;inset:0;padding:10px;display:flex;flex-direction:column;justify-content:flex-end;color:#fff;background:linear-gradient(0deg,rgba(40,25,32,.85),transparent)}.overlay small{font-size:8px;letter-spacing:1px;text-transform:uppercase}.overlay b{font-size:11px}.campaign-empty{flex:1;padding:24px;background:#fffafb;border:1px dashed #cdbcc2;border-radius:24px}@keyframes scroll{to{transform:translateX(-50%)}}.grid{display:grid;grid-template-columns:390px 1fr;gap:20px}.card{background:#fffafb;border:1px solid #dfd3d6;border-radius:26px;padding:23px;box-shadow:0 12px 34px rgba(83,54,64,.08)}.counter-label{font-size:12px;letter-spacing:2px;text-transform:uppercase;color:#a27484;font-weight:800}.counter{font:600 76px 'Playfair Display';margin:14px 0 8px}.handle{color:#8b727b;font-weight:600}.change{display:inline-block;margin-top:9px;padding:8px 12px;border-radius:999px;background:#ead8df;color:#76495b;font-size:12px;font-weight:800}.main-qr{margin-top:20px;padding:17px;background:#fff;border:1px solid #eadfe2;border-radius:22px;text-align:center}.main-qr h2{font:600 25px 'Playfair Display'}.main-qr img{width:230px}.main-qr p{font-size:12px;color:#8b727b}.metric{margin-top:16px;background:#f4efeb;border:1px solid #eadfe2;border-radius:16px;padding:13px}.metric b{font-size:21px}.metric span{display:block;font-size:10px;text-transform:uppercase;color:#8b727b}.qrs{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:16px}.qr{background:#fff;border:1px solid #eadfe2;border-radius:15px;padding:9px;text-align:center}.qr img{width:78px}.qr span{display:block;font-size:9px;color:#8b727b;font-weight:700}.foot{margin-top:16px;text-align:center;font-size:11px;color:#9b858d}.stack{display:flex;flex-direction:column;gap:18px}.section-title{font:600 23px 'Playfair Display';margin-bottom:13px}.posts{display:grid;grid-template-columns:repeat(4,1fr);gap:13px}.post{background:#fff;border:1px solid #eadfe2;border-radius:18px;overflow:hidden;text-decoration:none;color:#4c3540;min-height:300px}.post img{width:100%;height:165px;object-fit:cover}.post-body{padding:11px}.post-label{font-size:9px;color:#a27484;font-weight:800;text-transform:uppercase}.post-stats{font-size:12px;color:#8b727b;font-weight:700;margin-top:6px}.post-caption{font-size:11px;color:#8b727b;line-height:1.4;margin-top:7px}.empty{padding:22px;border:1px dashed #cdbcc2;border-radius:16px;color:#8b727b}.toast{position:fixed;top:30px;left:50%;transform:translateX(-50%);z-index:999999;background:#fffafb;border:1px solid #d7c6cc;border-radius:25px;padding:19px 25px;text-align:center;box-shadow:0 22px 60px rgba(83,54,64,.22)}.toast small,.toast b,.toast span{display:block}.toast small{letter-spacing:2px;text-transform:uppercase;color:#a27484}.toast b{font:600 27px 'Playfair Display';margin:8px}.toast span{color:#a27484}@media(max-width:1100px){.header{flex-wrap:wrap}.campaign-strip{flex-basis:100%;min-width:0}.grid{grid-template-columns:1fr}.posts{grid-template-columns:repeat(2,1fr)}}
html, body, [data-testid="stAppViewContainer"], .stApp {
    width: 100vw !important;
    min-height: 100vh !important;
    min-min-height: 100vh !important;
    overflow-x: hidden !important;
}

[data-testid="stAppViewContainer"] > .main {
    min-height: 100vh !important;
    overflow-x: hidden !important;
}

.block-container {
    width: 100vw !important;
    min-height: 100vh !important;
    max-width: none !important;
    padding: 10px 14px 10px 14px !important;
    overflow-x: hidden !important;
}

.shell {
    width: 100%;
    min-height: calc(100vh - 20px);
    max-width: none !important;
    display: grid;
    grid-template-rows: 68px auto;
    gap: 10px;
    overflow: hidden;
}

.header {
    margin: 0 !important;
    min-height: 68px;
    height: 68px;
    gap: 12px !important;
}

.brand {
    min-width: 250px;
    gap: 10px !important;
}

.logo {
    width: 60px !important;
    height: 60px !important;
    border-radius: 20px !important;
}

.logo strong {
    font-size: 15px !important;
}

.logo span {
    font-size: 6px !important;
    letter-spacing: 4px !important;
    margin-top: 6px !important;
}

.title {
    font-size: 28px !important;
    line-height: 1 !important;
}

.subtitle {
    font-size: 11px !important;
    margin-top: 4px !important;
}

.campaign-strip {
    height: 68px !important;
    min-width: 0 !important;
    padding: 6px 8px !important;
    border-radius: 20px !important;
}

.kicker {
    font-size: 10px !important;
}

.marquee {
    margin-top: 4px !important;
}

.campaign-card,
.single-campaign .campaign-card {
    height: 44px !important;
}

.campaign-card {
    width: 165px !important;
    border-radius: 13px !important;
}

.single-campaign {
    height: 46px !important;
}

.grid {
    height: 100%;
    min-height: 0;
    display: grid !important;
    grid-template-columns: 250px 1fr !important;
    gap: 10px !important;
    overflow: hidden;
}

.grid > .card,
.stack {
    min-height: 0;
}

.card {
    padding: 11px !important;
    border-radius: 20px !important;
}

.counter-label {
    font-size: 10px !important;
    letter-spacing: 1.5px !important;
}

.counter {
    font-size: 48px !important;
    margin: 8px 0 4px !important;
}

.handle {
    font-size: 11px !important;
}

.change {
    margin-top: 5px !important;
    padding: 5px 9px !important;
    font-size: 10px !important;
}

.main-qr {
    margin-top: 10px !important;
    padding: 10px !important;
    border-radius: 16px !important;
}

.main-qr h2 {
    font-size: 15px !important;
    margin: 0 0 6px 0 !important;
    line-height: 1.1 !important;
}

.main-qr img {
    width: 132px !important;
}

.main-qr p {
    font-size: 10px !important;
    line-height: 1.25 !important;
    margin: 5px 0 0 0 !important;
}

.metric {
    margin-top: 8px !important;
    padding: 8px !important;
    border-radius: 12px !important;
}

.metric b {
    font-size: 15px !important;
}

.metric span {
    font-size: 10px !important;
}

.qrs {
    margin-top: 8px !important;
    gap: 6px !important;
}

.qr {
    padding: 5px !important;
    border-radius: 11px !important;
}

.qr img {
    width: 42px !important;
}

.qr span {
    font-size: 6px !important;
}

.foot {
    margin-top: 7px !important;
    font-size: 10px !important;
    line-height: 1.2 !important;
}

.stack {
    height: 100%;
    display: grid !important;
    grid-template-rows: 1fr 1fr;
    gap: 10px !important;
    overflow: hidden;
}

.stack > .card {
    min-height: 0;
    overflow: hidden;
}

.section-title {
    font-size: 19px !important;
    margin-bottom: 8px !important;
    line-height: 1.05 !important;
}

.posts {
    height: calc(100% - 28px);
    grid-template-columns: repeat(4, minmax(0, 1fr)) !important;
    gap: 8px !important;
    overflow: hidden;
}

.post {
    min-height: 0 !important;
    height: 100%;
    border-radius: 13px !important;
    overflow: hidden;
}

.post img {
    height: 78px !important;
}

.post-body {
    padding: 8px !important;
}

.post-label {
    font-size: 6px !important;
}

.post-stats {
    font-size: 10px !important;
    margin-top: 3px !important;
}

.post-caption {
    font-size: 10px !important;
    line-height: 1.2 !important;
    margin-top: 4px !important;
    display: -webkit-box;
    -webkit-line-clamp: 5;
    -webkit-box-orient: vertical;
    overflow: hidden;
}

.empty {
    padding: 12px !important;
    font-size: 10px !important;
}

.toast {
    top: 12px !important;
    padding: 10px 16px !important;
    border-radius: 18px !important;
}

.toast b {
    font-size: 16px !important;
}

/* Tablet 10" landscape: 1024x600, 1280x800, 1366x768 */
@media (max-width: 1100px) {
    .header {
        flex-wrap: nowrap !important;
    }

    .campaign-strip {
        flex-basis: auto !important;
    }

    .grid {
        grid-template-columns: 245px 1fr !important;
    }

    .posts {
        grid-template-columns: repeat(4, minmax(0, 1fr)) !important;
    }
}

/* Ajuste para tablets 10" em modo retrato */
@media (orientation: portrait) {
    .shell {
        grid-template-rows: 76px 1fr;
    }

    .header {
        height: 76px;
        min-height: 76px;
    }

    .brand {
        min-width: 240px;
    }

    .title {
        font-size: 28px !important;
    }

    .grid {
        grid-template-columns: 250px 1fr !important;
    }

    .posts {
        grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
    }

    .post img {
        height: 92px !important;
    }

    .post-caption {
        -webkit-line-clamp: 3;
    }

    .campaign-card {
        width: 170px !important;
    }
}


html, body, [data-testid="stAppViewContainer"], .stApp {
    overflow-y: auto !important;
}

[data-testid="stAppViewContainer"] > .main {
    overflow-y: auto !important;
}

.block-container {
    overflow: visible !important;
    padding-bottom: 24px !important;
}

.shell {
    overflow: visible !important;
}

.grid,
.stack,
.stack > .card {
    overflow: visible !important;
}

.grid {
    align-items: start !important;
}

.stack {
    height: auto !important;
    grid-template-rows: auto auto !important;
}

.stack > .card {
    height: auto !important;
}

.posts {
    height: auto !important;
    min-height: 0 !important;
}

.post {
    height: auto !important;
    min-height: 190px !important;
}

.post img {
    height: 78px !important;
}

.post-label {
    font-size: 9px !important;
}

.post-stats {
    font-size: 10px !important;
}

.post-caption {
    font-size: 10px !important;
    line-height: 1.28 !important;
}

.main-qr {
    overflow: visible !important;
}

.main-qr img {
    width: 132px !important;
    height: auto !important;
    max-width: 100% !important;
    display: block !important;
    margin: 0 auto !important;
}

/* Garante que o QR principal apareça inteiro na primeira dobra */
.grid > .card:first-child {
    min-height: 0 !important;
}

@media (max-width: 1100px) {
    .posts {
        grid-template-columns: repeat(4, minmax(0, 1fr)) !important;
    }
}

@media (max-width: 900px) {
    .posts {
        grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
    }
}

</style>'''
    page=f'''<div class="shell">{toast}<div class="header"><div class="brand"><div class="logo"><div><strong>LIIVV</strong><span>BEAUTY</span></div></div><div><div class="title">{esc(cfg['BRAND_NAME'])}</div><div class="subtitle">@{esc(data['username'])}</div></div></div>{campaign_html(pubs)}</div><div class="grid"><div class="card"><div class="counter-label">Seguidores no Instagram</div><div class="counter">{data['followers']:,}</div><div class="handle">@{esc(data['username'])}</div>{change}<div class="main-qr"><h2>{esc(cfg['CTA_TITLE'])}</h2><img src="{qr_data_uri(cfg['PROFILE_URL'])}"><p>{esc(cfg['CTA_SUBTITLE'])}</p></div><div class="metric"><b>{data['media_count']}</b><span>publicações</span></div><div class="qrs"><div class="qr"><img src="{qr_data_uri(cfg['BOOKING_URL'])}"><span>Agendamento</span></div><div class="qr"><img src="{qr_data_uri(cfg['SECONDARY_URL'])}"><span>Conheça a LIIVV</span></div></div><div class="foot">{esc(cfg['FOOTER_TEXT'])}</div></div><div class="stack"><div class="card"><div class="section-title">Últimos conteúdos</div><div class="posts">{latest_html}</div></div><div class="card"><div class="section-title">Conteúdos com maior interação</div><div class="posts">{top_html}</div></div></div></div></div>'''
    st.markdown(css+page, unsafe_allow_html=True)

if __name__ == '__main__': render()
