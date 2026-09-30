from __future__ import annotations

from starlette.datastructures import FormData, Headers, UploadFile
from starlette.formparsers import MultiPartParser
from starlette.requests import Request


class TestcaseFileTooLarge(Exception):
    pass


class LimitedTestcaseMultiPartParser(MultiPartParser):
    def __init__(self, headers: Headers, stream, *, file_limit_bytes: int) -> None:
        super().__init__(
            headers,
            stream,
            max_files=2,
            max_fields=16,
            max_part_size=max(file_limit_bytes, 64 * 1024),
        )
        self.file_limit_bytes = file_limit_bytes
        self._current_file_bytes = 0

    def on_part_begin(self) -> None:
        super().on_part_begin()
        self._current_file_bytes = 0

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self._current_part.file is not None:
            self._current_file_bytes += end - start
            if self._current_file_bytes > self.file_limit_bytes:
                raise TestcaseFileTooLarge(
                    f"Each uploaded file must not exceed {self.file_limit_bytes} bytes."
                )
        super().on_part_data(data, start, end)


async def testcase_form(request: Request, file_limit_bytes: int) -> FormData:
    content_type = request.headers.get("content-type", "").lower()
    if content_type.startswith("multipart/form-data"):
        parser = LimitedTestcaseMultiPartParser(
            request.headers, request.stream(), file_limit_bytes=file_limit_bytes
        )
        return await parser.parse()
    return await request.form(
        max_files=2, max_fields=16, max_part_size=file_limit_bytes
    )


async def uploaded_bytes(value: object, file_limit_bytes: int) -> bytes | None:
    if not isinstance(value, UploadFile) or not value.filename:
        return None
    await value.seek(0)
    data = await value.read(file_limit_bytes + 1)
    if len(data) > file_limit_bytes:
        raise TestcaseFileTooLarge(
            f"Each uploaded file must not exceed {file_limit_bytes} bytes."
        )
    return data
