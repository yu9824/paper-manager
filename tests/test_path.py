"""パスの変換と比較キー (``paper_manager.helper._path``) の単体テスト.

対象は次のとおり。

- 拡張長のパスへの変換 (``_to_extended_length``): ``ntpath`` の意味論で
  絶対パスに正規化してから ``\\\\?\\`` を付けること。``/`` 区切りは ``\\``
  になり、``..`` は畳まれ、UNC は ``\\\\?\\UNC\\`` になり、変換済みのパスは
  そのまま返ること (冪等)。ドライブ相対などの完全修飾でないパスは、
  Windows では ``abspath`` で解決してから変換し、解決できない環境では
  prefix を付けずにそのまま返すこと (要件 1.1)
- 表示用の逆変換 (``_from_extended_length``): prefix を外したパスを返し、
  変換の結果を逆変換すると ``ntpath.abspath`` の結果に戻ること (要件 1.4)
- OS による切り替え (``_select_converters``): Windows (``os.name == "nt"``)
  では上の 2 つ、それ以外ではパスを変えない変換を選ぶこと。Windows 以外では
  ``to_fs_path`` が ``Path(p)``、``to_display_path`` が ``str(p)`` に等しく、
  パスを正規化も絶対パス化もしないこと (要件 1.1、1.3)。空のパスは、
  カレントディレクトリを指さないよう ``ValueError`` になること
- 差し替え口 (``_convert`` / ``_unconvert``): ``to_fs_path`` /
  ``to_display_path`` が呼び出し時にモジュールの属性を参照するため、名前で
  import した呼び出し側にも差し替えが効くこと (要件 12.3 の前提)
- Windows のパスの扱いの模倣 (fixture ``windows_like_paths``): 有効にすると
  ``to_fs_path`` の結果が ``//`` で始まる別の形になり、``to_display_path``
  で元の形に戻ること。変換後のパスと変換前のパスの比較が一致せず、
  ``relative_to`` が ``ValueError`` になること (prefix の混在の再現)。
  変換後のパスへのファイル操作が成功すること。fixture を使わないテストでは
  変換が元に戻っていること (要件 12.3)
- フォルダ名の比較キー (``folder_name_key``): 大文字小文字だけ、または
  Unicode 正規化だけが違う名前が同じキーになり、別の名前は別のキーになる
  こと。文字列だけから決まり、OS やファイルシステムに依存しない (要件 3.4)
- PDF を扱う各機能の配線 (末尾の節。known-bug-fixes の要件 1.2, 12.3):
  保存 → 一覧 → 孤立 PDF の検出 → バックアップ → 全削除 → 復元の一連の流れを、
  変換後のパスが別の空間へ写る 2 通りの状態 (``converted_space``) で通す。
  ``windows-like`` は変換後のパスと変換前のパスを混ぜた比較と
  ``relative_to`` を壊し、``redirected`` は変換後のパスを使わない実装を、
  何も見つけられないことで検出する。zip のメンバー名に prefix が入らない
  こと、孤立 PDF の削除で PDF ルートが削除されないことも、両方の空間で確かめる

``_to_extended_length`` と ``_from_extended_length`` は純粋な文字列変換で、
``ntpath`` はどの OS でも動くため、Windows 以外でも検証できる。ドライブ相対
や相対パスの解決だけはカレントディレクトリと OS に依存するため、結果の
期待を ``os.name`` で分ける。``windows_like_paths`` は Windows では
スキップされる (本物の変換が働くため)。
"""

import ntpath
import os
import shutil
import unicodedata
import zipfile
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import NamedTuple, Union

import pytest
from conftest import (
    PDF_BYTES,
    ConvertedSpace,
    DataPaths,
    FakeUpload,
    StMessages,
    write_pdf,
)

import paper_manager.helper._path as _path
from paper_manager.app.components._common import delete_pdfs, save_pdfs
from paper_manager.app.helper._backup import (
    create_backup_zip,
    restore_from_zip,
)
from paper_manager.app.helper._orphan_pdf import (
    delete_orphaned_pdfs,
    find_orphaned_pdfs,
)
from paper_manager.entry import Entry, PaperList
from paper_manager.helper import (
    folder_name_key,
    to_display_path,
    to_fs_path,
)

_PREFIX = "\\\\?\\"
_UNC_PREFIX = "\\\\?\\UNC\\"

_non_windows_only = pytest.mark.skipif(
    os.name == "nt", reason="Windows 以外でパスを変えないことの検証"
)

