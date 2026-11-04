# Implementation Plan

## 全タスク共通の前提
- 既知バグ（test-suite spec で strict xfail として記録した KB1〜KB10）を直したタスクは、そのタスクの中で対応するテストの xfail の記録を外し、通常のテストとして成功させる
- プロダクションコードの変更は、設計の File Structure Plan に挙げたファイルと責務の範囲に限る。範囲外の不具合を見つけたら直さずに報告し、Implementation Notes に記録する
- テストは実データの保存先に触れない（test-suite spec のテスト基盤の隔離をそのまま使う）。画面テスト（AppTest）に関わる変更では、Streamlit 1.50 でもスイート全体を実行して成功を確認する
- 各タスクの検証では、担当するテストモジュールを実行したうえで、スイート全体を実行して回帰がないことを確認する
- Streamlit 1.50 での実行は、作業用の conda 環境には入れず、test-suite spec で記録した方式（スクラッチの `PYTHONPATH` に Streamlit 1.50.0 を展開して実行する）で行う。実行順序を入れ替える確認（逆順・ランダム順）も、スクラッチに置いた pytest プラグインで行い、プロジェクトの依存は増やさない
- Windows の実機での確認（拡張長のパスの入出力）は、CI では検証できないため、設計の「手動確認」に従って実装の完了時に利用者へ依頼する（タスクには含めない）

- [x] 1. 基盤: パスの変換と、Windows を模したテストの仕組み
- [x] 1.1 拡張長のパスへの変換・表示用の逆変換・フォルダ名の比較キーを用意する
  - Windows では、ファイル操作用のパスを絶対パスに正規化してから拡張長のパスに変換する（UNC にも対応し、既に変換済みならそのまま返す）。Windows 以外ではパスを変えない
  - 画面とログに出すために、拡張長のパスから prefix を外したパスを得られるようにする
  - 大文字小文字と Unicode 正規化だけが違うフォルダ名が同じ値になる比較キーを用意する（OS やファイルシステムに依存しない）
  - 変換の実体をモジュール内で差し替えられるようにする（テストで Windows を模すため）
  - 純粋な文字列変換の単体テスト（ドライブ付き、`/` 区切り、`..`、UNC、変換済み、ドライブ相対）と比較キーのテストが、どの OS でも成功する
  - _Requirements: 1.1, 1.3, 1.4, 3.4_

- [x] 1.2 Windows のパスの扱いを CI の Linux で模すテストの仕組みを用意する
  - 変換の実体を「絶対パスの前に区切りを 1 つ足す」別名変換に差し替える fixture を、共有のテスト基盤に追加する（Linux と macOS では同じ場所を指すが、パスの比較では別のルートとして扱われる）
  - fixture を有効にすると、変換後のパスと変換前のパスの比較や相対パスの計算が一致しなくなること（prefix の混在による不具合を再現できること）を、テストで確かめる
  - fixture を使うテストが成功し、使わない既存のテストはすべてそのまま成功する
  - _Requirements: 12.3_

- [x] 1.3 ページの描画で論文リストのファイルを書き換えないようにする
  - ページの描画後に論文リストをファイルへ書き戻す処理を削除する（論文リストの変更は、すべて既存の保存処理を通るため）
  - 論文リストの判定を広げる 2.3 より前に行い、壊れたファイルが描画で上書きされる中間状態を作らない
  - JSON として壊れた論文リストのファイルを置いて各ページを描画しても、ファイルのバイト列が変わらないこと、既存のスモークテストが引き続き成功することを確かめるテストが、Streamlit 1.50 でも成功する
  - _Boundary: ConfigPage_
  - _Requirements: 10.2_

- [x] 2. ドメイン層の修正
- [x] 2.1 PDF フォルダ名の生成を修正する
  - 年・著者・タイトルの長さにかかわらず、フォルダ名を 200 文字以内にする。現在 200 文字以内になる入力では、出力を修正前と完全に同一に保つ（KB7）
  - 著者がない、または空の論文では、著者を `Unknown` としてフォルダ名を作る（KB2a）
  - Windows では、フォルダ名の `"` も `_` に置き換える（KB5）。Windows 以外の置換規則は変えない
  - タイトルのない論文でも、PDF の有無と件数を例外なく返す（KB2b）
  - フォルダ名は保存先ディレクトリの長さに依存しない
  - 修正前の規則を、後方互換のために別に残す（KB7 の範囲の移行と孤立判定だけで使う）。境界のテストはこれと比較する
  - KB2a・KB2b・KB5・KB7 の xfail が外れて成功し、修正前の規則と出力が一致する範囲（接頭辞 197〜200 文字 × タイトル 0〜300 文字の境界）のテストが成功する
  - _Requirements: 1.3, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

- [x] 2.2 PDF の一覧の取得で、修正前の名前のフォルダと旧形式の PDF を移行する
  - 2.1 で残した修正前の規則から、KB7 の範囲で修正後と異なり、255 文字以内の名前を返せるようにする
  - 現在の名前のフォルダがなく、修正前の名前のフォルダがあれば、既存の PDF を上書きせずに現在の名前へ移す
  - 旧形式の単一 PDF は、フォルダの有無にかかわらず、フォルダの空いている番号へ上書きせずに移す。移せなかった場合は一覧に含めて返す
  - エントリの PDF フォルダのパスと旧形式の単一 PDF のパスは、変換後の PDF ルートから作る。250 文字を超えたときの警告ログは出さない
  - 移行に失敗したとき、または移す先が既にあるため移さなかったときは、ログと画面の警告（表示用のパス）の両方に出す
  - 1 回の描画の中で、同じ対象についての警告を繰り返し出さない（一覧の件数・統計・孤立 PDF の判定のそれぞれから呼ばれるため）
  - 修正前の名前のフォルダと、フォルダと同名の旧形式 PDF のそれぞれについて、移行後に PDF が一覧に出ること、移す先が既にあるときは何も上書きされないことを確かめるテストが成功する
  - _Depends: 1.1_
  - _Requirements: 1.1, 1.2, 1.4, 2.7, 8.3, 12.5_

- [x] 2.3 論文リストのファイルの読み込み状態の判定と、壊れたファイルの退避を用意する
  - 論文リストのファイルを読み込み、正常・ファイルなし・壊れている、のいずれかの状態を返す（JSON として解析できない、UTF-8 として読めない、論文の一覧の形をしていない、値が文字列でない、を壊れているとみなす）。画面の処理を呼ばない
  - 既存の読み込みは、壊れているときに従来どおりの警告を出し、空の論文リストを返す（例外にならない）
  - 壊れたファイルを、元のファイルを残したまま、日時入りの別名（衝突時は連番）へ退避できるようにする。復元が使う退避名とは別の名前にする
  - 壊れ方のパターンごとに状態が「壊れている」になること、退避先の名前と連番、元のファイルが変わらないことを確かめるテストが成功する
  - _Requirements: 10.1, 10.3, 12.5_

- [x] 2.4 (P) BibTeX の人名の整形と、著者のない BibTeX の読み込みを修正する
  - 名（ミドルネームを含む）と姓（`van der` などの前置詞を含む）のすべての部分を保って、`<名> <姓>` の形に整形する。単純な `First Last` と `Last, First` は修正前と同じ出力にする（KB4c）
  - `Jr.` などの接尾辞があるときは、エクスポート時の再解析で姓と名が入れ替わらない形にする
  - 著者のない BibTeX は、例外にならずに著者のないエントリとして読み込む。著者が 1 人もいなければ著者の項目を作らない（KB2c）
  - KB2c・KB4c の xfail が外れて成功し、整形の規則のテスト（単純な名前、ミドルネーム、前置詞、接尾辞、空の人名）が成功する
  - _Boundary: BibLoader_
  - _Requirements: 5.1, 5.3_

- [x] 2.5 引用キーから BibTeX の引用キーとして読めない文字を除く
  - 論文の登録・更新で作る引用キー（`<筆頭著者><年>_<n>`）から、空白・`,`・`{`・`}` を除く。読めない文字を含まない著者と年からは、修正前と同じ引用キーを作る
  - 表示・エクスポートのために、引用キーの並びを、整えたうえで大文字小文字を区別せずに重複しないものにする処理を用意する
  - 2.4 の接尾辞の形（`King, Jr., Martin`）の筆頭著者でも、引用キーに `,` が入らない
  - `, ` 区切りの著者、接尾辞付きの筆頭著者、波括弧を含む人名、空白を含む年で作った引用キーが pybtex で解析できること、単純な著者と年の引用キーが修正前と同じであること、重複の解消のテストが成功する
  - 2.4 の後に行う（2.4 だけでは、接尾辞付きの筆頭著者の引用キーに `,` が入る）
  - _Boundary: CitationKey_
  - _Requirements: 13.1, 13.2, 13.4_

- [x] 2.6 BOM 付きの論文リストのファイルを正常として読み込む
  - 先頭に BOM の付いた UTF-8 のファイルを、BOM の後が正しい形なら正常として読み込む。BOM の後が壊れていれば、従来どおり壊れているとみなす
  - 保存は従来どおり BOM なしの UTF-8 で書く
  - BOM 付きの正常なファイルが正常になって中身が一致すること、BOM 付きで壊れたファイルが壊れていると判定されること、BOM 付きの正常なファイルが退避されないことを確かめるテストが成功する
  - 2.5 と同じファイルを編集するため、2.5 の後に行う
  - _Boundary: PaperListStore_
  - _Requirements: 10.1, 10.4, 12.5_

