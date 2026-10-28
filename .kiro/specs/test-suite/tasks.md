# Implementation Plan

## 全タスク共通の前提
- タスク 2〜5 は共有 fixture（テスト基盤のファイル）を編集しない。そのタスクだけで使う補助は、担当するテストモジュールの中に置く
- 実装中に新しいバグが見つかったら、そのタスクのテストモジュールに、本来あるべき振る舞いを期待する strict な xfail（理由は `KNOWN BUG:` で始め、不具合内容と再現条件を書く）として記録する。プロダクションコード（`src/`）は変更しない。各タスクの完了条件にある xfail の件数は、この追加分だけ増えてよい
- (P) のタスクの検証では、担当するテストモジュールだけを実行する。スイート全体の実行はタスク 6 で行う（作業ツリーを共有する並行作業中に `-x` 付きで全体を実行すると、他のタスクの書きかけのモジュールで止まるため）

- [ ] 1. 基盤: テスト実行設定と隔離・共有 fixture
- [x] 1.1 テスト実行設定と、セッション開始時の実データ隔離を整える
  - テストの探索先を `tests` に限定し、xfail を既定で strict にする設定を追加する（実行コマンドは変えない）
  - テスト基盤が読み込まれた時点で、アプリのパッケージがまだ読み込まれていないことを確かめ、読み込み済みならセッションを止める
  - 実データディレクトリと、実行前にデータ保存先として指定されていたパスを禁止パスとして記録し、セッション専用の一時ディレクトリを保存先に指定してからアプリを読み込む
  - 確定したデータの保存先が一時ディレクトリの配下にあり、禁止パスと重ならないことを検査するガードを、単体で呼べる関数として用意する。違反時はテストを 1 件も実行せずに理由を示して止める
  - セッション終了時に一時ディレクトリを削除する
  - 環境変数を設定せずに `pytest -xs` を実行すると、データの保存先を示す 4 つのパスがすべて一時ディレクトリの配下になり、既存の 2 テストが成功する
  - _Requirements: 1.1, 1.2, 1.3, 13.3, 14.4_

- [x] 1.2 テストごとの初期化と復元を整える
  - 各テストの前に、データディレクトリを空にして作り直し、作業ディレクトリをテスト専用の一時ディレクトリへ移し、プロキシ関連の環境変数を消す
  - 各テストの前に、外部ネットワークへの接続と名前解決を、専用の例外を送出する処理に差し替える
  - 各テストの後に、環境変数全体・ライブラリのログレベル・画面の状態（bare mode の session state）をテスト前の状態に戻す
  - データディレクトリの 4 つのパスを、テストから参照できる fixture として提供する
  - 各テストの開始時点で、データディレクトリが空で存在し、作業ディレクトリがテスト専用の一時ディレクトリになり、プロキシ関連の環境変数がない（恒常的な検証は 1.3 のテストが担う）
  - _Requirements: 1.4, 1.5, 1.6, 1.7, 14.2_

- [x] 1.3 隔離の仕組みを検証するテストを書く
  - ガード関数が、一時ディレクトリの外のパス、実データディレクトリ、実行前に指定されていたパスを拒否し、一時ディレクトリの配下のパスを受け入れることを検証する
  - データの保存先を示す 4 つのパスが、一時ディレクトリの配下にあることを検証する
  - 外部ホストへの接続が専用の例外で失敗することと、作業ディレクトリがテスト専用の一時ディレクトリであることを検証する
  - テストの独立性と状態の復元を、2 つのテストの組で検証する。1 つ目は論文リストを書き込み、環境変数を追加し、ライブラリのログレベルを変える。2 つ目は、データディレクトリが空であること、その環境変数がないこと、ログレベルが元の値であることを検証する（2 つ目は単独で実行しても成功する）
  - 隔離の検証テストがすべて成功する
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_

- [x] 1.4 テストデータを作る共有 fixture を整える
  - 最小限の PDF の中身と、それを指定のパスに書き出す補助を用意する
  - アップロードされたファイルの代わりに使える、ファイル名付きのバイト列オブジェクトを用意する
  - 既定値（記事タイプ、著者、タイトル、掲載誌、年）を持ち、任意のフィールドを上書きできるエントリ生成 fixture を用意する
  - 論文リスト（PDF 2 件の日本語タイトルのエントリ、PDF のないエントリ）、プロキシ設定を含む設定ファイル、孤立 PDF 1 件をデータディレクトリに作るサンプルデータ fixture を用意する
  - サンプルデータ fixture を使うと、データディレクトリに論文リスト・設定・PDF 3 件（うち孤立 1 件）が作られる
  - _Requirements: 2.6, 6.1, 6.4, 8.1, 9.1, 11.2_

- [x] 1.5 外部依存の代替と、画面を実行する共有ヘルパーを整える
  - Crossref の DOI 取得を、指定した固定のメタデータ（または取得失敗）を返す処理に差し替え、問い合わせた DOI を記録する fixture を用意する。固定のメタデータを作る補助は、エントリタイプを指定できるようにする（記事を既定とし、必須項目の不足の検証に使う書籍も作れる）
  - ファイルアップロード欄を、ラベルの接頭辞に応じて指定したファイル（単体・一覧・なし）を返し、一致しないラベルは本来の動作に任せる処理に差し替える fixture を用意する
  - エラー・警告・成功の通知を記録する処理に差し替える fixture を用意する（画面テストとは併用しない）
  - 論文リストページを起点に対象ページへ移って実行する補助と、5 ページの一覧を用意する
  - 補助を使って、論文リストページと 1 つのサブページ（ページの切り替え経由）を実行できる。各代替を有効にすると、DOI 取得・アップロード欄・通知の呼び出しが代替の結果を返す（一時的な実行で確認する。5 ページすべての検証は 5.1、各代替の恒常的な検証は 2〜5 の各タスクが担う）
  - _Requirements: 1.7, 3.3, 9.12, 11.5, 12.2, 12.4, 12.9_

