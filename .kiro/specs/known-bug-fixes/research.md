# Research & Design Decisions

## Summary
- **Feature**: `known-bug-fixes`
- **Discovery Scope**: Extension（既存システムの不具合修正。light discovery を 4 領域に分けて並行実施し、Windows の長いパスと外部 API は外部の一次情報で確認した）
- **Key Findings**:
  - 利用者の報告（Windows で PDF が見えない）の原因は、パス全体が 260 文字を超えると、拡張長のパス（`\\?\`）を使わない限り Windows の API が失敗し、`is_dir()` などが黙って False を返すこと。フォルダ名は変えずに、ファイル操作の直前だけ `\\?\` 付きのパスに変換すれば解決できる。python.org 版と conda-forge 版の python.exe は `longPathAware` だが、レジストリの `LongPathsEnabled` が既定で 0 のため、通常の Windows では効かない
  - ページ描画の後に必ず論文リストを書き戻す処理（`config_page` の後処理）は保存に不要で、壊れた `list.json` を `{}` で上書きするだけでなく、CLI からの復元など外部の変更も古い内容で上書きする。論文リストの変更はすべて `save_paper_list` を通るため、後処理の書き戻しは削除できる
  - KB7 の範囲（接頭辞 198〜199 文字）で作られた 201〜255 文字のフォルダ名は、ASCII なら実際に作成できていた。名前の修正と後方互換を両立するため、修正前の規則の名前を計算し、そのフォルダがあれば新しい名前へ移す（利用者の判断で要件 2.6 / 2.7 を更新）

## Research Log

### Windows の長いパス（要件 1）
- **Context**: 利用者は Windows で `PAPER_MANAGER_DATA_DIR` を長いパスに設定しており、一覧・詳細に PDF が出ず、warning ログが出ていた
- **Sources Consulted**: Microsoft Learn「Maximum Path Length Limitation」「Naming Files, Paths, and Namespaces」、Jeremy Kuhne「Path Normalization」、Python ドキュメント「Using Python on Windows（Removing the MAX_PATH limitation）」、CPython の `ntpath.py`（3.9 / 3.13）と `Python/fileutils.c`（3.11）、`shutil.py`（3.12）、conda-forge の python.exe の manifest
- **Findings**:
  - `\\?\` は絶対パスのみで有効。区切りは `\` だけで、`.` と `..` は解釈されない（リテラル名になる）。UNC は `\\?\UNC\server\share\...`
  - 通常の Windows（`LongPathsEnabled=0`）では、prefix なしで 260 文字（ディレクトリ作成は 248 文字）を超えるパスは失敗する。`exists()` / `is_dir()` は例外ではなく False を返す
  - `open` / `os.stat` / `mkdir` / `rename` / `replace` / `scandir` / `shutil.rmtree` / `shutil.copy2` / `Path.glob` / `Path.iterdir` / `rglob` は `\\?\` 付きの絶対パスを受け付ける。ルートに付ければ、そこから得た子のパスにも付く
  - `zipfile.ZipFile.write()` は arcname を省略すると `\\?\C:` を落として絶対パスを arcname にする。arcname は必ず明示する
  - prefix の有無で `drive` が異なるため、`relative_to` は ValueError、`==` は False になる。比較は prefix をそろえた空間で行う必要がある
  - `os.path.normpath` は 3.9 / 3.10 では `\\?\` 付きのパスを変更しないが、3.11 以降は prefix の後ろを正規化する。変換は prefix を付ける前に `abspath` で正規化してから行う
  - Explorer は 260 文字を超えるパスを開けない・削除できない場合がある（利用者への注意事項）
- **Implications**: 変換は 1 つの関数に集約し、ファイル操作の起点（PDF ルート、エントリの PDF フォルダ、旧形式 PDF、バックアップ・復元の起点）でだけ適用する。比較は変換後の空間で行い、画面とログへの表示は prefix を外す。Windows では長さにかかわらず常に変換して、パスの形を一様にする

### PDF フォルダ名の生成（要件 2）
- **Context**: KB2a / KB2b / KB5 / KB7 の修正と、既存フォルダとの互換
- **Findings**（49,500 通りの入力で網羅確認）:
  - 200 文字を超えるのは、接頭辞 `<year> - <first_author> - ` が 198 または 199 文字でタイトルが 4 文字以上のとき（201〜497 文字）と、年が 200 文字を超えるときだけ
  - 「既存の処理の後、まだ 200 文字を超える場合だけ `<year> - <first_author>` に切り替える（それも超えれば年を切り詰める）」とすれば、現在 200 文字以内の全入力で出力が完全に同一のまま、超える入力だけが 200 文字以内になる
  - KB7 の範囲のうちタイトルが短い ASCII の名前（201〜255 文字）は、各 OS で作成できていた。名前を変えると、そのフォルダは孤立 PDF として扱われる
  - KB2a: `split("")` が空のタプルを返して `[0]` が IndexError。KB2b: 旧形式 PDF の名前 `pdf_filename` が `self[COLNAME_TITLE]` を直接参照する
  - KB5: Windows の置換規則に `"` を加えても、Windows では `"` を含むフォルダは作成できなかったので既存フォルダに影響しない
- **Implications**: 名前の修正は「超える場合だけ別の名前にする」形で行い、修正前の規則の名前（KB7 の範囲だけ異なる）を計算できるようにして、フォルダの移行と孤立判定に使う

### 大文字小文字・Unicode 正規化（要件 3）
- **Sources Consulted**: Apple APFS FAQ、Microsoft Learn（`FsRtlAreNamesEqual`）、macOS での実測
- **Findings**:
  - NTFS は大文字小文字を区別しない（`$UpCase` 表による比較）が、Unicode 正規化はしない。APFS は大文字小文字と正規化の両方を区別しない（NFC と NFD が同じ名前になる）
  - `os.rename` による大文字小文字だけの改名は APFS と NTFS で動作するが、FAT / exFAT や一部のネットワークファイルシステムは黙って無視することがある。大文字小文字を区別しない FS では `new.exists()` が真になる
  - POSIX では空のディレクトリへの rename は黙って上書きになる
- **Implications**: 重複判定の比較キーは「NFD → casefold → NFD」（Unicode の canonical caseless match）とする。`casefold` は NTFS より広く一致させる（例: `ß` と `ss`）ので、同じとみなす側（安全側）に倒れる。大文字小文字だけの改名は一時名を経由する 2 段階で行う

### 手動入力フォーム（要件 4、7）
- **Findings**:
  - KB4a: 著者を ` and ` で連結した直後に `, ` で上書きしている。KB4b: タグの既定値を ` and ` で分割している
  - 既定値の `split(" and ")` と保存時の `" and ".join` は往復で元の文字列に戻る（Streamlit 1.50 / 1.66 で確認）。既存の `, ` 区切りの著者は選択肢 1 つとしてそのまま保存される（4.4 は追加のコードなしで満たせる）
  - 年: `int(default)` が `n.d.` などで ValueError、範囲外は number_input が例外を出す。全角数字などは `int()` を通るため、往復（`str(int(v)) == v`）と範囲で有効性を判定する必要がある
  - 保存された年が無効なときだけ、警告と `st.text_input(key="year")` に切り替える案は、1.50 / 1.66 で 4 つの値すべてで例外が出ず、既存のテストもそのまま通る
  - 編集ダイアログは Done のあと必ず `st.rerun()` するため、更新のエラーが表示されずに消える
- **Implications**: 年のウィジェットは「有効なら number_input、無効なら警告 + text_input」にする。編集ダイアログは更新が成功したときだけ rerun する

### BibTeX（要件 5）
- **Sources Consulted**: pybtex 0.26.1（インストール済み）を実行して確認
- **Findings**:
  - `Person` は `first_names` / `middle_names` / `prelast_names` / `last_names` / `lineage_names` を持つ。`bibtex_first_names` は first と middle、`str(person)` は BibTeX の正規形（`von Last, Jr, First`）
  - `van der Waals, Johannes Diderik` は first=[Johannes]、middle=[Diderik]、prelast=[van, der]、last=[Waals]。`King, Jr., Martin Luther` は lineage=[Jr.]
  - `, Jane` は last が空で、現在の `last_names[0]` は IndexError
  - 構文エラーはすべて `pybtex.exceptions.PybtexError` の派生（`PybtexSyntaxError`、`PrematureEOF`、`TokenRequired`、`DuplicateField`、`BibliographyDataError`、`InvalidNameString`）。UTF-8 でないファイルは `UnicodeDecodeError`
  - 著者なしで登録できるようにするには、KB2c と KB2a を同時に直す必要がある。fields.json で著者が任意なのは misc だけ
- **Implications**: 名は「first + middle」、姓は「prelast + last」を空白でつなぎ、`<名> <姓>` とする（単純な `First Last` は現状と同じ出力）。lineage があるときは BibTeX の正規形を使う（エクスポート時の再パースで姓と名が入れ替わらない）。著者が 1 人もいなければ `author` キーを作らない

### DOI（要件 6）
- **Sources Consulted**: Crossref REST API の swagger と api_format.md、book / proceedings-article 各 100 件の実データ、crossrefapi 1.6.0 の `restful.py`、requests 2.34 の例外階層
- **Findings**:
  - `Works().doi()` は `requests.get(timeout=30)` だけで、404 なら None、それ以外は status を見ずに `result.json()["message"]` を返す。接続失敗・タイムアウトは `requests.exceptions.ConnectionError` / `Timeout`、JSON でない応答は `requests.exceptions.JSONDecodeError`（いずれも `RequestException` の派生）、JSON に `message` がなければ `KeyError`
  - book: `publisher` は常にある。`container-title` はシリーズ名か空リスト。著者は 63% にしかなく、編者だけのものがある
  - proceedings-article: `container-title` に論文集名、`event.name` に会議名がある
  - 年は `issued` が最も確実だが `[[None]]` のことがある。`issued` → `published-print` → `published-online` → `published` の順に探す
  - 団体著者は `given` / `family` ではなく `name` を持つ
- **Implications**: 応答の読み取りはキーの欠落で例外を出さない形にし、項目がなければキーごと省いて、必須項目の判定は既存の検証に任せる。通信エラーは `requests.exceptions.RequestException` と `KeyError` で捕捉する（`requests` は crossrefapi と Streamlit の依存として常に存在する）

### 復元・論文リスト・CLI（要件 9、10、11）
- **Findings**:
  - 画面側の `restore_from_zip`（`pages/_restore.py`）は helper の複製で、違いは session state の更新だけ。helper の `update_session_state=True` で置き換えられる
  - 現在の復元は、PDF の書き込みより前に `list.json` と `config.json` を書く。KB3 の zip にも `list.json` が入っているため、9.2（中止してデータを変えない）を満たすには、書き込みの前に全メンバー名を検証する 2 段構成が必要
  - 正当なバックアップのメンバー名は `version.json` / `list.json` / `config.json` / `pdf/**.pdf`（`/` 区切り。POSIX では `\` を含むフォルダ名もありうる）。成分が `..` に等しいか、絶対パス・ドライブ指定かで判定し、`resolve()` は使わない（利用者が `pdf/` を別ドライブへのシンボリックリンクにしていても正当な復元を拒否しないため）
  - `PaperList.from_file` は `json.JSONDecodeError` しか捕捉せず、`UnicodeDecodeError`・オブジェクトでない JSON・文字列でない値で例外になる
  - CLI: `main()` はナビゲーションの指定を全サブコマンドに付け、さらに `-o` / `backup_file` / `--force` を消費して `backup()` / `restore()` に渡さない（KB1 の 2 層）。`backup()` / `restore()` を「引数のリストを解析して本体を呼ぶ」ラッパーとして残せば、テストの呼び方（約 40 件）を変えずに済む
- **Implications**: 復元は helper に一本化し、メンバー名の検証を書き込みの前に行う。論文リストの読み込みは「状態（正常・ファイルなし・壊れている）」を返す純粋な関数にし、保存の直前に実ファイルを再判定して壊れていれば退避する。CLI は解析済みの引数を本体に渡す

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| 起点だけを変換（採用） | `to_fs_path` を PDF ルートなど約 8 か所の起点で呼び、派生パスは自動的に prefix 付きになる | 呼び出し箇所が少ない。比較は変換後の空間で一貫する | 表示の前に prefix を外す必要がある。DIRPATH_PDF と比べる 2 か所を変換後の値と比べるように直す | 変換関数の内部をテストで差し替えられるようにする |
| ファイル操作ごとに変換 | 論理パスのまま持ち、`open` などの直前で毎回変換する | 表示・比較は常に prefix なし | 約 25 か所に散らばり、漏れやすい。glob の結果を論理パスに戻す処理も要る | 不採用 |
| 定数そのものを変換 | `_constants.py` の `DIRPATH_DATA` を Windows では prefix 付きにする | 変更が 1 か所 | テストの隔離の検査（一時ルートとの比較）が Windows で壊れる。import 時に値が固定され、テストで差し替えにくい | 不採用 |
| フォルダ名を短くする | 保存先の長さに応じてフォルダ名を短くする | Explorer でも開ける | 保存先を移すと名前が変わり、既存フォルダが見つからなくなる | 利用者が「名前は変えない」を選択したため不採用 |

## Design Decisions

### Decision: Windows では常に拡張長のパスに変換する
- **Context**: 要件 1.1〜1.5。prefix の有無が混在すると比較と `relative_to` が壊れる
- **Alternatives Considered**:
  1. 248 文字以上のときだけ変換する — 短いパスと長いパスで形が変わり、混在のバグが起きる
  2. 常に変換する（採用）
- **Selected Approach**: `os.name == "nt"` のとき、`os.path.abspath` で正規化してから `\\?\`（UNC は `\\?\UNC\`）を付ける。既に付いていればそのまま返す。Windows 以外では `Path` をそのまま返す
- **Rationale**: 形を一様にすれば、比較・`relative_to` が変換後の空間で常に成り立つ
- **Trade-offs**: 画面とログに出すパスは prefix を外す必要がある
- **Follow-up**: CI（Linux）では本物の Windows の I/O を試せないため、利用者の Windows 環境で手動確認する

### Decision: 変換関数の内部に差し替え口を置く
- **Context**: 要件 12.3（Windows に固有の振る舞いを CI の Linux で検証する）
- **Selected Approach**: `to_fs_path` / `to_display_path` は、モジュール内の `_convert` / `_unconvert` を呼び出し時に参照する。テストは `_convert` を「`//` を前に付ける」別名変換に差し替える。Linux と macOS では `//x` は `/x` と同じ場所を指すが、pathlib では別のルートとして扱われるため、prefix の混在による比較・`relative_to` の不具合をそのまま再現できる
- **Rationale**: 呼び出し側は `from paper_manager.helper import to_fs_path` で名前を束縛しても、差し替えが効く
- **Trade-offs**: 本物の `\\?\` の I/O は検証できない。純粋な文字列変換は `ntpath` でどの OS でもテストできる

### Decision: 修正前の規則の名前からフォルダを移す（KB7 の範囲）
- **Context**: 要件 2.1 と 2.6 の衝突。利用者は「新しい名前へ自動で移す」を選択した
- **Selected Approach**: 修正前の規則の名前（`legacy_pdf_dir_name`）を計算する。修正後の名前と異なり、その名前のフォルダがあり、修正後の名前のフォルダがなければ、`get_pdf_files` が rename で移す。孤立 PDF の判定でも、修正前の名前を有効な名前として扱う
- **Rationale**: 旧形式の単一 PDF の移行と同じ考え方で、既存の仕組みに沿う
- **Trade-offs**: 修正前の規則のコードを残す必要がある（KB7 の範囲だけで使う。後方互換コードとして明示する）

### Decision: ページ描画後の論文リストの書き戻しを削除する
- **Context**: 要件 10.2。書き戻しは壊れたファイルを `{}` で上書きし、外部の変更も古い内容で上書きする
- **Alternatives Considered**:
  1. 壊れていないときだけ書き戻す — 外部の変更を上書きする問題が残る
  2. 書き戻しを削除する（採用）
- **Selected Approach**: `config_page` は描画前の読み込みだけを行う。保存は `save_paper_list` に一本化する
- **Rationale**: 論文リストの変更はすべて `save_paper_list` を通ることを確認済み（登録・更新・削除）。steering の「永続化の単一経路」にも沿う
- **Trade-offs**: 空のデータディレクトリで画面を開いただけでは `list.json` が作られなくなる（読み込み側はファイルがなくても空として扱う）

### Decision: 壊れた論文リストは保存の直前に再判定して退避する
- **Context**: 要件 10.3
- **Alternatives Considered**:
  1. session state に「壊れている」フラグを持つ — 外部でファイルが壊れた場合や、session の再読み込みのタイミングで古くなる
  2. 保存の直前に実ファイルを再判定する（採用）
- **Selected Approach**: `save_paper_list` が書き込みの前に `load_paper_list` で状態を確かめ、壊れていれば `list.json.broken-<YYYYmmdd_HHMMSS>`（衝突時は連番）へ `copy2` で退避してから書く。退避先は画面に表示し、rerun で消えないよう session state に通知として残して、次の描画で `config_page` が表示する
- **Rationale**: 状態を持たないので古くならない。`copy2` なら書き込みに失敗しても元のファイルが残る
- **Trade-offs**: 名前を `list.json.backup` にすると復元のたびに上書きされるため、別の名前にする

### Decision: 復元を helper に一本化し、書き込み前に検証する
- **Context**: 要件 9.1〜9.3
- **Selected Approach**: 画面は helper の `restore_from_zip(..., update_session_state=True)` を呼ぶ。helper は zip を開いた直後に全メンバー名を検証し、1 つでも不正なら何も書かずに `(False, message)` を返す
- **Rationale**: 実装が 1 つになり、9.3（画面と CLI の両方）が自動的に満たされる

### Decision: CLI は解析済みの引数を本体に渡す
- **Context**: 要件 11（KB1 の 2 層、KB8、KB9）
- **Selected Approach**: backup / restore の引数定義を共通の関数にまとめ、`main()` のサブパーサーと `backup()` / `restore()` の単独のパーサーで共用する。本体は `_run_backup(output)` / `_run_restore(backup_file, force)`。ナビゲーションの既定の指定は `run` のときだけ、明示指定（`=` 形式を含む）がなければ付ける
- **Rationale**: テストの呼び方（`cli_main.backup([...])`）を変えずに、`main()` 経由でも動く

### Decision: 引用キーから BibTeX の引用キーとして読めない文字を除く（実装中の追加）
- **Context**: 2.4 の実装で、接尾辞付きの筆頭著者（`King, Jr., Martin`）の引用キーに `,` が入り、詳細の BibTeX 表示とエクスポートが pybtex の解析（`TokenRequired: '=' expected`）で落ちることがわかった。手入力の `, ` 区切りの著者（KB4a で保存されたもの）の既存の論文でも、同じ理由で既に落ちていた。利用者の判断で、新しいキーと既存のキーの両方を直すことにした（要件 13 を追加）
- **Alternatives Considered**:
  1. 接尾辞の形を変える — `,` なしの形では、再解析で姓と名が入れ替わる（2.4 の要件に反する）
  2. 新しいキーだけ直す — 既存の手入力の論文は落ちたまま
  3. 新しいキーを作るときに除き、表示・エクスポートでも既存のキーを整える（採用）
- **Selected Approach**: pybtex が引用キーの区切りとして読む文字（空白、`,`、`}`）と `{` を除く。それ以外の文字は変えず、今読めているキーを変えない。表示・エクスポートは Entry の複製の ID だけを整え、`list.json` の ID は変えない。まとめてのエクスポートで大文字小文字を区別せずに重複したら連番を付ける
- **Trade-offs**: 除く文字を最小限にしたため、`\` や `"` などの LaTeX で扱いにくい文字はキーに残る（今読めているキーを変えないことを優先）

### Decision: BOM 付きの論文リストのファイルを正常として読む（実装中の追加）
- **Context**: 2.3 のレビューで、BOM 付きの UTF-8（古いメモ帳や PowerShell 5 の `Out-File -Encoding utf8` で手編集したもの）が壊れていると判定され、5.1 の後は保存のたびに退避されて空のリストから書き直されることがわかった。利用者の判断で、正常として読むことにした（要件 10.4 を追加）
- **Selected Approach**: 復号を `utf-8-sig` にする。保存は従来どおり BOM なしで書く

## Synthesis
- **Generalization**:
  - 「Windows で長いパスでも扱える」は、PDF の保存・一覧・削除・エクスポート・孤立判定・バックアップ・復元のすべてに共通する。変換関数 1 つに集約し、各機能は起点で呼ぶだけにする
  - 「大文字小文字だけ違う」「Unicode 正規化だけ違う」「修正前の規則の名前」は、どれも「フォルダ名が同じかどうか」の判定。比較キー `folder_name_key` に集約し、登録・更新の重複判定と孤立判定で使う
  - 「画面を例外で落とさずにエラーを 1 つ表示する」（KB6、KB10、DOI の欠けた項目）は、登録ページの各経路の「入力を Entry にする」段階のエラー処理として揃える
- **Build vs. Adopt**:
  - Windows の長いパス: OS 標準の拡張長のパス（`\\?\`）を採用する。サードパーティのライブラリは使わない
  - 大文字小文字の比較: 標準ライブラリの `unicodedata` と `str.casefold` を採用する
  - BibTeX の人名: pybtex の `Person` の分割結果をそのまま使う（自前でパースしない）
  - zip のパス検証: 標準ライブラリの `PurePosixPath` / `PureWindowsPath` で判定する
- **Simplification**:
  - 論文リストの「壊れている」状態は session に持たず、保存の直前に再判定する
  - 画面側の復元処理は削除して helper を呼ぶ
  - `config_page` の書き戻しを削除する（壊れているときだけ書かない、という条件分岐を足さない）
  - DOI の空の任意項目、OS をまたいだ置換規則の違い、Linux の 255 バイトの上限は扱わない（要件の範囲外）

## Risks & Mitigations
- 本物の Windows の I/O を CI で検証できない — 純粋な変換は `ntpath` で、配線は別名変換で検証し、利用者の Windows 環境で手動確認する（タスクに含める）
- `\\?\` のパスは正規化されないため、末尾の `.` や空白、予約名がそのまま使われる — フォルダ名は既に末尾の `.` と空白を除いている。予約名は範囲外
- 260 文字を超えるパスは Explorer で開けない場合がある — 利用者は承知のうえで「超えても保存・表示できる」を選択した。README か画面の説明に注意を書く
- 大文字小文字だけの改名を黙って無視するファイルシステムがある — 一時名を経由する 2 段階の改名にし、失敗時は元に戻す
- 修正前の規則のフォルダを移す途中で失敗する — rename は 1 回の操作で、失敗したら元の名前のまま残す。孤立判定は修正前の名前も有効とみなすので、削除されない
- `config_page` の書き戻しをやめると、空のデータディレクトリで `list.json` が作られなくなる — 読み込みはファイルがなくても空として扱うので、利用者への影響はない

## References
- [Maximum Path Length Limitation](https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation) — `\\?\` と LongPathsEnabled
- [Naming Files, Paths, and Namespaces](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file) — 拡張長のパスの規則
- [Path Normalization](https://learn.microsoft.com/en-us/archive/blogs/jeremykuhne/path-normalization) — `\\?\` が正規化を飛ばすこと
- [Using Python on Windows](https://docs.python.org/3/using/windows.html) — Removing the MAX_PATH limitation
- [glob.escape](https://docs.python.org/3/library/glob.html) — `\\?\C:` のドライブ部分は literal
- [Crossref REST API swagger](https://api.crossref.org/swagger-docs) / [api_format.md](https://github.com/CrossRef/rest-api-doc/blob/master/api_format.md) — work メタデータの項目
- [APFS FAQ](https://developer.apple.com/library/archive/documentation/FileManagement/Conceptual/APFS_Guide/FAQ/FAQ.html) — 大文字小文字と正規化を区別しない
- [FsRtlAreNamesEqual](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/nf-ntifs-_fsrtl_advanced_fcb_header-fsrtlarenamesequal) — NTFS の名前の比較
