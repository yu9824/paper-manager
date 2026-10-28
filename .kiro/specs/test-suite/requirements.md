# Requirements Document

## Project Description (Input)
既存コードの品質を保つための自動テストスイートを整備する。実データ（~/.paper-manager）を汚さないテスト隔離、ドメイン層（entry/bib/helper）・アプリ helper（backup/proxy/orphan_pdf/fields）・components・CLI の単体テスト、AppTest による全ページのスモークテストと登録フローの結合テストを対象とする。既知のバグは xfail(strict) で記録し、修正は別 spec とする。

- **誰が困っているか**: paper-manager の開発者（メンテナ）。リファクタリングや依存更新（CI: Python 3.9–3.13）のたびに、ユーザーデータを壊す回帰がないかを手動で確認する必要がある。
- **現状**: テストは `tests/test_app.py`（論文リストページの AppTest 起動のみ）と `tests/test_import.py`（pybtex の import 確認）の 2 件だけで、ドメイン層・helper・CLI の振る舞いは検証されていない。`conftest.py` がなく CI も `PAPER_MANAGER_DATA_DIR` を設定しないため、既存テストは実データディレクトリ（`~/.paper-manager`）を参照・作成しうる。
- **何を変えるか**: 実データから隔離された環境で、上記対象を網羅する自動テストを `pytest -xs` で実行できるようにする。既知のバグは `xfail(strict=True)` で仕様として記録し、本 spec ではプロダクションコードの修正を行わない。

## Introduction
paper-manager の既存機能の振る舞いを自動テストで固定し、リファクタリングや依存更新による回帰、とりわけユーザーデータ（論文リスト・設定・PDF）を壊す回帰を検出できるようにする。テストは実データディレクトリから完全に隔離され、外部ネットワークに依存せず、プロジェクト標準のテストコマンド（`pytest -xs`）だけでローカルと CI の双方で実行できる。現時点で判明している不具合は、本来あるべき振る舞いを期待する「既知の失敗」（`xfail(strict=True)`）として記録し、修正は後続の spec に委ねる。

本書における「Test Suite」は、本 spec で整備するテスト群とその実行時の振る舞い全体を指す。

## Boundary Context
- **In scope**:
  - テストの隔離（実データディレクトリ・環境変数・作業ディレクトリ・外部ネットワーク）
  - ドメイン層（`entry`・`bib`・`helper`）の単体テスト
  - アプリ helper（バックアップ・復元、プロキシ設定、孤立 PDF、フィールド定義・エントリタイプ変換）の単体テスト
  - components（PDF の保存・削除、論文の登録・更新、入力フォーム部品）の単体テスト
  - CLI（`paper-manager` の各サブコマンドと引数処理）の単体テスト
  - 全 5 ページ（リスト・編集、登録、バックアップ、復元、設定）のスモークテスト
  - 登録フロー（BibTeX・DOI・手動入力）の結合テスト
  - 既知バグの `xfail(strict=True)` による記録
- **Out of scope**:
  - プロダクションコードの修正（既知バグの修正は別 spec で行う）
  - `logging` サブパッケージの単体テスト
  - ページ固有機能（引用文の生成、BibTeX/XML エクスポート、PDF 一括 zip、編集・削除操作など）の詳細な振る舞い検証。これらのページはスモークテストで描画のみ確認する
  - 実ネットワーク（Crossref API）への問い合わせ
  - テストカバレッジの計測と閾値の設定
  - 実ブラウザを使った E2E テスト、CI のワークフロー構成（OS・Python バージョンの組み合わせ）の変更
- **Adjacent expectations**:
  - CI（GitHub Actions、Python 3.9–3.13）は既存どおり `pip install -e .[test]` → `pytest -xs` を実行し、Test Suite はそれだけで隔離された状態で成功する
  - DOI 経路のテストでは、Crossref API の応答を固定データで代替する
  - 既知バグを修正する後続 spec は、対応する既知の失敗の記録を解除し、通常のテストとして成功させる責務を持つ

## Requirements

### Requirement 1: 実データとテスト実行環境の隔離
**Objective:** As a 開発者, I want テストを実行しても自分の論文データや環境が一切変化しないこと, so that 普段使いのマシンで安心してテストを実行できる

