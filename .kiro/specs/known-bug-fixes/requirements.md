# Requirements Document

## Project Description (Input)
**誰が困っているか**: paper-manager の利用者（ローカルの単一ユーザー）。特に、Windows で保存先ディレクトリ（環境変数 `PAPER_MANAGER_DATA_DIR`）を長いパスに設定している利用者と、BibTeX・DOI・手動入力で論文を登録・編集する利用者。

**現状**:
- ユーザー報告（Windows）: 保存先ディレクトリが長いと、一覧・詳細で PDF が表示されない。ログには warning レベルで PDF が見つからない旨が出る。PDF フォルダ名は保存先の長さに関係なく最大 200 文字まで許され、`<保存先>\pdf\<フォルダ名>\00.pdf` が Windows のパス長の上限（260 文字）を超えうる。
- test-suite spec で strict xfail として記録した既知バグ KB1〜KB10（CLI の backup / restore / version、著者・タイトルの欠落、復元のパストラバーサル、手動入力の著者とタグの区切り、複合姓の欠落、孤立 PDF の削除件数、Windows の `"`、構文エラーの BibTeX、フォルダ名の長さ、`=` 形式のナビゲーション指定、DOI 取得の通信エラー）。
- test-suite spec の実装中に観察として記録した不具合のうち、次の 5 件: 大文字小文字だけが違うタイトルの論文が PDF フォルダを共有する、壊れた `list.json` が上書きされて論文リストが消える、旧形式の PDF が孤立 PDF と誤判定されて削除される、書籍・会議録の DOI を登録できない、年が不正な論文の編集フォームが開かない。

**何を変えるべきか**:
- 上記の不具合を修正し、修正した既知バグの strict xfail を外して通常のテストとして成功させる。プロダクションコード（`src/`）の修正を伴う。
- 利用者のデータ（論文リスト・設定・PDF フォルダ）を壊さないことを最優先とし、既存の PDF フォルダ（`<pdf_dir_name>/NN.pdf`）との後方互換を保つ。

## Introduction
paper-manager の既知バグ（test-suite spec の KB1〜KB10）、利用者から報告された Windows で長いパスの PDF が見えない不具合、および test-suite spec で観察として記録した不具合のうち 5 件を修正する。加えて、実装中に見つかった、引用キーに `,` などが入ると BibTeX の表示・エクスポートが落ちる不具合と、BOM 付きの論文リストのファイルが壊れていると判定される問題を修正する。修正後は、該当する xfail の記録を外し、通常のテストとして成功させる。修正によって既存の論文リスト・設定・PDF フォルダが読めなくなったり、黙って書き換えられたりしないことを前提とする。

## Boundary Context
- **In scope**:
  - Windows で、パス全体が 260 文字を超える PDF の保存・表示・削除・エクスポート・バックアップ・復元
  - PDF フォルダ名の生成（長さ、著者・タイトルの欠落、Windows の `"`、大文字小文字だけの違い）
  - 手動入力フォームの著者とタグの区切り
  - BibTeX からの登録（著者のない BibTeX、複合姓・ミドルネーム、構文エラー）
  - DOI からの登録（通信エラー、書籍・会議録、項目の欠けたメタデータ）
  - 年が不正な論文の編集
  - 孤立 PDF（フォルダを指定した削除の件数、旧形式の PDF の誤判定）
  - 復元（データディレクトリの外への書き込み。画面と CLI の両方）
  - 壊れた論文リストのファイルの扱い（BOM 付きの UTF-8 のファイルを含む）
  - BibTeX の表示・エクスポートで使う引用キー（BibTeX の引用キーとして読めない文字）
  - CLI（`backup` / `restore` / `version`、サイドバーのナビゲーションの指定）
- **Out of scope**:
  - OS ごとにファイル名の置換規則が違うことによる、OS をまたいだデータ移行時の不整合（`:` や `?` を含むタイトル。別 spec で扱う）
  - Linux のファイル名の上限（255 バイト）による、長い非 ASCII タイトルの PDF フォルダの作成失敗
  - 既に `, ` 区切りで保存されている著者の自動変換（利用者が編集フォームで直す）
  - 孤立 PDF の削除に、PDF ディレクトリの外のパスや PDF 以外のファイルを渡した場合の検証
  - 壊れた設定ファイルに保存したときに、プロキシ以外の設定が失われる問題
  - DOI のメタデータにない任意の項目（巻・号・ページ）が空の値で保存される問題
  - test-suite spec の Implementation Notes に観察として記録した、その他の軽微な不整合（1 語の著者名の整形、エラー文言の日英不統一など）
