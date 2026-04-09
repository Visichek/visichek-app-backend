from pymongo import ReturnDocument
from core.database import db
from fastapi import HTTPException, status
from typing import List, Optional
from schemas.department_schema import DepartmentCreate, DepartmentUpdate, DepartmentOut


async def create_department(department_data: DepartmentCreate) -> DepartmentOut:
    dept_dict = department_data.model_dump()
    result = await db.departments.insert_one(dept_dict)
    result = await db.departments.find_one({"_id": result.inserted_id})
    return DepartmentOut(**result)


async def get_department(filter_dict: dict) -> Optional[DepartmentOut]:
    try:
        result = await db.departments.find_one(filter_dict)
        if result is None:
            return None
        return DepartmentOut(**result)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching department: {str(e)}",
        )


async def get_departments(filter_dict: dict = {}, start=0, stop=100) -> List[DepartmentOut]:
    try:
        if filter_dict is None:
            filter_dict = {}
        cursor = db.departments.find(filter_dict).skip(start).limit(stop - start)
        dept_list = []
        async for doc in cursor:
            dept_list.append(DepartmentOut(**doc))
        return dept_list
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching departments: {str(e)}",
        )


async def update_department(filter_dict: dict, dept_data: DepartmentUpdate) -> DepartmentOut:
    update_dict = {k: v for k, v in dept_data.model_dump().items() if v is not None}
    result = await db.departments.find_one_and_update(
        filter_dict,
        {"$set": update_dict},
        return_document=ReturnDocument.AFTER,
    )
    return DepartmentOut(**result)


async def delete_department(filter_dict: dict):
    return await db.departments.delete_one(filter_dict)


async def count_departments(filter_dict: dict | None = None) -> int:
    if filter_dict is None:
        filter_dict = {}
    return await db.departments.count_documents(filter_dict)
