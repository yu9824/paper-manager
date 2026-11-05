import errno
import os
import re
import shutil
import uuid
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Optional, Union

import streamlit as st
from streamlit.runtime.uploaded_file_manager import UploadedFile

from paper_manager._constants import (
    AUTHOR_SEPARATOR,
    COLNAME_AUTHOR,
    COLNAME_TAGS,
    COLNAME_YEAR,
    DIRPATH_PDF,
    FILEPATH_LIST,
    TAG_SEPARATOR,
)
from paper_manager.app.helper import (
    KEY_NOTICES,
    MAP_FIELDS,
    MAP_REQUIRED_FIELDS,
)
from paper_manager.entry import Entry, PaperList, backup_broken_paper_list
from paper_manager.helper import folder_name_key, to_display_path, to_fs_path
from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)


def is_required_field(field_name: str, entrytype: str) -> bool:
    return field_name in MAP_REQUIRED_FIELDS[entrytype]


def pdf_upload_form(
    accept_multiple: bool = True,
) -> list[UploadedFile]:
    """PDFファイルアップロードウィジェットを表示する。

    Parameters
    ----------
    accept_multiple : bool, optional
        複数ファイルのアップロードを許可するかどうか。デフォルトはTrue。

    Returns
    -------
    list[UploadedFile]
        アップロードされたファイルオブジェクトのリスト。
    """
    uploaded_files = st.file_uploader(  # type: ignore[call-overload]
        "PDF file(s) (.pdf)",
        type="pdf",
        accept_multiple_files=accept_multiple,
        help="PDF file(s) (.pdf), optional",
    )

    if uploaded_files is None:
        return []

    if isinstance(uploaded_files, list):
        return uploaded_files
    else:
        return [uploaded_files]


# 数値入力 (``st.number_input``) で入力できる最小の年。最大は今年の翌年。
_MIN_YEAR = 1000


def _split_default(
    default: Union[str, list[str], None], separator: str
) -> tuple[str, ...]:
    """複数選択の既定値を、``separator`` で 1 つずつの選択肢に分ける。

    ``separator.join`` で連結すると、文字列の既定値は元の文字列に完全に
    一致する (空白、前後の空白、重複した ``separator`` を含む)。

    Parameters
    ----------
    default : Union[str, list[str], None]
        既定値。文字列は ``separator`` で分け、リストはそのまま使う。
        ``None`` と空の文字列は選択肢なし。
    separator : str
        区切り (著者は ``AUTHOR_SEPARATOR``、タグは ``TAG_SEPARATOR``)。

    Returns
    -------
    tuple[str, ...]
        選択肢 (既定値でもある)。
    """
    if isinstance(default, str):
        return tuple(default.split(separator)) if default else tuple()
    if isinstance(default, list):
        return tuple(default)
    return tuple()


def _multiselect_input(
    field: str,
    default: Union[str, list[str], None],
    separator: str,
    placeholder: Optional[str] = None,
) -> Optional[str]:
    """著者やタグの複数選択を表示し、選択を ``separator`` で連結して返す。

    既定値の分割と選択の連結に同じ ``separator`` を使うので、変更せずに
    保存した文字列は元の表記のまま。``separator`` を含まない文字列
    (例: ``, `` 区切りで保存済みの著者) は、分割されず 1 つの選択肢になる。

    Parameters
    ----------
    field : str
        フィールド名 (ウィジェットのキーでもある)。
    default : Union[str, list[str], None]
        既定値 (``_split_default`` を参照)。
    separator : str
        区切り。
    placeholder : Optional[str], default None
        何も選ばれていないときの表示。

    Returns
    -------
    Optional[str]
        選択を ``separator`` で連結した文字列。何も選ばれていなければ
        ``None``。
    """
    assert isinstance(default, (str, type(None), list))
    options = _split_default(default, separator)
    selected = st.multiselect(
        field,
        options=options,
        default=options,
        accept_new_options=True,
        key=field,
        placeholder=placeholder,
    )
    return separator.join(selected) if selected else None


def _as_valid_year(value: object, max_year: int) -> Optional[int]:
    """``value`` が数値入力で扱える年なら整数で、そうでなければ ``None``。

    整数として往復し (``str(int(value)) == str(value)``。``n.d.``、
    ``2020a``、``+2020``、``02020``、全角の数字、前後の空白は往復しない)、
    ``_MIN_YEAR`` 以上 ``max_year`` 以下の年を有効とする。

    Parameters
    ----------
    value : object
        保存されている年 (空でないもの)。
    max_year : int
        数値入力の最大値。

    Returns
    -------
    Optional[int]
        有効な年。無効なら ``None``。
    """
    text = str(value)
    try:
        year = int(text)
    except ValueError:
        return None
    if str(year) != text or not _MIN_YEAR <= year <= max_year:
        return None
    return year


