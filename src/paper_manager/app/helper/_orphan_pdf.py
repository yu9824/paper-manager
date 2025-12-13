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
from paper_manager.logging import get_child_logger

_logger = get_child_logger(__name__)


def find_orphaned_pdfs(paper_list: PaperList) -> list[Path]:
    """論文リストに紐づいていない孤立したPDFファイルを検出する。

    Parameters
    ----------
    paper_list : PaperList
        論文リスト

    Returns
    -------
    list[Path]
        孤立したPDFファイルのパスのリスト
    """
    orphaned_pdfs: list[Path] = []

    if not DIRPATH_PDF.is_dir():
        return orphaned_pdfs

    # 論文リスト内のすべてのエントリのpdf_dir_nameを取得
    valid_pdf_dirs = {entry.pdf_dir_name for entry in paper_list.values()}
    valid_pdf_files = set()

    # 各エントリのPDFファイルを収集
    for entry in paper_list.values():
        pdf_files = entry.get_pdf_files()
        valid_pdf_files.update(pdf_files)

    # PDFディレクトリ内のすべてのディレクトリをチェック
    for pdf_dir in DIRPATH_PDF.iterdir():
        if not pdf_dir.is_dir():
            # 旧形式の単一PDFファイルの可能性
            if pdf_dir.suffix == ".pdf":
                if pdf_dir not in valid_pdf_files:
                    orphaned_pdfs.append(pdf_dir)
                    _logger.debug(f"Found orphaned legacy PDF: {pdf_dir}")
            continue

        # ディレクトリ名が有効なpdf_dir_nameと一致するかチェック
        dir_name = pdf_dir.name
        if dir_name not in valid_pdf_dirs:
            # このディレクトリ内のすべてのPDFファイルを孤立としてマーク
            pdf_files_in_dir = list(pdf_dir.glob("*.pdf"))
            orphaned_pdfs.extend(pdf_files_in_dir)
            _logger.debug(
                f"Found orphaned PDF directory: {dir_name} "
                f"({len(pdf_files_in_dir)} files)"
            )

    return orphaned_pdfs


def find_closest_entry_for_orphaned_pdf(
    orphaned_pdf_path: Path, paper_list: PaperList
) -> Optional[tuple[Entry, float]]:
    """孤立PDFに最も近いエントリを見つける。

    Parameters
    ----------
    orphaned_pdf_path : Path
        孤立PDFファイルのパス
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
        # 親がDIRPATH_PDFの場合は、ファイル名から推測
        if orphaned_pdf_path.parent == DIRPATH_PDF:
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


def delete_orphaned_pdfs(orphaned_pdfs: list[Path]) -> int:
    """孤立したPDFファイルを削除する。

    Parameters
    ----------
    orphaned_pdfs : list[Path]
        削除するPDFファイルのパスのリスト

    Returns
    -------
    int
        削除したファイル数
    """
    deleted_count = 0

    for pdf_path in orphaned_pdfs:
        try:
            if pdf_path.is_file():
                pdf_path.unlink()
                deleted_count += 1
                _logger.debug(f"Deleted orphaned PDF: {pdf_path}")

                # ディレクトリが空になったら削除
                parent_dir = pdf_path.parent
                if parent_dir != DIRPATH_PDF and parent_dir.is_dir():
                    if not any(parent_dir.iterdir()):
                        parent_dir.rmdir()
                        _logger.debug(f"Removed empty directory: {parent_dir}")

            elif pdf_path.is_dir():
                # ディレクトリ全体を削除
                shutil.rmtree(pdf_path)
                deleted_count += len(list(pdf_path.glob("*.pdf")))
                _logger.debug(f"Deleted orphaned PDF directory: {pdf_path}")

        except Exception as e:
            _logger.warning(f"Failed to delete orphaned PDF {pdf_path}: {e}")

    return deleted_count
