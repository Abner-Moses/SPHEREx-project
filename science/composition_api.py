"""Mount ``router`` in the parent app to expose stateless sequence composition."""
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from archive_client import DataError
import sequence_composition as composition

router = APIRouter(prefix="/api/sequences", tags=["composition"])
Identifier = Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{32}$")]


class CompositionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frame_ids: list[Identifier] = Field(min_length=2, max_length=composition.MAX_FRAMES)
    op: composition.Operation


@router.get("/composition/operations")
def operations():
    return composition.capabilities()


@router.post("/{identifier}/combine")
def combine(identifier: Identifier, req: CompositionRequest,
            output: Annotated[composition.Output, Query(description="Explicit science FITS, metadata, or display-only PNG")]):
    try:
        product = composition.combine(identifier, req.frame_ids, req.op, output)
    except DataError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    headers = {"X-Composition-ID": product.id, "Cache-Control": "no-store",
               "X-Data-Product": "derived-display-only-preview" if output == "preview" else "derived-exploratory-composition",
               "X-Scientific-Interpretation": "exploratory; no change claim; variance unavailable"}
    if output == "metadata":
        return JSONResponse(product.metadata, headers=headers)
    extension = "png" if output == "preview" else "fits"
    headers["Content-Disposition"] = f'attachment; filename="composition-{product.id}.{extension}"'
    return Response(product.content, media_type=product.media_type, headers=headers)
