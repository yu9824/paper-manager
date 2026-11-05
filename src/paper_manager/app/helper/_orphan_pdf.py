"""孤立したPDFファイルを検出・削除する機能。

このモジュールは、論文リストに紐づいていない孤立したPDFファイルを
検出・削除する機能を提供します。

孤立PDFとは:
    論文リスト（list.json）にエントリが存在しないにもかかわらず、
    PDFディレクトリに残っているPDFファイルのことです。
    これは、エントリが削除されたがPDFファイルが残っている場合や、
    手動でPDFファイルが追加された場合などに発生します。

主な機能:
    - 孤立PDFファイルの検出
    - 孤立PDFファイルの削除
    - 孤立PDFに最も近いエントリの検出

パスの扱い:
    PDFルート（``DIRPATH_PDF``）は、起点で一度だけファイル操作用のパスに
    変換（``to_fs_path``。Windowsでは拡張長のパス）する。そこから得た
    子のパス（``iterdir`` や ``glob`` の結果）は、変換後の空間にある。
    比較は変換後のパスどうしで行い、変換前の ``DIRPATH_PDF`` とは比べない
    （Windowsでは拡張長のパスのprefixの有無で一致しないため）。画面とログ
    には ``to_display_path`` で戻したパスを出す。

例:
    >>> from paper_manager.app.helper._orphan_pdf import find_orphaned_pdfs
    >>> from paper_manager.entry import PaperList
    >>> paper_list = PaperList.from_file()
    >>> orphaned = find_orphaned_pdfs(paper_list)
    >>> print(f"Found {len(orphaned)} orphaned PDF files")

"""

import shutil
from pathlib import Path
from typing import Optional

from paper_manager._constants import DIRPATH_PDF
from paper_manager.entry import Entry, PaperList
from paper_manager.helper import folder_name_key, to_display_path, to_fs_path
from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)


def _valid_name_keys(paper_list: PaperList) -> tuple[set[str], set[str]]:
    """論文に紐づく名前の比較キーを集める。

    現在の名前（``pdf_dir_name``）と、修正前の名前（``legacy_pdf_dir_name``。
    KB7の範囲で移行前のフォルダが残っているとき）の両方を有効とする。
    比較キーは大文字小文字とUnicode正規化の違いを無視する（紐づくPDFを
    孤立と判定して削除しないため。判定に迷う場合は孤立にしない側に倒す）。

    Parameters
    ----------
    paper_list : PaperList
        論文リスト

    Returns
    -------
    tuple[set[str], set[str]]
        ``(フォルダ名の比較キー, 旧形式の単一PDFのファイル名の比較キー)``。
        旧形式のファイル名は、各エントリの ``pdf_filename`` と、
        ``<フォルダ名>.pdf``（現在の名前・修正前の名前）。
    """
    dir_keys: set[str] = set()
    file_keys: set[str] = set()
    for entry in paper_list.values():
        for dir_name in (entry.pdf_dir_name, entry.legacy_pdf_dir_name):
            if dir_name is None:
                continue
            dir_keys.add(folder_name_key(dir_name))
            file_keys.add(folder_name_key(f"{dir_name}.pdf"))
        file_keys.add(folder_name_key(entry.pdf_filename))
    return dir_keys, file_keys