- **Adjacent expectations**:
  - test-suite spec のテスト基盤（データの隔離と共有の fixture）と既知バグのテストを使い、修正した既知バグの xfail の記録を外す
  - test-suite spec の要件と食い違う振る舞いに変える箇所（9.3 の「保存先のパスが 250 文字を超えると保存をスキップする」）は、この spec の要件を優先し、該当するテストを更新する
  - CI（Python 3.9〜3.13）の実行コマンドは変えない

## Requirements

### Requirement 1: Windows で長いパスの PDF を扱う
**Objective:** As a Windows で保存先ディレクトリを長いパスに設定している利用者, I want PDF のパス全体が Windows の上限を超えても PDF を扱えること, so that 保存した PDF が一覧や詳細から見えなくならない

#### Acceptance Criteria
1. While Windows 上で動作している, when PDF の保存先のパス全体が 260 文字を超える, the Paper Manager shall その PDF を保存する
2. While Windows 上で動作している, the Paper Manager shall パス全体が 260 文字を超える既存の PDF を、一覧の PDF 件数、詳細のビューアとダウンロード、複数選択の zip エクスポート、削除、孤立 PDF の検出、バックアップと復元のすべてで、260 文字以内の PDF と同じように扱う
3. The Paper Manager shall PDF フォルダ名を、保存先ディレクトリの長さによって変えない
4. If PDF フォルダの作成、または PDF の読み込み・書き込みに失敗した, then the Paper Manager shall 失敗したことと対象のパスを画面に表示する
5. The Paper Manager shall パス全体の長さが 250 文字を超えることだけを理由に、PDF の保存を拒否しない
6. If 論文の編集と同時に追加した PDF の保存に失敗した, then the Paper Manager shall 論文の更新に成功して画面が再描画された後も、失敗したことと対象のパスを利用者が確認できるようにする

### Requirement 2: PDF フォルダ名の生成
**Objective:** As a 論文を登録する利用者, I want どの論文でも PDF フォルダ名を作れること, so that 著者やタイトルの内容によって PDF を保存できなくならない

#### Acceptance Criteria
1. The Paper Manager shall 年・著者・タイトルの長さにかかわらず、PDF フォルダ名を 200 文字以内にする（KB7）
2. The Paper Manager shall 切り詰めた PDF フォルダ名の末尾を `.` や空白にしない
3. When 論文に著者がない、または著者が空である, the Paper Manager shall 著者を `Unknown` として PDF フォルダ名を作る（KB2a）
4. When 論文にタイトルがない, the Paper Manager shall 例外にならずに、その論文の PDF の有無と件数を表示する（KB2b）
5. While Windows 上で動作している, the Paper Manager shall PDF フォルダ名に含まれる `"` を `_` に置き換える（KB5）
6. The Paper Manager shall 修正前に作成できていた PDF フォルダ名（各 OS で作成できた名前）のうち、KB7 の範囲（2.1 によって名前が変わるもの）以外を変えない
7. If 修正前の規則で作られた名前の PDF フォルダが、修正後の名前と異なる名前で存在する（KB7 の範囲）, then the Paper Manager shall 既存の PDF を上書きせずにそのフォルダを修正後の名前へ移し、移すまでの間もそのフォルダを孤立 PDF として扱わない

### Requirement 3: 大文字小文字だけが違う PDF フォルダ名
**Objective:** As a 論文を登録・更新する利用者, I want 大文字小文字だけが違うタイトルの論文を同じ論文として扱うこと, so that 別々の論文が同じ PDF フォルダを共有して PDF が消えることがない

#### Acceptance Criteria
1. When 既存の論文と PDF フォルダ名が大文字小文字だけ違う論文を登録しようとした, the Paper Manager shall 重複としてエラーを表示し、論文リストと PDF を変えない
2. When 論文の更新によって、PDF フォルダ名が大文字小文字だけ変わる, the Paper Manager shall 更新を受け付け、PDF フォルダ名を新しい表記に変え、PDF を保つ
3. If 更新後の PDF フォルダ名が、他の論文の PDF フォルダ名と大文字小文字だけ違う, then the Paper Manager shall 重複としてエラーを表示し、論文リストと PDF を変えない
4. The Paper Manager shall 前 3 項の判定を、ファイルシステムが大文字小文字を区別するかどうかにかかわらず、すべての OS で同じに行う

### Requirement 4: 手動入力フォームの著者とタグ
**Objective:** As a 手動入力フォームで論文を登録・編集する利用者, I want 著者とタグが正しい区切りで扱われること, so that 編集しただけで著者の表記や PDF フォルダ名が変わらない

#### Acceptance Criteria
1. When 手動入力フォームで複数の著者を入力して保存した, the Paper Manager shall 著者を ` and ` で区切って保存する（KB4a）
2. When ` and ` 区切りの著者を持つ既存の論文を編集フォームで開き、著者を変更せずに保存した, the Paper Manager shall 著者の表記と PDF フォルダ名を変えない
3. When 手動入力フォームがタグの既定値を表示する, the Paper Manager shall 既定値を `, ` で区切り、タグごとに選択肢にする（KB4b）
4. The Paper Manager shall 既に `, ` 区切りで保存されている著者を、自動で書き換えない

