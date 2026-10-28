# Research & Design Decisions

## Summary
- **Feature**: `test-suite`
- **Discovery Scope**: Extension（既存アプリへのテスト基盤の追加。プロダクションコードは変更しない）
- **Key Findings**:
  - Python 3.9 の CI ではインストールできる Streamlit が 1.50.0 まで（1.51 以降は `>=3.10`、1.66 以降は `>=3.11`）。`AppTest` のファイルアップロード操作（`at.file_uploader`）は 1.56 以降にしかないため、アップロードを伴うテストには使えない
  - サブページを `AppTest.from_file("pages/_x.py")` で直接開くと、`config_page` の `st.page_link` が `StreamlitPageNotFoundError` になる。トップページ（`_list.py`）を起点にして `at.switch_page("pages/_x.py")` で移ると、全ページが正しく描画される
  - データパスは import 時に確定し、各モジュールが値を束縛している。テストごとに別ディレクトリへ差し替えるのは壊れやすいため、セッション共通の一時データディレクトリを import 前に環境変数で指定し、テストごとに中身を空にする方式を採る

## Research Log

### データパスの隔離方法
- **Context**: 要件 1.1–1.4。`_constants.py` は import 時に `PAPER_MANAGER_DATA_DIR` を読み、`entry/_entry.py`・`app/helper/_backup.py`・`app/helper/_proxy.py`・`app/helper/_orphan_pdf.py`・`app/components/_common.py`・各ページが `from paper_manager._constants import ...` で値を束縛している
- **Sources Consulted**: `src/paper_manager/_constants.py`、各モジュールの import 文、steering `tech.md`（「テストでは環境変数を import 前に設定するのが確実」）
- **Findings**:
  - pytest は `tests/conftest.py` をテストモジュールより先に import する。conftest のモジュールレベルで環境変数を設定すれば、`paper_manager` の初回 import より前になる
  - 束縛済みの定数を差し替えるには、利用側モジュールを 6 か所以上 monkeypatch する必要がある。AppTest がページスクリプトを実行するたびに改めて import される定数もある
  - `PaperList.__init__` は `DIRPATH_PDF` を `os.makedirs` で作成する（副作用）
- **Implications**: 一時ディレクトリはセッション共通で 1 つにし、各テストの前に中身を消して作り直す。テストどうしの独立性（1.4）はこの初期化で保証する

### AppTest でのページ実行
- **Context**: 要件 11、12
- **Sources Consulted**: 手元の環境（Python 3.12.16、Streamlit 1.66.1）での実験、`streamlit/testing/v1/element_tree.py`
- **Findings**:
  - `AppTest.from_file(<app>/_list.py)` → `at.switch_page("pages/_register.py")` → `at.run()` で、5 ページすべてが例外なく描画される。タイトル `PAPER MANAGER` とページリンク（`at.get("page_link")`）5 件も取得できる
  - サブページの `from_file` 直接実行は `StreamlitPageNotFoundError` になる（`st.page_link` はエントリポイントからの相対パスで解決されるため）
  - AppTest は同じプロセス内でスクリプトを実行する。そのため `monkeypatch` で差し替えた `crossref.restful.Works.doi` や `streamlit.file_uploader` が、ページスクリプトにもそのまま効く
  - DOI 経路: `Works.doi` を固定辞書を返す関数に差し替えると、登録が成功して `list.json` にエントリが追加された
  - PDF 添付: `streamlit.file_uploader` を、ラベルが `PDF` で始まるときだけ `BytesIO`（`name` 属性付き）のリストを返す関数に差し替えると、`<pdf_dir_name>/00.pdf` が保存された
- **Implications**: ページ実行は「`_list.py` を起点に `switch_page`」を共通ヘルパーにまとめる。アップロードは AppTest の機能ではなく `streamlit.file_uploader` の差し替えで扱う

### Streamlit のバージョンと Python のサポート範囲
- **Context**: 要件 14.1（CI の Python 3.9–3.13 で成功）
- **Sources Consulted**: PyPI JSON API（`https://pypi.org/pypi/streamlit/json`）、GitHub の各タグの `lib/streamlit/testing/v1/element_tree.py`
- **Findings**:
  - 1.50.0: `!=3.9.7,>=3.9` / 1.51.0–1.65.0: `>=3.10` / 1.66.0 以降: `>=3.11`
  - `class FileUploader`（AppTest 要素）は 1.55.0 にはなく、1.56.0 から存在する
  - CI の Python 3.9 では Streamlit 1.50.0、Python 3.10 では 1.65.x、Python 3.11 以降では最新版が入る