#### Acceptance Criteria
1. The Test Suite shall 実データディレクトリ（`~/.paper-manager`、および実行前に `PAPER_MANAGER_DATA_DIR` が指していたディレクトリ）配下のファイルを作成・変更・削除しない
2. The Test Suite shall 実行者が環境変数などを事前に設定しなくても、プロジェクト標準のテストコマンド（`pytest -xs`）だけで隔離された状態で実行される
3. If アプリのデータ保存先が実データディレクトリを指したままテストが始まろうとした, the Test Suite shall テストを実行せず、隔離に失敗した旨を示して失敗する
4. The Test Suite shall テストごとに独立した一時データ領域を用い、あるテストが作成・変更した論文リスト・設定・PDF が他のテストの結果に影響しない
5. When 各テストが終了した, the Test Suite shall テスト中に変更したプロセス全体の状態（プロキシ関連を含む環境変数、作業ディレクトリ、ライブラリのログレベル）をテスト前の状態に戻す
6. When テストの実行が完了した, the Test Suite shall リポジトリの作業ツリーにテスト由来のファイル（バックアップ zip など）を残さない
7. The Test Suite shall 外部ネットワーク（Crossref API を含む）にアクセスせずに完了する

### Requirement 2: 論文エントリ（Entry）の単体テスト
**Objective:** As a 開発者, I want 論文エントリの正規化・識別子・PDF の保存先規則を検証すること, so that 保存形式やフォルダ構成を壊す変更を検出できる

#### Acceptance Criteria
1. When エントリが辞書から作成された, the Test Suite shall `ENTRYTYPE`・`ID`・`DOI` のキーが大文字、それ以外のキーが小文字に正規化され、値の前後の空白が除去されることを検証する
2. When 既存キーの集合を与えて識別子を生成した, the Test Suite shall 識別子が「空白を除いた第一著者名＋年＋`_`＋連番」の形式で既存キーと重複しないこと、および著者・年が欠落した場合はそれぞれ `Unknown`・`YYYY` が使われることを検証する
3. When PDF フォルダ名を生成した, the Test Suite shall フォルダ名が `<year> - <first_author> - <title>` 形式であり、年またはタイトルが欠落した場合は既定値（`YYYY`・`Unknown`）が使われることを検証する
4. If 生成したフォルダ名が 200 文字を超える, the Test Suite shall フォルダ名が 200 文字以内に切り詰められ、末尾がピリオドや空白で終わらないことを検証する
5. The Test Suite shall ファイル名に使えない文字の置換規則を、実行 OS に関係なく Windows 用（`\ / : * ? < > |`）とそれ以外の OS 用（`/`）の両方について検証する
6. When エントリの PDF 一覧を取得した, the Test Suite shall `<pdf_dir_name>/NN.pdf` 形式の PDF が番号順に返され、PDF が存在しない場合は空の一覧になること、および PDF の有無と件数が一覧と一致することを検証する
7. Where 旧形式の単一 PDF（`<base>/<旧ファイル名>.pdf`）が存在する, the Test Suite shall PDF 一覧の取得時に新形式（`<pdf_dir_name>/00.pdf`）へ移行され、元のファイルが残らないことを検証する
8. Where フォルダ内に旧命名規則の PDF が存在する, the Test Suite shall 既存の `NN.pdf` と番号が衝突せずに新命名規則へ改名され、既存ファイルが上書きされないことを検証する
9. When 保存先の基準ディレクトリを明示して PDF 関連の操作を行った, the Test Suite shall 既定の PDF ディレクトリではなく、指定したディレクトリ配下だけが参照・変更されることを検証する

### Requirement 3: 論文リスト（PaperList）の永続化の単体テスト
**Objective:** As a 開発者, I want 論文リストの保存・読み込みと画面状態との同期を検証すること, so that `list.json` の内容が失われる回帰を検出できる

#### Acceptance Criteria
1. When 論文リストをファイルへ保存してから読み込んだ, the Test Suite shall 読み込んだ論文リストが保存前と同じキーとエントリ内容を持つこと、および非 ASCII 文字が保持されることを検証する
2. If 論文リストのファイルが存在しない, the Test Suite shall 空の論文リストが返されることを検証する
3. If 論文リストのファイルが JSON として壊れている, the Test Suite shall 空の論文リストが返され、壊れている旨の警告が表示されることを検証する
4. When 論文リストを画面の状態へ書き込んでから読み出した, the Test Suite shall 同じ内容の論文リストが得られること、および未設定の状態からは空の論文リストが得られることを検証する

