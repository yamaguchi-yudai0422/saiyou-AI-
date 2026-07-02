# Noesia Persona API リファレンス

Noesia で育てた人格を、あなたのアプリに組み込むための API です。  
APIキーを1本差し込むだけで、そのスロットの人格がテキスト・画像に対して返答します。

---

## クイックスタート

### 1. APIキーを発行する

Noesia アプリにログイン → **設定 → API設定** → スロットを選んで「発行する」  
**キーは発行時の1回のみ表示されます。必ず保存してください。**

発行されるキーは `sk_...` 形式です。オリジン制限はなく、どのアプリ・どのドメインからでも使えます。  
**キーは秘密にしてください。公開リポジトリにコミットしないでください。**

### 2. 最初のリクエスト

```python
import requests

response = requests.post(
    "https://noesia.onrender.com/v1/chat",
    headers={"X-Api-Key": "sk_your_key_here"},
    json={"message": "こんにちは", "lang": "ja"},
)
print(response.json()["reply"])
```

---

## 基本情報

| 項目 | 内容 |
|------|------|
| ベース URL | `https://noesia.onrender.com` |
| 認証方式 | リクエストヘッダー `X-Api-Key` |
| レスポンス形式 | `application/json` |
| 文字コード | UTF-8 |

---

## 認証

すべてのエンドポイントで `X-Api-Key` ヘッダーが必須です。

```
X-Api-Key: sk_your_key_here
```

APIキーはスロット（人格）に1対1で紐づいています。  
スロットが違えば人格も違うため、複数の人格を使い分けたい場合はスロットごとにキーを発行してください。

---

## エンドポイント

### POST /v1/chat

人格としてテキストメッセージに返答します。

#### リクエスト

```http
POST https://noesia.onrender.com/v1/chat
X-Api-Key: sk_your_key_here
Content-Type: application/json
```

```json
{
  "message": "最近どう？",
  "context": [
    { "role": "user",      "content": "はじめまして" },
    { "role": "assistant", "content": "こちらこそ、よろしく。" }
  ],
  "lang": "ja",
  "max_tokens": 600
}
```

| フィールド | 型 | 必須 | デフォルト | 説明 |
|-----------|-----|------|-----------|------|
| `message` | string | ✅ | — | ユーザーの発言 |
| `context` | array | — | `[]` | 直前の会話履歴（セッション管理は呼び出し側が行う） |
| `context[].role` | string | — | — | `"user"` または `"assistant"` |
| `context[].content` | string | — | — | 発言内容 |
| `lang` | string | — | `"ja"` | `"ja"` または `"en"` |
| `max_tokens` | integer | — | `600` | 最大出力トークン数 |

#### レスポンス

```json
{
  "reply": "うん、まあぼちぼちかな。最近は考えることが多くて。",
  "tokens_used": 312,
  "request_id": "req_a1b2c3d4e5f6"
}
```

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `reply` | string | 人格の返答テキスト |
| `tokens_used` | integer | この呼び出しで消費したトークン数 |
| `request_id` | string | リクエストID（エラー調査時に使用） |

---

### POST /v1/analyze-image

画像を人格の視点で読み取り、コメントを返します。

#### リクエスト

```http
POST https://noesia.onrender.com/v1/analyze-image
X-Api-Key: sk_your_key_here
Content-Type: application/json
```

```json
{
  "image_url": "https://example.com/photo.jpg",
  "prompt": "この画像を見てどう思う？",
  "lang": "ja",
  "max_tokens": 800
}
```

| フィールド | 型 | 必須 | デフォルト | 説明 |
|-----------|-----|------|-----------|------|
| `image_data` | string | ※1 | — | Base64 エンコードした画像データ |
| `media_type` | string | — | `"image/jpeg"` | `image/jpeg` / `image/png` / `image/gif` / `image/webp` |
| `image_url` | string | ※1 | — | 画像の URL（`image_data` の代わりに使用可） |
| `prompt` | string | — | 自動 | 画像への質問・指示（省略時は「あなたらしく説明して」） |
| `lang` | string | — | `"ja"` | `"ja"` または `"en"` |
| `max_tokens` | integer | — | `800` | 最大出力トークン数 |