def _year_input(
    field: str, default: object, required: bool, placeholder: str
) -> Optional[str]:
    """年の入力欄を表示し、入力された年を文字列で返す。

    保存されている年 (``default``) が空か有効 (``_as_valid_year``) なら、
    数値入力を表示する。無効 (整数として往復しない、または入力できる範囲の
    外) なら、数値入力は例外になるので、警告を出して、元の値が入った文字の
    入力欄に切り替える。

    文字の入力欄では、触らなければ元の値を返す。入力し直せば、その値
    (前後の空白を除く) を返し、空 (空白だけを含む) にすれば ``None`` を
    返す。入力値が有効な年かは確かめない (保存されている年が無効な間は
    警告が出続ける)。

    Parameters
    ----------
    field : str
        フィールド名 (ウィジェットのキーでもある)。
    default : object
        保存されている年。
    required : bool
        年が必須か。必須で空のとき、今年を初期値にする。
    placeholder : str
        空のときの表示。

    Returns
    -------
    Optional[str]
        入力された年。空なら ``None``。
    """
    this_year = date.today().year
    max_year = this_year + 1
    is_empty = default is None or default == ""
    year = None if is_empty else _as_valid_year(default, max_year)

    if is_empty or year is not None:
        if is_empty and required:
            year = this_year
        selected = st.number_input(
            field,
            value=year,
            format="%4i",
            placeholder=placeholder,
            step=1,
            min_value=_MIN_YEAR,
            max_value=max_year,
            key=field,
        )
        assert isinstance(selected, (int, type(None)))
        return str(selected) if selected else None

    original = str(default)
    message = (
        f"The saved year {original!r} is not a valid year (an integer from "
        f"{_MIN_YEAR} to {max_year}). Leave it as it is to keep the saved "
        "value, or enter a corrected year to replace it."
    )
    _logger.warning(message)
    st.warning(message)
    typed = st.text_input(
        field, value=original, placeholder=placeholder, key=field
    )
    if typed == original:
        return original
    return typed.strip() or None


def custom_entry(entry: Entry) -> Entry:
    """Entryオブジェクトをカスタマイズするためのフォームを表示する。

    エントリタイプに基づいて入力フィールドを動的に生成し、
    ユーザーが入力した値でEntryオブジェクトを更新する。

    - 著者は ``AUTHOR_SEPARATOR`` (`` and ``) で、タグは
      ``TAG_SEPARATOR`` (``, ``) で、既定値を選択肢に分け、保存時に連結
      する。変更しなければ表記は変わらない。``, `` 区切りで保存済みの著者は
      1 つの選択肢として、そのまま保存される。
    - 年は、保存されている値が空か有効なら数値入力で入力する。無効 (整数
      として往復しない、または入力できる範囲の外) なら、警告を出して文字の
      入力欄に切り替える。触らなければ元の値を保ち、入力し直せばその値で
      保存する。

    Parameters
    ----------
    entry : Entry
        カスタマイズするEntryオブジェクト

    Returns
    -------
    Entry
        更新されたEntryオブジェクト
    """
    entrytype = entry["ENTRYTYPE"]

    for field in MAP_FIELDS[entrytype]:
        default = entry.get(field, st.session_state.get(field))
        # 必須判定はプレースホルダー表示など UI 用にのみ使用し、
        # バリデーション自体は submit ボタン押下時に行う
        required = is_required_field(field, entrytype)
        _logger.debug(
            f"field: {field}, type: {type(default)}, value: {default}"
        )

        placeholder = "Required" if required else ""
        # 入力された値。空 (None) ならエントリからフィールドを除く
        value: Optional[str]
        if field == COLNAME_YEAR:
            value = _year_input(field, default, required, placeholder)
        elif field == COLNAME_AUTHOR:
            value = _multiselect_input(
                field, default, AUTHOR_SEPARATOR, placeholder
            )
        elif field == COLNAME_TAGS:
            value = _multiselect_input(field, default, TAG_SEPARATOR)
        else:
            assert isinstance(default, (str, type(None)))
            value = (
                st.text_input(
                    field,
                    value=default,
                    placeholder=placeholder,
                    key=field,
                )
                or None
            )

        if value is not None:
            entry[field] = value
        elif field in entry:
            _ = entry.pop(field)

    return entry


def _failure_reason(error: OSError) -> str:
    """失敗の理由を、パスを含まない文字列で返す。

    ``str(error)`` は ``filename`` のパス (変換後のパス。Windows では拡張長の
    パスの prefix 付き) を含むことがある。``strerror`` があればそれを、
    なければ ``error`` が単一の文字列で持つメッセージを、どちらもなければ
    例外の型の名前を返す。

    Parameters
    ----------
    error : OSError
        捕捉した例外。

    Returns
    -------
    str
        画面とログに出す理由。
    """
    if error.strerror:
        return error.strerror
    if len(error.args) == 1 and isinstance(error.args[0], str):
        return error.args[0]
    return type(error).__name__


def _report_failure(
    action: str,
    path: Path,
    error: OSError,
    failures: Optional[list[str]] = None,
) -> None:
    """ファイル操作の失敗を、ログと画面の両方に出す。

    画面とログには、何をしようとして失敗したかと、``to_display_path`` で
    prefix を外したパス、パスを含まない理由 (``_failure_reason``) を出す。
    ``failures`` を渡したときは、画面 (``st.error``) に出したものと同じ文を
    追加する。

    Parameters
    ----------
    action : str
        失敗した操作 (例: ``"Failed to save PDF file"``)。
    path : Path
        対象のパス (変換後のパスでも変換前のパスでもよい)。
    error : OSError
        捕捉した例外。
    failures : Optional[list[str]], optional
        画面に出した文を追加する一覧。省略すると何も追加しない。
    """
    reason = _failure_reason(error)
    message = f"{action}: {to_display_path(path)}"
    sentence = f"{message}\nError: {reason}"
    _logger.error(f"{message}: {reason}")
    st.error(sentence)
    if failures is not None:
        failures.append(sentence)


