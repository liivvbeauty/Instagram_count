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
REFRESH_SECONDS = int(secret('REFRESH_SECONDS', '15'))
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

def credentials():
    if not gspread or not Credentials: return None
    try:
        if 'google_service_account' in st.secrets:
            data = dict(st.secrets['google_service_account'])
        elif 'gcp_service_account' in st.secrets:
            data = dict(st.secrets['gcp_service_account'])
        else:
            return None
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets.readonly',
            'https://www.googleapis.com/auth/drive.readonly'
        ]
        return Credentials.from_service_account_info(data, scopes=scopes)
    except Exception:
        return None

def sheet_rows(name):
    creds = credentials()
    if creds:
        return gspread.authorize(creds).open_by_key(SHEET_ID).worksheet(name).get_all_records()
    url = f'https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv&sheet={requests.utils.quote(name)}'
    r = requests.get(url, timeout=12); r.raise_for_status()
    return list(csv.DictReader(StringIO(r.text)))

@st.cache_data(ttl=60, show_spinner=False)
def get_config():
    cfg = DEFAULT_CONFIG.copy()
    try:
        for row in sheet_rows(CONFIG_SHEET):
            k = str(row.get('Chave') or '').strip(); v = str(row.get('Valor') or '').strip()
            if k and v: cfg[k] = v
    except Exception: pass
    return cfg

@st.cache_data(ttl=60, show_spinner=False)
def get_publicities():
    out = []
    try:
        for row in sheet_rows(PUBLICITY_SHEET):
            active = str(row.get('Ativo') or '0').strip().lower()
            title = str(row.get('Publicidade') or '').strip(); image = str(row.get('URL') or '').strip()
            if active not in {'1','true','sim'} or not title or not image: continue
            try: order = int(float(str(row.get('Ordem') or '999').replace(',','.')))
            except Exception: order = 999
            out.append({'title':title,'image':drive_image_url(image),'link':str(row.get('Link') or '#'),'category':str(row.get('Categoria') or 'LIIVV'),'order':order})
    except Exception: pass
    return sorted(out, key=lambda x:(x['order'],x['title']))

def graph_get(path, params=None):
    if not USER_ACCESS_TOKEN or not IG_BUSINESS_ID: raise RuntimeError('Meta API não configurada')
    q = dict(params or {}); q['access_token'] = USER_ACCESS_TOKEN
    r = requests.get(f'https://graph.facebook.com/{GRAPH_VERSION}/{path.lstrip("/")}', params=q, timeout=15)
    r.raise_for_status(); return r.json()

@st.cache_data(ttl=90, show_spinner=False)
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
    if not items: return '<div class="campaign-empty">Adicione campanhas na aba <b>Publicidades</b>.</div>'
    cards=''.join(f'<a class="campaign-card" href="{esc(i["link"])}" target="_blank"><img src="{esc(i["image"])}"><div class="overlay"><small>{esc(i["category"])}</small><b>{esc(i["title"])}</b></div></a>' for i in items)
    return f'<div class="campaign-strip"><div class="kicker">Destaques LIIVV</div><div class="marquee"><div class="track">{cards+cards}</div></div></div>'

def post_html(i,label):
    kind='Reel' if i.get('type')=='VIDEO' else 'Post'
    return f'<a class="post" href="{esc(i["permalink"])}" target="_blank"><img src="{esc(i["image"])}"><div class="post-body"><div class="post-label">{label} · {kind}</div><div class="post-stats">♥ {i["likes"]} · 💬 {i["comments"]}</div><div class="post-caption">{esc(i["caption"] or "Conteúdo LIIVV Beauty")}</div></div></a>'