# --- _to_extended_length (要件 1.1) ---------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        pytest.param("C:\\a\\b", _PREFIX + "C:\\a\\b", id="drive"),
        pytest.param("C:/a/b", _PREFIX + "C:\\a\\b", id="forward-slash"),
        pytest.param("C:\\a\\..\\b", _PREFIX + "C:\\b", id="dot-dot"),
        pytest.param(
            "C:\\a\\.\\b\\", _PREFIX + "C:\\a\\b", id="dot-and-trailing-sep"
        ),
        pytest.param(
            "C:\\データ\\論文", _PREFIX + "C:\\データ\\論文", id="non-ascii"
        ),
        pytest.param(
            "\\\\server\\share\\x",
            _UNC_PREFIX + "server\\share\\x",
            id="unc",
        ),
        pytest.param(
            "//server/share/x",
            _UNC_PREFIX + "server\\share\\x",
            id="unc-forward-slash",
        ),
        pytest.param(
            "\\\\server\\share\\a\\..\\x",
            _UNC_PREFIX + "server\\share\\x",
            id="unc-dot-dot",
        ),
    ],
)
def test_to_extended_length_normalizes_then_adds_prefix(
    path: str, expected: str
) -> None:
    """絶対パスに正規化してから prefix を付ける (要件 1.1)."""
    assert _path._to_extended_length(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("\\\\?\\C:\\a", id="extended"),
        pytest.param("\\\\?\\UNC\\server\\share\\x", id="extended-unc"),
        # prefix 付きのパスでは ``..`` はリテラルの名前なので、畳まない
        pytest.param("\\\\?\\C:\\a\\..\\b", id="extended-with-dot-dot"),
        pytest.param("\\\\.\\C:\\a", id="device"),
    ],
)
def test_to_extended_length_returns_prefixed_path_unchanged(
    path: str,
) -> None:
    """変換済み (device パスを含む) のパスはそのまま返す (冪等)."""
    assert _path._to_extended_length(path) == path


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        pytest.param("//?/C:/a", _PREFIX + "C:\\a", id="extended"),
        pytest.param("//./C:/a", "\\\\.\\C:\\a", id="device"),
    ],
)
def test_to_extended_length_keeps_device_path_written_with_slashes(
    path: str, expected: str
) -> None:
    """``/`` で書かれた device パスを UNC と取り違えない."""
    assert _path._to_extended_length(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        "C:\\a\\b",
        "C:/a/../b",
        "\\\\server\\share\\x",
        "//server/share/a/../x",
    ],
)
def test_to_extended_length_is_idempotent(path: str) -> None:
    """変換の結果をもう一度変換しても変わらない."""
    once = _path._to_extended_length(path)

    assert _path._to_extended_length(once) == once


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("C:a\\b", id="drive-relative"),
        pytest.param("\\a\\b", id="rooted-without-drive"),
        pytest.param("a\\b", id="relative"),
    ],
)
def test_to_extended_length_resolves_or_keeps_not_fully_qualified_path(
    path: str,
) -> None:
    """完全修飾でないパスに prefix だけを付けることはしない.

    ``\\\\?\\`` は ``abspath`` の解決を経ないため、``\\\\?\\C:a`` のような
    パスは別の場所を指してしまう。Windows では ``abspath`` で解決してから
    変換し、それ以外の環境 (ドライブのカレントディレクトリがない) では
    prefix を付けずにそのまま返す。
    """
    result = _path._to_extended_length(path)

    if os.name == "nt":
        resolved = ntpath.abspath(path)
        assert result == _PREFIX + resolved
        drive, rest = ntpath.splitdrive(resolved)
        assert drive.endswith(":")
        assert rest.startswith("\\")
    else:
        assert result == path


# --- _from_extended_length (要件 1.4) -------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        pytest.param(_PREFIX + "C:\\a\\b", "C:\\a\\b", id="extended"),
        pytest.param(
            _UNC_PREFIX + "server\\share\\x",
            "\\\\server\\share\\x",
            id="extended-unc",
        ),
        pytest.param("C:\\a\\b", "C:\\a\\b", id="not-prefixed"),
        pytest.param(
            "\\\\server\\share\\x", "\\\\server\\share\\x", id="plain-unc"
        ),
        pytest.param("/home/user/a", "/home/user/a", id="posix"),
    ],
)
def test_from_extended_length_strips_prefix(path: str, expected: str) -> None:
    """prefix を外したパスを返し、prefix のないパスは変えない (要件 1.4)."""
    assert _path._from_extended_length(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        "C:\\a\\b",
        "C:/a/../b",
        "C:\\データ\\論文.pdf",
        "\\\\server\\share\\x",
        "//server/share/a/../x",
    ],
)
def test_from_extended_length_inverts_to_extended_length(path: str) -> None:
    """変換の結果を逆変換すると ``ntpath.abspath`` の結果に戻る."""
    converted = _path._to_extended_length(path)

    assert _path._from_extended_length(converted) == ntpath.abspath(path)