### Requirement 4: BibTeX 読み込みの単体テスト
**Objective:** As a 開発者, I want BibTeX から論文情報を取り込む処理を検証すること, so that BibTeX 経路の登録で情報が欠ける回帰を検出できる

#### Acceptance Criteria
1. When 同じ内容の BibTeX をテキスト・バイト列・ファイルパスのそれぞれで読み込んだ, the Test Suite shall 3 つの入力形式で同じ結果が得られることを検証する
2. When 著者を含む BibTeX を読み込んだ, the Test Suite shall 各著者が「名 姓」の形式で ` and ` 区切りの 1 つの文字列にまとめられ、エントリタイプが `ENTRYTYPE` として付与され、引用キーが小文字化されることを検証する
3. When 複数のエントリを含む BibTeX を読み込んだ, the Test Suite shall すべてのエントリが引用キーごとに返されることを検証する
4. If 対応していない型の入力を与えた, the Test Suite shall 型エラーが発生することを検証する

### Requirement 5: 汎用ユーティリティの単体テスト
**Objective:** As a 開発者, I want 横断的に使われるユーティリティの振る舞いを検証すること, so that 著者・タグの分割や非推奨警告といった共通処理の回帰を検出できる

#### Acceptance Criteria
1. When 文字列を区切り文字で分割した, the Test Suite shall 各要素の前後の空白が除去されたタプルが返され、空文字列からは空のタプルが返されることを検証する
2. When 非推奨として印を付けた関数を呼び出した, the Test Suite shall 呼び出しのたびに非推奨警告が出され、元の戻り値が返り、説明文に非推奨の注記が付くことを検証する
3. When パッケージのインストール有無を判定した, the Test Suite shall インストール済みのパッケージでは真、存在しないパッケージでは偽が返されることを検証する
4. When 関数が特定の名前の引数を受け取るかを判定した, the Test Suite shall シグネチャに含まれる引数名では真、含まれない引数名では偽が返されることを検証する
5. When 進捗表示の代替オブジェクトを使った, the Test Suite shall 反復結果が元の反復可能オブジェクトと同じになり、それ以外の任意の操作が何もせずに完了することを検証する
6. When ダミー関数を呼び出した, the Test Suite shall 追加の引数に関係なく第 1 引数がそのまま返されることを検証する

### Requirement 6: バックアップ・復元処理の単体テスト
**Objective:** As a 開発者, I want バックアップの作成と復元を検証すること, so that データ退避・移行の手段が壊れる回帰を検出できる

#### Acceptance Criteria
1. When 論文リスト・設定・PDF が存在する状態でバックアップを作成した, the Test Suite shall バックアップ zip にバージョン情報・論文リスト・設定・すべての PDF（データディレクトリからの相対パス）が含まれることを検証する
2. Where 論文リスト・設定・PDF の一部が存在しない, the Test Suite shall 存在するものだけがバックアップに含まれ、作成が失敗しないことを検証する
3. When 出力先を指定してバックアップを作成した, the Test Suite shall 指定したパスに zip ファイルが作成されることを検証し、出力先を指定しなかった場合はファイルを作らずにメモリ上の zip が返されることを検証する
4. When バックアップを作成し、空のデータ領域へ復元した, the Test Suite shall 復元後の論文リスト・設定・PDF の構成と内容がバックアップ元と一致することを検証する
5. Where 復元先に既存の論文リストまたは設定が存在する, the Test Suite shall 復元前の内容が `.json.backup` として退避されることを検証する
6. When 復元が成功した, the Test Suite shall 成功を示す結果と、復元した PDF の件数を含むメッセージが返されることを検証する
7. If zip として不正なファイルを復元しようとした, the Test Suite shall 失敗を示す結果と「無効なzipファイルです。」というメッセージが返され、データ領域が変更されないことを検証する
8. When バックアップ情報を取得した, the Test Suite shall バージョン情報を含むバックアップからはバージョンと作成日時が得られ、バージョン情報のないバックアップや不正なファイルからは情報なしが返されることを検証する

### Requirement 7: プロキシ設定の単体テスト
**Objective:** As a 開発者, I want プロキシ設定の優先順位・保存・適用を検証すること, so that プロキシ環境で DOI 取得ができなくなる回帰を検出できる

