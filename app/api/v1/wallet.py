"""
GhostVault Intelligence System
API Router — /v1/wallet endpoints
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_db
from app.schemas.wallet import ErrorResponse, WalletAnalysisRequest, WalletAnalysisResponse
from app.services.blockchain.base_rpc import RPCConnectionError, RPCError
from app.services.wallet_intelligence import analyze_wallet

logger = get_logger(__name__)

router = APIRouter(prefix="/v1", tags=["Wallet Intelligence"])


@router.post(
    "/analyze-wallet",
    response_model=WalletAnalysisResponse,
    status_code=status.HTTP_200_OK,
    summary="Analyse a blockchain wallet address",
    description=(
        "Fetches on-chain data for the specified wallet address, "
        "runs the dormancy scoring engine, and returns comprehensive "
        "analytics including dormancy score, cold storage probability, "
        "and wallet type classification."
    ),
    responses={
        200: {"model": WalletAnalysisResponse},
        400: {"model": ErrorResponse, "description": "Invalid chain or address format"},
        422: {"description": "Request validation failed"},
        502: {"model": ErrorResponse, "description": "Blockchain RPC unreachable"},
        504: {"model": ErrorResponse, "description": "RPC timeout"},
    },
)
async def analyze_wallet_endpoint(
    request_body: WalletAnalysisRequest,
    http_request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WalletAnalysisResponse:
    request_id = str(uuid.uuid4())

    try:
        result = await analyze_wallet(request_body, db)
        return result

    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "INVALID_INPUT", "detail": str(exc), "request_id": request_id},
        ) from exc

    except RPCError as exc:
        logger.error(
            "rpc_method_error",
            request_id=request_id,
            code=exc.code,
            message=exc.message,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "RPC_ERROR",
                "detail": f"Blockchain node returned error {exc.code}: {exc.message}",
                "request_id": request_id,
            },
        ) from exc

    except RPCConnectionError as exc:
        logger.error(
            "rpc_connection_error",
            request_id=request_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={
                "error": "RPC_UNREACHABLE",
                "detail": "Could not connect to blockchain node. Check RPC configuration.",
                "request_id": request_id,
            },
        ) from exc

    except Exception as exc:
        logger.error(
            "unexpected_error",
            request_id=request_id,
            error=str(exc),
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "INTERNAL_ERROR",
                "detail": "An unexpected error occurred. Check server logs.",
                "request_id": request_id,
            },
        ) from exc