# --- OS による切り替え (要件 1.1、1.3) ------------------------------------


def test_windows_selects_extended_length_converters() -> None:
    """Windows では拡張長のパスへの変換と、その逆変換を選ぶ."""
    convert, unconvert = _path._select_converters("nt")

    assert convert("C:/a/../b") == _PREFIX + "C:\\b"
    assert unconvert(_PREFIX + "C:\\b") == "C:\\b"


@pytest.mark.parametrize("os_name", ["posix", "java"])
def test_non_windows_selects_converters_that_keep_path(os_name: str) -> None:
    """Windows 以外では、正規化も絶対パス化もしない変換を選ぶ."""
    convert, unconvert = _path._select_converters(os_name)

    for path in ("rel/a/../b", "/abs/a/../b", "C:\\a", _PREFIX + "C:\\a"):
        assert convert(path) == path
        assert unconvert(path) == path


def test_module_converters_match_running_os() -> None:
    """モジュールの変換は、実行中の OS 向けの組み合わせになっている."""
    convert, unconvert = _path._select_converters(os.name)

    assert _path._convert is convert
    assert _path._unconvert is unconvert


@_non_windows_only
@pytest.mark.parametrize(
    "path",
    [
        pytest.param("/abs/a/../b", id="absolute-with-dot-dot"),
        pytest.param("rel/a", id="relative"),
        pytest.param(Path("/abs/データ/論文"), id="path-object"),
    ],
)
def test_to_fs_path_keeps_path_on_non_windows(
    path: Union[str, Path],
) -> None:
    """Windows 以外では ``Path(path)`` をそのまま返す (要件 1.3)."""
    result = to_fs_path(path)

    assert isinstance(result, Path)
    assert result == Path(path)
    assert str(result) == str(Path(path))


@_non_windows_only
@pytest.mark.parametrize(
    "path",
    [
        pytest.param("/abs/a/../b", id="absolute-with-dot-dot"),
        pytest.param("rel/a", id="relative"),
        pytest.param(Path("/abs/データ/論文"), id="path-object"),
    ],
)
def test_to_display_path_keeps_path_on_non_windows(
    path: Union[str, Path],
) -> None:
    """Windows 以外では ``str(path)`` を返し、往復しても変わらない."""
    assert to_display_path(path) == str(path)
    assert to_display_path(to_fs_path(path)) == str(path)


@pytest.mark.parametrize("os_name", ["nt", "posix"])
def test_to_fs_path_rejects_empty_path(
    monkeypatch: pytest.MonkeyPatch, os_name: str
) -> None:
    """空のパスはカレントディレクトリを指してしまうため、例外にする."""
    convert, unconvert = _path._select_converters(os_name)
    monkeypatch.setattr(_path, "_convert", convert)
    monkeypatch.setattr(_path, "_unconvert", unconvert)

    with pytest.raises(ValueError, match="empty"):
        to_fs_path("")


# --- 差し替え口 (要件 12.3 の前提) ----------------------------------------


def test_replacing_convert_affects_name_bound_to_fs_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_convert`` の差し替えが、名前で import した呼び出し側に効く."""
    calls: list[str] = []

    def fake_convert(path: str) -> str:
        calls.append(path)
        return "/alias" + path

    monkeypatch.setattr(_path, "_convert", fake_convert)

    result = to_fs_path(Path("/data/pdf"))

    assert result == Path("/alias/data/pdf")
    assert calls == [os.fspath(Path("/data/pdf"))]


def test_replacing_unconvert_affects_name_bound_to_display_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_unconvert`` の差し替えが、名前で import した呼び出し側に効く."""
    calls: list[str] = []

    def fake_unconvert(path: str) -> str:
        calls.append(path)
        return "shown:" + path

    monkeypatch.setattr(_path, "_unconvert", fake_unconvert)

    result = to_display_path(Path("/data/pdf"))

    assert result == "shown:" + os.fspath(Path("/data/pdf"))
    assert calls == [os.fspath(Path("/data/pdf"))]