def _exists_as(check: Callable[[], bool], path: Path) -> bool:
    """``check`` (``path.is_file`` など) の結果を返す。名前が長すぎれば偽。

    名前が長すぎる (Linux と macOS の 255 バイトを超える) パスのファイルや
    フォルダは作れないので、``ENAMETOOLONG`` は存在しないものとして扱い、
    通知しない (``Entry`` の存在確認と同じ扱い)。``PermissionError`` など
    ほかの ``OSError`` は、存在しないことを意味しないので送出する。

    Parameters
    ----------
    check : Callable[[], bool]
        存在の確認 (例: ``path.is_file``)。
    path : Path
        確認するパス (ログ用)。

    Returns
    -------
    bool
        ``check()`` の結果。``ENAMETOOLONG`` のときは ``False``。

    Raises
    ------
    OSError
        ``ENAMETOOLONG`` 以外の理由で確認に失敗したとき。
    """
    try:
        return check()
    except OSError as e:
        if e.errno != errno.ENAMETOOLONG:
            raise
        _logger.debug(f"Name too long to exist: {to_display_path(path)}")
        return False


def _is_direct_child(path: Path, root: Path) -> bool:
    """``path`` が ``root`` の直下の名前であるかを、文字列の上で判定する。

    ``path`` と ``root`` は、どちらも ``to_fs_path`` で変換した起点から
    作ったパス (変換後の空間のパス) であること。変換前のパスと混ぜると、
    Windows では prefix の有無で一致しない。ファイルシステムは見ない
    (``resolve`` などで別名を外すと、変換後の空間から外れる)。

    Parameters
    ----------
    path : Path
        判定するパス。
    root : Path
        起点 (PDF ルート)。

    Returns
    -------
    bool
        ``path`` の親が ``root`` で、名前が空・``.``・``..`` でなければ
        ``True``。``root`` そのもの、``root`` の外、``root`` の孫以下は
        ``False``。
    """
    return path.parent == root and path.name not in ("", ".", "..")


def save_pdfs(
    uploaded_files: list[UploadedFile],
    entry: Entry,
    base_dir: Union[Path, None] = None,
    failures: Optional[list[str]] = None,
) -> int:
    """複数のPDFファイルを保存する。

    保存先は、ファイル操作用に変換したパス (``Entry.get_pdf_dir``。
    Windows では拡張長のパス) なので、パス全体の長さにかかわらず保存する。
    フォルダの作成やファイルの書き込みに失敗したら、そのパス (表示用の
    パス) と理由をログと画面に出し、残りのファイルの処理を続ける。

    画面のエラーは、画面が再描画されると消える。呼び出し元がその後も失敗を
    伝えられるよう、``failures`` を渡すと、画面 (``st.error``) に出したものと
    同じ文 (表示用のパスと理由。変換後の prefix は含まない) を、失敗ごとに
    1 文ずつ追加する。成功したファイルの分は何も追加しない。省略すると、
    これまでと同じ振る舞いになる。

    Parameters
    ----------
    uploaded_files : list[UploadedFile]
        アップロードされたPDFファイルのリスト
    entry : Entry
        対象の論文エントリ
    base_dir : Union[Path, None], optional
        PDFの基本ディレクトリ。Noneの場合はDIRPATH_PDFを使用。
    failures : Optional[list[str]], optional
        失敗の文を追加する一覧。渡された一覧の既存の中身は残し、後ろに
        追加する。

    Returns
    -------
    int
        保存したファイル数
    """
    if not uploaded_files:
        return 0

    pdf_dir = entry.get_pdf_dir(base_dir)

    # ディレクトリ作成時のエラーハンドリング
    try:
        pdf_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _report_failure("Failed to create PDF directory", pdf_dir, e, failures)
        return 0

    # 既存のPDFファイル名を取得しておき、重複しない新しい名前を付与する
    try:
        existing_files = {f.name for f in pdf_dir.glob("*.pdf")}
    except OSError as e:
        _report_failure("Failed to access PDF directory", pdf_dir, e, failures)
        return 0

    index = 0
    saved_count = 0

    for uploaded_file in uploaded_files:
        # エントリに基づいたわかりやすいファイル名を生成
        while True:
            new_name = entry._generate_pdf_filename(index)
            if new_name not in existing_files:
                break
            index += 1

        filepath = pdf_dir / new_name

        # ファイル保存時のエラーハンドリング
        try:
            with open(filepath, mode="wb") as f:
                f.write(uploaded_file.getvalue())
        except OSError as e:
            _report_failure("Failed to save PDF file", filepath, e, failures)
            continue  # このファイルはスキップして次のファイルを試す

        existing_files.add(new_name)
        _logger.debug(f"PDF saved: {to_display_path(filepath)}")
        saved_count += 1

    return saved_count


