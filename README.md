# embodied-ai

Claude API ベースのモバイル対応 PWA チャットボット。  
スマホ/PC カメラ画像を会話に添付して `see` 体験ができ、ElevenLabs 音声再生にも対応します。

## 特徴

- リッチな PWA UI（モバイル/デスクトップ両対応）
- ブラウザカメラ画像を Claude に送信して画像理解チャット
- Claude モデル一覧を API から取得し、UI で選択可能（Claude 系のみ許可）
- ElevenLabs TTS で返信を音声化（任意）
- MCP は任意（`mcpServers` が空でも利用可能）
- Lambda + DynamoDB で会話セッションを永続化（任意）

## ローカル起動

```bash
uv sync
cp .env.example .env
cp config.example.json config.json
cp desires.example.json desires.json
cp memories.example.json memories.json
cp self.example.json self.json
# 必要なら CLAUDE.md を編集（人格・口調・行動方針）
uv run embodied-ai --web --host 0.0.0.0 --port 8000
```

- PC: `http://localhost:8000`
- スマホ: `http://<PCのLAN IP>:8000`

注意: 実機カメラ利用は通常 HTTPS が必要です（Lambda Function URL は HTTPS）。

## API

- `GET /api/health`
- `GET /api/models` (Anthropic から Claude モデル一覧取得)
- `POST /api/chat`
  - `message`, `image_base64`, `speak`, `voice_id`, `model`
- `POST /api/autonomous/tick`
  - `speak`, `voice_id`, `model`, `force`
- `GET /api/autonomous/events`
  - `after_id`
- `POST /api/speak`

## AWS Lambda デプロイ

### 1. 設定ファイル作成

```bash
cp deploy/lambda-config.example.json deploy/lambda-config.json
cp config.lambda.example.json config.lambda.json
```

`deploy/lambda-config.json` を編集:
- `role_arn`
- `function_name`
- `environment.ANTHROPIC_API_KEY`
- `environment.ELEVENLABS_API_KEY` (任意)
- `environment.ELEVENLABS_VOICE_ID` (任意)
- `environment.EMBODIED_AI_SESSION_TABLE` (会話永続化する場合)

`config.lambda.json` の `claude.system_prompt_file` は `CLAUDE.md` を指定し、
`CLAUDE.md` はデプロイスクリプトが同梱します（Lambda上では `/var/task/CLAUDE.md` として解決）。

会話履歴を Lambda インスタンスをまたいで維持する場合は、`web.conversation_store.backend` を `dynamodb` にして、
`EMBODIED_AI_SESSION_TABLE` を設定してください。`scripts/deploy_lambda.sh` はテーブル未作成時に自動作成し、
TTL (`expires_at`) を有効化します。

### 2. デプロイ実行

```bash
./scripts/deploy_lambda.sh deploy/lambda-config.json config.lambda.json
```

完了後に Function URL（HTTPS）が表示されます。  
PWA フロントも同一 Lambda から配信されます。

## 構成

```text
src/embodied_ai/
├── web_app.py              # FastAPI API + PWA配信
├── lambda_handler.py       # Mangum Lambda entrypoint
├── tts.py                  # ElevenLabs連携
├── claude_client.py        # Claude API + モデル一覧
├── bot.py                  # 会話ロジック
└── web/
    ├── index.html
    ├── manifest.webmanifest
    ├── sw.js
    └── assets/
        ├── app.js
        ├── styles.css
        ├── icon-192.svg
        └── icon-512.svg
```
