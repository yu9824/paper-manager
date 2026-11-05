"""バックアップ・復元機能の共通モジュール。

このモジュールは、paper-managerのデータをバックアップ・復元するための
機能を提供します。

主な機能:
    - バックアップzipファイルの作成
    - バックアップzipファイルからの復元（書き込み前に、メンバー名と、書き込みに
      使う結合の結果の両方を検証し、すべてのメンバーを読み出せることも確かめる）
    - バックアップ情報の取得
    - 復元で不正なメンバー名（データディレクトリの外を指しうる名前）の検出
    - 復元で読み出せないメンバー（CRCの不一致、破損した圧縮データ、暗号化など）
      の検出

ファイル操作は、``to_fs_path`` で変換したパス（Windowsでは拡張長のパス）を
起点に行う。zipのメンバー名は、変換後のパスどうしの ``relative_to`` から作り、
prefixを含めない。

バックアップに含まれる内容:
    - paper-managerのバージョン情報
    - 論文リスト（list.json）
    - 設定ファイル（config.json）
    - すべてのPDFファイル

例:
    >>> from paper_manager.app.helper._backup import create_backup_zip
    >>> from pathlib import Path
    >>> backup_path = create_backup_zip("backup.zip")
    >>> print(backup_path)
    backup.zip

"""

import json
import re
import shutil
import zipfile
from collections.abc import Iterable
from datetime import datetime
from io import BytesIO
from pathlib import Path, PurePath
from typing import Optional, Union

from paper_manager import __version__
from paper_manager._constants import (
    DIRPATH_DATA,
    DIRPATH_PDF,
    ENCODING,
    FILEPATH_CONFIG,
    FILEPATH_LIST,
)
from paper_manager.helper import to_display_path, to_fs_path
from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)

# zipのメンバー名の区切り。zipの規格は ``/`` だが、Windowsで作られた
# zipには ``\`` も現れ、Windowsでは ``\`` も区切りとして解釈される
_SEPARATORS = re.compile(r"[/\\]")
# 中止のメッセージに載せる、メンバー名の件数の上限
_MAX_LISTED_NAMES = 10
# 読み出せるかの確認で、1回に読むバイト数（``ZipFile.testzip`` と同じ。大きな
# メンバーでも、メモリの使用量を一定に保つ）
_READ_CHUNK_SIZE = 2**20


def _is_unsafe_member_name(name: str) -> bool:
    r"""zipのメンバー名が、データディレクトリの外を指しうるかを判定する。

    判定は名前の文字列だけで行い、ファイルシステムの状態に依存しない。

    Parameters
    ----------
    name : str
        zipのメンバー名。

    Returns
    -------
    bool
        次のいずれかに当てはまれば ``True``。

        - 空、またはNUL文字を含む
        - ``/`` または ``\`` で始まる（POSIXの絶対パス、Windowsのルート、UNC）
        - ``/`` と ``\`` のどちらで分割しても、``..`` に等しい成分がある
        - 同じ分割で、2文字目が ``:`` の成分がある（``C:`` ``C:x`` ``C:/x``
          などのドライブの指定。Windowsの Python 3.9〜3.11 の ``pathlib`` は、
          結合する名前の途中にある ``pdf/C:x.pdf`` のドライブの指定も再解析
          して、ルートを置き換えるため、先頭の成分に限らない）
    """
    if not name or "\x00" in name:
        return True
    if name[0] in "/\\":
        return True
    return any(
        part == ".." or part[1:2] == ":" for part in _SEPARATORS.split(name)
    )


