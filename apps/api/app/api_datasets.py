"""/v1 routes for uploading a CSV file or importing a Google Sheet, and asking about it (see app/datasets)."""

import asyncio
from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Request, Response, UploadFile
from pydantic import BaseModel, Field

from app.datasets.csv_upload import Limits, Upload, UploadRejected, read_csv
from app.datasets.registry import DatasetAnalysts
from app.datasets.sheets import Sheet, SheetsClient, parse
from app.datasets.store import GONE, Datasets, DatasetV1
from app.problems import Problem
from app.usage import Visitor


class SheetV1(BaseModel):
    url: str = Field(max_length=500, description="A Google Sheets link, shared with \"Anyone with the link\". "
                                                 "A #gid=... part picks the tab; otherwise the first tab.")


def datasets_router(*, store: Callable[[], Datasets], analysts: Callable[[], DatasetAnalysts],
                    identify: Callable[[Request], Visitor], spend: Callable[[Visitor], None],
                    limits: Limits, sheets: Callable[[], SheetsClient]) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["v1"])

    def visitor(request: Request) -> Visitor:
        """Who is asking, who must have a session or be signed in: uploads belong to one or the other."""
        who = identify(request)
        if not who.owner:
            raise Problem(403, "session_required", "Start a session first",
                          "Uploads belong to a session: call POST /v1/session, then upload.")
        return who

    def owner(request: Request) -> str:
        return visitor(request).owner

    def sheet_of(url: str) -> Sheet:
        try:
            return parse(url)
        except UploadRejected as e:
            raise Problem(e.status, e.code, e.title, e.detail) from e

    def read_sheet(sheet: Sheet, name: str | None) -> Upload:
        """A sheet's tab, read like an uploaded file (named `name`, or after the sheet)."""
        data, file_name = sheets().fetch(sheet, limits.max_bytes)
        return read_csv(data, name or file_name, limits)

    @router.post("/datasets", status_code=201)
    async def upload(request: Request, file: Annotated[UploadFile, File()]) -> DatasetV1:
        """Upload a CSV file (by default up to 5 MB, 200,000 rows and 60 columns) to ask questions about it, by
        sending its id as `dataset_id` with each question. It is kept for a day. Personal data such as email
        addresses and phone numbers is found and never shown in answers."""
        who = visitor(request)
        spend(who)  # reading a file is real work, so it counts like a question
        data = await file.read(limits.max_bytes + 1)
        try:
            upload = await asyncio.to_thread(read_csv, data, file.filename or "data.csv", limits)
            return await asyncio.to_thread(store().add, who.owner, upload)
        except UploadRejected as e:
            raise Problem(e.status, e.code, e.title, e.detail) from e

    @router.post("/datasets/sheets", status_code=201)
    async def import_sheet(body: SheetV1, request: Request) -> DatasetV1:
        """Import a Google Sheet shared by link, as if its tab were an uploaded CSV file (same limits, kept for a
        day). Only Google's own hosts are contacted."""
        sheet = sheet_of(body.url)
        who = visitor(request)
        spend(who)
        try:
            upload = await asyncio.to_thread(read_sheet, sheet, None)
            return await asyncio.to_thread(store().add, who.owner, upload, sheet.url)
        except UploadRejected as e:
            raise Problem(e.status, e.code, e.title, e.detail) from e

    @router.post("/datasets/{dataset_id}/refresh")
    async def refresh(dataset_id: UUID, request: Request) -> DatasetV1:
        """Import an imported sheet again, keeping the dataset's id (and its conversations) and another day."""
        who = visitor(request)
        dataset = await asyncio.to_thread(store().get, dataset_id, who.owner)
        if dataset is None:
            raise Problem(404, *GONE)
        if not dataset.source:
            raise Problem(422, "not_refreshable", "Only an imported Google Sheet can be refreshed",
                          "Upload the file again instead.")
        spend(who)
        try:
            upload = await asyncio.to_thread(read_sheet, parse(dataset.source), dataset.name)
            fresh = await asyncio.to_thread(store().replace, dataset_id, who.owner, upload)
        except UploadRejected as e:
            raise Problem(e.status, e.code, e.title, e.detail) from e
        if fresh is None:
            raise Problem(404, *GONE)
        analysts().forget(dataset_id)  # the next question opens the new copy
        return fresh

    @router.get("/datasets")
    def list_datasets(request: Request) -> list[DatasetV1]:
        """Your uploaded files that haven't expired, newest first (none before you have a session)."""
        return store().list(identify(request).owner)

    @router.get("/datasets/{dataset_id}")
    def get_dataset(dataset_id: UUID, request: Request) -> DatasetV1:
        dataset = store().get(dataset_id, owner(request))
        if dataset is None:
            raise Problem(404, *GONE)
        return dataset

    @router.delete("/datasets/{dataset_id}", status_code=204)
    def delete_dataset(dataset_id: UUID, request: Request) -> Response:
        if not store().delete(dataset_id, owner(request)):
            raise Problem(404, *GONE)
        analysts().forget(dataset_id)
        return Response(status_code=204)

    return router