- **Implications**: 1.56 以降にしかない AppTest の API は使わない。`switch_page`、`from_function`、`at.get(...)`、`session_state` は 1.50 にも存在する前提だが、手元では 1.66.1 でしか確認できていない（Risks を参照）

### pytest の xfail と終了処理
- **Context**: 要件 13.1、13.3、CLI の既知バグ（`SystemExit(2)`）
- **Sources Consulted**: 手元の pytest 9.1.1 での実験
- **Findings**: `xfail(strict=True)` の付いたテストが `SystemExit` を送出すると `xfailed` になる。テストが成功すると `XPASS(strict)` で失敗する
- **Implications**: CLI の既知バグは `main([...])` を直接呼ぶテストとして記録できる。`pyproject.toml` に `xfail_strict = true` を設定すれば、マーカーごとに `strict` を書き忘れても strict になる

### Streamlit の bare mode
- **Context**: 要件 3.3、3.4、9 の単体テストを、AppTest なしで実行できるか
- **Findings**:
  - `streamlit run` 外（bare mode）でも、`st.session_state` への書き込みと読み出しは動作する
  - bare mode の `st.error` / `st.warning` / `st.success` は何もしない。表示内容を検証するには差し替えて記録する必要がある
  - bare mode の `st.session_state` はプロセス内で持続するため、テスト間で状態が残りうる
- **Implications**: UI 通知は `streamlit.error` などを記録関数に差し替えて検証する。テストごとに bare mode の session state を消す

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| A: セッション共通ディレクトリ + テストごとに中身を初期化 | conftest の import 時に環境変数を設定し、autouse fixture で毎回中身を消す | プロダクションコードを変更しない。束縛済みの定数に影響されない | テストの並列実行（xdist）には対応しない | 採用 |
| B: テストごとに tmp_path を割り当てて定数を monkeypatch | 利用側モジュールの属性をすべて差し替える | テストごとに完全に別のディレクトリになる | 差し替え漏れが実データ汚染に直結する。AppTest の再 import で効かない場合がある | 不採用 |
| C: `_constants` を実行時に解決する形へリファクタリング | パスを関数で取得するよう変更する | 最も素直 | プロダクションコードの変更であり、本 spec の対象外 | 将来の候補 |

## Design Decisions

### Decision: 隔離は conftest の import 時設定と、テストごとの初期化で行う
- **Context**: 1.1–1.7、14.2
- **Alternatives Considered**: 上表の A / B / C
- **Selected Approach**: A。環境変数は conftest がモジュールレベルで設定する。ガードは、`paper_manager` がまだ import されていないことと、確定した `DIRPATH_DATA` が一時ルート配下にあることを確認する。autouse fixture は、データディレクトリの初期化、作業ディレクトリの移動、環境変数・ロガーレベル・bare mode の session state の復元、外部ネットワークの遮断を行う
- **Rationale**: プロダクションコードを変更せずに、確実に隔離できる
- **Trade-offs**: 並列実行はできない（現状は不要）
- **Follow-up**: 既知バグの修正 spec で `_constants` を変える場合は、隔離の前提を見直す

### Decision: アップロードは `streamlit.file_uploader` の差し替えで扱う
- **Context**: 9.12、12.2、12.9、14.1
- **Alternatives Considered**:
  1. AppTest の `file_uploader` 要素 — 1.56 以降が必要で、Python 3.9 の CI（1.50）では使えない
  2. Python 3.9 だけ skip する — 3.9 では検証されなくなり、14.1 の趣旨に反する
  3. `streamlit.file_uploader` をラベルに応じて差し替える — 全バージョンで動く
- **Selected Approach**: 3。ラベルの接頭辞に応じて `FakeUpload`（`io.BytesIO` のサブクラスで `name` 属性を持つ）のリストまたは単体を返し、一致しないラベルは元の関数に委ねる
- **Trade-offs**: ファイルアップロードウィジェット自体の描画と操作は検証しない（Streamlit の責務）

### Decision: 既知バグはドメインごとのテストファイルに置き、理由に共通の接頭辞を付ける
- **Context**: 13.1–13.4
- **Alternatives Considered**: 1) `test_known_bugs.py` に集約する 2) 対象ドメインのテストファイルに置く
- **Selected Approach**: 2。`reason` は `"KNOWN BUG: "` で始め、不具合内容と再現条件を書く。`grep "KNOWN BUG"` で一覧できる
- **Rationale**: 修正 spec は xfail マーカーを外すだけで、通常のテストへ移せる

