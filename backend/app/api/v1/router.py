"""Aggregates every v1 router; mounted at /api/v1 by the app factory."""

from fastapi import APIRouter

from app.api.v1 import auth, documents, health, knowledge_bases, organizations

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(organizations.router)
api_router.include_router(knowledge_bases.router)
api_router.include_router(documents.router)