#### Acceptance Criteria
1. Where 設定ファイルと環境変数の両方にプロキシが設定されている, the Test Suite shall 設定ファイルの値が優先されることを検証する
2. Where 設定ファイルにプロキシがなく環境変数にだけ設定されている, the Test Suite shall 大文字・小文字いずれの環境変数名からも値が取得され、HTTPS のプロキシが未設定のときは HTTP のプロキシが使われることを検証する
3. Where どこにもプロキシが設定されていない, the Test Suite shall 空の設定が返されることを検証する
4. If 設定ファイルが JSON として壊れている, the Test Suite shall 例外を発生させずに環境変数の値へフォールバックすることを検証する
5. When プロキシ設定を保存した, the Test Suite shall 設定ファイル内のプロキシ以外の項目が保持されることを検証し、HTTP・HTTPS の両方を空にして保存した場合はプロキシ設定が削除されることを検証する
6. When プロキシ設定を環境へ適用した, the Test Suite shall HTTP・HTTPS それぞれについて大文字・小文字両方の環境変数名に値が設定されることを検証する

### Requirement 8: 孤立 PDF 検出・削除とフィールド定義の単体テスト
**Objective:** As a 開発者, I want 孤立 PDF の扱いとエントリタイプごとのフィールド定義を検証すること, so that 必要な PDF を誤って削除したり必須項目の判定を誤ったりする回帰を検出できる

#### Acceptance Criteria
1. When 孤立 PDF を検出した, the Test Suite shall 論文リストのどのエントリにも対応しないフォルダ内の PDF と、対応しない旧形式の単一 PDF が検出され、論文リストに紐づく PDF は検出されないことを検証する
2. Where PDF ディレクトリが存在しない, the Test Suite shall 孤立 PDF が 0 件と判定されることを検証する
3. When 孤立 PDF に最も近いエントリを探した, the Test Suite shall 類似度が 0.0〜1.0 の範囲で返され、フォルダ名が完全に一致するエントリの類似度が 1.0 になり、論文リストが空の場合は該当なしが返されることを検証する
4. When 孤立 PDF ファイルを削除した, the Test Suite shall 削除したファイル数が返され、空になったフォルダが削除され、PDF ディレクトリ自体は削除されないことを検証する
5. If 一部の孤立 PDF の削除に失敗した, the Test Suite shall 残りの削除が続行されることを検証する
6. The Test Suite shall 8 種類のエントリタイプ（article, book, inbook, inproceedings, masterthesis, phdthesis, techreport, misc）のフィールド定義が読み込まれ、各タイプの必須フィールドの集合が定義ファイルの `required` 指定と一致すること（misc は必須フィールドなし）を検証する
7. When Crossref のエントリタイプを変換した, the Test Suite shall `journal-article`→`article`、`proceedings-article`→`inproceedings`、`book`→`book` に変換され、それ以外は `misc` になることを検証する

### Requirement 9: components（PDF 操作・登録・更新・入力フォーム）の単体テスト
**Objective:** As a 開発者, I want 画面から呼ばれるファイル操作と論文リスト操作を検証すること, so that 登録・更新時に PDF やエントリが失われる回帰を検出できる

#### Acceptance Criteria
1. When 複数の PDF を保存した, the Test Suite shall エントリのフォルダに `00.pdf` から連番で保存され、既存の番号と重複しない名前が付き、保存件数が返されることを検証する
2. If 保存する PDF が 1 件もない, the Test Suite shall 何も保存されず 0 件が返されることを検証する
3. If 保存先のパスが長さ制限（250 文字）を超える, the Test Suite shall そのファイルの保存がスキップされてエラーが表示され、残りのファイルの処理が続行されることを検証する
4. When 特定の PDF だけを削除した, the Test Suite shall 指定したファイルだけが削除され、フォルダが空になった場合はフォルダも削除されることを検証する
5. When エントリの全 PDF を削除した, the Test Suite shall エントリのフォルダごと削除されて削除件数が返され、旧形式の単一 PDF しかない場合はそのファイルが削除されることを検証する
6. When 論文を登録した, the Test Suite shall エントリに一意な識別子が付与されて論文リストに追加され、添付 PDF が保存され、論文リストのファイルと画面の状態の両方に反映されることを検証する
7. If 登録する論文の PDF フォルダ名が既存エントリと重複する, the Test Suite shall 登録が拒否されて重複エラーが表示され、論文リストが変更されないことを検証する
8. When PDF フォルダ名が変わらない更新を行った, the Test Suite shall 同じ識別子のままエントリ内容が更新されることを検証する
9. When PDF フォルダ名が変わる更新を行った, the Test Suite shall 既存の PDF フォルダが新しい名前へ改名されて PDF が保持され、エントリが新しい識別子で登録し直されることを検証する
10. If 更新後の PDF フォルダ名が他のエントリと重複する、または改名先のフォルダが既に存在する, the Test Suite shall 更新が拒否されてエラーが表示され、論文リストと PDF フォルダが変更されないことを検証する
11. When 手動入力フォームに値を入力した, the Test Suite shall 選択したエントリタイプのフィールドが表示され、入力した値がエントリに反映され、空欄にした項目がエントリから除かれることを検証する
12. When PDF アップロード欄を表示した, the Test Suite shall ファイル未選択時は空の一覧が返され、単一選択・複数選択のいずれの場合も一覧として返されることを検証する