def find_unsafe_member_names(names: Iterable[str]) -> list[str]:
    r"""データディレクトリの外を指しうる、zipのメンバー名を返す。

    絶対パス、ドライブまたはルートの指定（成分の途中のものを含む）、``..`` の
    成分、空の名前、NUL文字を含む名前が該当する。判定は名前の文字列だけで行う
    ため、ファイルシステムの状態に依存せず、``resolve()`` も使わない（利用者の
    シンボリックリンクを壊さない）。

    実際の結合の結果がデータディレクトリの外になるかは、``pathlib`` の版や
    OSで変わりうるため、この関数の規則だけでは保証しない。``restore_from_zip``
    は、書き込みの前に、結合の結果も ``_find_escaping_members`` で確かめる。

    ``\`` を含むが ``..`` の成分を作らない名前（POSIXで作った
    ``pdf/2020 - A\B - T/00.pdf`` のようなフォルダ名）は、正当として受け付ける。
    一方、``..\x`` や ``pdf/a\..\..\x`` は、Windowsで ``..`` として解釈される
    ため拒否する。

    Parameters
    ----------
    names : Iterable[str]
        zipのメンバー名。

    Returns
    -------
    list[str]
        不正な名前（与えた順序）。すべて安全なら空のリスト。
    """
    return [name for name in names if _is_unsafe_member_name(name)]


def _join_member(data_root: Path, name: str) -> Path:
    """zipのメンバー名を、書き込み先のパスにする。

    復元の書き込み先の計算そのもの。書き込み前の検証
    （``_find_escaping_members``）と実際の書き込みで同じ計算を使う。

    Parameters
    ----------
    data_root : Path
        変換後のデータディレクトリ（``to_fs_path(DIRPATH_DATA)``）。
    name : str
        zipのメンバー名。

    Returns
    -------
    Path
        ``data_root`` と結合したパス。
    """
    return data_root / Path(name)


def _find_escaping_members(
    data_root: PurePath, names: Iterable[str]
) -> list[str]:
    """結合の結果がデータディレクトリの外になるメンバー名を返す。

    ``find_unsafe_member_names`` とは独立の、二重の防御。実際の書き込みに使う
    結合（``_join_member``）の結果が、``data_root`` の下に収まることを、
    パスの成分で確かめる（``..`` の成分が残る場合も外とみなす）。
    ファイルシステムには触れない。

    Parameters
    ----------
    data_root : PurePath
        変換後のデータディレクトリ。
    names : Iterable[str]
        zipのメンバー名。

    Returns
    -------
    list[str]
        外になる名前（与えた順序）。
    """
    root_parts = data_root.parts

    def escapes(name: str) -> bool:
        joined = _join_member(data_root, name).parts
        return (
            joined[: len(root_parts)] != root_parts
            or ".." in joined[len(root_parts) :]
        )

    return [name for name in names if escapes(name)]


def _printable(name: str) -> str:
    """メッセージに載せるために、制御文字をエスケープした名前を返す。

    Parameters
    ----------
    name : str
        zipのメンバー名。

    Returns
    -------
    str
        印字できない文字を ``\\x1b`` のように置き換えた名前。空の名前は
        ``（空の名前）``。
    """
    if not name:
        return "（空の名前）"
    return "".join(
        c if c.isprintable() else c.encode("unicode_escape").decode("ascii")
        for c in name
    )


def _abort_message(reason: str, names: list[str]) -> str:
    """復元を中止したときのメッセージを作る。

    Parameters
    ----------
    reason : str
        中止した理由（バックアップに含まれていたもの）を表す句。
        「バックアップに、<reason>が含まれているため」の形で使う。
    names : list[str]
        理由に当たるメンバー名（1件以上）。

    Returns
    -------
    str
        中止したこと、データを変更していないこと、メンバー名（上限まで）。
    """
    lines = [
        f"バックアップに、{reason}が含まれているため、復元を中止しました。"
        "データは変更していません。"
    ]
    lines.extend(f"- {_printable(name)}" for name in names[:_MAX_LISTED_NAMES])
    rest = len(names) - _MAX_LISTED_NAMES
    if rest > 0:
        lines.append(f"- ほか {rest} 件")
    return "\n".join(lines)


def _unsafe_members_message(unsafe_names: list[str]) -> str:
    """不正なメンバー名で復元を中止したときのメッセージを作る。

    Parameters
    ----------
    unsafe_names : list[str]
        不正なメンバー名（1件以上）。

    Returns
    -------
    str
        中止したこと、データを変更していないこと、不正な名前（上限まで）。
    """
    return _abort_message(
        "データディレクトリの外を指しうる不正なメンバー名", unsafe_names
    )


