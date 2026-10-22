"""/v1 routes for uploading a CSV file and asking about it (see app/datasets)."""

import asyncio
from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Request, Response, UploadFile

from app.datasets.csv_upload import Limits, UploadRejected, read_csv
from app.datasets.registry import DatasetAnalysts
from app.datasets.store import GONE, Datasets, DatasetV1
from app.problems import Problem
from app.usage import Visitor


def datasets_router(*, store: Callable[[], Datasets], analysts: Callable[[], DatasetAnalysts],
                    identify: Callable[[Request], Visitor], spend: Callable[[Visitor], None],
                    limits: Limits) -> APIRouter:
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

    @router.get("/datasets")
    def list_datasets(request: Request) -> list[DatasetV1]:
        """Your uploaded files that haven't expired, newest first."""
        return store().list(owner(request))

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
