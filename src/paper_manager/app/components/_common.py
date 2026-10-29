import errno
import shutil
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
    TAG_SEPARATOR,
)
from paper_manager.app.helper import MAP_FIELDS, MAP_REQUIRED_FIELDS
from paper_manager.entry import Entry, PaperList
from paper_manager.helper import to_display_path, to_fs_path
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


def custom_entry(entry: Entry) -> Entry:
    """Entryオブジェクトをカスタマイズするためのフォームを表示する。

    エントリタイプに基づいて入力フィールドを動的に生成し、
    ユーザーが入力した値でEntryオブジェクトを更新する。

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
        if field == COLNAME_YEAR:
            assert isinstance(default, (str, int, type(None)))
            int_default: Optional[int]
            if default:
                int_default = int(default)
            elif required:
                int_default = date.today().year  # this year
            else:
                int_default = None
            _year_input = st.number_input(
                field,
                value=int_default,
                format="%4i",
                placeholder=placeholder,
                step=1,
                min_value=1000,
                max_value=date.today().year + 1,
                key=field,
            )
            assert isinstance(_year_input, (int, type(None)))
            if _year_input:
                entry[field] = str(_year_input)
            elif field in entry:
                _ = entry.pop(field)

        elif field == COLNAME_AUTHOR:
            assert isinstance(default, (str, type(None), list))
            if isinstance(default, str):
                if default:
                    tup_default = tuple(default.split(AUTHOR_SEPARATOR))
                else:
                    tup_default = tuple()
            elif isinstance(default, list):
                tup_default = tuple(default)
            else:  # None
                tup_default = tuple()

            _author_input_temp = st.multiselect(
                field,
                options=tup_default,
                default=tup_default,
                accept_new_options=True,
                key=field,
                placeholder=placeholder,
            )
            entry[field] = AUTHOR_SEPARATOR.join(_author_input_temp)

            if _author_input_temp:
                entry[field] = TAG_SEPARATOR.join(_author_input_temp)
            elif field in entry:
                _ = entry.pop(field)
        elif field == COLNAME_TAGS:
            assert isinstance(default, (str, type(None), list))
            if isinstance(default, str):
                if default:
                    tup_default = tuple(default.split(AUTHOR_SEPARATOR))
                else:
                    tup_default = tuple()
            elif isinstance(default, list):
                tup_default = tuple(default)
            else:  # None
                tup_default = tuple()

            _tags_input_temp = st.multiselect(
                field,
                options=tup_default,
                default=tup_default,
                accept_new_options=True,
                key=field,
            )
            if _tags_input_temp:
                entry[field] = TAG_SEPARATOR.join(_tags_input_temp)
            elif field in entry:
                _ = entry.pop(field)
        else:
            assert isinstance(default, (str, type(None)))
            _text_input_temp = st.text_input(
                field,
                value=default,
                placeholder=placeholder,
                key=field,
            )
            if _text_input_temp:
                entry[field] = _text_input_temp
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


def _report_failure(action: str, path: Path, error: OSError) -> None:
    """ファイル操作の失敗を、ログと画面の両方に出す。

    画面とログには、何をしようとして失敗したかと、``to_display_path`` で
    prefix を外したパス、パスを含まない理由 (``_failure_reason``) を出す。

    Parameters
    ----------
    action : str
        失敗した操作 (例: ``"Failed to save PDF file"``)。
    path : Path
        対象のパス (変換後のパスでも変換前のパスでもよい)。
    error : OSError
        捕捉した例外。
    """
    reason = _failure_reason(error)
    message = f"{action}: {to_display_path(path)}"
    _logger.error(f"{message}: {reason}")
    st.error(f"{message}\nError: {reason}")


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
) -> int:
    """複数のPDFファイルを保存する。

    保存先は、ファイル操作用に変換したパス (``Entry.get_pdf_dir``。
    Windows では拡張長のパス) なので、パス全体の長さにかかわらず保存する。
    フォルダの作成やファイルの書き込みに失敗したら、そのパス (表示用の
    パス) と理由をログと画面に出し、残りのファイルの処理を続ける。

    Parameters
    ----------
    uploaded_files : list[UploadedFile]
        アップロードされたPDFファイルのリスト
    entry : Entry
        対象の論文エントリ
    base_dir : Union[Path, None], optional
        PDFの基本ディレクトリ。Noneの場合はDIRPATH_PDFを使用。

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
        _report_failure("Failed to create PDF directory", pdf_dir, e)
        return 0

    # 既存のPDFファイル名を取得しておき、重複しない新しい名前を付与する
    try:
        existing_files = {f.name for f in pdf_dir.glob("*.pdf")}
    except OSError as e:
        _report_failure("Failed to access PDF directory", pdf_dir, e)
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
            _report_failure("Failed to save PDF file", filepath, e)
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