- [ ] 2. ドメイン層の単体テスト
- [x] 2.1 (P) 論文エントリの正規化・識別子・フォルダ名のテストを書く
  - キーの正規化と値の前後の空白除去、識別子の形式・重複回避・欠落時の既定値、フォルダ名の形式と欠落時の既定値、200 文字を超える場合の切り詰めと末尾の扱いを検証する
  - ファイル名の置換規則を、OS の判定を差し替えて Windows 用とそれ以外の両方について検証する
  - 既知バグ（KB2a: 著者のないエントリのフォルダ名、KB5: Windows で `"` が置換されない）を、本来あるべき振る舞いを期待する strict な xfail として、理由（不具合内容と再現条件）付きで記録する
  - 通常のテストは成功し、既知バグのテスト 2 件は xfailed になる
  - _Boundary: test_entry_
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 13.1, 13.2, 14.2_

- [x] 2.2 論文エントリの PDF 一覧と旧形式の移行のテストを書く
  - PDF 一覧が番号順に返ること、PDF がなければ空であること、有無と件数が一覧と一致することを検証する
  - 旧形式の単一 PDF と旧命名規則の PDF が、既存ファイルを上書きせずに新形式へ移行されることを検証する（非推奨警告は許容する）
  - 保存先の基準ディレクトリを指定した場合に、既定の PDF ディレクトリが変化しないことを検証する
  - 既知バグ（KB2b: タイトルのないエントリの PDF 一覧取得）を strict な xfail として記録する
  - 通常のテストは成功し、既知バグのテスト 1 件は xfailed になる
  - 2.1 と同じテストファイルを編集するため、2.1 の後に行う
  - _Requirements: 2.6, 2.7, 2.8, 2.9, 13.1, 13.2_

- [x] 2.3 (P) 論文リストの永続化のテストを書く
  - ファイルへの保存と読み込みの往復で、キー・エントリ内容・非 ASCII 文字が保たれることを検証する
  - ファイルがない場合は空になり、JSON が壊れている場合は空になったうえで警告が通知されることを検証する
  - 画面の状態への書き込みと読み出しの往復、および未設定時に空になることを検証する
  - 論文リストのテストがすべて成功する
  - _Boundary: test_paper_list_
  - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [x] 2.4 (P) BibTeX 読み込みのテストを書く
  - テキスト・バイト列・ファイルパスの 3 つの入力形式で結果が等しいこと、著者の整形とエントリタイプの付与と引用キーの小文字化、複数エントリ、未対応の入力型での型エラーを検証する
  - 既知バグ（KB2c: 著者のない BibTeX、KB4c: 複合姓・ミドルネームの欠落）を strict な xfail として記録する
  - 通常のテストは成功し、既知バグのテスト 2 件は xfailed になる
  - _Boundary: test_bib_
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 13.1, 13.2_

- [x] 2.5 (P) 汎用ユーティリティのテストを書く
  - 文字列の分割、非推奨デコレータ（警告・戻り値・説明文の注記）、インストール有無の判定、引数の有無の判定、進捗表示の代替、ダミー関数を検証する
  - 汎用ユーティリティのテストがすべて成功する
  - _Boundary: test_helper_
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

- [ ] 3. アプリ helper の単体テスト
- [x] 3.1 (P) バックアップ・復元のテストを書く
  - バックアップの内容（バージョン情報・論文リスト・設定・PDF の相対パス）、一部が欠けた状態での作成、出力先の指定の有無による違いを検証する
  - サンプルデータのバックアップを空のデータディレクトリへ復元すると、論文リスト・設定・PDF の構成とバイト列が一致することを検証する
  - 既存ファイルの退避、成功時のメッセージ、不正な zip での失敗とデータ不変、バックアップ情報の取得を検証する
  - 既知バグ（KB3: データディレクトリの外への書き込み）を strict な xfail として記録し、テストの後に到達先のファイルを削除する
  - 通常のテストは成功し、既知バグのテスト 1 件は xfailed になる
  - _Boundary: test_app_backup_
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 13.1, 13.2_

- [x] 3.2 (P) プロキシ設定のテストを書く
  - 設定ファイルの優先、環境変数の大文字・小文字の両方からの取得、HTTPS 未設定時の HTTP へのフォールバック、未設定時の空、壊れた設定ファイルでのフォールバックを検証する
  - 保存時に他の設定項目が保たれること、両方を空にするとプロキシ設定が消えること、環境への適用で 4 つの環境変数が設定されることを検証する
  - プロキシ設定のテストがすべて成功し、テスト後に環境変数が元に戻っている
  - _Boundary: test_app_proxy_
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

- [x] 3.3 (P) 孤立 PDF の検出・削除のテストを書く
  - フォルダ内の孤立 PDF と旧形式の孤立 PDF が検出され、紐づく PDF は検出されないこと、PDF ディレクトリがなければ 0 件であることを検証する
  - 最も近いエントリの類似度の範囲・完全一致・空リストでの該当なしを検証する
  - 削除件数、空になったフォルダの削除、PDF ディレクトリ自体が残ること、一部の削除失敗後も続行することを検証する
  - 既知バグ（KB4d: フォルダを渡したときの削除件数）を strict な xfail として記録する
  - 通常のテストは成功し、既知バグのテスト 1 件は xfailed になる
  - _Boundary: test_app_orphan_pdf_
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 13.1, 13.2_

