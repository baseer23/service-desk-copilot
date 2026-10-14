"""Bounded segmentation preserves complete extraction within F0 page limits."""

import copy
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from defusedxml.ElementTree import fromstring


def word_segments(data: bytes) -> list[tuple[bytes, int]]:
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    with ZipFile(BytesIO(data)) as archive:
        if sum(item.file_size for item in archive.infolist()) > 20_000_000:
            raise ValueError("The Word document expands beyond the safe processing limit")
        xml = archive.read("word/document.xml")
        if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
            raise ValueError("Unsupported Word document structure")
        root = fromstring(xml)
        body = root.find("{" + namespace + "}body")
        if body is None:
            raise ValueError("The Word document has no readable body")
        section = body.find("{" + namespace + "}sectPr")
        groups, group, length = [], [], 0
        for block in body:
            if block is section:
                continue
            size = sum(len(node.text or "") for node in block.iter("{" + namespace + "}t"))
            if size > 5000:
                raise ValueError("A Word paragraph or table is too large; upload its PDF version")
            if group and length + size > 5000:
                groups.append(group)
                group, length = [], 0
            group.append(block)
            length += size
        if group:
            groups.append(group)
        if not groups or len(groups) > 50:
            raise ValueError("Choose a Word document with at most 100 processing pages")
        segments = []
        for index, blocks in enumerate(groups):
            document = copy.deepcopy(root)
            target = document.find("{" + namespace + "}body")
            target.clear()
            target.extend(copy.deepcopy(blocks))
            if section is not None:
                target.append(copy.deepcopy(section))
            output = BytesIO()
            with ZipFile(output, "w") as part:
                for item in archive.infolist():
                    part.writestr(
                        item,
                        ET.tostring(document, encoding="utf-8", xml_declaration=True)
                        if item.filename == "word/document.xml"
                        else archive.read(item.filename),
                    )
            segments.append((output.getvalue(), index * 2))
        return segments