def _unreadable_members_message(unreadable_names: list[str]) -> str:
    """読み出せないメンバーがあって復元を中止したときのメッセージを作る。

    Parameters
    ----------
    unreadable_names : list[str]
        読み出せないメンバーの名前（1件以上）。

    Returns
    -------
    str
        中止したこと、データを変更していないこと、読み出せない名前（上限
        まで）。
    """
    return _abort_message(
        "読み出せないメンバー（内容の破損、暗号化、未対応の圧縮方式など）",
        unreadable_names,
    )


def _find_unreadable_members(zip_file: zipfile.ZipFile) -> list[str]:
    """読み出せないメンバーの名前を返す。

    すべてのメンバーを最後まで読み、CRCを検証する。``ZipFile.testzip`` と
    同じ読み方（チャンク読み）だが、次の点が異なる。

    - 読み出しが例外になる場合（破損した圧縮データの ``zlib.error``、暗号化の
      ``RuntimeError``、未対応の圧縮方式の ``NotImplementedError`` など）も、
      そのメンバーの名前を記録して続ける（``testzip`` は ``BadZipFile`` 以外を
      名前なしで送出する）
    - 読み出せないメンバーを、最初の1つで止めずにすべて集める
    - 同じ名前のメンバーが複数あっても、すべてを確かめる（``testzip`` は名前で
      開くため、最後の1つしか読まない）

    Parameters
    ----------
    zip_file : zipfile.ZipFile
        読み取りで開いたzip。

    Returns
    -------
    list[str]
        読み出せないメンバーの名前（zipの順序、重複なし）。すべて読み出せれば
        空のリスト。
    """
    unreadable: list[str] = []
    for info in zip_file.infolist():
        try:
            with zip_file.open(info) as member:
                while member.read(_READ_CHUNK_SIZE):
                    pass
        except Exception as e:
            _logger.error(
                f"Unreadable member in backup: {info.filename!r} "
                f"({type(e).__name__}: {e})"
            )
            if info.filename not in unreadable:
                unreadable.append(info.filename)
    return unreadable


def create_backup_zip(output_path: Optional[Union[Path, str]] = None) -> Union[BytesIO, Path]:
    """バックアップ用のzipファイルを作成する。

    Parameters
    ----------
    output_path : Optional[Union[Path, str]], optional
        出力先のファイルパス。Noneの場合はBytesIOを返す。デフォルトはNone。

    Returns
    -------
    Union[BytesIO, Path]
        出力先が指定された場合はPath、そうでない場合はBytesIO
    """
    if output_path is None:
        zip_buffer = BytesIO()
        zip_file_obj = zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED)
        should_close = True
    else:
        output_path = Path(output_path)
        zip_file_obj = zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED)
        should_close = True

    try:
        # バージョン情報を保存
        version_info = {
            "version": __version__,
            "backup_date": datetime.now().isoformat(),
        }
        zip_file_obj.writestr(
            "version.json",
            json.dumps(version_info, ensure_ascii=False, indent=2),
        )

        # ファイル操作は変換後のパスを起点にする
        data_root = to_fs_path(DIRPATH_DATA)
        list_path = to_fs_path(FILEPATH_LIST)
        config_path = to_fs_path(FILEPATH_CONFIG)
        pdf_root = to_fs_path(DIRPATH_PDF)

        # list.jsonを保存
        if list_path.is_file():
            zip_file_obj.write(list_path, "list.json")
            _logger.debug(
                f"Added list.json to backup: {to_display_path(list_path)}"
            )

        # config.jsonを保存
        if config_path.is_file():
            zip_file_obj.write(config_path, "config.json")
            _logger.debug(
                f"Added config.json to backup: {to_display_path(config_path)}"
            )

        # PDFディレクトリを保存
        if pdf_root.is_dir():
            pdf_files = list(pdf_root.rglob("*.pdf"))
            for pdf_file in pdf_files:
                # 変換後のパスどうしの相対パス（pdf/ から始まる、prefixなし）。
                # 区切りは zip の規格どおり "/" にする
                arcname = pdf_file.relative_to(data_root).as_posix()
                zip_file_obj.write(pdf_file, arcname)
                _logger.debug(
                    f"Added PDF to backup: {to_display_path(pdf_file)}"
                    f" -> {arcname}"
                )

    finally:
        if should_close:
            zip_file_obj.close()

    if output_path is None:
        zip_buffer.seek(0)
        return zip_buffer
    else:
        return output_path