def test_windows_converters_through_public_functions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows の変換に差し替えると、公開関数が拡張長のパスを扱う."""
    convert, unconvert = _path._select_converters("nt")
    monkeypatch.setattr(_path, "_convert", convert)
    monkeypatch.setattr(_path, "_unconvert", unconvert)

    fs_path = to_fs_path("C:/data/a/../pdf")

    assert str(fs_path) == _PREFIX + "C:\\data\\pdf"
    assert to_display_path(fs_path) == "C:\\data\\pdf"


# --- windows_like_paths (要件 12.3) ---------------------------------------
# 別名変換: 絶対パスに正規化してから、前に ``/`` を 1 つ足す (``/x`` を
# ``//x`` にする)。逆変換は先頭の ``//`` を ``/`` に戻す。

_windows_like = pytest.mark.usefixtures("windows_like_paths")


@_windows_like
@pytest.mark.parametrize(
    "path",
    [
        pytest.param("/data/pdf", id="absolute"),
        pytest.param("/data/a/../pdf", id="absolute-with-dot-dot"),
        pytest.param("rel/a", id="relative"),
        pytest.param(Path("/データ/論文"), id="path-object"),
    ],
)
def test_windows_like_paths_changes_form_and_display_restores_it(
    path: Union[str, Path],
) -> None:
    """変換後のパスは ``//`` で始まる別の形になり、表示用の逆変換で戻る."""
    absolute = os.path.abspath(path)

    fs_path = to_fs_path(path)

    assert isinstance(fs_path, Path)
    assert str(fs_path) == "/" + absolute
    assert fs_path.root == "//"
    assert fs_path != Path(absolute)
    assert to_display_path(fs_path) == absolute


@_windows_like
def test_windows_like_paths_is_idempotent() -> None:
    """変換済みのパスはそのまま、変換前のパスの表示は変わらない."""
    once = to_fs_path("/data/pdf")

    assert str(to_fs_path(once)) == str(once)
    assert to_display_path("/data/pdf") == "/data/pdf"


@_windows_like
def test_windows_like_paths_reproduces_prefix_mixing(tmp_path: Path) -> None:
    """変換前と変換後のパスは同じ場所でも、比較・相対パスで一致しない.

    Windows の拡張長のパスと同じく、prefix の混在が不具合になる。変換後の
    空間どうしなら、比較も ``relative_to`` も成り立つ。
    """
    root = tmp_path / "pdf"
    (root / "a").mkdir(parents=True)

    fs_root = to_fs_path(root)
    fs_child = next(fs_root.iterdir())

    assert fs_root.samefile(root)
    assert fs_root != root
    assert fs_root not in {root}
    with pytest.raises(ValueError):
        fs_child.relative_to(root)
    with pytest.raises(ValueError):
        (root / "a").relative_to(fs_root)

    assert fs_child == fs_root / "a"
    assert fs_child.relative_to(fs_root) == Path("a")
    assert to_fs_path(root / "a").relative_to(fs_root) == Path("a")


@_windows_like
def test_windows_like_paths_file_operations_succeed(tmp_path: Path) -> None:
    """変換後のパスへの作成・書き込み・一覧・改名・削除が成功する."""
    data = tmp_path / "data"
    fs_data = to_fs_path(data)
    fs_folder = fs_data / "pdf" / "2020 - Smith - 論文"
    fs_folder.mkdir(parents=True)
    (fs_folder / "00.pdf").write_bytes(b"%PDF-00")
    with open(fs_folder / "01.pdf", "wb") as f:
        f.write(b"%PDF-01")
    (fs_folder / "01.pdf").rename(fs_folder / "02.pdf")

    assert sorted(fs_data.glob("pdf/*/*.pdf")) == [
        fs_folder / "00.pdf",
        fs_folder / "02.pdf",
    ]
    folder = data / "pdf" / "2020 - Smith - 論文"
    assert (folder / "00.pdf").read_bytes() == b"%PDF-00"
    assert (folder / "02.pdf").read_bytes() == b"%PDF-01"

    fs_zip = to_fs_path(tmp_path / "backup.zip")
    with zipfile.ZipFile(fs_zip, "w") as zf:
        for pdf in sorted(fs_data.rglob("*.pdf")):
            zf.write(pdf, pdf.relative_to(fs_data).as_posix())
    with zipfile.ZipFile(tmp_path / "backup.zip") as zf:
        assert zf.namelist() == [
            "pdf/2020 - Smith - 論文/00.pdf",
            "pdf/2020 - Smith - 論文/02.pdf",
        ]

    (fs_folder / "02.pdf").unlink()
    assert not (folder / "02.pdf").exists()
    shutil.rmtree(fs_data)
    assert not data.exists()