- [x] 3.4 (P) フィールド定義とエントリタイプ変換のテストを書く
  - 8 種類のエントリタイプが読み込まれ、必須フィールドの集合が定義ファイルを独立に読み込んだ結果と一致すること（misc は必須なし）を検証する
  - Crossref のエントリタイプの変換と、未知のタイプが misc になることを検証する
  - フィールド定義のテストがすべて成功する
  - _Boundary: test_app_fields_
  - _Requirements: 8.6, 8.7_

- [ ] 4. components と CLI の単体テスト
- [x] 4.1 (P) PDF の保存・削除のテストを書く
  - 複数の PDF が連番で保存され既存の番号と重複しないこと、0 件なら何も保存されないこと、パス長の上限を超えるファイルはエラー通知とともに飛ばされ残りは処理されることを検証する
  - 特定ファイルだけの削除と空フォルダの削除、全削除でのフォルダごとの削除と件数、旧形式の単一 PDF の削除を検証する
  - PDF の保存・削除のテストがすべて成功する
  - _Boundary: test_components_
  - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5_

- [x] 4.2 論文の登録・更新のテストを書く
  - 登録時の識別子の付与、論文リストへの追加、添付 PDF の保存、ファイルと画面の状態の両方への反映を検証する
  - フォルダ名が重複する登録の拒否と重複エラーの通知、論文リストが変わらないことを検証する
  - フォルダ名が変わらない更新と変わる更新（フォルダの改名、PDF の保持、識別子の再生成）、重複や改名先の既存による更新の拒否を検証する
  - 登録・更新のテストがすべて成功する
  - 4.1 と同じテストファイルを編集するため、4.1 の後に行う
  - _Requirements: 9.6, 9.7, 9.8, 9.9, 9.10_

- [x] 4.3 入力フォーム部品のテストを書く
  - 手動入力フォームを画面テストで実行し、選んだエントリタイプのフィールドが表示されること、入力値が反映されること、空欄の項目がエントリから除かれることを検証する（年は明示的に入力し、実行日に依存させない）
  - PDF アップロード欄が、未選択では空、単一選択でも複数選択でも一覧を返すことを検証する
  - 既知バグ（KB4a: 著者の区切り、KB4b: タグの既定値の分割）を strict な xfail として記録する
  - 通常のテストは成功し、既知バグのテスト 2 件は xfailed になる
  - 4.2 と同じテストファイルを編集するため、4.2 の後に行う
  - _Requirements: 9.11, 9.12, 13.1, 13.2, 14.2_

- [x] 4.4 (P) CLI の引数処理と run / version のテストを書く
  - バージョン表示、Streamlit を起動しない状態での run / version への引数の受け渡しと起動対象、サイドバーナビゲーション無効化の既定の付与と重複しないこと、デバッグ指定でのログレベルを検証する
  - CLI の引数処理のテストがすべて成功し、テスト後にログレベルが元に戻っている
  - _Boundary: test_cli_
  - _Requirements: 10.1, 10.2, 10.3, 10.4_

- [x] 4.5 CLI の backup / restore のテストを書く
  - 出力先の指定の有無によるバックアップの作成先（省略時は作業ディレクトリに日時入りの名前）を検証する
  - 存在しないファイルの指定、確認入力の有無と内容、復元失敗のそれぞれでの終了コードとデータの状態を検証する
  - 既知バグ（KB1: コマンド全体の引数解析を経由した backup / restore の失敗）を strict な xfail として記録する
  - 通常のテストは成功し、既知バグのテスト 2 件は xfailed になり、リポジトリの作業ツリーにバックアップ zip が作られない
  - 4.4 と同じテストファイルを編集するため、4.4 の後に行う
  - _Requirements: 10.5, 10.6, 10.7, 10.8, 13.1, 13.2_

- [ ] 5. 画面のスモークテストと登録フローの結合テスト
- [x] 5.1 (P) 全ページのスモークテストを書き、既存のページ起動テストを統合する
  - 5 ページを、空データとサンプルデータのそれぞれで実行し、例外がないこと、共通のタイトルと 5 つのナビゲーションが表示されること、論文リストの内容が実行前後で変わらないことを検証する
  - 既存の論文リストページの起動テストはスモークテストに包含されるため削除し、pybtex の読み込み確認テストは残す
  - 10 通りのスモークテストがすべて成功する
  - _Boundary: test_pages_smoke_
  - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 14.3_

- [x] 5.2 (P) BibTeX 経路と重複登録の結合テストを書く
  - BibTeX テキストとファイルのアップロードのそれぞれで登録すると、論文リストのファイルにエントリが追加され、成功が表示されることを検証する
  - 未入力・複数エントリ・有効なエントリなし（構文として成立する非 BibTeX テキスト）のそれぞれでエラーが表示され、論文リストが変わらないことを検証する
  - 既存の論文とフォルダ名が重複する登録で重複エラーが表示され、件数が変わらないことを検証する
  - 既知バグ（KB6: 構文エラーの BibTeX で登録ページが例外で落ちる）を strict な xfail として記録する
  - 通常のテストは成功し、既知バグのテスト 1 件は xfailed になる
  - _Boundary: test_register_flow_
  - _Requirements: 12.1, 12.2, 12.3, 12.8, 13.1, 13.2, 13.4_

- [x] 5.3 DOI 経路・手動入力・必須項目・PDF 添付の結合テストを書く
  - 固定の Crossref 応答で DOI を登録すると、応答どおりのフィールドを持つエントリが追加されることと、未入力・取得失敗でエラーになり論文リストが変わらないことを検証する
  - 手動入力でエントリタイプを選び、必須フィールドを入力して登録できることを検証する
  - BibTeX・DOI・手動入力のそれぞれで、必須フィールドの不足が不足フィールド名を含むエラーになり、論文リストが変わらないことを検証する
  - PDF を添付して登録すると、エントリのフォルダに 00.pdf から連番で保存されることを検証する
  - 登録フローのテストがすべて成功し、外部ネットワークへのアクセスが発生しない
  - 5.2 と同じテストファイルを編集するため、5.2 の後に行う
  - _Requirements: 12.4, 12.5, 12.6, 12.7, 12.9_