※1 `image_data` または `image_url` のどちらか一方が必須です。

#### レスポンス

```json
{
  "result": "穏やかな夕暮れだね。光の角度が独特で、時間が止まったみたいな感じがする。",
  "tokens_used": 847,
  "request_id": "req_x9y8z7w6v5u4"
}
```

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `result` | string | 人格による画像へのコメント |
| `tokens_used` | integer | この呼び出しで消費したトークン数 |
| `request_id` | string | リクエストID |

---

## エラーレスポンス

すべてのエラーは以下の統一形式で返されます。

```json
{
  "error": {
    "code": "invalid_api_key",
    "message": "Invalid or revoked API key.",
    "request_id": "req_a1b2c3d4e5f6"
  }
}
```

| ステータス | `code` | 説明 |
|-----------|--------|------|
| `400` | `invalid_request` | リクエストパラメータが不正（`image_data` と `image_url` が両方未指定など） |
| `401` | `invalid_api_key` | APIキーが無効または失効している |
| `402` | `insufficient_balance` | APIトークン残高が不足している |
| `404` | `persona_not_found` | スロットに人格データが存在しない |
| `429` | `rate_limit_exceeded` | リクエスト数が制限を超えた。3〜5秒待ってリトライしてください |
| `500` | `internal_error` | サーバー側のエラー |

エラー調査が必要な場合は `request_id` をお知らせください。

---

## 人格再現を維持するためのプロンプト設計

Noesia Persona API は、`message` に渡された内容を **その人格の視点で返答** します。  
ただし、`message` の書き方によっては人格が薄まり、汎用的なAI回答になることがあります。

### なぜ薄まるのか

APIの内部では人格を最優先にする制約がシステムプロンプトに組み込まれています。  
しかし `message` に「分析して」「整理して」「正確に答えて」のような**タスク指示**が含まれると、  
モデルが分析者・助言者モードに引き寄せられ、人格が後退することがあります。

### メッセージの書き方

**推奨（人格が出やすい）**

```
"最近どう？"
"この写真見てどう思う？"
"今日疲れたんだけど"
"これ面白いと思う？"
```

**避けるべき（人格が薄まりやすい）**

```
"この内容を分析してください"       ← 分析者モードになる
"わかりやすく整理して答えてください" ← 丁寧なAI回答になる
"適切なアドバイスをお願いします"    ← 助言者モードになる
"正確に、詳しく説明してください"    ← 客観的説明モードになる
```

### アプリ側での工夫

ユーザーの入力をそのまま `message` に渡すのが最も人格が出ます。  
アプリ側でプレフィックスを付けて送る場合は、タスク指示にならないよう注意してください。

```python
# 良い例：ユーザーの言葉をそのまま渡す
message = user_input  # "今日こんなことあってさ"

# 避ける例：アプリ側で指示を付け加える
message = f"以下を分析してコメントしてください：{user_input}"  # 人格が薄まる
```

### 画像コメント（/v1/analyze-image）の場合

`prompt` フィールドを省略すると、人格が自然な形でコメントします。  
`prompt` に「詳しく説明して」「プロの視点で分析して」のような指示を入れると人格が後退します。

```python
# 推奨：promptを省略、または自然な一言
{"image_url": "...", "lang": "ja"}
{"image_url": "...", "prompt": "どう思う？", "lang": "ja"}

# 避ける
{"image_url": "...", "prompt": "この画像を詳細に分析してください", "lang": "ja"}
```

---

## コードサンプル

### Python — テキストチャット（最小構成）

```python
import requests

API_KEY = "sk_your_key_here"

response = requests.post(
    "https://noesia.onrender.com/v1/chat",
    headers={"X-Api-Key": API_KEY},
    json={"message": "今日の気分は？", "lang": "ja"},
)
print(response.json()["reply"])
```

### Python — 会話を続ける（セッション管理）

API はステートレスです。複数ターンの会話を行う場合は、呼び出し側で会話履歴を保持し `context` に含めて送ってください。