@_non_windows_only
def test_conversion_is_restored_without_windows_like_paths() -> None:
    """fixture を使わないテストでは、パスを変えない変換に戻っている.

    上の ``windows_like_paths`` のテストより後に置き、差し替えがテストの
    後に戻ることを既定の順序でも確かめる。
    """
    convert, unconvert = _path._select_converters(os.name)

    assert _path._convert is convert
    assert _path._unconvert is unconvert
    assert str(to_fs_path("/data/pdf")) == "/data/pdf"
    assert to_display_path(Path("/data/pdf")) == "/data/pdf"


# --- folder_name_key (要件 3.4) -------------------------------------------

_NFC_E_ACUTE = "\u00e9"
_NFD_E_ACUTE = "e\u0301"


@pytest.mark.parametrize(
    ("left", "right"),
    [
        pytest.param(
            "2020 - Smith - Deep Learning",
            "2020 - smith - deep learning",
            id="case-only",
        ),
        pytest.param(
            "2020 - Smith - Deep Learning",
            "2020 - SMITH - DEEP LEARNING",
            id="upper-case",
        ),
        pytest.param(
            "Caf" + _NFC_E_ACUTE, "Caf" + _NFD_E_ACUTE, id="nfc-and-nfd"
        ),
        pytest.param(
            "CAF\u00c9", "caf" + _NFD_E_ACUTE, id="case-and-normalization"
        ),
        # casefold は NTFS より広く一致させる (同じとみなす安全側)
        pytest.param("Straße", "STRASSE", id="sharp-s"),
        # 結合文字の順序が正規でない名前は、NFD で並べ替えてから casefold
        # しないと、合成済みの文字と同じキーにならない
        pytest.param(
            "\u1fb4", "\u03b1\u0345\u0301", id="combining-mark-order"
        ),
        pytest.param("論文", "論文", id="identical-non-ascii"),
    ],
)
def test_folder_name_key_equal_for_case_or_normalization_only(
    left: str, right: str
) -> None:
    """大文字小文字・Unicode 正規化だけが違う名前は同じキー (要件 3.4)."""
    assert folder_name_key(left) == folder_name_key(right)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        pytest.param(
            "2020 - Smith - Deep Learning",
            "2020 - Smith - Deep Learnings",
            id="extra-char",
        ),
        pytest.param("Caf" + _NFC_E_ACUTE, "Cafe", id="accent"),
        pytest.param("Résumé", "Resume", id="accents"),
        pytest.param("a b", "a  b", id="whitespace"),
        pytest.param("論文", "論分", id="different-kanji"),
    ],
)
def test_folder_name_key_differs_for_different_names(
    left: str, right: str
) -> None:
    """大文字小文字・正規化以外が違う名前は別のキー (要件 3.4)."""
    assert folder_name_key(left) != folder_name_key(right)


def test_folder_name_key_is_nfd_casefolded_string() -> None:
    """キーは NFD の文字列で、もう一度キーにしても変わらない."""
    key = folder_name_key("CAF\u00c9 Straße")

    assert isinstance(key, str)
    assert key == unicodedata.normalize("NFD", key)
    assert key == "caf" + _NFD_E_ACUTE + " strasse"
    assert folder_name_key(key) == key


# --- PDF を扱う各機能の配線 (known-bug-fixes 要件 1.2, 12.3) ----------------
# 保存した PDF が一覧に出て、孤立 PDF の検出に出ず、バックアップに入り、全削除
# の後に復元で戻る。この流れを、変換後のパスが別の空間へ写る 2 通りの状態
# (``converted_space``) で通す。
#
# - ``windows-like``: 変換後のパス (``//x``) は変換前のパス (``/x``) と別の
#   ルートになる。変換前後のパスを混ぜた比較と ``relative_to`` が壊れる
# - ``redirected``: 変換後のパスは別のディレクトリを指す。変換後のパスを
#   使わずに変換前のパスで読み書きする実装は、何も見つけられない
#
# 期待値は、テストが作ったデータ (保存した中身と置いた場所) から導く。PDF の
# 置き場所の確認と準備は ``converted_space`` の普通のパスで行い、アプリの
# 関数が返すパスは、変換後のパス (``to_fs_path(DIRPATH_PDF)`` の下) と
# 比べる。

