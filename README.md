# LIIVV Instagram Counter

Tela publicitária Streamlit da LIIVV Beauty, conectada à planilha:

https://docs.google.com/spreadsheets/d/1M98FNCa83Y5V9grSlWazoY_xthp_cs32s5xuheBNM0o/edit

## Estrutura da planilha

- `Publicidades`: imagem, ativação, ordem, link e categoria das campanhas.
- `Configuracoes`: textos, Instagram, QR Codes e rodapé.

## Publicação

1. Crie um repositório na organização `liivvbeauty`.
2. Envie todos os arquivos deste pacote.
3. No Streamlit Community Cloud, selecione `app.py`.
4. Cadastre os Secrets usando `.streamlit/secrets.toml.example`.

## Integrações

- Para seguidores e posts reais: configure `USER_ACCESS_TOKEN` e `IG_BUSINESS_ID`.
- Para planilha privada: configure a conta de serviço Google e compartilhe a planilha com o `client_email`.
- Sem Meta API, o app abre normalmente em modo demonstração.
