from pymongo import ReturnDocument
from core.database import db
from fastapi import HTTPException, status
from typing import List, Optional
from schemas.visit_session_schema import VisitSessionCreate, VisitSessionUpdate, VisitSessionOut


async def create_visit_session(session_data: VisitSessionCreate) -> VisitSessionOut:
    session_dict = session_data.model_dump()
    result = await db.visit_sessions.insert_one(session_dict)
    result = await db.visit_sessions.find_one({"_id": result.inserted_id})
    return VisitSessionOut(**result)


async def get_visit_session(filter_dict: dict) -> Optional[VisitSessionOut]:
    try:
        result = await db.visit_sessions.find_one(filter_dict)
        if result is None:
            return None
        return VisitSessionOut(**result)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching visit session: {str(e)}",
        )


async def get_visit_sessions(filter_dict: dict = {}, start=0, stop=100) -> List[VisitSessionOut]:
    try:
        if filter_dict is None:
            filter_dict = {}
        cursor = (
            db.visit_sessions.find(filter_dict)
            .sort("check_in_time", -1)
            .skip(start)
            .limit(stop - start)
        )
        session_list = []
        async for doc in cursor:
            session_list.append(VisitSessionOut(**doc))
        return session_list
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching visit sessions: {str(e)}",
        )


async def get_active_visitors(tenant_id: str, department_id: str = None) -> List[VisitSessionOut]:
    filter_dict = {"tenant_id": tenant_id, "status": "checked_in"}
    if department_id:
        filter_dict["department_id"] = department_id
    return await get_visit_sessions(filter_dict=filter_dict, start=0, stop=1000)


async def get_visit_session_by_badge_token(badge_qr_token: str) -> Optional[VisitSessionOut]:
    return await get_visit_session({"badge_qr_token": badge_qr_token})


async def update_visit_session(filter_dict: dict, session_data: VisitSessionUpdate) -> VisitSessionOut:
    update_dict = {k: v for k, v in session_data.model_dump().items() if v is not None}
    result = await db.visit_sessions.find_one_and_update(
        filter_dict,
        {"$set": update_dict},
        return_document=ReturnDocument.AFTER,
    )
    return VisitSessionOut(**result)


async def count_visit_sessions(filter_dict: dict) -> int:
    return await db.visit_sessions.count_documents(filter_dict)


async def get_visitor_session_stats(tenant_id: str, department_id: str = None) -> dict:
    match = {"tenant_id": tenant_id}
    if department_id:
        match["department_id"] = department_id

    pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": None,
                "total_visits": {"$sum": 1},
                "total_checked_in": {
                    "$sum": {"$cond": [{"$eq": ["$status", "checked_in"]}, 1, 0]}
                },
                "total_checked_out": {
                    "$sum": {"$cond": [{"$eq": ["$status", "checked_out"]}, 1, 0]}
                },
                "avg_duration": {
                    "$avg": {
                        "$cond": [
                            {"$and": [
                                {"$ne": ["$check_out_time", None]},
                                {"$ne": ["$check_in_time", None]},
                            ]},
                            {"$subtract": ["$check_out_time", "$check_in_time"]},
                            None,
                        ]
                    }
                },
            }
        },
    ]
    result = await db.visit_sessions.aggregate(pipeline).to_list(1)
    if result:
        return result[0]
    return {"total_visits": 0, "total_checked_in": 0, "total_checked_out": 0, "avg_duration": 0}
