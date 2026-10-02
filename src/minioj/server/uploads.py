from __future__ import annotations

from starlette.datastructures import FormData, Headers, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import Request


class TestcaseFileTooLarge(MultiPartException):
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


async def testcase_form(
    request: Request, file_limit_bytes: int, *, body_limit_bytes: int | None = None
) -> FormData:
    content_type = request.headers.get("content-type", "").lower()
    if body_limit_bytes is not None:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError as exc:
                raise MultiPartException("Invalid Content-Length.") from exc
            if declared_length < 0:
                raise MultiPartException("Invalid Content-Length.")
            if declared_length > body_limit_bytes:
                raise TestcaseFileTooLarge("Upload request is too large.")
    if content_type.startswith("multipart/form-data"):

        async def bounded_stream():
            total = 0
            async for chunk in request.stream():
                total += len(chunk)
                if body_limit_bytes is not None and total > body_limit_bytes:
                    raise TestcaseFileTooLarge("Upload request is too large.")
                yield chunk

        parser = LimitedTestcaseMultiPartParser(
            request.headers, bounded_stream(), file_limit_bytes=file_limit_bytes
        )
        if body_limit_bytes is not None:
            parser.max_part_size = 64 * 1024
        return await parser.parse()
    if body_limit_bytes is not None:
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > body_limit_bytes:
                raise TestcaseFileTooLarge("Upload request is too large.")
            body.extend(chunk)
        request._body = bytes(body)
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
