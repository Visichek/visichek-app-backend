from pymongo import ReturnDocument
from core.database import db
from fastapi import HTTPException, status
from typing import List, Optional
from schemas.user_session_schema import (
    UserSessionCreate,
    UserSessionUpdate,
    UserSessionOut,
)


async def create_user_session(session_data: UserSessionCreate) -> UserSessionOut:
    session_dict = session_data.model_dump()
    result = await db.user_sessions.insert_one(session_dict)
    result = await db.user_sessions.find_one({"_id": result.inserted_id})
    return UserSessionOut(**result)


async def get_user_session(filter_dict: dict) -> Optional[UserSessionOut]:
    try:
        result = await db.user_sessions.find_one(filter_dict)
        if result is None:
            return None
        return UserSessionOut(**result)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching user session: {str(e)}",
        )


async def get_user_sessions(
    filter_dict: dict = {}, start=0, stop=100
) -> List[UserSessionOut]:
    try:
        if filter_dict is None:
            filter_dict = {}
        cursor = (
            db.user_sessions.find(filter_dict)
            .sort("started_at", -1)
            .skip(start)
            .limit(stop - start)
        )
        session_list = []
        async for doc in cursor:
            session_list.append(UserSessionOut(**doc))
        return session_list
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching user sessions: {str(e)}",
        )


async def update_user_session(
    filter_dict: dict, session_data: UserSessionUpdate
) -> UserSessionOut:
    update_dict = {k: v for k, v in session_data.model_dump().items() if v is not None}
    result = await db.user_sessions.find_one_and_update(
        filter_dict,
        {"$set": update_dict},
        return_document=ReturnDocument.AFTER,
    )
    return UserSessionOut(**result)