def _legacy_single_pdf_paths(entry: Entry, pdf_root: Path) -> list[Path]:
    """エントリの旧形式の単一PDFがありうるパスを返す。

    旧形式の単一PDFは、PDFルート直下の ``Entry.pdf_filename`` と
    ``<pdf_dir_name>.pdf`` (``get_pdf_files`` が移せなかったとき一覧に含めて
    返すものと、孤立PDFの判定が論文のものとみなすもの)。パスは、変換後の
    PDFルートから作る。

    Parameters
    ----------
    entry : Entry
        対象の論文エントリ
    pdf_root : Path
        変換後のPDFルート (``to_fs_path`` で変換したもの)

    Returns
    -------
    list[Path]
        重複のない、旧形式の単一PDFのパス。
    """
    names = dict.fromkeys([entry.pdf_filename, f"{entry.pdf_dir_name}.pdf"])
    return [pdf_root / name for name in names]


def _delete_file(filepath: Path, action: str) -> int:
    """ファイルが存在すれば削除する。失敗は通知して 0 を返す。

    Parameters
    ----------
    filepath : Path
        削除するファイル (変換後のパス)。ファイルでなければ何もしない。
    action : str
        失敗したときに画面に出す、何をしようとしたかの文。

    Returns
    -------
    int
        削除したら 1、存在しないか削除に失敗したら 0。
    """
    try:
        if not _exists_as(filepath.is_file, filepath):
            return 0
        filepath.unlink()
    except OSError as e:
        _report_failure(action, filepath, e)
        return 0
    _logger.debug(f"PDF deleted: {to_display_path(filepath)}")
    return 1


def _delete_pdf_dir(pdf_dir: Path) -> int:
    """PDFフォルダが存在すれば、中身ごと削除する。失敗は通知して 0 を返す。

    Parameters
    ----------
    pdf_dir : Path
        削除するエントリのPDFフォルダ (変換後のパス)。

    Returns
    -------
    int
        削除したPDFファイルの数 (削除の前に数える)。フォルダがないか、
        削除に失敗したら 0。
    """
    try:
        if not _exists_as(pdf_dir.is_dir, pdf_dir):
            return 0
        file_count = len(list(pdf_dir.glob("*.pdf")))
        shutil.rmtree(pdf_dir)
    except OSError as e:
        _report_failure("Failed to delete PDF directory", pdf_dir, e)
        return 0
    _logger.debug(f"PDF directory deleted: {to_display_path(pdf_dir)}")
    return file_count


def _delete_pdf_dir_if_empty(pdf_dir: Path) -> None:
    """PDFフォルダが存在して空なら削除する。失敗は通知する。

    Parameters
    ----------
    pdf_dir : Path
        エントリのPDFフォルダ (変換後のパス)。
    """
    try:
        if _exists_as(pdf_dir.is_dir, pdf_dir) and not any(pdf_dir.iterdir()):
            pdf_dir.rmdir()
            _logger.debug(
                f"Empty PDF directory deleted: {to_display_path(pdf_dir)}"
            )
    except OSError as e:
        # ファイルは削除済みなので、処理は続ける
        _report_failure("Failed to delete empty PDF directory", pdf_dir, e)


def delete_pdfs(
    entry: Entry,
    base_dir: Union[Path, None] = None,
    specific_files: Optional[list[Path]] = None,
) -> int:
    """PDFファイルを削除する。

    削除は、ファイル操作用に変換したパス (``to_fs_path``。Windows では
    拡張長のパス) で行う。``specific_files`` は変換前のパスでも変換後の
    パスでもよい。PDFフォルダが空かどうかの判定やPDFルートとの比較は、
    変換後のパスどうしで行う。

    エントリのPDFフォルダがPDFルートの直下の名前でないとき (フォルダ名の
    計算結果が空や ``..`` などで、PDFルートそのもの、またはその外を指す
    とき) は、フォルダを削除しない。

    ファイルやフォルダの削除に失敗したら、そのパス (表示用のパス) と理由
    をログと画面に出し、残りの削除を続ける。

    Parameters
    ----------
    entry : Entry
        対象の論文エントリ
    base_dir : Union[Path, None], optional
        PDFの基本ディレクトリ。Noneの場合はDIRPATH_PDFを使用。
    specific_files : Optional[list[Path]], optional
        削除する特定のファイル。Noneの場合は全PDFを削除（PDFフォルダごと、
        および移せなかった旧形式の単一PDFを削除）。

    Returns
    -------
    int
        削除したファイル数。削除に失敗したものは数えない。
    """
    # PDFルートとフォルダを、同じ値から作る
    base_dir = base_dir or DIRPATH_PDF
    pdf_root = to_fs_path(base_dir)
    pdf_dir = entry.get_pdf_dir(base_dir)
    is_safe_dir = _is_direct_child(pdf_dir, pdf_root)
    if not is_safe_dir:
        # 空の名前や `..` は、PDFルートそのもの、またはその外を指す
        message = (
            "Refused to delete a PDF directory that is not inside the PDF "
            f"root: {to_display_path(pdf_dir)}"
        )
        _logger.error(message)
        st.error(message)

    if specific_files is not None:
        # 特定のファイルのみ削除
        deleted_count = sum(
            _delete_file(to_fs_path(filepath), "Failed to delete PDF file")
            for filepath in specific_files
        )

        # ディレクトリが空になったら削除
        if is_safe_dir:
            _delete_pdf_dir_if_empty(pdf_dir)
        return deleted_count

    # 全PDFを削除（ディレクトリごと削除）
    deleted_count = _delete_pdf_dir(pdf_dir) if is_safe_dir else 0

    # 後方互換性: 旧形式の単一PDFファイルを削除（フォルダへ移せずに
    # PDFルートに残ったものが、孤立PDFとして現れないようにする）
    for legacy_pdf in _legacy_single_pdf_paths(entry, pdf_root):
        if _is_direct_child(legacy_pdf, pdf_root):
            deleted_count += _delete_file(
                legacy_pdf, "Failed to delete legacy PDF file"
            )

    return deleted_count