- [ ] 6. 全体の検証
- [x] 6.1 スイート全体の実行と既知バグの台帳を照合する
  - 既知の失敗の内訳を表示するオプション付きでスイート全体を実行し、既知の失敗が設計の既知バグ表のすべての行に対応し（KB1 は backup と restore の 2 件で、現時点では計 12 件）、想定外の成功が 0 件で、それ以外のテストがすべて成功することを確認する
  - 実装中に新しく見つかったバグがあれば、同じ規約で記録したうえで設計の既知バグ表に行を追記する
  - lint（ruff）をテストコードに対して実行する。ruff は開発環境に入っていないため、使えない場合はこの確認を飛ばしたことを報告する（インストールにはユーザーの承認が必要）
  - スイート全体が成功し、既知の失敗の内訳と既知バグ表が対応している
  - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 14.4_

- [x] 6.2 実データの保護・作業ツリー・再現性を手元で確認する
  - スイートの実行前後で、実データディレクトリにテスト実行中に更新されたファイルが 0 件であることを確認する
  - 実行前後でリポジトリの作業ツリーに差分が増えないことを確認する
  - スイートを 2 回続けて実行し、テストモジュールを個別に実行しても、同じ結果になることを確認する
  - 実データ・作業ツリーが変化せず、繰り返しや個別の実行でも同じ結果になる
  - _Requirements: 1.1, 1.6, 14.2_

- [ ] 6.3 CI の全 Python バージョンで成功することを確認する
  - ブランチの push についてユーザーの承認を得てから push し、CI の全マトリクス（Python 3.9〜3.13）の結果を確認する。Python 3.9 では Streamlit 1.50 になるため、ここで初めて設計の互換性の前提が検証される
  - 失敗した場合は、原因を特定してテスト側で対処する（プロダクションコードは変更しない）。手元で Python 3.9 / Streamlit 1.50 を再現するには新しい conda 環境が必要で、作成にはユーザーの承認が必要
  - CI の全 Python バージョンでテストが成功している
  - _Requirements: 14.1_