- [x] 3. アプリ層の修正
- [x] 3.1 (P) 復元のメンバー名を書き込み前に検証し、バックアップと復元を変換後のパスで行う
  - zip を開いた直後に全メンバー名を検証し、絶対パス、ドライブやルートの指定、`..` の成分、データディレクトリの外を指す名前が 1 つでもあれば、何も書かずに中止してエラーを返す
  - 既存の「無効なzipファイルです。」の扱いと、既存ファイルの退避は変えない
  - バックアップの作成と復元の書き込みは、変換後のパスを起点にする。zip のメンバー名は、変換後の空間での相対パスから作り、prefix を含めない
  - KB3 の xfail が外れて成功し、不正なメンバー名の各パターンで `list.json` を含むデータディレクトリが変わらないこと、正当なバックアップ（日本語名、`\` を含むフォルダ名、旧形式）が受け付けられることを確かめるテストが成功する
  - _Boundary: BackupRestore_
  - _Depends: 1.1_
  - _Requirements: 1.2, 9.1, 9.2_

- [x] 3.2 (P) 孤立 PDF の検出と削除を修正する
  - 走査と比較を変換後のパスの空間で行う（PDF ルートと比べる箇所も変換後の値と比べる）
  - 有効なフォルダ名を、全論文の現在の名前と修正前の名前の比較キーで判定する
  - PDF ルート直下の旧形式の PDF のうち、いずれかの論文の名前と一致するものは孤立と判定しない
  - フォルダを指定した削除では、削除の前に PDF を数えて件数を報告する（KB4d）。削除に失敗したパスと理由を、呼び出し元が受け取れるようにする
  - KB4d の xfail が外れて成功し、フォルダと同名の旧形式 PDF と修正前の名前のフォルダが孤立と判定されないこと、削除の失敗が呼び出し元に返ることを確かめるテストが成功する
  - _Boundary: OrphanPdf_
  - _Depends: 1.1, 2.2_
  - _Requirements: 1.2, 1.4, 2.7, 8.1, 8.2_

- [x] 3.3 (P) PDF の保存と削除を修正する
  - パス全体の長さだけを理由に保存をスキップする規則を削除する
  - 削除（フォルダごと、指定したファイル、旧形式の単一 PDF）は、変換後のパスで行う
  - 既存の引数の順序（論文、基準のディレクトリ、削除するファイル）と、位置引数での呼び出しは変えない
  - フォルダの作成やファイルの書き込み・削除に失敗したら、そのファイルについて失敗したことと表示用のパスを画面に出し、残りのファイルの処理を続ける
  - test-suite の要件 9.3 のテスト（250 文字の規則）を、250 文字を超えても保存されること、書き込みに失敗したときにエラーを出して残りを処理することのテストに書き換え、成功する
  - _Boundary: PdfStorage_
  - _Depends: 2.2_
  - _Requirements: 1.1, 1.2, 1.4, 1.5_

- [x] 3.4 論文の登録・更新の重複判定と、大文字小文字だけの改名を修正する
  - 登録では、既存の全論文とフォルダ名の比較キーが一致すれば重複として拒否する
  - 更新では、自分以外の論文と比較キーが一致すれば拒否する
  - 更新でフォルダ名が大文字小文字（または Unicode 正規化）だけ変わるときは、一時名を経由して改名し、失敗したら元に戻してエラーを出す。改名先が別のフォルダとして既にあるときは、従来どおり拒否する
  - 大文字小文字だけ違う登録の拒否、大文字小文字だけの改名の受け付け（PDF が保たれる）、他の論文と大文字小文字だけ違う更新の拒否、拒否したときに論文リストと PDF が変わらないことを確かめるテストが成功する
  - 3.3 と同じファイルを編集するため、3.3 の後に行う
  - 一時名を経由する改名は、大文字小文字を区別するファイルシステム（CI の Linux）と区別しないもの（macOS）で同じ結果になる
  - _Depends: 1.1_
  - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [x] 3.5 手動入力フォームの著者・タグ・年を修正する
  - 複数の著者を ` and ` で区切って保存する（KB4a）。` and ` 区切りの著者を持つ論文を、著者を変えずに保存しても表記が変わらない
  - タグの既定値を `, ` で区切って選択肢にする（KB4b）
  - 既に `, ` 区切りで保存されている著者は、書き換えずにそのまま保存する
  - 保存されている年が無効（整数として往復しない、または入力できる範囲の外）なときは、警告を出して文字の入力欄に切り替え、触らなければ元の値を保ち、直せばその値で保存する
  - KB4a・KB4b の xfail が外れて成功し、無効な年（`n.d.`、`2020a`、999、3000）でフォームが開くこと、元の値の保持と修正が反映されることを確かめるテストが、Streamlit 1.50 でも成功する
  - 3.4 と同じファイルを編集するため、3.4 の後に行う
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 7.1, 7.2, 7.3_

- [x] 4. 登録ページと CLI の修正
- [x] 4.1 (P) 登録ページの BibTeX 経路の構文エラーと、著者のない BibTeX の登録を修正する
  - 構文として正しくない BibTeX や、文字コードの読めないファイルを登録しようとしたときに、画面を例外で落とさずにエラーを 1 つ表示し、論文リストを変えない（KB6）。文言は他のエラーと区別できるものにする
  - 必須項目を満たす、著者のない BibTeX（misc）から論文を登録でき、成功が表示される
  - KB6 の xfail が外れて成功し、著者のない misc の BibTeX の登録が成功することを確かめるテストが、Streamlit 1.50 でも成功する
  - _Boundary: RegisterPage_
  - _Depends: 2.1, 2.4_
  - _Requirements: 5.2, 5.4_

- [x] 4.2 登録ページの DOI 経路のエラー処理と、Crossref の応答の取り込みを修正する
  - DOI のメタデータの取得中の通信エラー（接続できない、タイムアウト、JSON でない応答など）で、画面を例外で落とさずにエラーを 1 つ表示し、論文リストを変えない（KB10）
  - 応答の項目が欠けても例外にならないように取り込み、値のない項目は省いて、必須項目の判定は既存の検証に任せる
  - 書籍の DOI では出版社を、会議録の論文の DOI では会議録名を取り込む。年は出版日の候補を順に探す。団体著者の名前も取り込む
  - KB10 の xfail が外れて成功し、出版社付きの書籍と会議録名付きの会議録の DOI が登録できること、掲載誌名・著者・出版年が欠けた応答でページが落ちず不足した必須項目の名前を含むエラーになることを確かめるテストが成功する
  - 4.1 と同じファイルを編集するため、4.1 の後に行う
  - _Requirements: 6.1, 6.2, 6.3, 6.4_

- [x] 4.3 (P) CLI の引数の振り分けを修正する
  - backup / restore の引数の定義を 1 か所にまとめ、コマンド全体の引数の解析を経由しても、出力先・バックアップのファイル・確認の省略が本体に渡るようにする（KB1）。未知の引数が残れば終了コード 2 で終了する
  - サイドバーのナビゲーションの既定の指定は `run` のときだけ、明示指定（空白区切りと `=` 形式）がなければ付ける（KB8）。`version`・`backup`・`restore` には付けない（KB9）
  - 既存のテストが使う、引数のリストを受け取る backup / restore の呼び出し方は変えない
  - KB1（2 件）・KB8・KB9 の xfail が外れて成功し、`version` に渡る引数が与えた引数と完全に一致すること、backup / restore に未知の引数があると終了コード 2 になることを確かめるテストが成功する
  - CLI の restore に不正なメンバー名を含む zip を渡すと、データディレクトリを変えずに 0 以外の終了コードで終わることを確かめるテストが成功する
  - _Boundary: Cli_
  - _Depends: 3.1_
  - _Requirements: 9.3, 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8_

- [x] 5. 統合
- [x] 5.1 論文リストの保存時の退避と、その通知を統合する
  - 論文リストを保存するときは、書き込みの前にファイルの状態を確かめ、壊れていれば退避してから書く。退避先を画面に表示する
  - 画面の再描画で消えないよう、退避先を通知として残し、次の描画で表示してから消す（通知のキーはアプリの helper 側に定義し、依存の向きを守る）
  - 壊れた論文リスト（解析できない、UTF-8 として読めない、一覧の形をしていない）を置いて各ページを描画すると警告が出てページが落ちないこと、壊れた状態で論文を登録すると退避ファイルができて退避先が表示され、元の内容が退避ファイルに残ることを確かめるテストが、Streamlit 1.50 でも成功する
  - _Depends: 1.3, 2.3, 3.5_
  - _Requirements: 10.1, 10.3_

- [x] 5.2 論文リストページの PDF の表示・エクスポート・削除と、編集ダイアログを統合する
  - 詳細のビューアとダウンロード、複数選択の zip エクスポートで、PDF の読み込みに失敗したら、失敗したことと表示用のパスを画面に出す（エクスポートは残りのファイルを続ける）
  - 1 件だけの PDF の名前が番号でない場合（移せなかった旧形式の PDF）でも、ダウンロードの名前を作れる
  - 孤立 PDF の削除に失敗したパスと理由を画面に出す
  - 編集ダイアログは、更新に成功したときだけ再描画し、失敗したときはエラーを表示したままにする
  - 詳細の BibTeX 表示とエクスポートで、論文の複製の引用キーを 2.5 の処理で整えて書き出す（論文リストの引用キーは変えない）
  - 論文リストページのスモークテストが引き続き成功し、ビューアの読み込みの失敗と孤立 PDF の削除の失敗が画面に表示されること、引用キーに `,` を含む既存の論文の詳細とエクスポートが落ちずに整えた引用キーで出力され `list.json` の ID が変わらないこと、まとめてのエクスポートで整えた引用キーが重複しないことを確かめるテストが、Streamlit 1.50 でも成功する
  - _Depends: 2.2, 2.5, 3.2, 3.3, 3.4_
  - _Requirements: 1.2, 1.4, 3.3, 13.3, 13.4_

- [x] 5.3 (P) 画面からの復元とバックアップのページを統合する
  - 画面からの復元は、複製していた処理を削除して、共通の復元処理を呼ぶ（プレビューの表示は残す）
  - バックアップのページの PDF の件数を、変換後のパスから数える
  - 画面からの復元で、不正なメンバー名を含む zip が中止されてデータが変わらないこと、正当なバックアップが復元されることを確かめるテストが成功する（テストは、5.1・5.2 とは別の、復元の helper のテストと同じモジュールに置く）
  - _Boundary: RestorePage, BackupPage_
  - _Depends: 3.1_
  - _Requirements: 1.2, 9.3_

- [x] 5.4 Windows のパスの扱いを模して、PDF を扱う各機能の配線を検証する
  - 1.2 の fixture を有効にして、PDF の保存・一覧・削除・孤立 PDF の検出と削除・バックアップ・復元が成功することを確かめる
  - zip のメンバー名に prefix が入らないこと、孤立 PDF の削除で PDF ルート自体が削除されないことを確かめる
  - 配線のテストがすべて成功する
  - _Depends: 1.2, 2.2, 3.1, 3.2, 3.3, 5.3_
  - _Requirements: 1.2, 12.3_

- [x] 6. 全体の検証
- [x] 6.1 スイート全体の実行と、既知バグの記録の解除を確認する
  - スイート全体を、既定の順序・逆順・ランダム順で、手元の Streamlit と Streamlit 1.50 の両方で実行し、すべて成功することを確認する
  - 修正した既知バグ（KB1〜KB10 の 16 件）の xfail がすべて外れ、既知の失敗が 0 件で、想定外の成功（XPASS）も 0 件であることを確認する
  - この spec で修正した観察事項（Requirement 1、3、6.2〜6.4、7、8.2〜8.3、9.2〜9.3、10、13）にテストがあることを、要件の番号と照合して確認する
  - 実データの保存先がスイートの実行前後で変わらないこと、リポジトリの作業ツリーに差分が増えないことを確認する
  - lint（ruff）を実行する。開発環境に無い場合は、別の環境の既存の ruff を読み取り専用で使うか、実行できなかったことを報告する
  - スイート全体が成功し、上記の確認結果を記録している
  - _Requirements: 12.1, 12.2, 12.5_

- [x] 6.2 CI の全 Python バージョンで成功することを確認する
  - ブランチの push について利用者の承認を得てから push し、CI の全マトリクス（Python 3.9〜3.13）の結果を確認する。失敗した場合は原因を特定して対処する
  - CI の全 Python バージョンでテストが成功したことを記録している
  - _Requirements: 12.4_

## Implementation Notes
- 1.1: `paper_manager.helper` から `to_fs_path` / `to_display_path` / `folder_name_key` を再エクスポートした。差し替え口は `paper_manager.helper._path._convert` / `_unconvert`（呼び出し時に参照されるので、名前で import した側にも効く）。純粋な変換は `_to_extended_length` / `_from_extended_length`。`to_fs_path("")` は ValueError（空のパスは渡さない）。Python 3.9 / 3.10 の `ntpath.normpath` は `\\?\` 付きのパスを変えないが、3.11 以降は正規化する（変換は prefix を付ける前に abspath するので結果は同じ）。比較キーの既知の差: U+0131（ı）と i / I は NTFS 風の比較では同じ名前だが、比較キーでは別になる（トルコ語などのタイトルで、Windows ではフォルダを共有しうる。設計どおりで範囲外）
- 1.2: `tests/conftest.py` の opt-in の fixture `windows_like_paths` は、変換を `/x` → `//x` の別名に差し替える（pathlib では `root == "//"` で別のルートになり、`==`・集合・`relative_to` が一致しない。ファイル操作は同じ場所を指して成功する）。**変換後の空間にとどまるかの確認に `Path.resolve()` / `os.path.realpath` / `os.path.relpath` / `os.path.commonpath` を使わない**（別名が外れて混在を再現しない）。Windows では skip する。Linux で `//x` が `/x` を指すことは CI（6.2）で確認する
- 1.3: `config_page` は描画後に論文リストを書き戻さない。論文リストをファイルに書くのは `save_paper_list`（登録・更新・削除）と復元の直接書き込みだけ。空のデータディレクトリで画面を開いても `list.json` は作られない。**steering の `tech.md`（「実行後に session state → list.json を書き戻す」）と `structure.md`（「永続化の単一経路: … または `config_page` の後処理」）が古くなったので、この spec の完了時に steering を更新する**（それまで新しいコードは書き戻しに頼らない）
- 2.1: 修正前の規則は `_dir_name_before_kb7_fix`（モジュール関数）に残し、2.2 / 3.2 は `Entry._pdf_dir_name_before_kb7_fix()` を使う（著者なし・空、または名前が空なら `None`、例外なし。`None` でなければ修正前の名前）。`legacy_pdf_dir_name` は「`pdf_dir_name` と異なり 255 文字以内」の条件をこれに足して作る。`pdf_dir_name` は空にならない（空になる入力は `Unknown`。空の名前は PDF ルートを指し、削除で全 PDF が消えうるため）。**3.3 で、PDF ルートそのものを削除しない防御を入れる**。後方互換は修正前の規則と約 1,000 万通りの比較で確認済み（違いは KB7・空の名前・KB2a・Windows の `"` だけ）。**2.2 と 3.2 が入るまで、KB7 の範囲の既存フォルダは一覧に出ず孤立 PDF に見えるので、その間でリリースしない**。`tests/test_helper.py` の docstring にある KB2a の記述は 6.1 で更新する
- 2.2: `Entry.legacy_pdf_dir_name`、`get_pdf_dir`（`to_fs_path(base_dir or DIRPATH_PDF) / pdf_dir_name`）、`get_pdf_files` の移行（修正前の名前のフォルダ → 現在の名前、旧形式の単一 PDF → フォルダの空き番号（フォルダの有無を問わない）、旧命名 → `NN.pdf`。判定は `^\d{2,}\.pdf$`、上書きは `os.path.lexists` で事前に確認）を実装した。移せなかった旧形式の単一 PDF は一覧の末尾に含める。`is_file` / `is_dir` の ENAMETOOLONG は存在しないものとして扱う（Python ≤3.13）。警告は `_warn_pdf_problem`（`st.warning` + ログ、表示用のパス）で、`st.session_state[KEY_SHOWN_PDF_WARNINGS]` で抑止する。`MAX_PATH_LENGTH` は削除した
- 2.2 からの申し送り: **5.1** で `config_page` の描画の冒頭（fragment の再実行は含めない）に `st.session_state[KEY_SHOWN_PDF_WARNINGS]` を消し、再描画の後も解消していない警告が 1 回出ることを確かめる AppTest を追加し、`_warn_pdf_problem` / `get_pdf_files` の docstring の「セッションの中で 1 回だけ」を「1 回の描画の中で 1 回だけ」に直す（現状は抑止がセッション単位）。**3.2** で、移せなかった旧形式の単一 PDF（変換後のパス）と PDF ルートの走査（変換前）が Windows で一致せず孤立に見える点を、変換後の空間で比較して解消する。**3.3** で、フォルダごとの削除のとき移せなかった旧形式の単一 PDF も削除の対象にする（変換後のパスで）。**3.4** で、移す先が既にあってフォルダを移さなかった論文のタイトルを変えると、修正前の名前のフォルダが孤立に見える点に留意する（孤立判定で修正前の名前を有効にする 3.2 で、改名後は該当しなくなる。観察として扱う）。FYI: `test_legacy_single_pdf_never_overwrites_a_name_differing_in_case` は Windows の大文字小文字を区別しない glob で失敗しうる（CI は Ubuntu のみ）。`sorted(glob)` は辞書順で、100 件以上あると番号順にならない（以前からの挙動）
- 2.3: `load_paper_list` / `backup_broken_paper_list` / `PaperListStatus` / `PaperListLoad` を `paper_manager.entry` から再エクスポートした。テストは design の Testing Strategy に合わせて `tests/test_paper_list.py` に置いた。MISSING はパスにファイルがない、またはディレクトリのとき。桁数の上限を超える整数と深すぎる入れ子も BROKEN。退避先の名前は `O_EXCL` で確保してから `copy2` で複製する（既存のファイルやリンクを上書きしない）。修正前の判定と 6,000 通りで突き合わせ、修正前に読めたファイルはすべて OK、挙動が変わるのはトップレベルが `null` のファイル（警告なしの空 → BROKEN）だけ。UTF-8 の BOM 付きファイルは、2.3 では修正前と同じく壊れている扱いだったが、利用者の判断で 2.6 で正常として読むようにした
- 2.3 からの申し送り: **5.1** の `save_paper_list` は、`backup_broken_paper_list` が `OSError` を送出したら list.json を書かずにエラーを表示する（`None` は「壊れていない」の意味で、失敗とは区別している）。壊れたファイルに正常な論文が混ざっていても部分的には読み込まない（内容は退避ファイルに残る）
- 2.4: `format_person` を `paper_manager.bib` から再エクスポートした。接尾辞の形は `von Last, Jr, First` を自前で組み立てる（名か姓が空でも区切りの `,` を残す。`str(Person)` は空の部分を落とすため）。単純な名前の出力は HEAD と 529,347 通りで一致。1 語の人名の前後の空白と `and  others` の二重空白はなくなる（フォルダ名・引用キー・既存の論文リストには影響しない）。前置詞のない 2 語の姓、小文字の名、名だけの人名は、再解析で部分が変わる（`<名> <姓>` の形の限界。要件 5.3 は満たす）。ミドルネーム・前置詞・接尾辞を含む筆頭著者では、同じ BibTeX を再登録してもフォルダ名が修正前と違うため重複として見つからない（既存のフォルダ名は変わらない）。**2.4 だけでは接尾辞付きの筆頭著者の引用キーに `,` が入り、表示とエクスポートが落ちる。2.5 と 5.2 より前にリリースしない**
- 2.4 からの申し送り: **2.5** の規則（空白・`,`・`{`・`}` を除く）で、2.4 の出力（接尾辞の形、波括弧やアクセントを含む人名）から作ったキーがすべて pybtex で解析できることをレビュアーがスクラッチで確認済み（`scratchpad/rev24/key25.py`）。**5.2** で `_render_citation` / `_render_export` を変えるときは、`tests/test_bib.py` の再解析のテストが `paper_manager.app._list._render_citation` を import しているので合わせて直す（一覧ページのテストへ移してもよい）。整えたキーには `\`・`"`・`'` が残るので、XML のエクスポート（bib2xml の経路）でも落ちないことを 5.2 で確かめる
- 2.5: `sanitize_citation_key` / `unique_citation_keys` を `paper_manager.entry` から再エクスポートした（5.2 は `_list.py` からここ経由で使う）。`str.isspace` は pybtex の `\s` と全コードポイントで一致する（レビュアーが U+0000〜U+10FFFF を走査して確認）。除くのは空白と `,{}` だけ（`{` は pybtex では読めるが、波括弧の対応のために除く）。`unique_citation_keys` の大文字小文字は pybtex の `OrderedCaseInsensitiveDict` に合わせて `str.lower`（`casefold` ではない）。`Entry.get_key` の重複判定は従来どおり大文字小文字を区別し、`update_entry_in_list` が自分自身のキーを含めたまま呼ぶ現状も変えていない（読めない文字を含む旧 ID の論文を更新すると、空いていれば読める ID に再生成される。従来の仕様の範囲）。修正前と比べた差は、筆頭著者・年に空白（`str.isspace`）や `,{}` が残る場合だけ（20 万件の差分で確認）
- 2.5 からの申し送り: **5.2** は、表示・エクスポートで複製した Entry の `ID` を `unique_citation_keys` で整える。整えたキーに `\`・`"`・`'` が残るので、XML のエクスポート（bib2xml の経路）でも落ちないことを確かめる。`tests/test_bib.py` の再解析のテストが `paper_manager.app._list._render_citation` を import している点も合わせて直す
- 2.6: 復号を `_PAPER_LIST_DECODING = "utf-8-sig"`（private）にした。除かれるのは先頭の BOM 1 つだけで、BOM だけ・BOM が 2 つ・空白の後や途中や末尾の BOM・UTF-16 / UTF-32 の BOM・不正な UTF-8 は壊れている扱いのまま（Python 3.9 でも確認）。`ENCODING`・`to_file`・エラー文言は変えていない（保存は BOM なし）。値（JSON の文字列）の中の U+FEFF は消えない。BOM なしの 6,000 通りで修正前と判定が一致（違いは、先頭が BOM になった 2 件が壊れている → 正常に変わっただけ）。復元（zip の `list.json` をバイト列のまま書く）で BOM 付きの `list.json` を含む zip を復元すると、ディスク上は次の保存まで BOM 付きのまま残り、次の保存で BOM なしになる（データは壊れず、退避ファイルも増えない）。`version.json` の復号は BOM を無視する対象に入れていない（アプリが BOM なしで書くファイルのため）
- 3.1: `find_unsafe_member_names(names)` は名前の文字列だけで判定する（空・NUL・先頭の `/` `\`・`/` と `\` のどちらで分割しても `..` の成分・**各成分の 2 文字目が `:`**）。`\` を含む正当なフォルダ名は受け付ける。**Windows の Python 3.9〜3.11 の pathlib は、結合のときに成分の途中のドライブ指定を再解析する**（`\\?\C:\data` + `pdf/C:x.pdf` → `C:x.pdf`）ので、ドライブの判定は名前全体ではなく成分ごとに行い、さらに `restore_from_zip` が `_find_escaping_members(data_root, names)` で、書き込みと同じ計算 `_join_member`（`data_root / Path(name)`）の結果が `data_root` の下に収まることを書き込みの前に確かめる（CI は Ubuntu のみで検出できないため、二重に防ぐ）。Python 3.8 / 3.11 / 3.12 の実物の関数を約 15.8 万件ファジングして、外に出る名前 0 件（陰性対照で過去の欠陥は検出できることを確認済み）。この抜け道は修正前の実装にもあった。検証は `BadZipFile` の判定の後、`.json.backup` の退避・書き込みより前に完結する。`./` のディレクトリのエントリで IndexError になっていた不具合と、`.pdf` で終わるディレクトリのエントリを空ファイルとして書く挙動も直った。2 文字目が `:` の判定は、英字以外（`1:x`）も拒否するが、アプリが作るフォルダ名（`<年> - <著者> - <タイトル>`）では年が `<1 文字>:` で始まる場合だけなので実質ゼロ（データは安全で、zip から該当の名前を除けば復元できる）
- 3.1 からの申し送り: **5.3** で `pages/_restore.py` の複製した `restore_from_zip` を削除するまで、画面からの復元は KB3 のまま（3.1〜5.3 の間はリリースしない）。helper の `restore_from_zip` のシグネチャは `zip_path: Union[Path, str, BytesIO]` のまま（design の例の `zip_source` とは違うが、呼び出し元は位置引数）。**5.4** の配線テストでは、`windows_like_paths` だけでは変換後のパスを使っているか判別できない（変換しない旧実装でも別名で動く）ので、変換が別のディレクトリへ写す `redirect_data_dir`（`tests/test_app_backup.py`）の方式も使う。実機の Windows での確認は手動確認の対象
- 3.2: `find_orphaned_pdfs` は `to_fs_path(DIRPATH_PDF)` を起点に走査し（戻り値は変換後のパス）、有効なフォルダ名を全論文の `pdf_dir_name` と `legacy_pdf_dir_name` の `folder_name_key` で判定する。PDF ルート直下の PDF は、各論文の `pdf_filename`・`<現在の名前>.pdf`・`<修正前の名前>.pdf` のいずれかと比較キーが一致すれば孤立にしない。修正前の実装と 9 種のレイアウト（移行の失敗の組み合わせを含む）で比べ、修正前に有効だったものが孤立に変わった例は 0 件（孤立でなくなる方向だけ）。戻り値は名前順。`delete_orphaned_pdfs(orphaned_pdfs, failures=None)` は、フォルダを渡されたら削除の前に `rglob("*.pdf")` で数え（サブフォルダ内も）、失敗は `(渡されたパス, 理由)`（理由は `OSError.strerror` でパスを含まない）を `failures` に追加して続行する。空になった親フォルダの削除で PDF ルートかを `to_fs_path(DIRPATH_PDF)` と比べる（変換前の値との比較が、混在時に PDF ルート自体を削除する原因だった）。全エントリの `get_pdf_files()` を先に呼ぶ（移行を済ませる。修正前から同じ）
- 3.2 からの申し送り: **5.2** は `failures` を `to_display_path` を通して画面に出す。空になった親フォルダの `rmdir` の失敗も `failures` に入る（PDF 自体は削除済みで件数に数えている）ので、「PDF の削除に失敗」と一律に表示せず文言を分ける。`rmtree` が途中で失敗したフォルダは、一部削除済みでも件数に数えず `failures` に入れる。`_list.py` の `relative_to` も表示用のパスにする。**3.3**: `delete_orphaned_pdfs` に PDF ルート自体や無関係なフォルダを明示的に渡すと `rmtree` する（設計の Non-Goals「孤立 PDF の削除に渡すパスの検証」のため検証していない。`find_orphaned_pdfs` はルートを返さない）。3.3 の `delete_pdfs` の防御と合わせて扱うか判断する。移す先が既にあって移されなかった修正前の名前のフォルダは、有効なまま孤立にならない（UI からは削除できず、改名後に該当しなくなる。安全側）
- 3.3: `save_pdfs` から 250 文字の規則を削除し、フォルダ作成・`glob`・書き込みの `OSError` を `_report_failure`（何をしようとして失敗したか・`to_display_path` のパス・理由）で `st.error` とログに出して、残りの処理を続ける。`delete_pdfs(entry, base_dir=None, specific_files=None)` は引数の順序・戻り値 `int` を変えず、先に `base_dir = base_dir or DIRPATH_PDF` と解決して `to_fs_path(base_dir)` と `entry.get_pdf_dir(base_dir)` を同じ値から作る。**PDF ルート保護**: `_is_direct_child(path, root)` が `path.parent == root` かつ名前が空・`.`・`..` でないことを文字列の上で確かめ、満たさないフォルダ名（空・`.`・`..`・`../x`・絶対パス・`a/b` など）では `rmtree` も空フォルダの `rmdir` もしない（22 種 × 2 モードで消えたファイル 0 件）。**フォルダごとの削除で、PDF ルート直下の `entry.pdf_filename` と `<pdf_dir_name>.pdf` も削除する**（以前はフォルダがあると旧形式を削除しなかった）。`<legacy_pdf_dir_name>.pdf` は削除しない（その名前の単一 PDF を作った版は無い。3.2 の孤立判定は有効とみなすので、あれば孤立にもならず残る）。存在確認（`is_file()` / `is_dir()`）の `ENAMETOOLONG` は `_exists_as` で「存在しない」として扱い（`pdf_filename` が 255 文字超のとき、削除に成功しても偽のエラーが出る退行をレビューで検出して修正）、他の `OSError` は従来どおり通知する。失敗の理由は `_failure_reason`（`strerror` → 単一の文字列引数 → 型名。変換後のパスの repr を画面とログに出さない）。test-suite の要件 9.3（250 文字の規則）のテストは、250 文字を超えても保存されること・書き込み失敗でエラーを出して残りを処理することのテストに置き換えた（`tests/test_components.py` の docstring に明記）
- 3.3 からの申し送り: **5.2** の `_list.py` は `delete_pdfs(entry)` を呼ぶだけでよい（失敗は関数の中で `st.error` に出す。返り値に失敗の一覧はない）。**範囲外の既存の不具合（この spec では直さない。リリースノートか別 spec で扱う）**: `save_pdfs` は既存名を `glob("*.pdf")`（大文字小文字を区別する）で数えて `open(..., "wb")` で書くため、大文字小文字を区別しないファイルシステム（macOS・Windows）で、フォルダ内に手で置かれた `00.PDF` があると `00.pdf` の保存で上書きする（アプリが作るのは小文字の `.pdf` だけ）。書き込みが途中で失敗したときの部分ファイルは、`open` 自体の失敗が既存ファイルの書き込み不可のとき利用者のファイルを消すおそれがあるため消さない（修正前と同じ）。`tests/test_components.py` の `redirect_data_dir`（変換が別のディレクトリへ写す方式）は `tests/test_app_backup.py` と同じ内容の重複。5.4 の配線テストで 3 か所目が必要になったら `tests/conftest.py` へ移す。`_delete_pdf_dir` / `_delete_pdf_dir_if_empty` の `is_dir()` の `ENAMETOOLONG` の保護は、`_delete_file` と同じ関数 `_exists_as` を通るが、専用のテストはない（`pdf_dir_name` が 255 バイトを超える Linux の非 ASCII タイトルでだけ起きる）
- 3.4: 登録は全論文、更新は自分を除く全論文と `folder_name_key(pdf_dir_name)` を比べて拒否する（文言は `FAIL: Duplicated` のまま。大文字小文字や Unicode 正規化だけが違うときは既存の名前を添える）。重複でなければ、改名の前に `original_entry.get_pdf_files()` で移行を済ませる。`_rename_pdf_dir` は変換後のパスどうしで行い、改名先が既にあり元のフォルダ自身でないなら上書きも統合もせず拒否する。**比較キーが等しく文字列が異なる改名だけ**、一時名（`.rename-<uuid4 の 8 桁>-<元の名前の手がかり>`、255 バイト以内、PDF ルートの同じ階層）を経由する（元 → 一時名 → 新）。2 回目の失敗は一時名から元に戻し、戻せなければ一時名のフォルダの場所をエラーに含める（PDF は失われず、孤立 PDF の走査が拾う）。元のフォルダ自身かの判定は、設計の `os.path.samefile` ではなく **`_is_same_folder`（`os.path.samestat(a.lstat(), b.lstat())`）**: `samefile` はシンボリックリンクをたどり、元のフォルダを指すリンクを「同じ」とみなして改名が進んでしまうため（`test_a_link_to_the_folder_is_not_the_same_folder` で固定）。macOS と、大文字小文字を区別する APFS の一時ディスクイメージ（Linux 相当）の両方で、スイート全体の結果が一致することを確認した（区別する環境でだけ実行されるテストが 4 件ある）。修正前の実装と 9 シナリオで比べ、仕様どおりの変更以外の差はない
- 3.4 からの申し送り（観察。この spec では直さない。最終の報告・リリースノートに載せる）: (1) **修正前に作られた、大文字小文字だけ違う 2 論文が 1 つのフォルダを共有している状態（macOS・Windows）で一方を改名すると、フォルダ全体が改名され、もう一方の PDF が見えなくなる**。PDF 自体は失われず、修正前も同じ挙動。この spec の登録・更新は、同じ状態をこれ以上作らない。**5.2** の編集ダイアログで、共有の警告を出すかを検討してよい。(2) 修正前に登録された重複の組でも、フォルダ名を変えない更新（タグの追加など）は拒否しない（要件 3.3 の趣旨は、新しい重複を作る更新の拒否。テストで固定）。(3) 登録は既存の論文との重複だけを見て、論文に紐づかない孤立フォルダが同名でも拒否せず、そのフォルダに保存する（修正前から同じ）。(4) 旧形式の単一 PDF の移行に失敗すると、更新は進み、タイトルを変えた後はその PDF が孤立に見える（移行の失敗は警告として出る）。(5) 現在の名前のフォルダと修正前の名前のフォルダが両方ある論文を更新すると、現在の名前のフォルダだけが改名され、修正前のフォルダは中身ごと残り、孤立に見える（PDF は失われない）。(6) `False` を返した場合でも、改名前の `get_pdf_files()` による移行（上書きなし）は済んでいる。(7) `_list.py` の編集ダイアログは戻り値を見ずに `st.rerun()` するので、拒否のエラーが画面に残らない（5.2 の担当）。(8) 一時名の再試行ループ（`_unused_temporary_path`）の変異は検出されない
- 3.5: `custom_entry` の著者とタグは同じ補助 `_multiselect_input`（`_split_default`）で、著者は ` and `（`AUTHOR_SEPARATOR`）、タグは `, ` で分割・連結する。` and ` を含むタグは分かれない。分割と連結は同じ `str.split` / `str.join` なので、どんな文字列でも触らずに保存すれば元と完全に一致する（重複する ` and `、前後の空白を含む）。`, ` 区切りで保存済みの著者（`King, Jr., Martin` を含む）は分割も書き換えもしない（自動変換は Non-Goals）。年は、空または有効（`str(int(v)) == str(v)` で往復し、`_MIN_YEAR = 1000`（`_common.py` 内、修正前の `min_value` と同じ）以上かつ今年+1 以下）なら `st.number_input`、無効なら `st.warning` + ログを出して同じキー `year` の `st.text_input`（値は元の文字列）に切り替える。触らなければ元の値を保ち、直せばその値で保存し、空にすれば年を除く（必須のタイプでは既存の必須項目の検証が拒否）。入力し直した値が無効な年のままでも拒否しない（BibTeX 取り込みで `n.d.` などは既に入りうるため、データ品質が新たに悪化する経路は増えない）。修正前の実装と、著者 33 種・タグ 24 種・年 35 種（article / misc、180 通り）で触らずに保存した結果を Streamlit 1.66 と 1.50 で比べ、値が変わるのは意図した修正だけ。**`02020`・`+2020`・`2_020`・全角やアラビア・インド数字・前後の空白を持つ年は、修正前は保存時に黙って `2020` などへ書き換わっていたが、今は警告を出して元の値を保つ**（要件 7.1「整数として往復しない」に沿った変更）。「今年」は `frozen_today`（`_common.date` を差し替え）でテストを固定している
- 3.5 からの申し送りと観察: 数値入力の `min_value` / `max_value` の値そのものを確かめるテストはない（判定側の `_as_valid_year` が範囲外の値を数値入力に渡さないので誤動作はしない）。リスト型の既定値の分岐、必須で年が空のときに今年を初期値にする分岐、著者のプレースホルダは、修正前から未テスト。必須の年が空の既存論文を編集して保存すると年が今年に置き換わる（数値入力の既存の仕様）。`entry.get(field, st.session_state.get(field))` が古い状態を引き継ぐ既存の仕組みにより、編集ダイアログを閉じたあと登録ページへ移ると、年が文字列のまま残って警告が出る可能性がある（以前から `title` なども同じで、新しい種類の問題ではない）。警告は再実行のたびに `_logger.warning` と `st.warning` で出る。`tests/test_register_flow.py:692` のコメント「複数人は KB4a」が古くなったので、**4.1 / 4.2 で更新する**。`{Barnes and Noble}` のような団体名は ` and ` で 2 つの選択肢に分かれる（結合すると元に戻る。design の Out of Boundary）
- 4.1: `_register.py` の `_load_bib_entries` が `load_bib` を呼び、**`PybtexError` 系（構文エラー・重複キー・重複項目・未定義マクロ・人名の誤りなど）と `UnicodeDecodeError` だけ**を捕捉して `(None, メッセージ)` にし、`_render_bib_form` は `st.error` を 1 つ出して `(False, None, [])` を返す（保存を呼ばない。ログにも `_logger.error` を 1 回）。文言は「BibTeX の構文が正しくありません: <pybtex の理由>」と「BibTeX ファイルの文字コードを読めません（UTF-8 で保存してください）。」で、既存のエラー（入力されていません・1つだけ・有効なエントリ・Duplicated・無効なDOI）と区別できる。文字コードのデコーダの詳細は画面に出さずログにだけ出す。想定外の例外（`IndexError` や `_validate_required_fields` の `KeyError`）は握りつぶさない（テストで固定）。`load_bib` を変異ファジング 19 万回（StringIO / BytesIO、UTF-16、10MB、5 万エントリ、NUL・BOM・CRLF を含む）にかけて、`PybtexError` 系と `UnicodeDecodeError` 以外の例外は 0 件。著者のない BibTeX の登録（5.2）は 2.1 と 2.4 で既に直っていた（4.1 は `_register.py` の著者まわりを変えていない。2.4 の直前の `src` では `KeyError: 'author'` で RED になることを確認）。`tests/test_register_flow.py` の古いコメント（「複数人は KB4a」）を直し、複数著者のケースを足した。KB6 の xfail が外れた
- 4.1 からの申し送りと観察（**この spec では直さない。最終の報告・リリースノートに載せる**）: (1) **構文は正しいが `fields.json` にないエントリタイプ（`@unpublished`・`@online`・`@proceedings`・`@conference`、標準の綴りの `@mastersthesis`（`fields.json` のキーは `masterthesis`））を登録すると、`_validate_required_fields` の `MAP_REQUIRED_FIELDS[entry_type]` が `KeyError` を出してページが落ちる**（修正前から。`_validate_required_fields` は DOI 経路と共有で、`fields.json` は Out of Boundary）。**4.2** は DOI 経路で同じ関数を使うので、4.2 で Crossref の type がこの表にないときの扱いを確かめる。(2) pybtex の理由（利用者の入力を含む）を `st.error` にそのまま渡すので、キーや人名に含まれる Markdown のリンク・画像が解釈される（HTML は解釈されない。ローカルの単一ユーザーアプリで、既存の `_report_duplicate` も同様のため低リスク）。(3) 同じ著者なしの BibTeX を 2 回登録、または同じタイトル・年の別の著者なし論文を登録すると、フォルダ名が `Unknown` で一致して `FAIL: Duplicated` になる（3.1 の比較キーから導かれる。文言は `Unknown` が原因とは示さない）。(4) NUL を含むタイトルと PDF を同送すると `ValueError: mkdir: embedded null character in path` でページが落ちる（2.1 の領域。修正前から）。(5) `@string` を 2 倍ずつ 40 段つなぐ入力は pybtex が展開しきれず OOM になる（pybtex 側。範囲外）。(6) 「握りつぶさない」テストは `IndexError` だけ（`except (PybtexError, KeyError)` への変更は検出されない）。(7) `clear_on_submit=True` のため、エラー後は入力が消えて貼り直しになる（他のエラーと同じ既存の挙動）
- 4.2: `_fetch_doi_metadata` は `Works().doi(doi)` の呼び出しだけを `(requests.exceptions.RequestException, KeyError)` で捕捉する（crossrefapi 1.6.0 と requests 2.34 のソースで確認: 接続失敗・タイムアウト・SSL・プロキシ・リダイレクト過多・URL 不正・JSON でない応答（`requests.exceptions.JSONDecodeError`）は `RequestException` の派生、JSON に `message` がないとき `KeyError`、404 は `None` で既存の「無効なDOI」）。取り込み処理の `KeyError` は握りつぶさない（`try` の外）。`_render_doi_form` は `st.error` を 1 つ（「DOIのメタデータを取得できませんでした（通信エラー: <例外の型名>）…」。例外の文面は入力やプロキシの URL を含みうるので画面に出さず、ログにだけ ERROR で出す）出して保存しない。`ERROR_MARKERS` に `network`（「通信」）を足し、「無効なDOI」・4.1 の「構文」「文字コード」と相互に排他的。`Works()` は引数なし（crossrefapi の既定の匿名 `mailto:anonymous`）のまま変えておらず、環境変数 `EMAIL` / `USER` が URL・params・ヘッダに混ざらないことをテストで固定した。`_create_entry_from_doi_metadata`（補助 `_as_text`・`_first_text`・`_author_name`・`_author_names`・`_publication_year`）: 欠けた項目と空・空白だけ・`None` の値は Entry に入れない（ファジング 3 万件で例外 0・空値 0・`ENTRYTYPE` は常に `fields.json` 内）。book は `publisher` を取り込み `journal` を設定しない。inproceedings は `booktitle`（`container-title` → `event.name`）と任意の `publisher`。年は **`issued` → `published-print` → `published-online` → `published`**（`created` は出版日ではないので入れない。research.md と一致）。団体著者（`name`）、`family` だけ・`given` だけの著者も取り込む。`type` が欠落・`None`・リスト・未知なら `misc`。応答が `dict` でない、または `DOI` を持たなければ「無効なDOI」と同じ扱い。`_constants.py` に `COLNAME_BOOKTITLE` / `COLNAME_PUBLISHER` を追加。KB10 の xfail が外れた
- 4.2 からの申し送りと観察（**この spec では直さない。最終の報告・リリースノートに載せる**）: (1) **design との差**: design は「巻・号・ページ・DOI は既存のまま（範囲外の観察: 任意項目の空の値）」としていたが、tasks.md 4.2 の「値のない項目は省いて」に従い、**空の `volume` / `number` / `pages` は保存されなくなった**（旧実装は `""` を保存）。これらを読むコードは src にない。(2) `entrytype4doi` の既存の対応表により、Crossref の `monograph`・`edited-book`・`reference-book`・`book-chapter` は `misc` になる（`publisher` は取り込まれない。design の Out of Boundary）。(3) 捕捉範囲の外に残る稀な経路（実測）: `REQUESTS_CA_BUNDLE` / `CURL_CA_BUNDLE` が存在しないパスを指すと素の `OSError`、最上位が辞書でない有効な JSON 応答（`[]`・`null`・`"x"`）で crossrefapi の `result["message"]` が `TypeError`、`x-rate-limit-*` ヘッダが不正だと crossrefapi 内部で `UnboundLocalError` / `ZeroDivisionError`。設計が `RequestException` と `KeyError` に限定し、バグを握りつぶさない方針なので放置。(4) 入力欄のヘルプには `doi.org/10.1107/...` 形式も入力できると書かれているが、コードはその文字列をそのまま `Works().doi()` に渡す（404 で「無効なDOI」になるはず。実ネットワークでは未確認）。(5) 編者だけの書籍は著者がなく、「author が必要」のエラーになる（design の Out of Boundary）。(6) `title` / `container-title` が複数要素のとき先頭の空でない要素を使う、`family` と `name` の両方を持つ著者の優先順位、年に bool・文字列が入ったとき省かれることは、テストで固定されていない（変異が生き残る）。(7) ログには例外の文面（URL と DOI のパス）を出す。認証情報付きプロキシでも、パスワードとプロキシの URL は出ないことを実スタックで確認した
- 4.3: backup / restore の引数定義を `_add_backup_arguments` / `_add_restore_arguments` の 1 か所にまとめ、`main()` のサブパーサーと単独の `backup(argv)` / `restore(argv)` のパーサーで共用する。本体は `_run_backup(output)` / `_run_restore(backup_file, force)`（`datetime`・`restore_from_zip`・`create_backup_zip`・`input` はモジュールの属性として参照するので、テストの差し替えは従来どおり効く）。`main()` は解析済みの値を本体に渡し、backup / restore に未知の引数が残れば `_reject_unrecognized`（サブパーサーの `error`、終了コード 2）で本体を実行しない。ナビゲーションの既定の指定は `with_navigation_default(cli_args) -> list[str]`（定数 `NAVIGATION_OPTION`）で、`run` のときだけ、空白区切りと `=` 形式の明示指定がなければ付ける。`version` に渡る引数は与えた引数と完全に一致し、backup / restore には何も付けない。`[project.scripts]` の `paper_manager.__main__:entrypoint`、`--help`・`-h`・`-v`・未知のサブコマンド・引数なしの出力と終了コードは、修正前の実装と同一（レビュアーが比較）。`restore_from_zip` が `(False, msg)` を返したときの `sys.exit(1)` は元からあり、不正なメンバー名の zip は `--force` あり・確認 yes のどちらも終了コード 1 でデータ不変・外へ書き込まない（`main` 経由と関数直接の両方をテスト）。KB1×2・KB8・KB9 の xfail が外れ、**既知の失敗（xfail）は 0 件になった**
- 4.3 からの申し送り: **範囲を少し超えた変更（レビューで妥当と判断）**: `--` を含む `run` では、既定の指定を `--` の**前**に付ける（修正前は `--` の後ろに付いていたため、アプリの引数 `sys.argv` に漏れて Streamlit に効かなかった）。Streamlit 1.66.1 / 1.50.0 の click 実装（`@click.argument("args", nargs=-1)`、`--` が区切り）と、`bootstrap.run` を差し替えた端から端までの実行で、アプリへ渡る引数が `['app-a','--app-flag']` に、`client.showSidebarNavigation` が False になることを確認した。アプリ側で `sys.argv` を読むコードは `src/` にない。`--` 以降の `--client.showSidebarNavigation` は明示指定とみなさない。argparse の `--` の扱いは Python 3.9.6・3.10・3.11・3.12・3.15 で同一（3.13 は環境になく未確認。**6.2 の CI で 3.13 を確認する**）。この挙動は `with_navigation_default` の単体テストだけで固定されていて、`main` 経由のテストはない（3.13 で argparse の扱いが異なる可能性があったため。CI で 3.13 を確認したら足してよい）。FYI（直さない）: `--force` なしで標準入力が閉じていると `restore` は `EOFError` のトレースバックで終了コード 1（修正前と同じ）。`restore_from_zip` は既存の `list.json` / `config.json` を `.backup` として残す（helper 側）
- 5.1: `save_paper_list` は書き込みの直前に `backup_broken_paper_list()` で実ファイルを判定する（session state の状態には依存しない）。**退避に失敗（`OSError`）したら書かずに `st.error` とログを出して `False` を返す**（session state も変えない）。退避したら、表示用のパス（`to_display_path`、Windows の `\` が Markdown で消えないようバッククォートで囲む）を `st.warning`・ログ・`st.session_state[KEY_NOTICES]` に出す。`KEY_NOTICES = "notices"` は `app/helper/_config.py` に定義し（`helper/__init__.py` から公開）、`_common.py` がそこから import する（依存の向き）。`config_page` は `st.set_page_config` の直後に `st.session_state.pop(KEY_SHOWN_PDF_WARNINGS, None)`（fragment の再実行は `config_page` を通らないので消えない）、`st.title` の後・本体の前に保留中の通知を `pop` して表示する。5 ページすべてで、通知は見出しより前に 1 回だけ出る。2.2 の申し送りの docstring（`_warn_pdf_problem`・`get_pdf_files`・`KEY_SHOWN_PDF_WARNINGS` のコメント）は「1 回の描画の中で 1 回だけ」に直した。`entry/__init__.py` に `KEY_SHOWN_PDF_WARNINGS` を再エクスポートした。**`save_paper_list` の戻り値を design の `None` から `bool` に変えた**（True なら保存済み、False なら退避できず保存していない。保存していないのに `SUCCESS: Registered` と出すのを避ける。例外にすると `_delete_entry` などで画面が落ちる）。呼び出し元は `register_entry_to_list`（False で追加したエントリを論文リストから外し、成功の通知を出さず False を返す）・`update_entry_in_list`（False で論文リストを更新前の内容と順序に戻して False を返す）・`_list.py::_delete_entry`（保存に成功したときだけ `st.rerun()`。5.2 の担当ファイルだが、この 1 か所だけ入れた）の 3 つだけで、いずれも戻り値を見る。論文リストが正常なときの保存の内容・session state の更新・ログは修正前と同じ（違いは書き込みの前に `list.json` を 1 回読んで判定すること）。BOM 付きの正常なファイル・ファイルなし・正常なファイルでは退避しない。同じ秒に 3 回保存すると退避名は `…`、`-1`、`-2`。16 スレッドの同時退避でも 16 個の別名が作られる（`O_EXCL`）
- 5.1 からの申し送りと観察（**この spec では直さない。最終の報告・リリースノートに載せる**）: (1) **保存に失敗したとき、保存の前に済んだ副作用は元に戻らない**: 登録で保存済みの PDF は孤立 PDF に見える（再登録すると同じフォルダに重複して保存される）、更新で改名済みの PDF フォルダは新しい名前のまま論文リストは旧タイトルのまま（新しい名前のフォルダが孤立 PDF に見える）、削除で「PDF も削除」を選んでいた場合は PDF だけ消えてエントリが残る（再度削除すれば完了する）。PDF 自体が失われるのは、利用者が削除を選んだ PDF だけ。実際に起きる条件は、論文リストがセッションの開始後に外から壊れる・退避の複製が失敗する・利用者が「PDF も削除」を選ぶ、の 3 つがすべて重なるときだけ（壊れた論文リストを最初から見ている間は空として読まれ、リストページは「論文が登録されていません」で早期 return するので、削除ボタンも孤立 PDF の削除ボタンも出ない）。修正前も `to_file` が例外を出せば同じ副作用の状態になった。事前確認が必要なら 5.2 か別の spec で検討する。(2) **fragment の再実行で通知が消えない/出ない挙動は、AppTest では fragment だけの再実行を再現できないため、テストで固定されていない**（コードと Streamlit の仕様の読みによる）。(3) ウィジェット操作による再実行のたびに、解消していない PDF 警告が 1 回ずつ出る（2.2 の申し送りが求めた挙動。ログにも描画のたびに出る）。(4) 退避に成功した後に `to_file()` が例外を出すと、通知に「退避してから保存しました」が残る（例外は画面に出る。退避ファイルに元の内容がある）。(5) 複数のタブ・プロセスからの同時保存で、一方が BROKEN を読んだ後に他方が先に書くと、退避ファイルに正常な内容が入ることがある（無害な余分なファイル。更新の競合は修正前からの既存の問題）。(6) 編集ダイアログ（`edit_entry`）は `update_entry_in_list` の戻り値を見ずに `st.rerun()` するので、保存に失敗するとエラーが画面に残らない（ログにはある）。**5.2** の「成功したときだけ再描画」で解消する。(7) **スクラッチ領域はシステムに消されることがある**（2026-11-04 に `st150/full150`・`rev43/order_plugin.py`・共通コンテキスト 2 ファイルが消えた）。`pip install --target <スクラッチ>/st150/full150 streamlit==1.50.0`（作業用の環境には入れない）で Streamlit 1.50.0 を作り直し、プラグインとコンテキストを書き直した
- 5.2: `app/_list.py` に `_read_pdf`（読めなければ `_report_failure` で表示用パスと理由を `st.error` に出し、`None`）・`_download_filename`（番号でない名前の単独 PDF は PDF の名前のまま）・`_add_pdf_to_zip` / `_zip_pdfs`（`ZipInfo.from_file` + `writestr`。通常のファイルでは修正前の `zipfile.write` とバイト列が一致。読めなかった PDF は入れずに続け、1 件も入らなければ `st.info("No PDF files available.")`）・`_with_citation_keys` / `_to_bibtex`（`Entry(entry)` の複製の ID だけを 2.5 の処理で整える。`list.json` の ID は変わらない。`.bib` と `.xml` の両方で、不正なキー 2 万組・大文字小文字の重複 3 万組が通る）・`_report_orphan_failures`（失敗したパスが依頼したパスそのものなら「PDF の削除に失敗」、依頼していなければ「空フォルダを削除できなかった」。失敗があれば rerun しない）を追加。`edit_entry` は `update_entry_in_list(...)` が `True` のときだけ `st.rerun()`（重複拒否・必須項目エラー・改名失敗では呼ばない）。`_report_failure` は `_common` の非公開名を import している（同じ文言形式に揃えるため。許容）。スイートは 2033 passed + 4 skipped（Streamlit 1.66・1.50・逆順・ランダム順）、xfail 0、HEAD の `_list.py` では新テストが 18 件失敗（レビュアーが独立に確認）、変異 26 件中 25 件を検出（残り 1 件は等価）
- 5.2 からの申し送りと観察（**この spec では直さない。最終の報告に載せる**）: (1) AppTest は fragment（編集ダイアログ）だけの再実行を再現できないので、`edit_entry` を直接呼ぶ `edit_screen` で `st.rerun` の呼び出し回数を数えて検証している（レビュアーが `st.rerun` を差し替えた直接記録と一致）。(2) 単一論文の `.bib` / `.xml` / `.zip` のダウンロード名は `Path.with_suffix` を使うため、フォルダ名に `.` を含むと途中で切れる（例: 著者 `King, Jr.` で `2020 - King, Jr.bib`。修正前から）。(3) 編集ダイアログで追加 PDF の保存に失敗（`save_pdfs` が `st.error`）しても、`update_entry_in_list` が `True` を返すと直後の `st.rerun()` でエラーが消える（HEAD も同じ。要件 1.4 に関わる）。(4) 孤立 PDF の削除が一部失敗すると rerun しないため、孤立 PDF の一覧と件数が次の操作まで古いまま残る（害はない）。(5) 複数選択の zip エクスポートで mtime が 1980 年より前の PDF があると未捕捉の `ValueError` で画面が落ちる（HEAD も同じ。`ZipInfo.from_file(..., strict_timestamps=False)` で回避できる）。(6) 共有フォルダの警告（任意項目）は実装していない。案: `update_entry_in_list` の前に、`pdf_dir_name` が変わるなら `folder_name_key(pdf_dir_name)` が元と同じ他のエントリを探して `st.warning`。(7) `tests/test_bib.py` の 10 種 × 2 位置の名前の再解析は `paper_manager.app._list` を通らなくなった（`_to_bibtex` は Streamlit を呼ばない純関数なので、必要なら import して戻せる）。(8) `st.info("No PDF files available.")` の文言のテストはない
- 5.3: `pages/_restore.py` の複製した `restore_from_zip` を削除し、helper の `restore_from_zip(BytesIO(uploaded_file.getvalue()), update_session_state=True)` を呼ぶ（プレビューは維持）。`pages/_backup.py` の PDF 件数は `to_fs_path(DIRPATH_PDF)` から `rglob("*.pdf")` で数える（`list.json` / `config.json` のサイズ表示は範囲外で変えていない）。画面のテスト 17 件を `tests/test_app_backup.py` に追加（本物のページを `run_page` で通し、`fake_file_uploader` と `st.rerun` の記録だけを差し替え。Streamlit 1.50 の AppTest には `at.file_uploader` が無いため）。HEAD の 2 ページに戻すと 8 件失敗（不正名 6、変換後の復元 1、変換後の件数 1）、変異 19 種中、生き残りは等価または範囲外の 4 種のみ。旧実装と新実装で正当な復元の結果（バイト列・`.json.backup`・セッション・メッセージ・rerun の回数）は 18 通りで一致。意図した差は、`./` のディレクトリエントリで落ちなくなった・`.pdf` で終わるディレクトリを空ファイルとして書かなくなった・不正名を含む zip は全体中止（旧は有効部分だけ復元、または外へ書いた）・`pdf/a/../b.pdf` を拒否する（3.1 の仕様）の 4 点。**これで画面の復元の KB3 は解消した。スイートは 2050 passed + 4 skipped（Streamlit 1.66・1.50・逆順・ランダム順）、xfail 0**
- 5.3 からの申し送りと観察（**この spec では直さない。最終の報告に載せる**）: (1) 画面の復元は成功すると `st.success` / `st.info` の直後に `st.rerun()` するため、成功メッセージが画面に残らない（旧実装から。本物の `st.rerun` の AppTest で確認。修正案: 成功メッセージを `KEY_NOTICES`（5.1）に積む）。(2) 復元した `config.json` にプロキシ設定が無くても、`apply_proxy_to_environment` は環境変数を消さないので、以前に設定した `HTTP_PROXY` / `HTTPS_PROXY` が同じプロセスに残る（設定ページの保存も同じ）。(3) 壊れた `list.json` を含む zip を復元しても成功と表示され、セッションの論文リストは空になる（次の保存時に 5.1 が退避する）。(4) `PaperList.__init__`（`entry/_entry.py:1047`）が変換前の `DIRPATH_PDF` を `os.makedirs` するので、ページを開くたびに空の PDF ルートが作られる。Windows でパスが長いと失敗しうる。`pdf` が通常ファイルだと `FileExistsError`。**実機の手動確認の対象**。(5) `st.error` は Markdown として描画するので、`\` を含む不正名の表示が実際と違いうる（検出と中止には影響しない）。(6) `version.json` が有効な JSON でも辞書でない（`[1,2]`、`42`）場合、プレビューは通り、復元は書き込みの後に `'list' object has no attribute 'get'` で失敗する（旧実装から）。(7) `list.json` メンバーの CRC が壊れた zip を復元すると、`.json.backup` へ退避した後に `list.json` が 0 バイトで残る（退避ファイルから戻せる。復元の書き込みは原子的ではない。旧実装から）。(8) `rerun_calls` fixture が `tests/test_app_backup.py` と `tests/test_components.py:3101` で重複している（`redirect_data_dir` とあわせて **5.4** で `tests/conftest.py` へ集約する）。`UploadedBackup(FakeUpload)` は `size` を足すだけなので、`conftest.FakeUpload` に `size` を持たせれば不要になる
- 5.4: テストだけ（`src/` の差分 0）。`tests/conftest.py` に `redirect_data_dir`（関数のまま。`sample_data` など `Entry.get_pdf_dir` で場所を決める fixture より後に差し替える必要があるため fixture にしない）・`rerun_calls` を集約し、`FakeUpload.size` で `UploadedBackup` を不要にした。opt-in の `converted_space`（`windows-like` / `redirected` の 2 通り。**`sample_data` より先に要求すると、そちらも変換後の場所に作られる**）を追加。配線は `tests/test_path.py`（保存の書き込み先、一覧と表示用パス、孤立 PDF の検出、バックアップの全ファイルと prefix なしのメンバー名、バックアップ→全削除→復元→一覧・孤立判定の一連の流れ、全孤立の全削除で PDF ルートが残る）を2空間で通し、画面の流れ（`test_app_backup.py`）、一覧ページのビューア・ダウンロード・zip エクスポート（`test_pages_smoke.py`）、孤立 PDF の削除失敗の表示（`test_app_orphan_pdf.py`）も2空間で確かめる。消えたテストは、`test_entry.py::test_listing_and_migrations_work_in_the_converted_space` が `[windows-like]` / `[redirected]` に分かれた 1 件だけ（assertion は弱まっていない）。テスト ID は HEAD の 2054 → 2083（+29）。実装者の変異 29 種中 26 を検出、レビュアーの独自の変異 27 種中 新規テストだけで 19、既存テストを含めて 23 を検出（残り 4 種は等価）。**本体の不具合は見つからなかった。スイートは 2079 passed + 4 skipped（Streamlit 1.66・1.50・逆順・ランダム順）、xfail 0**
- 5.4 からの申し送り: (1) `windows_like_paths` は変換前のパスで動く旧実装でも成功する（変換後のパスを使っているかを判別するのは `redirected` 空間）。(2) `PaperList.__init__` が変換前の空の PDF ルートを作る副作用（5.3 の観察 4）のため、`redirected` 空間でも「変換前の PDF ルートの存在確認だけ」が変換前のパスという変異は検出できない（実機の手動確認の対象）。(3) `_orphan_pdf.py::record_failure` の `_logger.error` の表示用パスをどのテストも固定していない（画面側は固定済み）。(4) 全孤立の全削除のテストは、最後に削除するのがルート直下の旧形式 PDF である並びに依存する（フォルダ内の PDF を最後に削除して親を辿る実装は、既存の `test_delete_keeps_the_pdf_directory_itself[pdf-in-folder]` が検出する）。(5) 集約しなかった重複ヘルパー（`describe_exceptions`、`error_messages`、`fail_rename_of`、`pdf_tree`、`stored_papers`、`widget_in_form`、`snapshot`、`read_members` ほか）は、この spec では触らない
- 6.1: スイート全体 2087 passed + 4 skipped、xfailed 0、XPASS 0（Streamlit 1.66.1・1.50.0 × 既定・逆順・ランダム順）。`tests/` に `xfail` マークは 0 件（`pyproject.toml` の `xfail_strict = true` は残す）。実データ（`~/.paper-manager`）の `list.json` / `config.json` の mtime は不変。ruff check は `src` と `tests` で成功。`ruff format --check` の差分は、`main` にもある分だけ（このブランチで増えた 2 か所 `tests/test_bib.py` / `tests/test_entry.py` は整形した）。要件と受け入れ基準の照合（独立のサブエージェント、読み取り専用）: 対象要件（1、3、6.2〜6.4、7、8.2〜8.3、9.2〜9.3、10、13）の 26 項のうち 25 項は完全に検証されていた。不足は **13.1 の 1 項だけ**（`get_key` の単体止まりで、登録・更新の経路（`register_entry_to_list` / `update_entry_in_list`）を `,` や空白を含む著者で確かめるテストがなく、その配線を修正前の方式に差し替えても 1118 件が全部通った）。`tests/test_components.py` に登録と更新の各 4 パターン（`Smith, Alice`・`King, Jr., Martin`・`Alice {Smith}`・複数著者）の 8 件を足し、サニタイズを外した変異で 8 件とも失敗することを確かめた。サンプル検証（観察事項 16 項目で、実装を修正前の挙動に戻す変異）では、対応するテストがすべて失敗した。ステアリングの `tech.md` / `structure.md` を、`config_page` が書き戻さないこと・`save_paper_list` が成否を返すこと・パスの変換・壊れた `list.json` の退避・復元の事前検証・引用キーの整形・テストの隔離と Windows のパスを模す fixture に合わせて更新した。`tests/test_helper.py` の docstring（KB2a は修正済み）も更新した
- 6.1 からの申し送り: 大文字小文字を区別する環境でだけ実行されるテストが 4 件ある（macOS の既定では skip。CI の Linux で実行される）ので、本機の結果だけでは検証が済まない。**6.2 の CI の結果で確認する**。照合で気づいた点（この spec では直さない）: 12.5 の「修正前と同じ入力で結果が変わらない」の個別の精査は行っていない。`tests/` 外の README・docs に既知バグやデータ形式の記述が残っていないかは未確認
- 6.2: 利用者の承認を得て `fix/known-bugs` を push し（upstream を設定）、CI（GitHub Actions、Python 3.9〜3.13）を確認した。**最初の push の CI はテスト基盤の 2 件で失敗した**（本体のコードの不具合ではない）: (1) `tests/test_app_backup.py` の `write_zip` が `ZipFile.writestr("", ...)` を呼ぶと、Python 3.11 以前の zipfile は `IndexError` になる（3.12 で直った）。空の名前だけ `ZipInfo` で渡すようにした。(2) ライブラリのルートロガーは `propagate = False` なので、Python 3.9 で入る pytest 8.4 の `caplog` はライブラリのログを記録できない（pytest 9 は `propagate = False` のロガーにもハンドラを付ける）。`caplog` を読む 9 テストが落ちた。`caplog.text` の「含まない」の assertion は、何も記録されなくても成功する空振りだった。`tests/conftest.py` で `caplog` を上書きし、ハンドラをライブラリのルートロガーにも付けた（付いている版・伝播する場合は何もしない）。再現と確認は、手元の別の Python（3.10 / 3.11）に依存パッケージを `pip --target` でスクラッチに入れ（既存の環境には入れていない）、pytest 8.4.2 と Streamlit 1.50.0 の組み合わせで行った。**修正後の CI は、Python 3.9・3.10・3.11・3.12・3.13 のすべてで成功（各 2091 passed、skipped 0）。macOS では skip になる、大文字小文字を区別する環境向けの 4 件も Linux で実行されて成功した**
- 6.2 からの申し送り: 手元の主環境（Python 3.12・pytest 9.1）だけでは、古い pytest・古い Python でだけ起きる不具合を見落とす。CI と同じ組み合わせ（pytest 8.4 + Streamlit 1.50）での確認が有効だった。`workflow_dispatch` / `push` の CI は `pytest -xs` と fail-fast の matrix なので、最初に落ちた 1 バージョン・1 件しか分からない（他の版は cancelled になる）
- 検証（`/kiro-validate-impl`）: **GO**。全タスク 1.1〜6.2 が完了。スイートは手元で 2087 passed + 4 skipped（Streamlit 1.66.1・1.50.0 × 既定・逆順・ランダム順）、Python 3.10・3.11 でも通り、CI は Python 3.9〜3.13 のすべてで成功（各 2091 passed、skipped 0）。追加行の TODO / FIXME / 秘密情報 / メールアドレスは 0 件。スモーク: `paper-manager version` / `--help` / `backup` / `restore`（スクラッチのデータ）と、ヘッドレスのサーバー起動（`/_stcore/health` が ok、ルートが HTTP 200）が成功。判断系（独立のサブエージェント）: 依存の向きの逆流 0 件・循環 0 件、File Structure Plan と実際の配置が一致、境界の侵食なし、`list.json` の書き込み経路は `save_paper_list` と復元の 2 つだけ（`config_page` は書かない）、`restore_from_zip` / `create_backup_zip` の実装は 1 か所（画面と CLI が共用）、ユーザーデータの安全（自動移行・孤立 PDF の削除・復元・フォルダ改名）は再現して確認。Critical なし。検証で直した記述のずれ: `_dir_name_before_kb7_fix` の docstring、steering のパス変換の対象（`list.json` / `config.json` は対象外）
- 検証で見つかった未解決事項（この spec では直さない。利用者への最終報告に載せる。重要度の高い順）: (1) 復元で zip メンバーの CRC が壊れていると、読む前に書き込み先を開くので `list.json`（`config.json`・PDF も）が 0 バイトになり、続けて正常な zip を復元すると `.json.backup` の旧内容も失われる（旧実装から。修正は数行: 書き込み前に内容を読む）。(2) 編集ダイアログで追加 PDF の保存に失敗しても、直後の `st.rerun()` でエラーが消える（要件 1.4 の編集ダイアログ経路。旧実装から）。(3) 論文リストの保存に失敗すると、保存前の副作用（保存した PDF・改名したフォルダ・削除した PDF）は戻らない（退避の失敗などが重なる稀な場合）。(4) `fields.json` にない BibTeX のエントリタイプ（`@online`、`@unpublished`、`@proceedings`、`@conference`、`@mastersthesis`）で登録ページが `KeyError` で落ちる（旧実装から）。(5) 画面の復元の成功メッセージが `st.rerun()` で消える（旧実装から）。(6) 実機の Windows で未確認の経路: `list.json` / `config.json` / `PaperList.__init__` の `makedirs` は変換しない（データディレクトリ自体が約 250 文字を超える環境は未対応）。(7) 修正前に作られた、大文字小文字だけ違う 2 論文が 1 つのフォルダを共有する状態で一方を改名すると、もう一方の PDF が見えなくなる（PDF は失われない）。ほか Info 約 30 件（複数選択の zip エクスポートで 1980 年より前の mtime の PDF、DOI の稀な例外、`Path.with_suffix` によるダウンロード名の切れ、`save_pdfs` が大文字小文字を区別しない FS で手で置いた `00.PDF` を上書きしうる、プロキシ環境変数が残る、共有フォルダの警告は未実装 など）
- 実機の Windows で人が確認する項目（CI では確認できない）: 260 文字超の PDF の保存・表示・削除・エクスポート・バックアップ・復元、`\\?\` パスでの `glob` / `rglob` / `rename`、Python 3.9〜3.11 の pathlib が成分途中のドライブ指定を再解析することへの二重防御