_ignore_legacy_deprecation = pytest.mark.filterwarnings(
    "ignore:(pdf_filename|_migrate_legacy_pdf_filenames) is deprecated"
    ":DeprecationWarning"
)
# 論文リストに無い (孤立した) PDF の置き場所
ORPHAN_DIR = "2001 - Nobody - Unregistered Paper"
ORPHAN_LEGACY = "Unregistered Legacy Paper.pdf"


class SavedPdfs(NamedTuple):
    """``saved_pdfs`` が保存した論文と PDF、置いたファイル."""

    paper_list: PaperList
    alice: Entry  # PDF が 2 件
    bob: Entry  # PDF が 1 件
    # 論文に紐づく PDF: データディレクトリからの相対パス (``/`` 区切り) -> 中身
    linked: dict[str, bytes]
    # 論文に紐づかない PDF (新形式のフォルダに 2 件、旧形式の単一 PDF が 1 件)
    orphans: dict[str, bytes]
    # list.json と config.json: ファイル名 -> 中身
    meta: dict[str, bytes]


def pdf_member(entry: Entry, index: int) -> str:
    """``entry`` の ``index`` 番目の PDF の、データディレクトリからの相対パス."""
    return f"pdf/{entry.pdf_dir_name}/{index:02d}.pdf"