def save_paper_list(paper_list: PaperList) -> bool:
    """PaperListをセッションステートとファイルに保存する。

    書き込みの前に、論文リストのファイルの状態を確かめる (保存の直前に
    実ファイルを判定するので、画面を開いた後にファイルが壊れた場合も
    見逃さない)。壊れていれば、別名のファイル (``list.json.broken-<日時>``)
    へ退避してから書く。退避先 (表示用のパス) は、ログと画面 (``st.warning``)
    に出す。画面の再描画で消えないよう、同じ文言を session state の通知
    (``KEY_NOTICES``) にも残す。通知は、次の描画で ``config_page`` が
    表示してから消す。ファイルがない、または壊れていなければ、退避せずに
    書く。

    壊れたファイルを退避できなかった (またはファイルを読めず、状態を確かめ
    られなかった) ときは、壊れたファイルの内容を失わないよう、何も書かずに
    エラーをログと画面に出して ``False`` を返す。session state も変えない。

    Parameters
    ----------
    paper_list : PaperList
        保存する論文リスト

    Returns
    -------
    bool
        保存した場合True。退避できずに保存しなかった場合False。
    """
    try:
        backup = backup_broken_paper_list()
    except OSError as e:
        message = (
            "論文リストのファイルを確認または退避できなかったため、"
            f"保存しませんでした: `{to_display_path(FILEPATH_LIST)}`"
        )
        reason = _failure_reason(e)
        _logger.error(f"{message}: {reason}")
        st.error(f"{message}\nError: {reason}")
        return False

    if backup is not None:
        message = (
            "論文リストのファイルが壊れていたため、元のファイルを "
            f"`{to_display_path(backup)}` に退避してから保存しました。"
        )
        _logger.warning(message)
        st.warning(message)
        st.session_state.setdefault(KEY_NOTICES, []).append(message)

    paper_list.to_session_state()
    paper_list.to_file()
    _logger.debug("PaperList saved")
    return True


# 大文字小文字だけの改名で、PDF フォルダを一時的に移す名前の接頭辞
_TEMPORARY_PREFIX = ".rename-"
# 一時名に含める元の名前の手がかりの最大バイト数。一時名の全体が、ファイル名の
# 上限 (Linux・macOS は 255 バイト、Windows は 255 文字) に収まるようにする
_TEMPORARY_HINT_BYTES = 100
# 一時名の乱数部分が既存の名前と重なったときに、作り直す最大回数
_TEMPORARY_ATTEMPTS = 100
# Windows のファイル名に使えない文字と制御文字 (どの OS でも置き換える)
_UNSAFE_NAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _find_same_folder_name(
    paper_list: PaperList, pdf_dir_name: str, exclude_key: Optional[str] = None
) -> Optional[str]:
    """``pdf_dir_name`` と比較キーが等しい PDF フォルダ名を持つ論文を探す。

    比較キー (``folder_name_key``) は大文字小文字と Unicode 正規化を区別しない
    ので、ファイルシステムが大文字小文字を区別するかどうかにかかわらず、
    すべての OS で同じに判定する。

    Parameters
    ----------
    paper_list : PaperList
        論文リスト
    pdf_dir_name : str
        調べる PDF フォルダ名
    exclude_key : Optional[str], default None
        調べない論文のキー (更新する論文自身)

    Returns
    -------
    Optional[str]
        比較キーが等しい論文の PDF フォルダ名 (``pdf_dir_name`` と完全に
        同じこともある)。なければ ``None``
    """
    name_key = folder_name_key(pdf_dir_name)
    for key, existing in paper_list.items():
        if key == exclude_key:
            continue
        if folder_name_key(existing.pdf_dir_name) == name_key:
            return existing.pdf_dir_name
    return None


def _report_duplicate(headline: str, pdf_dir_name: str, existing: str) -> None:
    """PDF フォルダ名の重複を、ログと画面の両方に出す。

    既存の論文の名前が ``pdf_dir_name`` と完全に同じでなければ (大文字小文字や
    Unicode 正規化だけが違う)、利用者が理由を分かるように既存の名前を添える。

    Parameters
    ----------
    headline : str
        エラーの見出し
    pdf_dir_name : str
        登録・更新しようとした PDF フォルダ名
    existing : str
        重複した、既存の論文の PDF フォルダ名
    """
    message = headline
    if existing != pdf_dir_name:
        message += (
            "\nThe PDF folder name is the same as that of an existing paper "
            f"apart from letter case or Unicode normalization: {existing}"
        )
    _logger.error(message)
    st.error(message)


def _differs_only_in_form(old_name: str, new_name: str) -> bool:
    """フォルダ名が、大文字小文字や Unicode 正規化だけ変わるかを返す。

    Parameters
    ----------
    old_name : str
        元のフォルダ名
    new_name : str
        新しいフォルダ名

    Returns
    -------
    bool
        比較キーは等しく、文字列としては異なるとき ``True``
    """
    if old_name == new_name:
        return False
    return folder_name_key(old_name) == folder_name_key(new_name)


