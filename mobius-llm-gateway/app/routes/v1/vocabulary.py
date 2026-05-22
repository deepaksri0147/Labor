import logging

from fastapi import APIRouter, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.schemas.vocabulary import VocabularyCreate, VocabularyResponse
from app.services.vocabulary_service import VocabularyService
from app.utils.utils import decode_jwt, extract_identity

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vocabulary", tags=["Vocabulary"])
_service = VocabularyService()
auth_scheme = HTTPBearer()


def _get_identity(credentials: HTTPAuthorizationCredentials):
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Authorization token is required")
    decoded = decode_jwt(credentials.credentials)
    if not decoded:
        raise HTTPException(status_code=401, detail="Invalid or corrupted token")
    return extract_identity(decoded)


@router.post(
    "",
    response_model=VocabularyResponse,
    summary="Create a vocabulary",
    description=(
        "Define a persona or behavior instruction set. "
        "Pass the returned vocabulary_id in batch-submit to make all requests in that batch "
        "behave according to this vocabulary. "
        "injection_mode controls where instructions are placed: "
        "prepend (before system prompt), append (after), or replace (replaces entirely)."
    ),
)
def create_vocabulary(
    body: VocabularyCreate,
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme),
):
    identity = _get_identity(credentials)
    vocab = _service.create(body.model_dump())
    logger.info("Vocabulary created via API | id=%s tenant=%s", vocab["vocabulary_id"], identity.get("tenantId"))
    return vocab


@router.get(
    "",
    response_model=list[VocabularyResponse],
    summary="List all vocabularies",
    description="Returns all available vocabularies.",
)
def list_vocabularies(
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme),
):
    _get_identity(credentials)
    return _service.list_all()


@router.get(
    "/{vocabulary_id}",
    response_model=VocabularyResponse,
    summary="Get a vocabulary",
    description="Fetch a single vocabulary by its ID.",
)
def get_vocabulary(
    vocabulary_id: str,
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme),
):
    _get_identity(credentials)
    vocab = _service.get(vocabulary_id)
    if not vocab:
        raise HTTPException(status_code=404, detail=f"Vocabulary not found: {vocabulary_id}")
    return vocab


@router.delete(
    "/{vocabulary_id}",
    summary="Delete a vocabulary",
    description="Permanently delete a vocabulary.",
)
def delete_vocabulary(
    vocabulary_id: str,
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme),
):
    identity = _get_identity(credentials)
    if not _service.delete(vocabulary_id):
        raise HTTPException(status_code=404, detail=f"Vocabulary not found: {vocabulary_id}")
    logger.info("Vocabulary deleted via API | id=%s tenant=%s", vocabulary_id, identity.get("tenantId"))
    return {"deleted": True, "vocabulary_id": vocabulary_id}