def find_orphaned_pdfs(paper_list: PaperList) -> list[Path]:
    """論文リストに紐づいていない孤立したPDFファイルを検出する。

    走査はファイル操作用に変換したPDFルート（``to_fs_path(DIRPATH_PDF)``）
    から行い、戻り値のパスも変換後の空間にある（画面とログに出すときは
    ``to_display_path`` で戻す）。次のものは孤立と判定しない。

    - 名前が、いずれかの論文の現在の名前または修正前の名前（KB7の範囲）と
      一致する（大文字小文字とUnicode正規化の違いを除く）フォルダ内のPDF
    - 名前が、いずれかの論文の旧形式の単一PDFの名前（``pdf_filename``、
      ``<フォルダ名>.pdf``）と一致する、PDFルート直下のPDF。フォルダへ
      移せなかった旧形式のPDFもここに含まれる

    Parameters
    ----------
    paper_list : PaperList
        論文リスト

    Returns
    -------
    list[Path]
        孤立したPDFファイルのパスのリスト（変換後のパス。名前順）
    """
    orphaned_pdfs: list[Path] = []

    pdf_root = to_fs_path(DIRPATH_PDF)
    if not pdf_root.is_dir():
        return orphaned_pdfs

    # 各エントリのPDFファイルを取得する。旧形式のPDFと修正前の名前の
    # フォルダの移行を、PDFルートの走査の前に済ませるために呼ぶ
    for entry in paper_list.values():
        entry.get_pdf_files()

    valid_dir_keys, valid_file_keys = _valid_name_keys(paper_list)

    # PDFディレクトリ内のすべてのディレクトリをチェック
    for pdf_dir in sorted(pdf_root.iterdir()):
        if not pdf_dir.is_dir():
            # 旧形式の単一PDFファイルの可能性
            if pdf_dir.suffix == ".pdf":
                if folder_name_key(pdf_dir.name) not in valid_file_keys:
                    orphaned_pdfs.append(pdf_dir)
                    _logger.debug(
                        "Found orphaned legacy PDF: %s",
                        to_display_path(pdf_dir),
                    )
            continue

        # ディレクトリ名が有効なフォルダ名と一致するかチェック
        dir_name = pdf_dir.name
        if folder_name_key(dir_name) not in valid_dir_keys:
            # このディレクトリ内のすべてのPDFファイルを孤立としてマーク
            pdf_files_in_dir = sorted(pdf_dir.glob("*.pdf"))
            orphaned_pdfs.extend(pdf_files_in_dir)
            _logger.debug(
                "Found orphaned PDF directory: %s (%d files)",
                dir_name,
                len(pdf_files_in_dir),
            )

    return orphaned_pdfs


def find_closest_entry_for_orphaned_pdf(
    orphaned_pdf_path: Path, paper_list: PaperList
) -> Optional[tuple[Entry, float]]:
    """孤立PDFに最も近いエントリを見つける。

    Parameters
    ----------
    orphaned_pdf_path : Path
        孤立PDFファイルのパス。変換後のパスでも変換前のパスでもよい
        （PDFルートとの比較は、どちらも変換後のパスにして行う）。
    paper_list : PaperList
        論文リスト

    Returns
    -------
    Optional[tuple[Entry, float]]
        最も近いエントリと類似度のタプル。見つからない場合はNone。
        類似度は0.0から1.0の範囲で、1.0が完全一致。
    """
    if not paper_list:
        return None

    # 孤立PDFのディレクトリ名を取得
    if orphaned_pdf_path.is_file():
        # ファイルの場合、親ディレクトリ名を使用
        orphaned_dir_name = orphaned_pdf_path.parent.name
        # 親がPDFルートの場合は、ファイル名から推測
        if to_fs_path(orphaned_pdf_path.parent) == to_fs_path(DIRPATH_PDF):
            # 旧形式のPDFファイルの場合、ファイル名から推測
            orphaned_dir_name = orphaned_pdf_path.stem
    else:
        orphaned_dir_name = orphaned_pdf_path.name

    best_match: Optional[tuple[Entry, float]] = None
    best_similarity = 0.0

    for entry in paper_list.values():
        pdf_dir_name = entry.pdf_dir_name
        similarity = _calculate_similarity(orphaned_dir_name, pdf_dir_name)

        if similarity > best_similarity:
            best_similarity = similarity
            best_match = (entry, similarity)

    return best_match


def _calculate_similarity(s1: str, s2: str) -> float:
    """2つの文字列の類似度を計算する。

    Parameters
    ----------
    s1 : str
        比較対象の文字列1
    s2 : str
        比較対象の文字列2

    Returns
    -------
    float
        類似度（0.0から1.0の範囲、1.0が完全一致）
    """
    # 大文字小文字を無視
    s1_lower = s1.lower()
    s2_lower = s2.lower()

    # 完全一致
    if s1_lower == s2_lower:
        return 1.0

    # 一方が他方に含まれている場合
    if s1_lower in s2_lower or s2_lower in s1_lower:
        min_len = min(len(s1_lower), len(s2_lower))
        max_len = max(len(s1_lower), len(s2_lower))
        return min_len / max_len if max_len > 0 else 0.0

    # 共通部分の長さを計算（前方一致）
    common_prefix = 0
    min_len = min(len(s1_lower), len(s2_lower))
    for i in range(min_len):
        if s1_lower[i] == s2_lower[i]:
            common_prefix += 1
        else:
            break

    # 共通接頭辞の長さに基づく類似度
    if common_prefix > 0:
        max_len = max(len(s1_lower), len(s2_lower))
        return common_prefix / max_len if max_len > 0 else 0.0

    # 共通単語の数をカウント
    words1 = set(s1_lower.split())
    words2 = set(s2_lower.split())
    if words1 and words2:
        common_words = words1 & words2
        all_words = words1 | words2
        if all_words:
            return len(common_words) / len(all_words)

    return 0.0