### Requirement 10: CLI の単体テスト
**Objective:** As a 開発者, I want `paper-manager` コマンドの引数処理と各サブコマンドを検証すること, so that アプリの起動やコマンドラインからの退避・復元が壊れる回帰を検出できる

基準 5〜8 は、backup / restore サブコマンドが受け取った引数に対する処理を対象とする。コマンド全体の引数解析を経由した backup / restore は現状失敗するため、Requirement 13 で既知バグとして扱う。

#### Acceptance Criteria
1. When バージョン表示オプション（`-v` / `--version`）を指定した, the Test Suite shall パッケージのバージョンが表示されることを検証する
2. When `run` または `version` サブコマンドを実行した, the Test Suite shall Streamlit のサーバーを実際に起動せずに、未知の引数がそのまま Streamlit へ渡されること、`run` ではアプリのトップページが起動対象になることを検証する
3. Where 実行時の引数に `--client.showSidebarNavigation` が含まれない, the Test Suite shall `--client.showSidebarNavigation false` が既定で付与され、明示的に指定された場合は重複して付与されないことを検証する
4. When `run --debug` を実行した, the Test Suite shall ライブラリのログレベルがデバッグに設定されることを検証する
5. When 出力先を指定してバックアップ処理を実行した, the Test Suite shall 指定したパスにバックアップ zip が作成されることを検証し、出力先を省略した場合は作業ディレクトリに日時入りの名前（`paper-manager-backup_YYYYMMDD_HHMMSS.zip`）で作成されることを検証する
6. If 復元処理に存在しないバックアップファイルを指定した, the Test Suite shall 終了コード 1 で終了し、データ領域が変更されないことを検証する
7. Where 復元処理で `--force` が指定されていない, the Test Suite shall 確認入力が `yes` / `y` の場合だけ復元が実行され、それ以外の場合は復元せずに終了コード 0 で終了することを検証する
8. If 復元処理が失敗を返した, the Test Suite shall エラーメッセージを表示して終了コード 1 で終了することを検証する

### Requirement 11: 全ページのスモークテスト
**Objective:** As a 開発者, I want すべてのページが例外なく表示されることを検証すること, so that 依存更新やリファクタリングで画面が開けなくなる回帰を検出できる

#### Acceptance Criteria
1. When データ領域が空の状態で 5 つのページ（リスト・編集、登録、バックアップ、復元、設定）をそれぞれ表示した, the Test Suite shall 各ページが例外なく描画されることを検証する
2. When 論文（PDF あり・なしの両方）と孤立 PDF を含むサンプルデータの状態で 5 つのページをそれぞれ表示した, the Test Suite shall 各ページが例外なく描画されることを検証する
3. When 各ページを表示した, the Test Suite shall 共通のタイトルと、5 つのページへのナビゲーションが表示されることを検証する
4. When 操作をせずにページを表示した, the Test Suite shall 論文リストのファイルに保存されている論文（キーとエントリ内容）が表示前と同一であることを検証する
5. The Test Suite shall 実ブラウザや Streamlit サーバーを起動せずにページを実行する

### Requirement 12: 登録フローの結合テスト
**Objective:** As a 開発者, I want 登録ページの操作から論文リストへの保存までを通しで検証すること, so that 3 つの登録経路のいずれかが使えなくなる回帰を検出できる

