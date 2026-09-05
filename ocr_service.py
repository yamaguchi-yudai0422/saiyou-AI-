from __future__ import annotations

import asyncio
import re
from statistics import median

import pymupdf
from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapDecoder
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream


class ResumeOcrError(RuntimeError):
    """画像履歴書をローカルOCRできない場合のユーザー向け例外。"""


MAX_PDF_PAGES = 20
MAX_EXTRACTED_CHARS = 200_000


def _normalize_ocr_text(result) -> str:
    lines = []
    for line in result.lines:
        words = list(line.words)
        if not words:
            continue
        typical_width = median(word.bounding_rect.width for word in words)
        gap_threshold = max(6.0, typical_width * 0.35)
        parts = [words[0].text]
        for previous, current in zip(words, words[1:]):
            previous_right = previous.bounding_rect.x + previous.bounding_rect.width
            gap = current.bounding_rect.x - previous_right
            # 日本語OCRは各文字を単語として空白区切りにするため、座標上で
            # 実際に文字間隔が広い場合だけ空白を残す。
            if gap > gap_threshold:
                parts.append(" ")
            parts.append(current.text)
        lines.append("".join(parts).strip())
    return "\n".join(lines).strip() if lines else result.text.strip()


async def recognize_resume_image(image_bytes: bytes) -> dict:
    if not image_bytes:
        raise ResumeOcrError("画像データが空です。")

    language = Language("ja")
    if not OcrEngine.is_language_supported(language):
        raise ResumeOcrError(
            "Windowsの日本語OCRが利用できません。Windowsの言語設定で日本語を追加してください。"
        )

    stream = InMemoryRandomAccessStream()
    writer = None
    bitmap = None
    try:
        writer = DataWriter(stream)
        writer.write_bytes(image_bytes)
        await writer.store_async()
        writer.detach_stream()
        writer.close()
        writer = None
        stream.seek(0)

        try:
            decoder = await BitmapDecoder.create_async(stream)
        except Exception as exc:
            raise ResumeOcrError(
                "画像を開けませんでした。PNG、JPEG、BMP、TIFF形式の画像を選んでください。"
            ) from exc

        max_dimension = OcrEngine.max_image_dimension
        if decoder.pixel_width > max_dimension or decoder.pixel_height > max_dimension:
            raise ResumeOcrError(
                f"画像が大きすぎます。縦横とも{max_dimension}px以下の画像を使用してください。"
            )

        bitmap = await decoder.get_software_bitmap_async()
        engine = OcrEngine.try_create_from_language(language)
        if engine is None:
            raise ResumeOcrError("Windowsの日本語OCRを起動できませんでした。")

        result = await engine.recognize_async(bitmap)
        text = _normalize_ocr_text(result)
        if not text:
            raise ResumeOcrError(
                "文字を認識できませんでした。画像の向き、明るさ、解像度を確認してください。"
            )
        return {"text": text, "language": engine.recognizer_language.language_tag}
    except ResumeOcrError:
        raise
    except Exception as exc:
        raise ResumeOcrError(f"画像の読み取り中にエラーが発生しました：{exc}") from exc
    finally:
        if bitmap is not None:
            bitmap.close()
        if writer is not None:
            writer.close()
        stream.close()


def _prepare_pdf_pages(pdf_bytes: bytes) -> list[tuple[str, str | bytes]]:
    try:
        document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ResumeOcrError("PDFを開けませんでした。ファイルが壊れていないか確認してください。") from exc

    try:
        if document.needs_pass:
            raise ResumeOcrError("パスワード付きPDFは読み込めません。保護を解除したPDFを使用してください。")
        if document.page_count == 0:
            raise ResumeOcrError("PDFにページがありません。")
        if document.page_count > MAX_PDF_PAGES:
            raise ResumeOcrError(f"PDFは{MAX_PDF_PAGES}ページ以下にしてください。")

        prepared = []
        extracted_chars = 0
        for page in document:
            text = page.get_text("text", sort=True).strip()
            meaningful_chars = len(re.findall(r"[A-Za-z0-9\u3040-\u30ff\u3400-\u9fff]", text))
            replacement_ratio = text.count("\ufffd") / max(1, len(text))
            if meaningful_chars >= 10 and replacement_ratio < 0.05:
                extracted_chars += len(text)
                if extracted_chars > MAX_EXTRACTED_CHARS:
                    raise ResumeOcrError("PDFから抽出される文字数が多すぎます。履歴書PDFを確認してください。")
                prepared.append(("text", text))
                continue

            # 文字層がないスキャンページだけ画像化する。PNGはメモリ上でOCR後に破棄される。
            pixmap = page.get_pixmap(dpi=220, colorspace=pymupdf.csRGB, alpha=False)
            prepared.append(("image", pixmap.tobytes("png")))
        return prepared
    except ResumeOcrError:
        raise
    except Exception as exc:
        raise ResumeOcrError(f"PDFの解析中にエラーが発生しました：{exc}") from exc
    finally:
        document.close()


async def extract_resume_pdf(pdf_bytes: bytes) -> dict:
    if not pdf_bytes:
        raise ResumeOcrError("PDFデータが空です。")

    pages = await asyncio.to_thread(_prepare_pdf_pages, pdf_bytes)
    results = []
    warnings = []
    ocr_pages = 0

    for page_number, (kind, content) in enumerate(pages, start=1):
        if kind == "text":
            text = str(content).strip()
        else:
            try:
                ocr_result = await recognize_resume_image(bytes(content))
                text = ocr_result["text"]
                ocr_pages += 1
            except ResumeOcrError as exc:
                warnings.append(f"{page_number}ページ：{exc}")
                continue
        if text:
            results.append(f"【PDF {page_number}ページ】\n{text}")

    if not results:
        detail = " / ".join(warnings) if warnings else "文字を抽出できませんでした。"
        raise ResumeOcrError(f"PDFから履歴書の文字を読み取れませんでした。{detail}")

    return {
        "text": "\n\n".join(results),
        "pages": len(pages),
        "ocr_pages": ocr_pages,
        "warnings": warnings,
    }
