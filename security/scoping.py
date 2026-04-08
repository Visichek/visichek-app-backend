from __future__ import annotations

from security.principal import AuthPrincipal


def apply_department_scope(
    filter_dict: dict,
    principal: AuthPrincipal,
    department_id_field: str = "department_id",
) -> dict:
    """If the caller is a dept_admin, restrict the query to their department.

    Args:
        filter_dict: MongoDB filter dictionary to modify
        principal: AuthPrincipal containing user role and department info
        department_id_field: Field name in the document for department ID (default: "department_id")

    Returns:
        Modified filter_dict with department scope applied if applicable
    """
    if principal.role == "dept_admin" and principal.department_id:
        filter_dict[department_id_field] = principal.department_id
    return filter_dict