## Implementation Notes
- 1.1: conftest 読み込み中の `pytest.exit` は pytest に usage error として包まれ、終了コードは `returncode` に関わらず 4 になる（設計の `returncode=1` は効かない）。ガード違反でテスト 0 件・理由表示・非ゼロ終了という要件 1.3 は満たす
- 実行は PATH 上の python ではなく conda 環境 `paper-manager` を使う: `/Users/yu9824/opt/miniforge3/envs/paper-manager/bin/python -m pytest -xs`（ruff は未導入）
- `paper_manager` を import するコードは conftest の「隔離の確立」より後に置く（先に import されるとガードがセッションを止める）。conftest 内の import は `# noqa: E402` か遅延 import にする
- 1.2: `getaddrinfo` は localhost も含め無条件で遮断される（`NetworkBlockedError`）。`socket.connect_ex` は遮断対象外。ライブラリのルートロガーは level のみ復元し、handlers / propagate は復元しない（変更するテストは自分で戻す）。`st.session_state.clear()` が bare mode の ScriptRunContext 警告ログを `-s` 出力に出すが無害
- 1.3: テストモジュールから `from conftest import ...` で conftest の名前を使える（pytest 既定の prepend import mode 前提。`--import-mode=importlib` では収集時に失敗する）。ログレベルなどの「元の値」は期待値を決め打ちせず、収集時に取得した基準値と比較する。復元を検証するテストは `monkeypatch` を使わず直接変更する（`monkeypatch` が自分で戻してしまうため）
- 1.4: `make_entry` は既定値を消せない。著者なし・タイトルなしのエントリ（KB2a/KB2b）は `del entry["author"]` / `del entry["title"]` で作る。孤立 PDF のフォルダ名は `sample_data.orphan_pdf.parent.name`。`Entry.get_pdf_files()` はフォルダがあると `DeprecationWarning` を出す（警告はエラー扱いではない）。サンプルの `without_pdf` エントリには PDF フォルダを作らない
- 1.5: `crossref_article(doi, type)` は design の Service Interface に無い第 2 引数 `type`（Crossref の type 文字列。`"book"` で `publisher` 不足を再現）を持つ。`run_page` はページの例外を送出せず `at.exception` に残すので、テスト側で assert する。`st_messages` は AppTest と併用不可。登録ページには、ラベル `DOI` の TextInput が 2 つ（`doi_form` と `custom_form`）、送信ボタン `📤 登録` が 3 つ（出現順 BIB・DOI・CUSTOM）あるため、ラベルだけでなく `form_id` か出現順で特定する
- 5.3 向け: `_register.py` の `_create_entry_from_doi_metadata` は `metadata["container-title"][0]` を無条件に参照する（空だと IndexError）。`crossref_article` は `container-title` を必ず含めるので回避されるが、空の場合を検証するなら 13.4 に従い KNOWN BUG 候補として扱う
- 2.1: 新規バグ KB7（仮称）を `tests/test_entry.py::test_name_stays_within_200_chars_when_prefix_leaves_almost_no_room` に strict xfail で記録済み。接頭辞 `<year> - <first_author> - ` が 198〜199 文字のとき `pdf_dir_name` が 200 文字を超える（`title[:max_title_length - 3]` が負のインデックスになるため。要件 2.4 違反。Repro: year=2020、著者名 188/189 文字、タイトル 300 文字）。**6.1 で design.md の既知バグ表に行を追記する**。このため tasks.md の 2.1 完了条件の xfailed は 2 件ではなく 3 件、6.1 の既知の失敗の件数は 12 件ではなく 13 件になる
- 2.1: `get_key` は `author=""` を `Unknown` にしない（`2020_0`）が、`pdf_dir_name` は `author=""` で IndexError（KB2a と同根）。観察のみで xfail にしていない。OS 判定の差し替えは `paper_manager.entry._entry.platform` を `SimpleNamespace(system=...)` に `monkeypatch` で差し替える（`tests/test_entry.py` の `set_os`）。xfail の reason は `"KNOWN BUG: "`（コロン付き）で始める（レビューで指摘された）
- 2.2: KB2b（タイトルなしの `get_pdf_files`）は PDF フォルダが存在しないときだけ再現する（旧形式の単一 PDF を探す分岐が `pdf_filename` を評価するため）。6.1 で design の KB2b の行にこの条件を補足してよい。観察のみ（xfail にしていない）: 旧単一 PDF は PDF フォルダが既にあると移行されない（ファイルは消えず上書きもされないが一覧に出ない）。複数の旧命名ファイルの番号割当は `glob` 順で不定（上書きなし・データ保全は満たすので仕様の範囲内。テストは内容の集合で比較）。`_migrate_legacy_single_pdf` の衝突回避は公開 API 経由では到達不能
- 2.3: **KB 候補（観察のみ。xfail にしていない。最終報告で挙げる）**: (1) `list.json` が切り詰められて日本語マルチバイト文字の途中で切れると `PaperList.from_file` が `UnicodeDecodeError` で落ち、警告も出ない（`except json.JSONDecodeError` では捕捉できない）。書き込み中断が最も現実的な原因。(2) 妥当な JSON だがオブジェクトでない（`[]`, `"x"`, `1`）と `AttributeError`。(3) **ユーザーデータ安全**: 壊れた `list.json` を置いてトップページを 1 回描画すると、警告のあと `config_page`（`_config.py` の `from_file()` → `to_session_state()` → 実行後の `from_session_state().to_file()`）が `{}` で `list.json` を上書きし、バックアップも作られない（AppTest で再現）。要件 3.3 の範囲外で、期待する振る舞いが spec で未決のため xfail にしていない
- 2.3: `st_messages` を使うテストは AppTest と併用しない。保存ファイルに日本語が文字のまま入る（`ensure_ascii=False`, UTF-8）ことは product.md の「人間が読める `list.json`」に基づく検証
- 2.4: `load_bib` の入力は `io.StringIO` / `io.BytesIO` / `Path` / `str`（`str` は本文でなくファイルパス扱い）。素の `bytes`・`bytearray` は `TypeError`。戻り値の `ID` は付かず、引用キーの小文字化は戻り値の辞書キーに現れる（`ID` は `_common.py` の登録時に付与）。**KB 候補（観察のみ）**: 1 語の著者名（`Cher`, `{ACME Collaboration}`）の整形結果が先頭空白付きになり、`John Smith and others` は空白が 2 つになる。`TypeError` のメッセージが入力型でなく `type(bib_parser)` を出している
- 2.5: `dummy_tqdm` は tqdm を import せず有無の分岐を持たない（`sys.modules['tqdm']=None` でも動く）。観察のみ: `with dummy_tqdm(...)` は `TypeError`（`__getattr__` が特殊メソッドに効かない。要件 5.5 に `with` は無く `src/` に呼び出し箇所も無い）。`is_installed("a.b")` は親パッケージ不在で `ModuleNotFoundError`。`split("  ", ",")` は `("",)`、末尾の区切りは空要素が残る
- 3.1: KB3 の xfail は design の KnownBugConvention（`raises=` は付けない）から逸脱して `raises=AssertionError` を付けた。fixture の安全検査（到達先が一時ルート内・データディレクトリ外・禁止パス外・事前に存在しない）が `pytest.fail`（`Failed` は `AssertionError` ではない）で失敗したとき、`raises` なしだと XFAIL に化けて安全検査の壊れを隠すため。XPASS(strict) の挙動（13.3）は損なわれない。**6.1 で design.md の KnownBugConvention に例外として追記する**。KB3 の到達先は `data_paths.root.parent / "escaped.pdf"`（`pdf/../../escaped.pdf`）で、teardown で必ず削除される。`pages/_restore.py` にも同じ実装がある（このタスクの対象外）。観察のみ: `get_backup_info` は不正な zip でも `_logger.exception` で ERROR とトレースバックを出す。`restore_from_zip` は存在しないパスでは `(False, "復元中にエラーが発生しました: ...")` を返す
- 3.2: 環境変数の復元は子プロセスの pytest（`pytest.main(plugins=[Observer])`）で確認する（順序付きの 2 テストでは、conftest が各テスト開始時に 4 変数を削除するため復元漏れが隠れる）。`[set-outside]` は `monkeypatch` の undo が外側の値を戻すため conftest の `_restore_environ` の欠落を検出できず、検出するのは `[unset-outside]`。**KB 候補（観察のみ。最終報告で挙げる）**: `save_proxy_config` は既存の `config.json` が壊れていると警告ログだけで読み込みを諦め、空の設定から書き直す（プロキシ以外の項目が失われ、バックアップも作られない。2.3 の KB 候補（3）と同種のユーザーデータ保護上の懸念。現状 `config.json` に入るのは `proxy` のみなので実害は小さい）。手編集した `{"proxy": {"http": ""}}` では `get_proxy_config()` が `{"http": ""}` を返し環境変数のプロキシが隠れる（UI 経由では到達不能）
- 3.3: `find_orphaned_pdfs` は読み取り専用でなく、各エントリの `get_pdf_files()` が旧形式 PDF をエントリのフォルダへ移行する副作用がある（紐づく旧形式 PDF を孤立にしないための仕様どおり）。`PaperList.__init__` が PDF ディレクトリを作るため、「PDF ディレクトリなし」の状態は論文リストを作ってから `rmdir` して作る。削除失敗の再現は `Path.unlink` の `monkeypatch` 差し替えで行う（chmod は環境依存のため不使用）。private 関数 `_calculate_similarity` の直接テストは design.md が許可している
- 3.3: **KB 候補（観察のみ。最終報告で挙げる）**: (1) PDF フォルダと同名の旧形式 PDF がルート直下に共存すると（フォルダがあるため移行されず）紐づく旧形式 PDF が孤立と判定され、UI の「すべての孤立PDFを削除」で消える（再現済み）。(2) `delete_orphaned_pdfs` は渡されたパスが `DIRPATH_PDF` 配下か検証せず、フォルダを渡すと PDF 以外も `rmtree` する（現状の UI は `find_orphaned_pdfs` の戻り値のファイルだけを渡すので届かない）
- 3.4: `entrytype4doi` の docstring が実装と矛盾している（`entrytype4doi("article") -> "journal"` と書かれているが、引数は Crossref のタイプで、`"article"` は変換表に無く警告付きで `"misc"` を返す）。要件 8.7 には適合しておりバグではなく docstring の不正確さのみ（最終報告で docstring 修正の提案として触れる）。テストは `"article"` を入力に使わない。変換表は `journal-article`→`article`、`proceedings-article`→`inproceedings`、`book`→`book` の 3 件のみ
- 4.1: `tests/test_components.py` は 4.2/4.3 が末尾に追記する。補助は同モジュール内の `entry` / `other_entry` / `pdf_root` fixture と `make_uploads` / `make_pdfs` / `pdf_names` / `padded_base_dir`。`delete_pdfs` の `specific_files` はファイル名でなく `list[Path]`。パス長の上限 250 は `save_pdfs` 内に直書き（`len(str(filepath)) > 250`）で、`padded_base_dir` が `base_dir` を伸ばして境界を作る。`save_pdfs` はスキップしても `index` を進めないため、本番の入力では「飛ばしたあとに別のファイルが保存される」状況を作れない（`_generate_pdf_filename` を細工した人工的なスタブでのみ可能で、テストにしていない）。`pytestmark` の `filterwarnings`（`pdf_filename` / `_migrate_legacy_pdf_filenames`）は module 全体に効く。`st_messages` を使うテストと AppTest を使うテストは別関数にする
- 4.1: 観察のみ（KB 候補としては重大度が低い）: `save_pdfs` は全ファイルがパス長超過で飛ばされても先に `mkdir` するため空のフォルダが残る（データ損失なし）
- 4.2: 登録・更新の補助は `tests/test_components.py` の「論文の登録・更新: 補助」にまとまっている（`ListState` / `Stored` / `as_fields` / `read_state` / `assert_list_is` / `seed_paper_list` / `edited` / `pdf_tree` / `stored`）。`register_entry_to_list` / `update_entry_in_list` は `bool` を返す。`to_session_state` は内部の辞書を直接格納するため、前提の論文リストは session state から読み直した別オブジェクトにして保存漏れを検出する。更新でタイトルだけ変えると新しい識別子は旧キーと異なるよう採番される（`_0` ではなく `_1`）
- 4.2: **KB 候補（新規・ユーザーデータ保護に関わる。観察のみで xfail にしていない。最終報告で挙げる）**: 大文字・小文字を区別しないファイルシステム（macOS APFS 既定、Windows）で、`pdf_dir_name` の重複判定は大文字・小文字を区別するが実フォルダは区別されない。(i) 登録: 既存エントリ（PDF あり）とタイトルが大文字・小文字だけ違うエントリは重複と判定されず登録でき、添付 PDF が既存エントリのフォルダに保存されて両エントリが互いの PDF を見る（片方の PDF 削除でもう片方のフォルダごと消える恐れ。重大度高）。(ii) 更新: PDF のあるエントリのタイトルを大文字・小文字だけ変えると `new_pdf_dir.exists()` が真になり「Cannot rename PDF directory: destination already exists」で拒否される（データ損失はない）。macOS で再現済み、Linux（CI）では再現しないため決定論的な xfail にはできない（記録するならファイルシステムを実行時に判定する条件付き xfail）。その他の観察のみ: 元の PDF フォルダが無く改名先に同名の孤立 PDF フォルダがあると更新は成功し孤立フォルダが紐づく。未移行の旧形式単一 PDF を持つエントリで関数を直接呼んでタイトルを変えると旧 PDF が取り残される（実画面の編集フローは先に `get_pdf_files()` で移行するため UI からは届かない）。拒否された登録でも渡した `entry` に `ID` が書き込まれる
- 4.3: **Streamlit 1.50 では `PagesManager.uses_pages_directory` がプロセス共有で、最初の AppTest が決める**。最初が `from_file(_list.py)` なら後続の `from_function` が `StreamlitAPIException: The title of the page cannot be empty...` で、`from_function` が先なら後続の `from_file` / `switch_page` が `KeyError: 'url_pathname'` で失敗する（1.66 では再現しない）。対処として `tests/conftest.py` の `_isolate_each_test` が各テストの前に `_reset_page_detection` で `None` に戻す（commit `fix(test-suite): reset Streamlit page detection before each test`）。5.x の AppTest 利用テストも守られる。1 つのテストの中で `from_file` と `from_function` を両方走らせると最初の実行で固定される。単体実行だけでは見つからないため、**Streamlit 1.50 の確認はスイート全体で行う**（スクラッチの `PYTHONPATH` に 1.50.0 を展開して実行。環境へはインストールしない）
- 4.3: 手動入力フォーム `custom_entry` は `AppTest.from_function` で実行し（ウィジェットは `key=` で特定）、`pdf_upload_form` は `fake_file_uploader` を直接呼び出しで使う。`number_input` を `set_value(None)` で空にできないため、年の空欄除去は `misc` の初期値 `year=""` で検証する。`st_messages` は使わない。KB4a の影響: ` and ` 区切りの著者を持つエントリの編集フォームは `author` を `, ` 区切りで返し、編集の Done で `update_entry_in_list` の改名経路に入り PDF フォルダ名が変わる（コード上の挙動）
- 4.3: **KB 候補（観察のみ。最終報告で挙げる）**: 年が整数でない（`n.d.`, `2020a`）または 1000〜翌年の範囲外（999, 3000, 2028）のエントリを `custom_entry` に渡すと例外になり編集フォームが開かない（BibTeX 取り込みは年を検証しないため登録・一覧表示はできる。データは失われないが UI から年を修正できない）。期待する振る舞いが spec で未決のため xfail にしていない
- 4.4: **新規バグ 2 件を strict xfail で記録（design.md の KB 表への追記は 6.1）**。**KB8（仮称）**: `--client.showSidebarNavigation=true` のような `=` 形式の明示指定を `main()` が認識せず（`"--client.showSidebarNavigation" not in set(unknown)` がトークン完全一致のため）、`--client.showSidebarNavigation false` を重複して付与し、後ろが勝つのでユーザーの `true` が無視される（要件 10.3）。**KB9（仮称、KB1 と同根）**: `paper-manager version` が常に終了コード 2 で失敗する（`version` にも `--client.showSidebarNavigation false` が付与され、`streamlit version` がそのオプションを受け付けない。`-v` / `--version` は正常）。KB1 を直すと KB8 / KB9 / KB1 が同時に XPASS(strict) になりうる。このため xfailed の総数は現在 12 件で、4.5 で KB1×2 が加わって 14 件、5.3 の KB6 を加えて最終的に 15 件になる見込み。6.1 の「既知の失敗の件数」は 13 件ではなく 15 件に更新する
- 4.4: Streamlit の起動は `paper_manager.__main__.cli` を `main_run` / `main_version` だけを持つ名前空間に丸ごと差し替えて避ける（他の起動経路は AttributeError）。補助は `tests/test_cli.py` の `streamlit_calls` / `library_logger` / `_run_forwarded` / `_version_forwarded`。4.5 は末尾にセクションを追記し、モジュール docstring に backup / restore と KB1 を足す。`--debug` のログレベル復元は 3.2 と同じ子プロセスの pytest + Observer で確認する。10.1 は `-v` / `--version`（サブコマンド `version` は 10.2 の対象）。`run -h` / `--help` は Streamlit に渡される
- 4.5: **KB1 は二層構造**（6.1 で design.md の KB1 の行に反映する）。(一層目) `main()` が `--client.showSidebarNavigation false` を backup / restore にも付与し、サブパーサーが `unrecognized arguments` で終了コード 2。(二層目) `main()` のサブパーサーが `-o` / `backup_file` / `--force` を消費し、`backup()` / `restore()` には未知の引数しか渡さない。一層目だけ直すと backup は `-o` を無視して cwd に既定名で作り、restore は `backup_file` が足りず終了コード 2 のまま。2 層を直すと KB1×2 が XPASS(strict) になり、付与を run のみにすると KB9 も同時に XPASS する（KB8 は `=` 形式の問題なので別）。単一 ID のまま reason に両層を明記する。通常テストは `cli_main.backup(args)` / `cli_main.restore(args)` に引数リストを直接渡す（KB1 を踏まない）。`input()` は `builtins.input` の差し替え。キャンセルは終了コード 0、失敗は終了コード 1。確認入力は大文字小文字を無視（実装の `.lower()` を固定）。観察のみ: 確認の `input()` は try の外にあり、EOF（非対話実行）では `EOFError` のトレースバックになる
- 4.5: xfailed の総数は 14 件。5.3 の KB6 を加えて最終的に 15 件になる見込み
- 5.1: `tests/test_app.py` を削除（旧 `test_app` は `AppTest.from_file(_list.py).run()` を呼ぶだけで何も assert しておらず、新しい `[empty-list]` / `[sample-list]` が包含する）。`tests/test_import.py` の `test_import_pybtex` は残した。Streamlit 1.50 には `PageLink` クラスが無く `at.get("page_link")` が `UnknownElement` を返すため `.value` ではなく `.proto.label` / `.proto.page` を使う。ナビゲーションのラベル順（`リスト・編集` / `登録` / `バックアップ` / `復元` / `設定`）と遷移先の重複なしを検証する。タイトルとナビゲーションは全ページ共通のため、`switch_page` が効かず全ページが論文リストページになっても通ってしまうので、ページ自身の `st.header` も検証する。11.4 の「変わらない」は `list.json` を `json.load` した辞書（キーとエントリ内容）の比較で、空データでは実行後に `config_page` が `{}` を書くが保存された論文は 0 件のままなので変更なしとみなす（モジュール docstring に明記）
- 5.2: 登録ページのウィジェットは出現順でなく `proto.form_id`（`bib_form` / `doi_form` / `custom_form`）で特定する（1.66 / 1.50 の両方で動く。`bib_form` の text_area・ボタンは 1 つずつ、`doi_form` の text_input は 1 つ、`custom_form` の text_input は 10 個で `DOI` を含む）。5.3 は `widget_in_form(at.text_input, FORM_DOI)` で DOI 経路を、`[w for w in widgets_in_form(at.text_input, FORM_CUSTOM) if w.label == "DOI"]` で手動入力の DOI を特定できる。補助は `tests/test_register_flow.py` の `widgets_in_form` / `widget_in_form` / `open_register_page` / `submit` / `error_messages` / `success_messages` / `stored_papers` / `pdf_tree` / `assert_registered` / `assert_rejected` / `ERROR_MARKERS` / `papers_before` / `submit_bib`。エラーの種類は最小限の語（`入力されていません` / `1つだけ` / `有効なエントリ` / `Duplicated`）で区別する。KB6 の再現入力は `@article{k, title={T}`（`pybtex.scanner.PrematureEOF`）で、**6.1 で design.md の KB6 行の再現入力をこれに合わせ、送信ボタンの特定方式を出現順から `form_id` に更新する**。xfailed は 15 件で、5.3 は KB6 を加える必要がない（新規バグが出なければ最終的に 15 件）
- 5.2: 観察のみ: 著者なしの BibTeX を登録しても登録ページが `KeyError: 'author'` で落ちる（KB2c と同根。KB2c を直しても登録時の `pdf_dir_name`（KB2a の `IndexError`）に当たるので、KB2c の修正は KB2a と同時に必要）。テキストとファイルの両方を入力するとファイルが黙って優先される（`uploaded_file_bib if uploaded_file_bib else StringIO(text)`）。pybtex の既定エンコーディングは固定の UTF-8
- 5.3: **新規バグ KB10（仮称）を strict xfail で記録（design.md の KB 表への追記は 6.1）**: DOI の問い合わせ（`Works.doi`）が `requests.exceptions.ConnectionError`（オフライン・タイムアウト・プロキシ誤設定）を送出すると、`_render_doi_form` が捕捉せず登録ページが例外で落ち、エラーも表示されない（要件 12.5 に反する。`_register.py` の `works.doi(doi)` が try の外）。KB6 と同形。**xfailed の総数は 16 件**（KB1×2、KB2a / KB2b / KB2c、KB3、KB4a / KB4b / KB4c / KB4d、KB5、KB6、KB7、KB8、KB9、KB10）。6.1 の「既知の失敗の件数」は 16 件に更新する。`fake_crossref` は dict / None しか扱えないので、通信例外は `monkeypatch` で `Works.doi` を差し替える
- 5.3: **KB 候補（観察のみ。最終報告で挙げる）**: (1) DOI 経路は `publisher` と `booktitle` を Crossref 応答から取り込まないため、実際の書籍（`book`）と会議録（`proceedings-article`）の DOI が常に `('publisher') is/are necessary.` / `('booktitle') is/are necessary.` で拒否され、DOI フォームから補う手段がない（手動入力か BibTeX で登録するしかない）。`test_doi_with_missing_required_fields_is_rejected` は設計が指定する `type="book"` の固定応答（`publisher` 不足）でこの挙動に依存するので、直すときは入力を差し替える。(2) `type="book"` で `container-title` が `[]`・`author` なし・`published` なしの応答だと `IndexError` / `KeyError` で登録ページが落ちる（実 Crossref の `book` は `container-title` が空のことが多い）。(3) DOI 経路は応答にない volume / number / pages を空文字 `""` で保存する（BibTeX と手動入力は保存しない。テストは `ignore_blank` で保存の有無に結合しない）。(4) 空白だけの DOI は `if not doi` を通り Crossref に問い合わせる。(5) 手動入力の必須不足エラーの順序は集合順で不定、文言が日英不統一
- 5.3: PDF アップロード欄のラベル `PDF file(s) (.pdf)` は 3 フォーム共通で、`fake_file_uploader` の接頭辞では送信フォームの欄だけを区別できない（別フォームの `uploaded_files_*` を渡すコピペ回帰はテストで検出できない。強化するなら呼び出し順 BIB → DOI → CUSTOM で返す fake を test モジュール内に置く）。手動入力の必須不足のテストは article / book / inbook を使い、inproceedings の `booktitle` 不足は検証していない
- 6.1: xfailed は 16 件（KB1×2、KB2a / KB2b / KB2c、KB3、KB4a / KB4b / KB4c / KB4d、KB5、KB6、KB7、KB8、KB9、KB10）で、design.md の既知バグ表の全行と 1 対 1 に対応する。XPASS は 0 件、reason はすべて `KNOWN BUG:` で始まる。実装中に判明した KB7〜KB10 と、実装で食い違った設計記述（KB1 の二層構造、KB2b の再現条件、KB6 の再現入力、KB3 の `raises=AssertionError` の例外、隔離ガードの終了コード 4、`PagesManager.uses_pages_directory` のテストごとの初期化、`crossref_article` の `type` 引数、登録ページのウィジェット特定を `form_id` に変更、スモークテストの追加検証）を design.md に反映した
- 6.1: lint は開発環境（conda 環境 `paper-manager`）に ruff が無いため、別の conda 環境（`py312`）にある既存の ruff 0.14.13 を、プロジェクトの `ruff.toml` で読み取り専用に実行した（`--no-cache`、インストールはしていない）。`ruff check --config ruff.toml tests` と `src tests` はいずれも All checks passed。`ruff format --check` は全角文字を幅 2 と数えるため数ファイルに整形差分が出るが、CI は ruff を実行しない
- 6.2: 手元（Streamlit 1.66.1、conda 環境 `paper-manager`）で確認した。(1) スイート全体を 2 回続けて実行し、どちらも 662 passed + 16 xfailed で xfail の内訳も同一。(2) 14 のテストモジュールを個別に実行した合計が 662 passed + 16 xfailed で全体と一致。(3) `~/.paper-manager` はファイル一覧・更新時刻・サイズが実行前後で同一で、実行中に更新されたファイルは 0 件。(4) リポジトリは `git status`・HEAD が実行前後で同一で、zip などの生成物も一時ディレクトリの取り残しもない。(5) Streamlit 1.50.0 でも全体を既定順・逆順・ランダム順で実行して 662 passed + 16 xfailed（スクラッチの `PYTHONPATH` 方式）