def render():
    st.set_page_config(page_title='LIIVV Beauty', page_icon='✨', layout='wide', initial_sidebar_state='collapsed')
    st_autorefresh(interval=REFRESH_SECONDS*1000, key='liivv_refresh')
    cfg=get_config(); pubs=get_publicities(); username=cfg['INSTAGRAM_USERNAME'].replace('@','')
    data=instagram_data(username,cfg['PROFILE_URL'])
    if data.get('error'):
        st.error('Falha na Meta API: ' + data['error'])
    prev=read_prev(); delta=0 if prev is None else data['followers']-prev; write_prev(data['followers'])
    latest=data['items'][:4]; top=sorted(data['items'], key=lambda x:x['score'], reverse=True)[:4]
    latest_html=''.join(post_html(i,'Último post') for i in latest) or '<div class="empty">Conecte a Meta API para carregar os posts.</div>'
    top_html=''.join(post_html(i,'Maior interação') for i in top) or '<div class="empty">As métricas aparecerão após a conexão da Meta API.</div>'
    change=f'<div class="change">+{delta} novas seguidoras</div>' if delta>1 else ('<div class="change">+1 nova seguidora</div>' if delta==1 else '')
    toast=f'<div class="toast"><small>Nova seguidora</small><b>{esc(cfg["WELCOME_MESSAGE"])}</b><span>✨ ♡ ✨</span></div>' if delta>0 else ''
    css='''<style>@import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700;800&family=Playfair+Display:wght@500;600&display=swap');#MainMenu,footer,header{visibility:hidden}.stApp{background:#f4efeb;color:#4c3540;font-family:Montserrat}.block-container{padding:22px 30px;max-width:100%}.shell{max-width:1500px;margin:auto}.header{display:flex;gap:22px;align-items:center;margin-bottom:20px}.brand{display:flex;gap:16px;align-items:center}.logo{width:104px;height:104px;border-radius:26px;background:#fffafb;border:1px solid #dfd3d6;display:flex;align-items:center;justify-content:center;text-align:center}.logo strong{font:600 28px 'Playfair Display';letter-spacing:4px}.logo span{display:block;font-size:9px;letter-spacing:5px;margin-top:8px;color:#a27484}.title{font:600 42px 'Playfair Display'}.subtitle{color:#8b727b}.campaign-strip{height:110px;flex:1;min-width:520px;background:#fffafb;border:1px solid #dfd3d6;border-radius:26px;padding:10px 16px;overflow:hidden}.kicker{font-size:11px;letter-spacing:2px;text-transform:uppercase;color:#a27484;font-weight:800}.marquee{overflow:hidden;margin-top:8px}.track{display:flex;gap:12px;width:max-content;animation:scroll 32s linear infinite}.campaign-card{width:245px;height:70px;position:relative;overflow:hidden;border-radius:17px;flex-shrink:0}.campaign-card img{width:100%;height:100%;object-fit:cover}.overlay{position:absolute;inset:0;padding:10px;display:flex;flex-direction:column;justify-content:flex-end;color:#fff;background:linear-gradient(0deg,rgba(40,25,32,.85),transparent)}.overlay small{font-size:8px;letter-spacing:1px;text-transform:uppercase}.overlay b{font-size:11px}.campaign-empty{flex:1;padding:24px;background:#fffafb;border:1px dashed #cdbcc2;border-radius:24px}@keyframes scroll{to{transform:translateX(-50%)}}.grid{display:grid;grid-template-columns:390px 1fr;gap:20px}.card{background:#fffafb;border:1px solid #dfd3d6;border-radius:26px;padding:23px;box-shadow:0 12px 34px rgba(83,54,64,.08)}.counter-label{font-size:12px;letter-spacing:2px;text-transform:uppercase;color:#a27484;font-weight:800}.counter{font:600 76px 'Playfair Display';margin:14px 0 8px}.handle{color:#8b727b;font-weight:600}.change{display:inline-block;margin-top:9px;padding:8px 12px;border-radius:999px;background:#ead8df;color:#76495b;font-size:12px;font-weight:800}.main-qr{margin-top:20px;padding:17px;background:#fff;border:1px solid #eadfe2;border-radius:22px;text-align:center}.main-qr h2{font:600 25px 'Playfair Display'}.main-qr img{width:230px}.main-qr p{font-size:12px;color:#8b727b}.metric{margin-top:16px;background:#f4efeb;border:1px solid #eadfe2;border-radius:16px;padding:13px}.metric b{font-size:21px}.metric span{display:block;font-size:10px;text-transform:uppercase;color:#8b727b}.qrs{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:16px}.qr{background:#fff;border:1px solid #eadfe2;border-radius:15px;padding:9px;text-align:center}.qr img{width:78px}.qr span{display:block;font-size:9px;color:#8b727b;font-weight:700}.foot{margin-top:16px;text-align:center;font-size:11px;color:#9b858d}.stack{display:flex;flex-direction:column;gap:18px}.section-title{font:600 23px 'Playfair Display';margin-bottom:13px}.posts{display:grid;grid-template-columns:repeat(4,1fr);gap:13px}.post{background:#fff;border:1px solid #eadfe2;border-radius:18px;overflow:hidden;text-decoration:none;color:#4c3540;min-height:300px}.post img{width:100%;height:165px;object-fit:cover}.post-body{padding:11px}.post-label{font-size:9px;color:#a27484;font-weight:800;text-transform:uppercase}.post-stats{font-size:12px;color:#8b727b;font-weight:700;margin-top:6px}.post-caption{font-size:11px;color:#8b727b;line-height:1.4;margin-top:7px}.empty{padding:22px;border:1px dashed #cdbcc2;border-radius:16px;color:#8b727b}.toast{position:fixed;top:30px;left:50%;transform:translateX(-50%);z-index:999999;background:#fffafb;border:1px solid #d7c6cc;border-radius:25px;padding:19px 25px;text-align:center;box-shadow:0 22px 60px rgba(83,54,64,.22)}.toast small,.toast b,.toast span{display:block}.toast small{letter-spacing:2px;text-transform:uppercase;color:#a27484}.toast b{font:600 27px 'Playfair Display';margin:8px}.toast span{color:#a27484}@media(max-width:1100px){.header{flex-wrap:wrap}.campaign-strip{flex-basis:100%;min-width:0}.grid{grid-template-columns:1fr}.posts{grid-template-columns:repeat(2,1fr)}}</style>'''
    page=f'''<div class="shell">{toast}<div class="header"><div class="brand"><div class="logo"><div><strong>LIIVV</strong><span>BEAUTY</span></div></div><div><div class="title">{esc(cfg['BRAND_NAME'])}</div><div class="subtitle">@{esc(data['username'])}</div></div></div>{campaign_html(pubs)}</div><div class="grid"><div class="card"><div class="counter-label">Seguidores no Instagram</div><div class="counter">{data['followers']:,}</div><div class="handle">@{esc(data['username'])}</div>{change}<div class="main-qr"><h2>{esc(cfg['CTA_TITLE'])}</h2><img src="{qr_data_uri(cfg['PROFILE_URL'])}"><p>{esc(cfg['CTA_SUBTITLE'])}</p></div><div class="metric"><b>{data['media_count']}</b><span>publicações</span></div><div class="qrs"><div class="qr"><img src="{qr_data_uri(cfg['BOOKING_URL'])}"><span>Agendamento</span></div><div class="qr"><img src="{qr_data_uri(cfg['SECONDARY_URL'])}"><span>Conheça a LIIVV</span></div></div><div class="foot">{esc(cfg['FOOTER_TEXT'])}</div></div><div class="stack"><div class="card"><div class="section-title">Últimos conteúdos</div><div class="posts">{latest_html}</div></div><div class="card"><div class="section-title">Conteúdos com maior interação</div><div class="posts">{top_html}</div></div></div></div></div>'''
    st.markdown(css+page, unsafe_allow_html=True)

if __name__ == '__main__': render()
