# 3h-transcript-bot

Slack App per la pre-approvazione dei meeting transcript di Fireflies.

## Architettura

```
Fireflies meeting completed
        ↓
Zap 1 (Zapier) — posta in #rq-approve-transcripts
        ↓
RQ mette ✅
        ↓
Slack Events API → questa app (Railway) → POST in #transcripts-fetch-bot con PDF
```

Sostituisce il Zap 2 precedente. Il vantaggio: il bot è autenticato come Slack App
workspace-wide, non dipende da un utente Slack autenticato in Zapier. Matteo può
essere fuori da `#rq-approve-transcripts` senza rompere il flow.

## Setup

### 1. Slack App configuration

Su https://api.slack.com/apps:

- **OAuth & Permissions → Bot Token Scopes:**
  - `chat:write` — postare messaggi
  - `chat:write.customize` — postare con username custom
  - `channels:history`, `groups:history` — leggere messaggi nei canali
  - `reactions:read` — ricevere eventi reazione
  - `files:write` — upload PDF transcript

- **Event Subscriptions:**
  - Enable Events: ON
  - Request URL: `https://<railway-domain>/slack/events`
  - Subscribe to Bot Events: `reaction_added`

- **Install to Workspace** → copia il Bot User OAuth Token (`xoxb-...`)

- **Basic Information → App-Level Tokens:** non serve per questo flow

- **Signing Secret:** in Basic Information → App Credentials

### 2. Invita il bot nei canali

In Slack:
```
/invite @Transcript Bot
```
nei canali:
- `#rq-approve-transcripts` (deve leggere messaggi e reazioni)
- `#transcripts-fetch-bot` (deve postare e uploadare file)

### 3. Railway deploy

1. Crea nuovo project Railway → "Deploy from GitHub"
2. Seleziona repo `matteoscollo/3h-transcript-bot`
3. Setta le environment variables (vedi `.env.example`):
   - `SLACK_BOT_TOKEN`
   - `SLACK_SIGNING_SECRET`
   - `APPROVE_CHANNEL_ID`
   - `OUTPUT_CHANNEL_ID`
   - `APPROVER_USER_ID`
4. Deploy automatico al primo push
5. Genera un dominio pubblico (Settings → Networking → Generate Domain)
6. Aggiorna l'URL nelle Event Subscriptions Slack

### 4. URL verification

Quando salvi la Request URL in Slack Events Subscriptions, Slack invia un
challenge POST. L'app risponde automaticamente con il challenge — dovresti
vedere "Verified" verde in Slack.

## Variabili ambiente

| Var | Esempio | Descrizione |
|---|---|---|
| `SLACK_BOT_TOKEN` | `xoxb-...` | Bot User OAuth Token |
| `SLACK_SIGNING_SECRET` | `abc...` | Per verifica firma payload (opzionale per ora) |
| `APPROVE_CHANNEL_ID` | `C0BB1PGHT8C` | Canale dove RQ approva |
| `OUTPUT_CHANNEL_ID` | `C0B9GA5V8LU` | Canale di output (`#transcripts-fetch-bot`) |
| `APPROVER_USER_ID` | `U08NJV6H37B` | Solo questo user può approvare |
| `APPROVE_REACTION` | `white_check_mark` | Emoji di approvazione |
| `BOT_USERNAME` | `Transcript Bot` | Nome mostrato nel post |
| `LOG_LEVEL` | `INFO` | Log verbosity |

## Test locale

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edita .env con i valori reali
export $(cat .env | xargs)
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Espone su `http://localhost:8000`. Per test con Slack Events serve un tunnel
(`ngrok http 8000`) e usare l'URL `https://xxx.ngrok.io/slack/events` come
Request URL in Slack.

## Migrazione produzione

Quando passi da test a produzione:
1. Cambia `APPROVE_CHANNEL_ID` da `C0BB1PGHT8C` → `C0BA8LPF197` (canale reale)
2. Cambia `APPROVER_USER_ID` da `U08NJV6H37B` (Matteo) → `U0907GVMY04` (RQ)
3. Invita il bot nel canale reale `#rq-approve-transcripts`
4. Riavvia il deploy Railway

## Roadmap

- **F2:** un secondo handler (`message` event in `#transcripts-fetch-bot`) che
  estrae il cliente dal contenuto del transcript e salva il PDF in SharePoint
  nella cartella del cliente corrispondente. Richiede Microsoft Graph API auth.