def _failure_reason(error: Exception) -> str:
    """削除の失敗の理由を、パスを含まない文字列で返す。

    ``str(error)`` には変換後のパス（Windowsでは拡張長のパスのprefix付き）
    が含まれるため、パスは呼び出し元が ``to_display_path`` で出す。

    Parameters
    ----------
    error : Exception
        削除で送出された例外

    Returns
    -------
    str
        ``OSError`` は ``strerror``（なければ ``str(error)``）、
        それ以外は ``str(error)``
    """
    if isinstance(error, OSError) and error.strerror:
        return error.strerror
    return str(error)


def _count_pdfs(folder: Path) -> int:
    """フォルダの下にあるPDFファイルの数を数える。

    Parameters
    ----------
    folder : Path
        数えるフォルダ（変換後のパス）

    Returns
    -------
    int
        ``folder`` の下（サブフォルダを含む）にある ``*.pdf`` ファイルの数
    """
    return sum(1 for path in folder.rglob("*.pdf") if path.is_file())


def delete_orphaned_pdfs(
    orphaned_pdfs: list[Path],
    failures: Optional[list[tuple[Path, str]]] = None,
) -> int:
    """孤立したPDFファイルを削除する。

    削除は変換後のパス（``to_fs_path``）で行う。ファイルを削除した結果
    フォルダが空になったら、そのフォルダも削除する（PDFルート自体は
    削除しない。PDFルートかどうかは、変換後のPDFルートと比べて判定する）。
    フォルダを渡されたら、削除の前にその下のPDFを数え、フォルダごと削除
    して、削除したPDFの件数を数える。1件の失敗で残りの削除は止めない。

    Parameters
    ----------
    orphaned_pdfs : list[Path]
        削除するPDFファイル（またはフォルダ）のパスのリスト
    failures : Optional[list[tuple[Path, str]]], default None
        削除に失敗したパスと理由（パスを含まない）を追加するリスト。
        渡されたリストに、失敗のたびに ``(パス, 理由)`` を追加する。
        ``None`` なら失敗はログにだけ出す。画面には ``to_display_path`` で
        戻したパスと理由を出す。ファイルの削除後に、空になったフォルダを
        削除できなかったときは、そのフォルダを追加する（ファイルは削除
        済みで、件数には数える）

    Returns
    -------
    int
        削除したPDFファイルの数。フォルダを渡した場合は、フォルダごと
        削除したPDFの数。削除に失敗したフォルダのPDFは数えない
    """
    deleted_count = 0
    pdf_root = to_fs_path(DIRPATH_PDF)

    def record_failure(path: Path, error: Exception) -> None:
        reason = _failure_reason(error)
        _logger.error(
            "Failed to delete orphaned PDF %s: %s",
            to_display_path(path),
            reason,
        )
        if failures is not None:
            failures.append((path, reason))

    for pdf_path in orphaned_pdfs:
        try:
            target = to_fs_path(pdf_path)
            is_file = target.is_file()
            if is_file:
                target.unlink()
                count = 1
            elif target.is_dir():
                # ディレクトリ全体を削除（削除の前にPDFを数える）
                count = _count_pdfs(target)
                shutil.rmtree(target)
            else:
                continue
        except Exception as e:
            record_failure(pdf_path, e)
            continue
        deleted_count += count
        _logger.debug("Deleted orphaned PDF: %s", to_display_path(target))

        if not is_file:
            continue
        # ディレクトリが空になったら削除（PDFルート自体は削除しない）
        parent_dir = target.parent
        try:
            if (
                parent_dir != pdf_root
                and parent_dir.is_dir()
                and not any(parent_dir.iterdir())
            ):
                parent_dir.rmdir()
                _logger.debug(
                    "Removed empty directory: %s", to_display_path(parent_dir)
                )
        except Exception as e:
            record_failure(pdf_path.parent, e)

    return deleted_count