### Requirement 5: BibTeX からの登録
**Objective:** As a BibTeX から論文を登録する利用者, I want 著者のない BibTeX や構文の誤った BibTeX でも画面が落ちないこと, so that 入力の誤りに気づいて直せる

#### Acceptance Criteria
1. When 著者のない BibTeX を読み込んだ, the Paper Manager shall 例外にならずに、著者のないエントリとして読み込む（KB2c）
2. When 必須項目を満たす、著者のない BibTeX から論文を登録した, the Paper Manager shall 論文を登録して成功を表示する
3. When 複合姓やミドルネームを含む著者の BibTeX を読み込んだ, the Paper Manager shall 姓と名のすべての部分を保って著者を整形する（例: `van der Waals, Johannes Diderik` は `van der Waals` と `Johannes Diderik` の両方を含む）（KB4c）
4. If 構文として正しくない BibTeX を登録しようとした, then the Paper Manager shall 画面を例外で落とさずにエラーを 1 つ表示し、論文リストを変えない（KB6）

### Requirement 6: DOI からの登録
**Objective:** As a DOI から論文を登録する利用者, I want 通信エラーや書籍・会議録の DOI でも登録の操作が破綻しないこと, so that DOI 経路で登録できる論文の種類が限られない

#### Acceptance Criteria
1. If DOI のメタデータの取得中に通信エラー（接続できない、タイムアウトなど）が起きた, then the Paper Manager shall 画面を例外で落とさずにエラーを 1 つ表示し、論文リストを変えない（KB10）
2. When 書籍の DOI を登録し、取得したメタデータに出版社が含まれる, the Paper Manager shall 出版社を含むエントリとして論文を登録する
3. When 会議録の論文の DOI を登録し、取得したメタデータに会議録名が含まれる, the Paper Manager shall 会議録名を含むエントリとして論文を登録する
4. If 取得したメタデータに掲載誌名・著者・出版年のいずれかが含まれない, then the Paper Manager shall 画面を例外で落とさず、不足している必須項目があればその項目名を含むエラーを表示し、論文リストを変えない

### Requirement 7: 年が不正な論文の編集
**Objective:** As a 論文を編集する利用者, I want 年が整数でない論文でも編集フォームを開けること, so that 登録済みの論文の年を画面から直せる

#### Acceptance Criteria
1. When 年が整数でない（例: `n.d.`、`2020a`）、または入力できる範囲の外である論文の編集フォームを開いた, the Paper Manager shall 例外にならずに編集フォームを表示する
2. When 前項の論文を、年を変更せずに保存した, the Paper Manager shall 元の年の値を保つ
3. When 前項の論文の年を編集フォームで直して保存した, the Paper Manager shall 直した年で論文を保存する

### Requirement 8: 孤立 PDF
**Objective:** As a 孤立 PDF を整理する利用者, I want 論文に紐づく PDF が孤立 PDF として削除されないこと, so that 「すべての孤立 PDF を削除」で必要な PDF を失わない

#### Acceptance Criteria
1. When 孤立 PDF の削除にフォルダを指定した, the Paper Manager shall 削除した PDF の件数を報告する（KB4d）
2. If PDF ディレクトリの直下に、既存の論文の PDF フォルダと同じ名前の旧形式の PDF がある, then the Paper Manager shall その PDF を孤立 PDF として検出しない
3. When 前項の旧形式の PDF を見つけた, the Paper Manager shall 既存の PDF を上書きせずに、その論文の PDF フォルダの空いている番号へ移し、その論文の PDF として表示する

### Requirement 9: 復元
**Objective:** As a バックアップから復元する利用者, I want 復元でデータディレクトリの外にファイルが書き込まれないこと, so that 不正なバックアップによって他の場所のファイルが壊されない

#### Acceptance Criteria
1. If 復元する zip に、データディレクトリの外を指すパスが含まれる, then the Paper Manager shall データディレクトリの外にファイルを作らない（KB3）
2. If 前項の zip を復元しようとした, then the Paper Manager shall 復元を中止してエラーを表示し、データディレクトリの中身を変えない
3. The Paper Manager shall 前 2 項を、画面からの復元と CLI からの復元の両方で満たす
4. If 復元する zip のメンバーが読み出せない（CRC の不一致、破損した圧縮データ、暗号化など）, then the Paper Manager shall 何も書かずに復元を中止してエラーを表示し、データディレクトリの中身（`.json.backup` を含む）を変えない