def _is_same_folder(first: Path, second: Path) -> bool:
    """2 つのパスが、同じフォルダの同じ名前を指すかを返す。

    大文字小文字を区別しないファイルシステム (macOS・Windows) では、大文字
    小文字だけ違う名前は同じフォルダを指す。区別するファイルシステム (Linux)
    では別のフォルダである。シンボリックリンクをたどらずに比べるので、元の
    フォルダを指すリンクは同じフォルダとみなさない。

    Parameters
    ----------
    first, second : Path
        存在するパス (変換後のパス)

    Returns
    -------
    bool
        同じデバイスの同じエントリ (inode) なら ``True``

    Raises
    ------
    OSError
        どちらかの状態を取得できないとき
    """
    return os.path.samestat(first.lstat(), second.lstat())


def _temporary_dir_name(original: str) -> str:
    """大文字小文字だけの改名で使う、PDF フォルダの一時名を作る。

    ``.rename-<乱数 8 桁>-<元の名前の手がかり>`` の形。PDF フォルダ名
    (``<年> - <著者> - <タイトル>``) とは重ならず、PDF ルートの走査が拾っても
    孤立 PDF として元の名前が分かる。手がかりは、元の名前の Windows で使えない
    文字を ``_`` に置き換え、UTF-8 で 100 バイト以内に切り、末尾の ``.`` と
    空白を除いたもの。一時名の全体は、どの OS のファイル名の上限 (255) にも
    収まる。

    Parameters
    ----------
    original : str
        元のフォルダ名

    Returns
    -------
    str
        一時名 (呼ぶたびに別の名前)
    """
    hint = _UNSAFE_NAME_CHARS.sub("_", original)
    hint = (
        hint.encode("utf-8", errors="ignore")[:_TEMPORARY_HINT_BYTES]
        .decode("utf-8", errors="ignore")
        .rstrip(". ")
    )
    name = f"{_TEMPORARY_PREFIX}{uuid.uuid4().hex[:8]}"
    return f"{name}-{hint}" if hint else name


def _unused_temporary_path(parent: Path, original: str) -> Path:
    """``parent`` の下の、まだ存在しない一時名のパスを返す。

    Parameters
    ----------
    parent : Path
        PDF ルート (変換後のパス)
    original : str
        元のフォルダ名

    Returns
    -------
    Path
        ``parent`` の直下で、存在しない一時名のパス

    Raises
    ------
    FileExistsError
        何度作り直しても既存の名前と重なったとき
    """
    for _ in range(_TEMPORARY_ATTEMPTS):
        candidate = parent / _temporary_dir_name(original)
        if not os.path.lexists(candidate):
            return candidate
    raise FileExistsError(
        errno.EEXIST, "No unused temporary folder name", str(parent)
    )


def _report_rename_failure(
    old_pdf_dir: Path, new_pdf_dir: Path, error: OSError, note: str = ""
) -> None:
    """PDF フォルダの改名の失敗を、ログと画面の両方に出す。

    Parameters
    ----------
    old_pdf_dir, new_pdf_dir : Path
        改名の前後のパス (変換後のパス。表示用に戻して出す)
    error : OSError
        捕捉した例外 (理由だけを出す。パスは含めない)
    note : str, default ""
        理由の後に添える説明
    """
    message = (
        "Failed to rename PDF directory:\n"
        f"From: {to_display_path(old_pdf_dir)}\n"
        f"To: {to_display_path(new_pdf_dir)}\n"
        f"Error: {_failure_reason(error)}{note}"
    )
    _logger.error(message)
    st.error(message)


def _report_destination_exists(old_pdf_dir: Path, new_pdf_dir: Path) -> None:
    """改名先が別のフォルダとして既にあることを、ログと画面の両方に出す。

    Parameters
    ----------
    old_pdf_dir, new_pdf_dir : Path
        改名の前後のパス (変換後のパス。表示用に戻して出す)
    """
    message = (
        "Cannot rename PDF directory: destination already exists\n"
        f"From: {to_display_path(old_pdf_dir)}\n"
        f"To: {to_display_path(new_pdf_dir)}\n"
        "This may indicate a duplicate pdf_dir_name."
    )
    _logger.error(message)
    st.error(message)


def _rename_via_temporary_name(old_pdf_dir: Path, new_pdf_dir: Path) -> bool:
    """大文字小文字だけ違う名前へ、一時名を経由して PDF フォルダを改名する。

    ファイルシステムが大文字小文字を区別するかどうかにかかわらず、元の
    フォルダを同じ階層の一時名へ移してから、新しい名前へ移す (区別しない
    ファイルシステムや一部のネットワークファイルシステムは、大文字小文字
    だけの改名を黙って無視することがある)。2 回目に失敗したら元の名前に
    戻す。戻すのにも失敗したら、PDF は一時名のフォルダに残っているので、その
    場所をエラーに含める (PDF は失われない)。

    Parameters
    ----------
    old_pdf_dir, new_pdf_dir : Path
        改名の前後のパス (変換後のパス)

    Returns
    -------
    bool
        改名できたとき ``True``。失敗したとき、エラーを出して ``False``
        (元の名前に戻せたときは、フォルダは元のまま)
    """
    try:
        temporary = _unused_temporary_path(
            old_pdf_dir.parent, old_pdf_dir.name
        )
        old_pdf_dir.rename(temporary)
    except OSError as e:
        _report_rename_failure(old_pdf_dir, new_pdf_dir, e)
        return False

    try:
        temporary.rename(new_pdf_dir)
    except OSError as e:
        try:
            temporary.rename(old_pdf_dir)
        except OSError:
            _report_rename_failure(
                old_pdf_dir,
                new_pdf_dir,
                e,
                note=(
                    "\nThe PDF files were not lost. They are in the "
                    f"temporary folder {to_display_path(temporary)} "
                    "(rename it back to the From name to restore them)."
                ),
            )
        else:
            _report_rename_failure(old_pdf_dir, new_pdf_dir, e)
        return False

    _logger.debug(
        "PDF directory renamed via a temporary name: "
        f"{to_display_path(old_pdf_dir)} -> {to_display_path(new_pdf_dir)}"
    )
    return True