#### Acceptance Criteria
1. When BibTeX テキストを入力して登録した, the Test Suite shall 論文リストのファイルにエントリが 1 件追加されて識別子が付与され、登録成功が表示されることを検証する
2. When BibTeX ファイルをアップロードして登録した, the Test Suite shall テキスト入力の場合と同じ内容のエントリが追加されることを検証する
3. If BibTeX が未入力、複数のエントリを含む、または有効なエントリを含まない, the Test Suite shall それぞれに対応するエラーが表示され、論文リストが変更されないことを検証する
4. When Crossref の応答を固定データで代替した状態で DOI を入力して登録した, the Test Suite shall タイトル・著者・掲載誌・年・DOI が応答の内容どおりに設定されたエントリが追加されることを検証する
5. If DOI が未入力、またはメタデータを取得できない, the Test Suite shall エラーが表示され、論文リストが変更されないことを検証する
6. When 手動入力でエントリタイプを選び、必須フィールドを入力して登録した, the Test Suite shall 入力内容どおりのエントリが追加されることを検証する
7. If 必須フィールドが不足した状態で登録した（どの登録経路でも）, the Test Suite shall 不足しているフィールド名を含むエラーが表示され、論文リストが変更されないことを検証する
8. If 既存の論文と PDF フォルダ名が重複する論文を登録した, the Test Suite shall 重複エラーが表示され、論文リストの件数が変わらないことを検証する
9. When PDF を添付して登録した, the Test Suite shall 添付した PDF がエントリのフォルダに `00.pdf` から連番で保存されることを検証する

### Requirement 13: 既知バグの記録
**Objective:** As a 開発者, I want 判明している不具合を、本来あるべき振る舞いのテストとして記録しておくこと, so that 修正が必要な箇所が明確になり、修正されたときに記録の更新漏れを検出できる

#### Acceptance Criteria
1. The Test Suite shall 次の既知バグを、本来あるべき振る舞いを期待するテストとして記録し、現状の失敗が Test Suite 全体を失敗させない既知の失敗として扱う
   - **CLI のバックアップ・復元が常に失敗する**: `paper-manager backup` / `paper-manager restore` が、既定で付与される `--client.showSidebarNavigation` を受け付けずに終了コード 2 で終了する。本来はバックアップの作成・復元が行われる
   - **著者・タイトルが欠落したエントリで例外が発生する**: 著者のないエントリ（misc では著者は任意）の PDF フォルダ名生成、およびタイトルのないエントリの PDF 一覧取得で例外が発生する。本来は既定値（`Unknown`）で処理される。著者のない BibTeX の読み込みでも例外が発生する。本来は著者なしのエントリとして読み込まれる
   - **復元時にデータディレクトリの外へ書き込まれる**: `pdf/../../x.pdf` のようなパスを含む zip を復元すると、データディレクトリの外にファイルが書き込まれる。本来はデータディレクトリの外へは書き込まれない（画面側の復元処理にも同じ実装がある）
   - **軽微な不整合**: 手動入力で登録した著者が ` and ` ではなく `, ` 区切りで保存される。手動入力フォームでタグの既定値が `, ` ではなく ` and ` で分割される。BibTeX 読み込みで複合姓（例: `van der Waals`）やミドルネームの一部が欠落する。孤立 PDF の削除にフォルダを渡すと、削除件数が 0 と報告される
   - **Windows で禁止されている `"` が置換されない**（要件定義中に判明）: Windows 用のファイル名置換規則に `"` が含まれず、タイトルに `"` を含む論文の PDF フォルダを Windows で作成できない
2. The Test Suite shall 各既知バグの記録に、不具合の内容と再現条件を説明する理由を付与する
3. When 既知バグが修正され、記録したテストが期待どおりに成功するようになった, the Test Suite shall そのテストを失敗として報告し、既知の失敗の記録を解除するよう促す
4. When テストの実装中に新たなバグが判明した, the Test Suite shall プロダクションコードを修正せずに、既知バグと同じ形式で記録する
5. The Test Suite shall 既知の失敗として記録したもの以外のテストがすべて成功する状態で提供される

### Requirement 14: 実行性と保守性
**Objective:** As a 開発者, I want テストがどの環境でも同じ結果になること, so that CI とローカルの結果を信頼して変更を判断できる

#### Acceptance Criteria
1. The Test Suite shall CI がサポートするすべての Python バージョン（3.9〜3.13）で成功する
2. The Test Suite shall 実行日時、実行順序、実行 OS に依存せず、繰り返し実行しても同じ結果になる
3. The Test Suite shall 既存のテストが検証していた内容（論文リストページが起動できること、pybtex を import できること）を引き続き検証する
4. The Test Suite shall テストの追加にあたり、既存の実行コマンド（`pip install -e .[test]` → `pytest -xs`）を変更しない