```python
import requests

API_KEY = "sk_your_key_here"
history = []

def chat(message: str) -> str:
    response = requests.post(
        "https://noesia.onrender.com/v1/chat",
        headers={"X-Api-Key": API_KEY},
        json={"message": message, "context": history, "lang": "ja"},
    )
    data = response.json()
    reply = data["reply"]
    history.append({"role": "user",      "content": message})
    history.append({"role": "assistant", "content": reply})
    return reply

print(chat("はじめまして"))
print(chat("好きな食べ物は？"))
```

### Python — 画像を解析する

```python
import base64, requests

API_KEY = "sk_your_key_here"

# ローカルファイル
with open("photo.jpg", "rb") as f:
    image_b64 = base64.b64encode(f.read()).decode()

response = requests.post(
    "https://noesia.onrender.com/v1/analyze-image",
    headers={"X-Api-Key": API_KEY},
    json={
        "image_data": image_b64,
        "media_type": "image/jpeg",
        "prompt": "この画像を見てどう思う？",
        "lang": "ja",
    },
)
print(response.json()["result"])

# URL を直接渡す場合
response = requests.post(
    "https://noesia.onrender.com/v1/analyze-image",
    headers={"X-Api-Key": API_KEY},
    json={"image_url": "https://example.com/photo.jpg", "lang": "ja"},
)
print(response.json()["result"])
```

### Node.js / TypeScript

```typescript
const API_KEY = "sk_your_key_here";

async function chat(message: string, context: {role: string, content: string}[] = []) {
  const res = await fetch("https://noesia.onrender.com/v1/chat", {
    method: "POST",
    headers: {
      "X-Api-Key": API_KEY,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ message, context, lang: "ja" }),
  });

  if (!res.ok) {
    const err = await res.json();
    throw new Error(`${err.error.code}: ${err.error.message}`);
  }

  return res.json(); // { reply, tokens_used, request_id }
}

// 使用例
const { reply } = await chat("こんにちは");
console.log(reply);
```

### React / ブラウザアプリ

`sk_` キーはブラウザからも直接使えます。オリジン制限はありません。

```tsx
const API_KEY = "sk_your_key_here";

async function sendMessage(message: string) {
  const res = await fetch("https://noesia.onrender.com/v1/chat", {
    method: "POST",
    headers: {
      "X-Api-Key": API_KEY,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ message, lang: "ja" }),
  });
  const data = await res.json();
  return data.reply;
}
```

> **注意**: フロントエンドコードに含めるとキーが公開されます。GitHubなどに上げる場合は環境変数（`.env`）に入れてください。

---

## トークン消費について

API 呼び出しごとにトークンが消費されます。

| エンドポイント | 消費トークン |
|---------------|------------|
| `/v1/chat` | 入力トークン + 出力トークン |
| `/v1/analyze-image` | 画像（約 1,000 token 相当）+ テキスト入出力 |

残高は Noesia アプリの **設定 → API設定** で確認・チャージできます。  
残高が 0 になると `402 insufficient_balance` が返ります。自動チャージを設定している場合は自動補充されます。

---

## よくある質問

**Q. 1つのアプリで複数の人格を切り替えられますか？**  
A. 可能です。スロットごとにAPIキーを発行し、用途に応じて使い分けてください。

**Q. 会話の記憶はAPIが持ちますか？**  
A. いいえ。APIはステートレスです。会話履歴は `context` フィールドで毎回送信してください。  
`context` に含める履歴は **直近10〜20ターン程度を目安**にしてください。長くなりすぎるとトークン消費が増えます。

**Q. レートリミットはありますか？**  
A. あります。`429 rate_limit_exceeded` が返った場合は **3〜5秒待ってからリトライ**してください。連続リトライはさらに制限される場合があります。

**Q. 返答が普通のAIっぽくなってしまいます。**  
A. `message` に「分析して」「整理して」「詳しく説明して」のようなタスク指示が含まれていないか確認してください。  
これらの指示はモデルを分析者・助言者モードに引き寄せ、人格を後退させます。  
ユーザーの言葉をそのまま渡す形が最も人格が出ます。詳しくは「人格再現を維持するためのプロンプト設計」を参照してください。

**Q. エラーが解決しない場合は？**  
A. レスポンスの `request_id` を添えてサポートにご連絡ください。