### Decision: UI 通知の検証は、bare mode では記録関数、ページでは AppTest の要素で行う
- **Context**: 3.3、9.3、9.7、9.10、12.x
- **Selected Approach**: 単体テストでは `st_messages` fixture が `streamlit.error/warning/success` を記録関数に差し替える。AppTest を使うテストでは `at.error` などの要素を参照する。この 2 つは同じテストで併用しない（AppTest は同じプロセスで動くため、差し替えが要素の生成を妨げる）

## Synthesis Outcomes
- **Generalization**: 5 ページのスモークテストと登録フローは、どちらも「トップページを起点にページを移って実行する」に集約できる。`run_page(page)` という 1 つのヘルパーにした
- **Build vs. Adopt**: ネットワーク遮断（pytest-socket など）、ファイル用の fake（pyfakefs など）、モック用プラグイン（pytest-mock）は採用しない。標準の `monkeypatch` と `socket` の差し替えで足り、依存を増やさずに済む（14.4）
- **Simplification**: テスト用の補助モジュールは作らず、隔離と共有 fixture は `tests/conftest.py` の 1 ファイルにまとめる。既存の `tests/test_app.py` はスモークテストに包含されるため削除する

## Risks & Mitigations
- Streamlit 1.50（Python 3.9 の CI）での AppTest の挙動は、手元では確認できていない — CI の Python 3.9 ジョブで検証する。失敗した場合は、そのバージョンでの差異を design に追記して対処する。手元で検証するには Python 3.9 の環境が必要（作成には承認が必要）
- bare mode の session state がテスト間で残る — autouse fixture でテストごとに消す
- 隔離ガードの誤判定でテストが全停止する — ガードを純粋関数（`check_isolation`）として切り出し、単体テストで検証する
- 新しく判明したバグで xfail が増え続ける — 理由の接頭辞で一覧化し、修正 spec へ引き継ぐ