def restore_from_zip(
    zip_path: Union[Path, str, BytesIO],
    update_session_state: bool = False,
) -> tuple[bool, str]:
    """zipファイルからデータを復元する。

    zipを開いた直後に、すべてのメンバー名を ``find_unsafe_member_names`` で
    検証し、さらに書き込みに使う結合の結果がデータディレクトリの下に収まる
    ことも ``_find_escaping_members`` で確かめる（Windows の Python 3.9〜3.11
    の pathlib は、成分の途中のドライブ指定を結合のときに再解析するため、
    名前の判定だけに頼らない）。1つでも不正な名前があれば、既存ファイルの
    退避も含めて何も書かずに ``(False, メッセージ)`` を返す。

    名前の検証の後、書き込みの前に、すべてのメンバーを最後まで読み出せる
    ことも確かめる（``_find_unreadable_members``。CRCの不一致、破損した
    圧縮データ、暗号化、未対応の圧縮方式）。読み出せないメンバーが1つでも
    あれば、同じく何も書かずに（``.json.backup`` も作らずに）
    ``(False, メッセージ)`` を返す。書き込みの途中で読み出しに失敗すると、
    開いた直後の空の ``list.json`` が残り、続けて復元したときにそれが
    ``.json.backup`` に退避されて元の内容を失うため。

    書き込みは、変換後のパス（``to_fs_path``）を起点に行う。

    Parameters
    ----------
    zip_path : Union[Path, str, BytesIO]
        復元するzipファイルのパスまたはBytesIO
    update_session_state : bool, optional
        Streamlitのセッションステートを更新するかどうか。デフォルトはFalse。

    Returns
    -------
    tuple[bool, str]
        (成功フラグ, メッセージ)。zipとして不正なときは
        ``(False, "無効なzipファイルです。")``、不正なメンバー名があるときは
        ``(False, 不正な名前を挙げたメッセージ)``、読み出せないメンバーが
        あるときは ``(False, 読み出せない名前を挙げたメッセージ)``（どちらも
        データは変更されない）。
    """
    try:
        if isinstance(zip_path, (Path, str)):
            zip_file = zipfile.ZipFile(zip_path, "r")
        else:
            zip_file = zipfile.ZipFile(zip_path, "r")

        with zip_file:
            # ファイル操作は変換後のパスを起点にする
            data_root = to_fs_path(DIRPATH_DATA)
            list_path = to_fs_path(FILEPATH_LIST)
            config_path = to_fs_path(FILEPATH_CONFIG)

            # 書き込みの前に、すべてのメンバー名を検証する。名前の判定と、
            # 書き込みに使う結合の結果の判定の、どちらかに該当すれば中止する
            names = zip_file.namelist()
            rejected = set(find_unsafe_member_names(names))
            rejected.update(_find_escaping_members(data_root, names))
            unsafe_names = [name for name in names if name in rejected]
            if unsafe_names:
                message = _unsafe_members_message(unsafe_names)
                _logger.error(f"Restore aborted without writing.\n{message}")
                return False, message

            # 書き込みの前に、すべてのメンバーを読み出せることを確かめる。
            # 書き込み先を開いた後に読み出しに失敗すると、空のファイルが残る
            unreadable_names = _find_unreadable_members(zip_file)
            if unreadable_names:
                message = _unreadable_members_message(unreadable_names)
                _logger.error(f"Restore aborted without writing.\n{message}")
                return False, message

            # バージョン情報を確認
            version_info = None
            if "version.json" in zip_file.namelist():
                version_data = zip_file.read("version.json")
                version_info = json.loads(version_data.decode(ENCODING))
                _logger.debug(f"Backup version info: {version_info}")

            # list.jsonを復元
            if "list.json" in zip_file.namelist():
                # 既存のlist.jsonをバックアップ（存在する場合）
                if list_path.is_file():
                    backup_path = list_path.with_suffix(".json.backup")
                    shutil.copy2(list_path, backup_path)
                    _logger.debug(
                        "Backed up existing list.json to "
                        f"{to_display_path(backup_path)}"
                    )

                # 新しいlist.jsonを書き込み
                list_path.parent.mkdir(parents=True, exist_ok=True)
                with open(list_path, "wb") as f:
                    f.write(zip_file.read("list.json"))
                _logger.info("Restored list.json from backup")

            # config.jsonを復元（存在する場合のみ）
            config_restored = False
            if "config.json" in zip_file.namelist():
                try:
                    # 既存のconfig.jsonをバックアップ（存在する場合）
                    if config_path.is_file():
                        backup_path = config_path.with_suffix(".json.backup")
                        shutil.copy2(config_path, backup_path)
                        _logger.debug(
                            "Backed up existing config.json to "
                            f"{to_display_path(backup_path)}"
                        )

                    # 新しいconfig.jsonを書き込み
                    config_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(config_path, "wb") as f:
                        f.write(zip_file.read("config.json"))
                    _logger.info("Restored config.json from backup")
                    config_restored = True
                except Exception as e:
                    _logger.warning(f"Failed to restore config.json: {e}")
            else:
                _logger.debug("config.json not found in backup, skipping")

            # PDFファイルを復元
            pdf_restored_count = 0
            for file_info in zip_file.filelist:
                file_path = Path(file_info.filename)

                # pdf/ で始まるファイルのみ処理（"./" のようにデータ
                # ディレクトリ自身を指すエントリは parts が空）
                if (
                    file_path.parts[:1] == ("pdf",)
                    and file_path.suffix == ".pdf"
                    and not file_info.is_dir()
                ):
                    # 完全なパスを構築（変換後のルートの下。検証済みの結合）
                    target_path = _join_member(data_root, file_info.filename)

                    # ディレクトリを作成
                    target_path.parent.mkdir(parents=True, exist_ok=True)

                    # ファイルを書き込み
                    with open(target_path, "wb") as f:
                        f.write(zip_file.read(file_info.filename))
                    pdf_restored_count += 1
                    _logger.debug(
                        f"Restored PDF: {to_display_path(target_path)}"
                    )

            # セッションステートを更新（Streamlitアプリの場合のみ）
            if update_session_state:
                try:
                    from paper_manager.entry import PaperList

                    PaperList.from_file().to_session_state()
                except ImportError:
                    _logger.warning(
                        "Streamlit is not available, skipping session state update"
                    )

            message = "復元が完了しました。\n"
            if version_info:
                message += (
                    f"- バックアップ時のバージョン: {version_info.get('version', 'N/A')}\n"
                )
                message += (
                    f"- バックアップ日時: {version_info.get('backup_date', 'N/A')}\n"
                )
            message += f"- 復元したPDFファイル数: {pdf_restored_count}\n"
            if config_restored:
                message += "- config.jsonを復元しました\n"
            message += f"- 現在のバージョン: {__version__}"

            return True, message

    except zipfile.BadZipFile:
        return False, "無効なzipファイルです。"
    except Exception as e:
        _logger.exception("Failed to restore from backup")
        return False, f"復元中にエラーが発生しました: {str(e)}"


def get_backup_info(zip_path: Union[Path, str, BytesIO]) -> Optional[dict]:
    """バックアップzipファイルの情報を取得する。

    Parameters
    ----------
    zip_path : Union[Path, str, BytesIO]
        バックアップzipファイルのパスまたはBytesIO

    Returns
    -------
    Optional[dict]
        バージョン情報を含む辞書。取得できない場合はNone。
    """
    try:
        if isinstance(zip_path, (Path, str)):
            zip_file = zipfile.ZipFile(zip_path, "r")
        else:
            zip_file = zipfile.ZipFile(zip_path, "r")

        with zip_file:
            if "version.json" in zip_file.namelist():
                version_data = zip_file.read("version.json")
                return json.loads(version_data.decode(ENCODING))
    except Exception as e:
        _logger.exception(f"Failed to read backup info: {e}")
    return None

