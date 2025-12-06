"""孤立したPDFファイルを検出・削除する機能。"""

import shutil
from pathlib import Path

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
    orphaned_pdfs = []

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