def _rename_pdf_dir(old_pdf_dir: Path, new_pdf_dir: Path) -> bool:
    """更新に伴って、論文の PDF フォルダを新しい名前へ改名する。

    元のフォルダがなければ (PDF がまだ保存されていない) 何もしない。新しい
    名前が、元のフォルダ自身でない既存のフォルダを指すときは、上書き・統合
    せずに拒否する。大文字小文字を区別しないファイルシステムでは、大文字
    小文字だけ違う新しい名前は元のフォルダ自身を指して存在するので、これを
    別のフォルダと誤判定しない。大文字小文字 (または Unicode 正規化) だけが
    変わるときは一時名を経由し (``_rename_via_temporary_name``)、それ以外は
    直接改名する。

    Parameters
    ----------
    old_pdf_dir, new_pdf_dir : Path
        改名の前後のパス (``Entry.get_pdf_dir`` の変換後のパス)

    Returns
    -------
    bool
        更新を続けてよいとき ``True`` (改名できた、または改名するフォルダが
        ない)。拒否・失敗したとき、エラーを出して ``False`` (論文リストと
        PDF は変えない)
    """
    via_temporary_name = _differs_only_in_form(
        old_pdf_dir.name, new_pdf_dir.name
    )
    try:
        if not _exists_as(old_pdf_dir.is_dir, old_pdf_dir):
            # PDFがまだ保存されていない場合
            _logger.debug(
                "PDF directory does not exist, skipping rename: "
                f"{to_display_path(old_pdf_dir)}"
            )
            return True
        if os.path.lexists(new_pdf_dir) and not (
            via_temporary_name and _is_same_folder(old_pdf_dir, new_pdf_dir)
        ):
            # 同じpdf_dir_nameを持つ別のエントリーが存在する可能性がある
            # 重複チェックでエラーになるはずだが、念のためエラーを返す
            _report_destination_exists(old_pdf_dir, new_pdf_dir)
            return False
    except OSError as e:
        _report_rename_failure(old_pdf_dir, new_pdf_dir, e)
        return False

    if via_temporary_name:
        return _rename_via_temporary_name(old_pdf_dir, new_pdf_dir)
    try:
        old_pdf_dir.rename(new_pdf_dir)
    except OSError as e:
        # リネームに失敗した場合は、更新を中止
        _report_rename_failure(old_pdf_dir, new_pdf_dir, e)
        return False
    _logger.debug(
        f"PDF directory renamed: {to_display_path(old_pdf_dir)} -> "
        f"{to_display_path(new_pdf_dir)}"
    )
    return True


def register_entry_to_list(
    paper_list: PaperList,
    entry: Entry,
    uploaded_files: Optional[list[UploadedFile]] = None,
) -> bool:
    """論文エントリをリストに登録する。

    重複チェックを行い、PDFファイルがあれば保存する。重複は、既存の全論文と
    PDFフォルダ名の比較キー (``folder_name_key``) が一致するかで判定する
    (大文字小文字やUnicode正規化だけが違う名前も重複。ファイルシステムが
    大文字小文字を区別するかにかかわらず、すべてのOSで同じ)。重複なら
    エラーを出し、論文リストもPDFも変えない。

    Parameters
    ----------
    paper_list : PaperList
        論文リスト
    entry : Entry
        登録する論文エントリ
    uploaded_files : Optional[list[UploadedFile]]
        アップロードされたPDFファイルのリスト

    Returns
    -------
    bool
        登録成功した場合True。重複、または壊れた論文リストのファイルを退避
        できずに保存しなかった場合は、論文リストを変えずにFalse
    """
    _logger.debug(f"Registering entry: {entry}")

    # IDを設定
    entry["ID"] = entry.get_key(paper_list.keys())

    # PDFディレクトリ名の比較キーで重複を確認する
    duplicate = _find_same_folder_name(paper_list, entry.pdf_dir_name)
    if duplicate is not None:
        _report_duplicate("FAIL: Duplicated", entry.pdf_dir_name, duplicate)
        return False

    # ラインナップとして追加
    paper_list[entry["ID"]] = entry

    # PDFファイルを保存
    if uploaded_files:
        save_pdfs(uploaded_files, entry)

    # PaperListを保存 (退避できずに保存しなかった場合は、登録しなかったことにする)
    if not save_paper_list(paper_list):
        del paper_list[entry["ID"]]
        return False

    st.success("SUCCESS: Registered")
    return True