def pdfs_under(root: Path) -> dict[str, bytes]:
    """``root`` の下の PDF を、相対パス (``/`` 区切り) -> 中身で返す."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*.pdf"))
    }


def read_members(source: BytesIO) -> dict[str, bytes]:
    """zip のすべてのメンバーを、名前 -> 中身で返す."""
    with zipfile.ZipFile(source) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def files_under(root: Path) -> list[Path]:
    """``root`` の下のファイル (ディレクトリを除く) を返す."""
    return sorted(path for path in root.rglob("*") if path.is_file())


@pytest.fixture
def saved_pdfs(
    converted_space: ConvertedSpace,
    make_entry: Callable[..., Entry],
    st_messages: StMessages,
) -> SavedPdfs:
    """変換後の空間に、論文 2 件の PDF と孤立 PDF、list.json を用意する.

    論文の PDF は、アプリの ``save_pdfs`` で保存する (alice に 2 件、bob に
    1 件)。孤立 PDF と list.json、config.json は、``converted_space`` の
    場所に直接置く。``converted_space`` を先に有効にしてから保存するので、
    保存は変換後の空間で行われる。保存した件数とエラー通知がないことは、
    この fixture の中で確かめる。
    """
    alice = make_entry()
    bob = make_entry(author="Bob Jones", title="Quantum Widgets", year="2019")
    paper_list = PaperList({})
    for entry in (alice, bob):
        entry["ID"] = entry.get_key(paper_list.keys())
        paper_list[entry["ID"]] = entry

    linked: dict[str, bytes] = {}
    for entry, labels in ((alice, ("a0", "a1")), (bob, ("b0",))):
        uploads = [
            FakeUpload(
                f"upload-{label}.pdf", PDF_BYTES + f"%{label}\n".encode()
            )
            for label in labels
        ]
        assert save_pdfs(uploads, entry) == len(uploads)
        for index, upload in enumerate(uploads):
            linked[pdf_member(entry, index)] = upload.getvalue()
    assert st_messages.error == []

    orphans = {
        f"pdf/{ORPHAN_DIR}/00.pdf": PDF_BYTES + b"%orphan 00\n",
        f"pdf/{ORPHAN_DIR}/01.pdf": PDF_BYTES + b"%orphan 01\n",
        f"pdf/{ORPHAN_LEGACY}": PDF_BYTES + b"%orphan legacy\n",
    }
    for name, content in orphans.items():
        write_pdf(converted_space.root / name, content)

    list_file = converted_space.root / "list.json"
    paper_list.to_file(list_file)
    config = b'{"proxy": {"http": "http://proxy.example.com:8080"}}\n'
    (converted_space.root / "config.json").write_bytes(config)
    return SavedPdfs(
        paper_list=paper_list,
        alice=alice,
        bob=bob,
        linked=linked,
        orphans=orphans,
        meta={
            "list.json": list_file.read_bytes(),
            "config.json": config,
        },
    )


def converted_orphans(data_paths: DataPaths) -> list[Path]:
    """孤立 PDF の、変換後のパスでの期待 (名前順)."""
    fs_pdf = to_fs_path(data_paths.pdf)
    return [
        fs_pdf / ORPHAN_DIR / "00.pdf",
        fs_pdf / ORPHAN_DIR / "01.pdf",
        fs_pdf / ORPHAN_LEGACY,
    ]


def test_the_converted_space_differs_from_the_plain_one(
    converted_space: ConvertedSpace, data_paths: DataPaths
) -> None:
    """前提: 変換後の PDF ルートは、変換前のものと別のパスで、実体を指す.

    ``windows-like`` では同じ場所を指す別の形 (``//`` 付き) で、
    ``redirected`` では別のディレクトリ。どちらも、表示用の逆変換で
    変換前のパスに戻る。
    """
    fs_pdf = to_fs_path(data_paths.pdf)

    assert fs_pdf != data_paths.pdf
    assert to_display_path(fs_pdf) == str(data_paths.pdf)
    if converted_space.redirected:
        assert fs_pdf == converted_space.pdf
    else:
        assert str(fs_pdf).startswith("//")
        assert converted_space.pdf == data_paths.pdf


@_ignore_legacy_deprecation
def test_saved_pdfs_are_written_into_the_converted_space(
    converted_space: ConvertedSpace,
    saved_pdfs: SavedPdfs,
    data_paths: DataPaths,
) -> None:
    """保存は、変換後のパスの下に書く (``redirected`` では変換前に書かない)."""
    assert pdfs_under(converted_space.root) == {
        **saved_pdfs.linked,
        **saved_pdfs.orphans,
    }
    if converted_space.redirected:
        assert files_under(data_paths.root) == []


@_ignore_legacy_deprecation
def test_listing_returns_converted_paths_that_display_as_plain_ones(
    saved_pdfs: SavedPdfs, data_paths: DataPaths
) -> None:
    """一覧は変換後の PDF ルートの下のパスを返し、表示用には変換前に戻る.

    保存した件数と中身が、一覧のパスから読める。
    """
    fs_pdf = to_fs_path(data_paths.pdf)
    alice, bob = saved_pdfs.alice, saved_pdfs.bob

    alice_files = alice.get_pdf_files()
    bob_files = bob.get_pdf_files()

    assert alice_files == [
        fs_pdf / alice.pdf_dir_name / "00.pdf",
        fs_pdf / alice.pdf_dir_name / "01.pdf",
    ]
    assert bob_files == [fs_pdf / bob.pdf_dir_name / "00.pdf"]
    assert alice.get_pdf_count() + bob.get_pdf_count() == 3
    for path in alice_files + bob_files:
        member = path.relative_to(fs_pdf)  # 変換前のパスと混ざっていれば失敗
        assert (
            path.read_bytes() == saved_pdfs.linked[f"pdf/{member.as_posix()}"]
        )
        assert to_display_path(path) == str(data_paths.pdf / member)


@_ignore_legacy_deprecation
def test_orphans_are_found_in_the_converted_space(
    saved_pdfs: SavedPdfs, data_paths: DataPaths
) -> None:
    """孤立 PDF だけが、変換後のパスで検出される.

    保存した PDF は検出されない。検出したパスは、変換後の PDF ルートからの
    相対パスを求められ、表示用には変換前のパスに戻る。
    """
    orphans = find_orphaned_pdfs(saved_pdfs.paper_list)

    assert orphans == converted_orphans(data_paths)
    fs_pdf = to_fs_path(data_paths.pdf)
    assert [to_display_path(path) for path in orphans] == [
        str(data_paths.pdf / path.relative_to(fs_pdf)) for path in orphans
    ]


@_ignore_legacy_deprecation
def test_backup_has_every_file_and_no_prefix_in_member_names(
    saved_pdfs: SavedPdfs,
) -> None:
    """バックアップは、変換後の空間のすべてのファイルを入れ、prefix が入らない.

    メンバー名は、データディレクトリからの相対パス (``pdf/<フォルダ>/NN.pdf``)
    だけで、別名の ``//`` も、データディレクトリまでの絶対パスも含まない。
    """
    members = read_members(create_backup_zip())

    assert set(members) == {
        "version.json",
        *saved_pdfs.meta,
        *saved_pdfs.linked,
        *saved_pdfs.orphans,
    }
    for name in members:
        assert not name.startswith("/"), name
        assert "//" not in name, name
        assert ".." not in name.split("/"), name
    del members["version.json"]
    assert members == {
        **saved_pdfs.meta,
        **saved_pdfs.linked,
        **saved_pdfs.orphans,
    }


@_ignore_legacy_deprecation
def test_backup_delete_everything_and_restore_bring_every_pdf_back(
    converted_space: ConvertedSpace,
    saved_pdfs: SavedPdfs,
    data_paths: DataPaths,
    st_messages: StMessages,
) -> None:
    """バックアップして全削除し、復元すると、一覧と孤立 PDF の検出が元に戻る.

    削除は、論文の PDF (``delete_pdfs``) と孤立 PDF
    (``delete_orphaned_pdfs``) のそれぞれで行う。全削除の後も PDF ルートは
    残る。復元は、変換後のパスの下に書き、中身も配置も元と同じになる。
    アプリは通知を出さない。
    """
    alice, bob = saved_pdfs.alice, saved_pdfs.bob
    listed = [alice.get_pdf_files(), bob.get_pdf_files()]
    orphans = find_orphaned_pdfs(saved_pdfs.paper_list)
    assert len(orphans) == len(saved_pdfs.orphans)  # 前提
    backup = create_backup_zip().getvalue()

    failures: list[tuple[Path, str]] = []
    assert delete_pdfs(alice) == 2
    assert delete_pdfs(bob) == 1
    assert delete_orphaned_pdfs(orphans, failures) == len(saved_pdfs.orphans)

    assert failures == []
    assert pdfs_under(converted_space.root) == {}
    assert converted_space.pdf.is_dir()
    assert list(converted_space.pdf.iterdir()) == []
    assert alice.get_pdf_files() == []
    assert bob.get_pdf_files() == []
    assert find_orphaned_pdfs(saved_pdfs.paper_list) == []

    success, message = restore_from_zip(BytesIO(backup))

    assert success, message
    restored = len(saved_pdfs.linked) + len(saved_pdfs.orphans)
    assert f"復元したPDFファイル数: {restored}" in message
    assert pdfs_under(converted_space.root) == {
        **saved_pdfs.linked,
        **saved_pdfs.orphans,
    }
    for name, content in saved_pdfs.meta.items():
        assert (converted_space.root / name).read_bytes() == content
    assert [alice.get_pdf_files(), bob.get_pdf_files()] == listed
    assert find_orphaned_pdfs(saved_pdfs.paper_list) == orphans
    assert st_messages.error == []
    assert st_messages.warning == []
    if converted_space.redirected:
        assert files_under(data_paths.root) == []


@_ignore_legacy_deprecation
@pytest.mark.parametrize("requested", ["found", "plain-paths", "folders"])
def test_deleting_every_orphan_keeps_the_pdf_root(
    requested: str, converted_space: ConvertedSpace, data_paths: DataPaths
) -> None:
    """全 PDF が孤立していて、すべてを依頼しても、PDF ルートは削除されない.

    論文が 1 件もないので、すべての PDF が孤立 PDF になる。依頼するパスは、
    検出が返した変換後のパス (``found``)、表示用に戻した変換前のパス
    (``plain-paths``)、フォルダと単一の PDF (``folders``)。どれでも、PDF の
    削除で PDF ルートが空になったとき、PDF ルートかどうかは変換後の PDF
    ルートと比べて判定され、PDF ルートは残る。
    """
    for name in (
        f"pdf/{ORPHAN_DIR}/00.pdf",
        f"pdf/{ORPHAN_DIR}/01.pdf",
        f"pdf/{ORPHAN_LEGACY}",
    ):
        write_pdf(converted_space.root / name)
    orphans = find_orphaned_pdfs(PaperList({}))
    assert orphans == converted_orphans(data_paths)  # 前提: すべて孤立
    fs_pdf = to_fs_path(data_paths.pdf)
    targets = {
        "found": orphans,
        "plain-paths": [Path(to_display_path(path)) for path in orphans],
        "folders": [fs_pdf / ORPHAN_DIR, fs_pdf / ORPHAN_LEGACY],
    }[requested]
    failures: list[tuple[Path, str]] = []

    assert delete_orphaned_pdfs(targets, failures) == len(orphans)

    assert failures == []
    assert converted_space.pdf.is_dir()
    assert list(converted_space.pdf.iterdir()) == []
    assert find_orphaned_pdfs(PaperList({})) == []