## References
- [PyPI: streamlit](https://pypi.org/project/streamlit/) — バージョンごとの `requires_python`
- [Streamlit App Testing](https://docs.streamlit.io/develop/api-reference/app-testing) — `AppTest.from_file` / `switch_page` / `from_function`
- [pytest: xfail](https://docs.pytest.org/en/stable/how-to/skipping.html#xfail-mark-test-functions-as-expected-to-fail) — `strict` と `xfail_strict`

---

# Gap Analysis（2026-10-27）

## Analysis Summary
- 今回の対象は **テストの新規追加だけ** で、プロダクションコードの変更は要件で禁止されている。課題は、隔離の仕組みと、Streamlit 依存部分をテストから観測する方法の 2 点に集中する
- 最大の制約は、データパスが **import 時に確定し、利用側モジュールに束縛される** こと。テストの隔離は、`paper_manager` の初回 import より前に環境変数を設定する以外に確実な方法がない
- 2 つ目の制約は **Python 3.9 の CI で使える Streamlit が 1.50.0 まで** であること。`AppTest` のファイルアップロード操作（1.56 以降）は使えない。`switch_page`・`from_function(args, kwargs)`・`get` は 1.50.0 のソースに存在する
- 全 5 ページは「`_list.py` を起点に `switch_page`」で、空データでもサンプルデータでも例外なく描画でき、`list.json` は変化しない（手元の Python 3.12 / Streamlit 1.66.1 で確認済み）
- 推奨は Option B（ドメインごとの新規テストモジュール + `conftest.py`）。規模は M、リスクは Medium

## 1. Current State Investigation

### 既存のテスト資産
| 資産 | 内容 | 再利用性 |
|------|------|----------|
| `tests/test_app.py` | `AppTest.from_file(_list.py).run()` のみ。assert なし。隔離なし | スモークテストに包含できる |
| `tests/test_import.py` | `pybtex.__version__` の確認 | そのまま残せる |
| `conftest.py` / pytest 設定 | なし（`pyproject.toml` に `[tool.pytest.ini_options]` がない） | 新規作成が必要 |
| CI | `pip install -e .[test]` → `pytest -xs`（ubuntu、Python 3.9–3.13）。環境変数は設定しない | 変更せずに使える |
| `.gitignore` | `.pytest_cache/` と `__pycache__/` は除外済み | — |
| `ruff.toml` | `line-length = 79`、numpy docstring。`tests/` も対象 | テストコードにも適用される |

### テスト対象の観測しやすさ
| 対象 | 観測しやすい点 | 観測を難しくする点 |
|------|----------------|----------------------|
| `entry`（Entry / PaperList） | `base_dir` 引数で保存先を差し替えられる。純粋な振る舞いが多い | `PaperList.__init__` が `DIRPATH_PDF` を作る。`sanitize_filename` が `platform.system()` で分岐する。`get_pdf_files` の旧形式移行が `DeprecationWarning` を出す。`from_file` の警告は `st.warning` |
| `bib`（load_bib） | 入力 → 戻り値の純粋関数 | — |
| `helper` | 純粋関数・デコレータ | — |
| `app.helper._backup` | 戻り値 `(bool, str)` と zip で観測できる | 束縛済みの `FILEPATH_LIST` / `FILEPATH_CONFIG` / `DIRPATH_PDF` / `DIRPATH_DATA` を直接使う |
| `app.helper._proxy` | 戻り値と環境変数で観測できる | `apply_proxy_to_environment` が `os.environ` を書き換える |
| `app.helper._orphan_pdf` | 戻り値で観測できる | 検出処理が `get_pdf_files` 経由で移行（改名）の副作用を持つ |
| `app.helper._config` | `MAP_FIELDS` / `MAP_REQUIRED_FIELDS` / `entrytype4doi` | import 時に `fields.json` を読む |
| `app.components` | 戻り値・ファイル・`PaperList` で観測できる。アップロードは `.getvalue()` だけを使う（`BytesIO` で代替できる） | 成否の通知が `st.error` / `st.success`（bare mode では何もしない）。`custom_entry` / `pdf_upload_form` はウィジェットが必要 |
| `__main__`（CLI） | `main(argv)` / `backup(argv)` / `restore(argv)` を直接呼べる | `run` / `version` が Streamlit の click コマンドを呼ぶ。`backup` の既定の出力先が cwd。`restore` が `input()` を使う。`--debug` がロガーのレベルを変える |
| ページ（5 つ） | AppTest の要素ツリーで観測できる | サブページの直接の `from_file` は `StreamlitPageNotFoundError`。DOI 経路が Crossref に接続する。送信ボタンのラベル `📤 登録` が 3 つ重複する |

## 2. Requirement-to-Asset Map

凡例: **Missing** = テスト側に仕組みがない / **Constraint** = 既存の構造による制約 / **Unknown** = 要調査

| 要件 | 関係する資産 | ギャップ |
|------|--------------|----------|
| 1.1–1.3 実データ保護・ガード | `_constants.py`（import 時に確定） | **Missing**: conftest、ガード。**Constraint**: import 前に環境変数を設定する必要がある。値が 6 か所以上のモジュールに束縛される |
| 1.4 テストの独立性 | `PaperList.__init__`（mkdir）、`config_page`（`list.json` の書き戻し） | **Missing**: テストごとの初期化。**Constraint**: 束縛があるためテストごとの別ディレクトリは扱いにくい |
| 1.5 プロセス状態の復元 | `apply_proxy_to_environment`、`run --debug`、bare mode の `st.session_state` | **Missing**: 復元の仕組み |
| 1.6 作業ツリーを汚さない | `backup` の既定の出力先（cwd） | **Missing**: cwd の移動 |
| 1.7 外部ネットワーク遮断 | `crossref.restful.Works` | **Missing**: 遮断の仕組みと Crossref の代替 |
| 2.x Entry | `entry/_entry.py` | **Constraint**: OS 分岐、`DeprecationWarning`。既知バグ KB2a / KB2b / KB5 |
| 3.x PaperList | 同上 | **Constraint**: `st.warning` を観測する仕組みが必要 |
| 4.x load_bib | `bib/_load.py` | 既知バグ KB2c / KB4c |
| 5.x helper | `helper/_helper.py` | ギャップなし |
| 6.x バックアップ・復元 | `app/helper/_backup.py` | 既知バグ KB3。往復の検証には「データディレクトリを空にしてから復元する」手順が必要 |
| 7.x プロキシ | `app/helper/_proxy.py` | **Constraint**: 開発者の環境のプロキシ変数が結果に影響する（事前に消す必要がある） |
| 8.1–8.5 孤立 PDF | `app/helper/_orphan_pdf.py` | 既知バグ KB4d。**Constraint**: 検出時に移行の副作用がある |
| 8.6–8.7 フィールド定義 | `app/helper/_config.py`、`app/fields.json` | ギャップなし |
| 9.1–9.10 components | `app/components/_common.py` | **Missing**: `st.error` / `st.success` の観測。アップロードの代替（`BytesIO` + `name`） |
| 9.11–9.12 フォーム部品 | 同上 | **Constraint**: ウィジェットの実行には AppTest（`from_function`）が必要。既知バグ KB4a / KB4b |
| 10.x CLI | `__main__.py` | **Missing**: `cli.main_run` / `main_version` / `input` の代替。既知バグ KB1 |
| 11.x スモーク | 全ページ、`config_page` | **Constraint**: `switch_page` 経由で実行する必要がある。描画は確認済み |
| 12.x 登録フロー | `pages/_register.py` | **Constraint**: AppTest のアップロード操作は Python 3.9 の CI で使えない。送信ボタンのラベルが重複する。**Unknown**: 不正な BibTeX テキストを pybtex が例外にするか（12.3） |
| 13.x 既知バグ | pytest | **Missing**: `xfail_strict` の設定。`SystemExit` が xfail として扱われることは確認済み |
| 14.x 実行性 | CI マトリクス | **Constraint**: Python 3.9 / Streamlit 1.50 での AppTest の挙動は、手元では未確認。**Unknown**: `at.get("page_link")` が 1.50 で 5 件を返すか |

## 3. Implementation Approach Options

### Option A: 既存のテストファイルを拡張する
- `tests/test_app.py` に、ページと登録フローのテストを追加する。ドメインのテストは `tests/test_import.py` か、少数の大きなファイルにまとめる
- ✅ ファイル数が少ない
- ❌ 1 ファイルに複数のドメインが混在し、既知バグの修正 spec がどのテストを触るか判断しにくい
- ❌ 隔離の仕組み（conftest）はいずれにせよ新規作成が必要で、「既存の拡張」で済む部分がほとんどない

### Option B: ドメインごとにテストモジュールを新規作成する（+ conftest）
- `tests/conftest.py` に隔離と共有 fixture を置き、`tests/test_<domain>.py` をプロダクションのサブパッケージ・モジュールに 1 対 1 で対応させる。既存の `test_app.py` はスモークテストに包含して削除し、`test_import.py` は残す
- ✅ steering（`tests/` 直下のフラット配置、`test_*.py`）に沿う
- ✅ 既知バグを対象ドメインのファイルに置けるため、修正 spec の作業範囲が明確になる
- ✅ プロダクションコードを変更しない
- ❌ ファイル数が増える（13〜15 個）
- ❌ conftest に隔離と fixture が集中し、肥大化しうる

### Option C: Hybrid（B + テストしやすくするためのプロダクションの小改修）
- B に加えて、`_constants` をパスの実行時解決にする、`st` 通知を差し替えられるようにする、などの改修を行う
- ✅ 隔離が単純になり、テストごとに別ディレクトリを使える
- ❌ 要件の Out of scope（プロダクションコードの修正）に反する。改修は別の spec で扱うべき

### 横断的な選択肢（設計フェーズで決める）
| 論点 | 選択肢 | 補足 |
|------|--------|------|
| データの隔離 | (i) セッション共通ディレクトリを import 前に環境変数で指定し、テストごとに中身を初期化 / (ii) テストごとに束縛済みの定数を monkeypatch / (iii) テストごとにサブプロセス | (i) は単純で確実だが並列実行に向かない。(ii) は差し替え漏れが実データ汚染に直結する。(iii) は遅く、AppTest と相性が悪い |
| アップロードの扱い | (i) `AppTest.file_uploader` を使い、古い Streamlit では skip / (ii) `streamlit.file_uploader` を差し替える / (iii) 画面経由をやめ、components の単体テストだけにする | (i) は Python 3.9 で検証されない。(ii) は全バージョンで動く（確認済み）。(iii) は 12.2 / 12.9 を満たさない |
| UI 通知の観測 | (i) `streamlit.error` などを記録関数に差し替え / (ii) すべて AppTest で実行 | (i) は単体テストが軽い。AppTest との併用はできない |
| ネットワーク遮断 | (i) `socket` の差し替え / (ii) pytest-socket などのプラグイン | (ii) は依存の追加になる（要件 14.4 の趣旨に照らして避けたい） |
| 既知バグの置き場所 | (i) ドメインごとのファイル / (ii) `test_known_bugs.py` に集約 | (i) は修正時にマーカーを外すだけで済む。(ii) は一覧性が高い |

## 4. Implementation Complexity & Risk
- **Effort: M（3–7 日）** — 15 前後のテストモジュールと約 120 件のテスト。既存のパターン（pytest、AppTest）の範囲に収まるが、AppTest による画面操作と隔離の仕組みの作り込みが必要
- **Risk: Medium** — 技術的な前提の大半は手元で確認済み。ただし Python 3.9 / Streamlit 1.50 での AppTest の挙動は CI でしか確かめられない。隔離の誤りは実データの汚染に直結するため、ガードを自己検証する必要がある

## 5. Recommendations for Design Phase
- **Preferred approach**: Option B。データの隔離は (i)、アップロードは (ii)、UI 通知は (i)、ネットワーク遮断は (i)、既知バグの置き場所は (i)
- **Key decisions to make in design**:
  - conftest のモジュールレベルの処理順（`paper_manager` が import 済みかの検査 → 環境変数の設定 → import → ガード）と、ガードを純粋関数として切り出して自己検証する方法
  - テストごとに初期化・復元する状態の一覧（データディレクトリ、cwd、プロキシ環境変数、`os.environ`、ロガーのレベル、bare mode の session state、ネットワーク）
  - ページ実行の共通ヘルパー（`_list.py` → `switch_page`）と、重複する送信ボタンの特定方法
  - 既知バグの記録規約（`xfail_strict = true` と `reason` の接頭辞）と、各既知バグの期待値
- **Research Needed**（design または実装で確認する）:
  - Python 3.9 / Streamlit 1.50 で `switch_page` 後のページ描画と `at.get("page_link")` が手元と同じ結果になるか（手元で確認するには Python 3.9 の新しい環境が必要で、作成には承認が必要）
  - 不正な BibTeX テキストを pybtex が例外にするか、空として返すか（12.3。例外になる場合は新しい既知バグとして記録する）
  - bare mode の `st.session_state` をテスト間で確実に消す方法

---

# Design Phase Updates（2026-10-27）

## Gap Analysis の Research Needed の解消状況
| 項目 | 結果 | design への反映 |
|------|------|-----------------|
| 不正な BibTeX テキストの扱い（12.3） | `hello` のような非 BibTeX テキストは 0 件として返り、ページは「有効なエントリが見つかりませんでした。」と表示する。閉じ括弧のない `@article{k, title={T}` は `pybtex.scanner.PrematureEOF` を送出し、登録ページが例外で落ちる（AppTest で `at.exception` を確認） | 12.3 のテストには `hello` を使う。構文エラーの場合は新しい既知バグ **KB6** として記録する（13.4） |
| bare mode の `st.session_state` の消去 | `st.session_state.clear()` で消える（手元で確認） | IsolationHarness がテストの後に `clear()` を呼ぶ |
| Streamlit 1.50.0 の AppTest の API | `from_function(script, *, default_timeout, args, kwargs)`、`switch_page`、`get`、`Multiselect` / `NumberInput` / `Selectbox` / `TextArea` / `TextInput` / `Button` / `Exception` / `Title` / `Error` / `Warning` / `Success` の各要素が、1.50.0 のソースに存在する | Allowed Dependencies で、使ってよい API をこれらに限定する |
| Python 3.9 / Streamlit 1.50 での実際の挙動 | 未確認（手元にない環境で、作成には承認が必要） | Testing Strategy で、CI の全マトリクスで確認することにする |

## 追加の検証
- サンプルデータ（日本語タイトル、PDF 2 件のエントリ、PDF のないエントリ、孤立 PDF、プロキシ設定）を入れた状態でも、5 ページすべてが例外なく描画され、`list.json` の内容は実行前後で変わらない（手元の Python 3.12 / Streamlit 1.66.1）

## Consulted Guidance
- `kiro-spec-design` の `design-principles.md`、`design-discovery-light.md`、`design-synthesis.md`、`design-review-gate.md` に従った。gap analysis の Option B と、横断的な選択肢の推奨（データの隔離 (i)、アップロード (ii)、UI 通知 (i)、ネットワーク遮断 (i)、既知バグの置き場所 (i)）を、そのまま設計判断として採用した
