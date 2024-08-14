# リリース手順

リリースすると決めたら。

## ソースの作成

```bash
sphinx-apidoc -f -o ./docs_src ./src/python_template --module-first    # 'python_template'部分は適宜変更
```

## コミット

```bash
git commit -m "Release v0.0.1"  # バージョンは適宜変更
```

## リリース作成 および タグつけ

Github上でリリースおよびタグを作成する。
