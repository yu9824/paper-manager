# プロキシ設定ガイド

paper-managerでは、プロキシサーバーを使用してDOIからメタデータを取得できます。このガイドでは、プロキシ設定の方法について説明します。

## プロキシ設定の必要性

プロキシ設定が必要な場合：
- 企業や大学のネットワークでプロキシサーバーを使用している場合
- DOI取得時にプロキシ経由でアクセスする必要がある場合

## 設定方法

### Webアプリケーションから設定

1. アプリケーションの「設定」ページに移動
2. 「プロキシ設定」セクションで以下を入力：
   - **HTTPプロキシ**: HTTPプロキシのURL（例: `http://proxy.example.com:8080`）
   - **HTTPSプロキシ**: HTTPSプロキシのURL（空欄の場合はHTTPプロキシと同じ）
3. 「設定を保存」ボタンをクリック

### プロキシURLの形式

プロキシURLは以下の形式で指定します：

```
http://[username:password@]proxy.example.com:port
```

例：
- `http://proxy.example.com:8080`
- `http://user:pass@proxy.example.com:8080`

### 環境変数から設定

環境変数を使用してプロキシを設定することもできます：

```bash
# Linux/macOS
export HTTP_PROXY=http://proxy.example.com:8080
export HTTPS_PROXY=http://proxy.example.com:8080

# Windows (コマンドプロンプト)
set HTTP_PROXY=http://proxy.example.com:8080
set HTTPS_PROXY=http://proxy.example.com:8080
```

## 設定の優先順位

プロキシ設定は以下の優先順位で適用されます：

1. **設定ファイル** (`~/.paper-manager/config.json`) - 最優先
2. **環境変数** (`HTTP_PROXY`, `HTTPS_PROXY`)

設定ファイルにプロキシ設定がある場合、環境変数よりも優先されます。

## 設定ファイルの場所

プロキシ設定は以下のファイルに保存されます：

```
~/.paper-manager/config.json
```

設定ファイルの内容例：

```json
{
  "proxy": {
    "http": "http://proxy.example.com:8080",
    "https": "http://proxy.example.com:8080"
  }
}
```

## プロキシ設定の確認

設定ページで現在のプロキシ設定を確認できます。設定ファイルと環境変数の両方が表示されます。

## プロキシ設定の削除

プロキシ設定を削除するには：

1. 設定ページでHTTPプロキシとHTTPSプロキシのフィールドを空欄にする
2. 「設定を保存」ボタンをクリック

これにより、設定ファイルからプロキシ設定が削除され、環境変数のみが使用されます。

## トラブルシューティング

### DOI取得が失敗する場合

- プロキシURLが正しいか確認してください
- プロキシサーバーが動作しているか確認してください
- 認証情報が必要な場合は、URLに含めてください（`http://user:pass@proxy.example.com:8080`）
- ファイアウォールやセキュリティソフトがプロキシ接続をブロックしていないか確認してください

### プロキシ設定が反映されない場合

- 設定を保存した後、アプリケーションを再起動してください
- 設定ファイルの内容を確認してください
- 環境変数が設定されている場合は、設定ファイルの値が優先されることを確認してください