def update_entry_in_list(
    paper_list: PaperList,
    key_original: str,
    entry_updated: Entry,
    uploaded_files: Optional[list[UploadedFile]] = None,
    failures: Optional[list[str]] = None,
) -> bool:
    """論文エントリを更新する。

    PDFフォルダ名が変わらない更新は、そのまま更新する。変わる更新は、自分
    以外の論文とPDFフォルダ名の比較キー (``folder_name_key``) が一致する
    なら重複として拒否する (大文字小文字やUnicode正規化だけが違う名前も重複。
    ファイルシステムが大文字小文字を区別するかにかかわらず、すべてのOSで
    同じ)。重複でなければ、PDFフォルダを新しい名前へ改名してから更新する。

    改名の前に、元の論文の旧形式の単一PDFと修正前の名前のフォルダの移行
    (``Entry.get_pdf_files``) を済ませるので、改名後も新しい名前のフォルダの
    PDFとして見える。移行は既存のPDFを上書きせず、一覧の表示でも起きる。

    大文字小文字 (またはUnicode正規化) だけが変わる改名は、一時名を経由して
    行う。改名先が元のフォルダ自身でない既存のフォルダなら、上書き・統合せず
    に拒否する。拒否・失敗したとき (``False``) は、論文リストを変えず、PDF
    フォルダは元の名前に保つ (一時名からも戻せなかったときだけ、一時名の場所を
    エラーに含める)。

    追加したPDFの一部を保存できなくても、論文を保存できれば ``True`` を返す
    (保存の失敗は ``save_pdfs`` が画面に出す)。呼び出し元が更新の後に再描画
    すると画面のエラーは消えるので、``failures`` を渡すと、更新に成功した
    ときだけ、保存に失敗したPDFの文 (``save_pdfs`` が画面に出したものと同じ
    文) を追加する。更新を拒否・中止して ``False`` を返すときは、
    ``failures`` を変えない (再描画しなければ、画面のエラーが残る)。

    Parameters
    ----------
    paper_list : PaperList
        論文リスト
    key_original : str
        更新する論文の元のキー
    entry_updated : Entry
        更新後の論文エントリ
    uploaded_files : Optional[list[UploadedFile]]
        アップロードされたPDFファイルのリスト
    failures : Optional[list[str]], optional
        追加したPDFの保存の失敗の文を追加する一覧。渡された一覧の既存の
        中身は残し、後ろに追加する。省略すると、これまでと同じ振る舞いに
        なる。

    Returns
    -------
    bool
        更新成功した場合True。重複、改名の失敗、または壊れた論文リストの
        ファイルを退避できずに保存しなかった場合は、論文リストを変えずに
        False
    """
    _logger.debug(f"Updating entry: {key_original}")

    original_entry = paper_list[key_original]
    original_entries = dict(paper_list)

    # PDFディレクトリ名が変わらない場合は単純に更新
    if original_entry.pdf_dir_name == entry_updated.pdf_dir_name:
        paper_list[key_original] = entry_updated
    else:
        # PDFディレクトリ名が変わる場合
        # 0. 新しいpdf_dir_nameが他のエントリーと重複していないかチェック
        duplicate = _find_same_folder_name(
            paper_list, entry_updated.pdf_dir_name, exclude_key=key_original
        )
        if duplicate is not None:
            _report_duplicate(
                f"FAIL: Duplicated pdf_dir_name: {entry_updated.pdf_dir_name}",
                entry_updated.pdf_dir_name,
                duplicate,
            )
            return False

        # 1. 古いPDFディレクトリを新しい名前にリネーム
        # (旧形式の単一PDFと修正前の名前のフォルダは、改名の前に移行する)
        original_entry.get_pdf_files()
        if not _rename_pdf_dir(
            original_entry.get_pdf_dir(), entry_updated.get_pdf_dir()
        ):
            return False

        # 2. 新しいキーで登録
        new_id = entry_updated.get_key(paper_list.keys())
        del paper_list[key_original]
        entry_updated["ID"] = new_id
        paper_list[new_id] = entry_updated

    # 新しいPDFファイルを保存 (失敗の文は、更新に成功したときだけ呼び出し元へ返す)
    pdf_failures: list[str] = []
    if uploaded_files:
        save_pdfs(uploaded_files, entry_updated, failures=pdf_failures)

    # PaperListを保存 (退避できずに保存しなかった場合は、更新しなかったことにする)
    if not save_paper_list(paper_list):
        paper_list.clear()
        paper_list.update(original_entries)
        return False

    if failures is not None:
        failures.extend(pdf_failures)

    _logger.debug(f"Entry updated: {paper_list=}")
    return True


# 後方互換性のためのエイリアス（非推奨）
def save_pdf(
    uploaded_file_pdf: Optional[UploadedFile],
    filepath_pdf: Path,
) -> bool:
    """PDFファイルを保存する。

    .. deprecated::
        Use `save_pdfs()` instead for multiple PDF support.

    Parameters
    ----------
    uploaded_file_pdf : Optional[UploadedFile]
        アップロードされたPDFファイル
    filepath_pdf : Path
        保存先のファイルパス

    Returns
    -------
    bool
        保存に成功した場合True
    """
    if uploaded_file_pdf is None:
        return False

    filepath_pdf.parent.mkdir(parents=True, exist_ok=True)

    with open(filepath_pdf, mode="wb") as f:
        f.write(uploaded_file_pdf.getvalue())

    _logger.debug(f"PDF saved: {filepath_pdf}")
    return True
