# claude-mcp-bot

Claude API + MCPクライアントを組み合わせた、欲求・記憶・自己認識を持つ自律AIアシスタント。

## 概要

「高野ゆき」という名前の関西弁の陽気な女の子として振る舞うAIボット。MCPサーバー経由でセンサー（カメラ、温度計など）にアクセスでき、自分の意思で行動する自律モードを搭載。

### 主な機能

- **欲求システム** - 視覚欲求、情報欲求、つながり欲求など12種類の欲求を持ち、満足度に応じて自発的に行動
- **記憶システム** - 会話から重要な記憶を抽出し、長期記憶として保存
- **自己認識** - アイデンティティ、価値観、自己一貫性ルールを持つ
- **MCPツール統合** - 複数のMCPサーバーからツールを取得し活用

## インストール

```bash
# リポジトリをクローン
git clone https://github.com/your-username/claude-mcp-bot.git
cd claude-mcp-bot

# 依存関係をインストール（uvを使用）
uv sync

# 環境変数を設定
cp .env.example .env
# .envファイルにANTHROPIC_API_KEYを設定
```

## 設定

### config.json

```json
{
  "mcpServers": {
    "usb-webcam": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/usb-webcam-mcp", "usb-webcam-mcp"]
    }
  },
  "claude": {
    "model": "claude-sonnet-4-20250514",
    "system_prompt": "あなたの名前は「高野ゆき」..."
  },
  "bot": {
    "autonomous_interval": 30,
    "memory_path": "memories.json",
    "desire_path": "desires.json",
    "self_path": "self.json"
  }
}
```

### MCPサーバー

対応しているMCPサーバー:
- `usb-webcam-mcp` - USBカメラからの画像取得（視覚）
- `system-temperature-mcp` - システム温度取得（体温感覚）
- `web-browse-mcp` - Web検索・閲覧（情報収集）

## 使い方

### 対話モード

```bash
uv run claude-mcp-bot
```

### 自律モード

```bash
uv run claude-mcp-bot --autonomous
# または
uv run claude-mcp-bot -a
```

自律モードでは、欲求に基づいて定期的に自発的な行動を行う。

### オプション

- `-a, --autonomous` - 自律モードを有効化
- `-c, --config` - 設定ファイルのパスを指定

## アーキテクチャ

```
src/claude_mcp_bot/
├── main.py          # エントリーポイント
├── bot.py           # ボットのメインロジック
├── claude_client.py # Claude APIクライアント
├── mcp_client.py    # MCPクライアント
├── memory.py        # 記憶システム
├── desire.py        # 欲求システム
└── self.py          # 自己認識システム
```

### 欲求システム

12種類の欲求を持ち、各欲求には:
- `satisfaction` - 現在の満足度（0.0〜1.0）
- `base_importance` - 基本重要度
- `decay_rate` - 満足度の減衰率
- `tools` - 欲求を満たすために使うツール

時間とともに満足度が減衰し、最も優先度の高い欲求が選ばれて自発的に行動する。

### 記憶システム

- **短期記憶** - 直近の会話履歴
- **長期記憶** - 重要な記憶を抽出・保存
- 記憶には `type`（episode/semantic/emotion）、`importance`、`keywords` がある

### 自己認識システム

- **アイデンティティ** - 名前、性格、関係性
- **自己概念** - 価値観、強み、成長領域
- **自己一貫性** - 関西弁を使う、陽気で前向き、一人称は「ウチ」

## データファイル

- `memories.json` - 長期記憶の保存先
- `desires.json` - 欲求状態の保存先
- `self.json` - 自己認識の保存先

## 依存関係

- Python 3.12+
- `mcp>=1.0.0` - Model Context Protocol
- `anthropic>=0.30.0` - Claude API
- `python-dotenv>=1.0.0` - 環境変数管理

## ライセンス

MIT