def save_paper_list(paper_list: PaperList) -> None:
    """PaperListをセッションステートとファイルに保存する。

    Parameters
    ----------
    paper_list : PaperList
        保存する論文リスト
    """
    paper_list.to_session_state()
    paper_list.to_file()
    _logger.debug("PaperList saved")


def register_entry_to_list(
    paper_list: PaperList,
    entry: Entry,
    uploaded_files: Optional[list[UploadedFile]] = None,
) -> bool:
    """論文エントリをリストに登録する。

    重複チェックを行い、PDFファイルがあれば保存する。

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
        登録成功した場合True
    """
    _logger.debug(f"Registering entry: {entry}")

    # IDを設定
    entry["ID"] = entry.get_key(paper_list.keys())

    # PDFディレクトリ名で重複を確認する
    existing_pdf_dirs = {_entry.pdf_dir_name for _entry in paper_list.values()}
    if entry.pdf_dir_name in existing_pdf_dirs:
        st.error("FAIL: Duplicated")
        return False

    # ラインナップとして追加
    paper_list[entry["ID"]] = entry

    # PDFファイルを保存
    if uploaded_files:
        save_pdfs(uploaded_files, entry)

    # PaperListを保存
    save_paper_list(paper_list)

    st.success("SUCCESS: Registered")
    return True


def update_entry_in_list(
    paper_list: PaperList,
    key_original: str,
    entry_updated: Entry,
    uploaded_files: Optional[list[UploadedFile]] = None,
) -> bool:
    """論文エントリを更新する。

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

    Returns
    -------
    bool
        更新成功した場合True
    """
    _logger.debug(f"Updating entry: {key_original}")

    original_entry = paper_list[key_original]

    # PDFディレクトリ名が変わらない場合は単純に更新
    if original_entry.pdf_dir_name == entry_updated.pdf_dir_name:
        paper_list[key_original] = entry_updated
    else:
        # PDFディレクトリ名が変わる場合
        # 0. 新しいpdf_dir_nameが他のエントリーと重複していないかチェック
        existing_pdf_dirs = {
            _entry.pdf_dir_name
            for _key, _entry in paper_list.items()
            if _key != key_original  # 元のエントリーを除外
        }
        if entry_updated.pdf_dir_name in existing_pdf_dirs:
            st.error(
                f"FAIL: Duplicated pdf_dir_name: {entry_updated.pdf_dir_name}"
            )
            return False

        # 1. 古いPDFディレクトリを新しい名前にリネーム
        old_pdf_dir = original_entry.get_pdf_dir()
        new_pdf_dir = entry_updated.get_pdf_dir()

        # 古いディレクトリが存在する場合のみリネームを試みる
        if old_pdf_dir.is_dir():
            # 新しいディレクトリが既に存在する場合の処理
            if new_pdf_dir.exists():
                # 同じpdf_dir_nameを持つ別のエントリーが存在する可能性がある
                # 重複チェックでエラーになるはずだが、念のためエラーを返す
                error_msg = (
                    f"Cannot rename PDF directory: destination already exists\n"
                    f"From: {old_pdf_dir}\n"
                    f"To: {new_pdf_dir}\n"
                    f"This may indicate a duplicate pdf_dir_name."
                )
                _logger.error(error_msg)
                st.error(error_msg)
                return False
            else:
                # 新しいディレクトリが存在しない場合、リネームを実行
                try:
                    old_pdf_dir.rename(new_pdf_dir)
                    _logger.debug(
                        f"PDF directory renamed: {old_pdf_dir} -> {new_pdf_dir}"
                    )
                except OSError as e:
                    error_msg = (
                        f"Failed to rename PDF directory:\n"
                        f"From: {old_pdf_dir}\n"
                        f"To: {new_pdf_dir}"
                    )
                    _logger.error(f"{error_msg}: {e}")
                    st.error(f"{error_msg}\nError: {str(e)}")
                    # リネームに失敗した場合は、更新を中止
                    return False
        else:
            # 古いディレクトリが存在しない場合（PDFがまだ保存されていない場合）
            _logger.debug(
                f"PDF directory does not exist, skipping rename: {old_pdf_dir}"
            )

        # 2. 新しいキーで登録
        new_id = entry_updated.get_key(paper_list.keys())
        del paper_list[key_original]
        entry_updated["ID"] = new_id
        paper_list[new_id] = entry_updated

    # 新しいPDFファイルを保存
    if uploaded_files:
        save_pdfs(uploaded_files, entry_updated)

    # PaperListを保存
    save_paper_list(paper_list)

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
