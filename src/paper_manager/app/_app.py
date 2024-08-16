import json
import os
import sys
from logging import DEBUG
from pathlib import Path

if sys.version_info >= (3, 9):
    from collections.abc import Sequence
else:
    from typing import Sequence

import pandas as pd
import streamlit as st
from bibtexparser.bibdatabase import BibDatabase
from bibtexparser.bwriter import BibTexWriter
from crossref.restful import Works
from paper_manager.logging import get_child_logger
from streamlit_pdf_viewer import pdf_viewer

_logger = get_child_logger(__name__)

DIRPATH_ROOT = Path(__file__).parent
DIRPATH_DATA = DIRPATH_ROOT / "data"
DIRPATH_PDF = DIRPATH_DATA / "pdf"

FILEPATH_LIST = DIRPATH_DATA / "list.json"

ENTRY = dict[str, str]


def get_key(entry: ENTRY, keys: Sequence[str]) -> str:
    i = 0
    st_keys = set(keys)

    first_author = entry["author"].split(" and ")[0]
    if (
        key := "{0}{1}_{2}".format(
            first_author.replace(" ", ""), entry["year"], i
        )
    ) in st_keys:
        i += 1
    else:
        return key


def get_filename_pdf(entry: ENTRY) -> str:
    first_author = entry["author"].split(" and ")[0]
    year = entry["year"]
    title = entry["title"]
    return "{} - {} - {}.pdf".format(first_author, year, title)


def main():
    st.header("List")

    os.makedirs(DIRPATH_PDF, exist_ok=True)

    if FILEPATH_LIST.exists():
        try:
            with open(FILEPATH_LIST, mode="r") as f:
                dict_paper_list: dict[str, ENTRY] = json.load(f)
        except json.JSONDecodeError:
            dict_paper_list = dict()
    else:
        dict_paper_list = dict()

    if dict_paper_list:
        paper_selected = st.dataframe(
            pd.DataFrame.from_dict(
                dict_paper_list, orient="index", dtype=str
            ).loc[
                :,
                [
                    "author",
                    "year",
                    "title",
                    "journal",
                    "volume",
                    "number",
                    "pages",
                    "doi",
                ],
            ],
            hide_index=True,
            selection_mode="single-row",
            on_select="rerun",
        )
        # 行が選択されていたら
        if index_list_selected := paper_selected["selection"]["rows"]:
            key_selected = tuple(dict_paper_list.keys())[
                index_list_selected[0]
            ]

            if st.button("Delete"):
                del dict_paper_list[key_selected]
                with open(FILEPATH_LIST, mode="w") as f:
                    json.dump(dict_paper_list, f, indent=4)
                st.rerun()

            options_file_ext = ("bib", "xml")

            filepath_pdf_selected = DIRPATH_PDF / get_filename_pdf(
                dict_paper_list[key_selected]
            )
            if filepath_pdf_selected.is_file():
                options_file_ext = ("pdf",) + options_file_ext

            ext = st.radio(
                "ext",
                options=options_file_ext,
                horizontal=True,
                label_visibility="hidden",
            )

            if ext == "pdf":
                with open(filepath_pdf_selected, mode="rb") as f:
                    pdf_contents = f.read()

                st.download_button(
                    "Download",
                    data=pdf_contents,
                    file_name=filepath_pdf_selected.name,
                )
                pdf_viewer(pdf_contents, width=700, height=1000)
            elif ext == "bib":
                bib_database = BibDatabase()
                bib_database.entries = [dict_paper_list[key_selected]]

                bib_writer = BibTexWriter()

                st.download_button(
                    "Download",
                    bib_writer.write(bib_database),
                    file_name=filepath_pdf_selected.with_suffix(".bib").name,
                )
                st.text(bib_writer.write(bib_database))
            elif ext == "xml":
                # HACK
                st.warning("Now developping")
                pass
    # st.download_button("Download pdf", data=DIRPATH_PDF / )

    st.header("Upload")

    doi = st.text_input(
        "DOI",
    )
    uploaded_file = st.file_uploader(
        "paper",
        type="pdf",
        accept_multiple_files=False,
    )

    if st.button("Upload"):
        works = Works()
        metadata: dict = works.doi(doi)

        if doi and metadata:
            entry = {
                "ENTRYTYPE": "article",
                "title": metadata["title"][0],
                "author": " and ".join(
                    [
                        author["given"] + " " + author["family"]
                        for author in metadata["author"]
                    ]
                ),
                "journal": metadata["container-title"][0],
                "year": str(metadata["published"]["date-parts"][0][0]),
                "volume": metadata.get("volume", ""),
                "number": metadata.get("issue", ""),
                "pages": metadata.get("page", ""),
                "doi": metadata["DOI"],
            }
            entry["ID"] = get_key(entry, keys=dict_paper_list.keys())

            filename_pdf = get_filename_pdf(entry)
            # pdfのファイル名で重複を確認する (DOIだと、今後DOIがないものが難しくなるため)
            st_doi: set[str] = {
                get_filename_pdf(_entry) for _entry in dict_paper_list.values()
            }
            if filename_pdf in st_doi:
                st.error("FAIL: Duplicated")
            else:
                # ラインナップとして追加して
                dict_paper_list[get_key(entry, dict_paper_list.keys())] = entry
                with open(FILEPATH_LIST, mode="w") as f:
                    json.dump(dict_paper_list, f, indent=4)

                # pdfをdataディレクトリ内に保存する
                if uploaded_file:
                    with open(DIRPATH_PDF / filename_pdf, mode="wb") as f:
                        f.write(uploaded_file.getvalue())

                # reload
                st.rerun()
        else:
            st.error("FAIL: Invalid DOI")


if __name__ == "__main__":
    _logger.setLevel(DEBUG)

    main()