### Requirement 10: 壊れた論文リストのファイル
**Objective:** As a 論文リストのファイルが壊れてしまった利用者, I want 壊れたファイルの内容が失われないこと, so that 壊れたファイルから論文リストを取り戻せる

#### Acceptance Criteria
1. If 論文リストのファイルが壊れている（JSON として解析できない、文字コードとして読めない、論文の一覧の形をしていない）, then the Paper Manager shall 画面を例外で落とさずに、壊れている旨の警告を表示する
2. While 論文リストのファイルが壊れている, the Paper Manager shall ページを表示しただけでは、論文リストのファイルを書き換えない
3. If 論文リストのファイルが壊れた状態で、利用者が論文の登録・更新・削除などによって論文リストを保存した, then the Paper Manager shall 保存する前に壊れたファイルの内容を別のファイルに退避し、退避先を画面に表示する
4. When 論文リストのファイルが先頭に BOM の付いた UTF-8 で、それ以外は正しい形をしている, the Paper Manager shall 壊れているとみなさずに、論文リストとして読み込む

### Requirement 11: CLI
**Objective:** As a CLI を使う利用者, I want `backup`・`restore`・`version` が動き、ナビゲーションの指定が正しく扱われること, so that 画面を開かずにバックアップ・復元やバージョン確認ができる

#### Acceptance Criteria
1. When `paper-manager backup -o <パス>` を実行した, the Paper Manager CLI shall 指定したパスにバックアップを作成し、終了コード 0 で終了する（KB1）
2. When `paper-manager backup` を出力先を指定せずに実行した, the Paper Manager CLI shall 作業ディレクトリに日時入りの名前でバックアップを作成し、終了コード 0 で終了する（KB1）
3. When `paper-manager restore <パス> --force` を実行した, the Paper Manager CLI shall 確認を求めずにバックアップから復元し、終了コード 0 で終了する（KB1）
4. When `paper-manager restore <パス>` を `--force` なしで実行した, the Paper Manager CLI shall 確認を求め、確認された場合だけ復元する（KB1）
5. When `paper-manager version` を実行した, the Paper Manager CLI shall Streamlit のバージョンを表示し、終了コード 0 で終了する（KB9）
6. When `paper-manager run` を、サイドバーのナビゲーションの指定を `=` 形式（例: `--client.showSidebarNavigation=true`）で明示して実行した, the Paper Manager CLI shall 既定の指定を追加せず、利用者の指定をそのまま渡す（KB8）
7. When `paper-manager run` をサイドバーのナビゲーションの指定なしで実行した, the Paper Manager CLI shall サイドバーのナビゲーションを無効にする指定を付ける
8. The Paper Manager CLI shall `version`・`backup`・`restore` に、サイドバーのナビゲーションの指定を付けない

### Requirement 12: 回帰の防止と後方互換
**Objective:** As a 開発者, I want 修正した不具合がテストで守られ、既存のデータがそのまま読めること, so that 修正による新たな回帰やデータの破損を防げる

#### Acceptance Criteria
1. When 既知バグを修正した, the Test Suite shall その既知バグの xfail の記録を外し、本来あるべき振る舞いを通常のテストとして検証する
2. The Test Suite shall この spec で修正した観察事項（Requirement 1、3、6.2〜6.4、7、8.2〜8.3、9.2〜9.3、10、13 の振る舞い）を検証するテストを持つ
3. The Test Suite shall Windows に固有の振る舞い（Requirement 1、2.5）を、CI の実行環境にかかわらず検証する
4. The Test Suite shall 既知の失敗として記録したもの以外のテストがすべて成功する状態で、CI の全 Python バージョン（3.9〜3.13）で成功する
5. The Paper Manager shall 修正前に作成された論文リスト・設定・PDF フォルダを、修正後もそのまま読み込み、PDF を表示する

### Requirement 13: 引用キー
**Objective:** As a 論文の BibTeX を表示・エクスポートする利用者, I want 著者の表記にかかわらず BibTeX を表示・エクスポートできること, so that 登録した論文を引用に使える

#### Acceptance Criteria
1. When 論文を登録・更新して引用キーを作った, the Paper Manager shall 引用キーに、BibTeX の引用キーとして読めない文字（空白、`,`、`{`、`}`）を含めない
2. The Paper Manager shall BibTeX の引用キーとして読めない文字を含まない著者と年からは、修正前と同じ引用キーを作る
3. When 引用キーに BibTeX の引用キーとして読めない文字を含む既存の論文の BibTeX を表示・エクスポートした, the Paper Manager shall 画面を例外で落とさずに、その文字を除いた引用キーで出力し、論文リストに保存された引用キーは書き換えない
4. When 複数の論文をまとめてエクスポートし、出力する引用キーが大文字小文字を区別せずに重複した, the Paper Manager shall 画面を例外で落とさずに、重複しない引用キーで出力する
