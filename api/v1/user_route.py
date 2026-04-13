from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, RedirectResponse

from core.response_envelope import document_response, success_payload
from core.settings import get_settings
from schemas.user_schema import (
    LoginType,
    UserBase,
    UserLogin,
    UserOut,
    UserRefresh,
    UserSignupRequest,
)
from services.user_service import (
    add_user_from_signup,
    authenticate_user,
    authenticate_user_google,
    oauth,
    refresh_user_tokens_reduce_number_of_logins,
    remove_user,
    retrieve_users,
)
from security.account_status_check import check_user_account_status_and_permissions
from security.auth import verify_user_refresh_token
from security.cookie_utils import (
    set_auth_cookies,
    clear_auth_cookies,
    REFRESH_TOKEN_COOKIE,
)
from security.principal import AuthPrincipal
import os
from dotenv import load_dotenv

load_dotenv()

router = APIRouter(prefix="/users", tags=["Users"])

SUCCESS_PAGE_URL = os.getenv("SUCCESS_PAGE_URL", "http://localhost:8080/success")
ERROR_PAGE_URL = os.getenv("ERROR_PAGE_URL", "http://localhost:8080/error")


@router.get("/google/auth")
async def login_with_google_account(request: Request):
    redirect_uri = request.url_for("auth_callback_user")
    print("REDIRECT URI:", redirect_uri)
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/auth/callback")
async def auth_callback_user(request: Request):
    token = await oauth.google.authorize_access_token(request)
    user_info = token.get("userinfo")

    if user_info:
        print("✅ Google user info:", user_info)
        rider = UserBase(
            firstName=user_info["name"],
            password="",
            lastName=user_info["given_name"],
            email=user_info["email"],
            loginType=LoginType.google,
        )
        data = await authenticate_user_google(user_data=rider)
        access_token = data.access_token
        refresh_token = data.refresh_token

        success_url = f"{SUCCESS_PAGE_URL}?access_token={access_token}&refresh_token={refresh_token}"

        return RedirectResponse(
            url=success_url,
            status_code=status.HTTP_302_FOUND,
        )

    raise HTTPException(status_code=400, detail={"message": "No user info found"})


@router.get(
    "/",
    dependencies=[Depends(check_user_account_status_and_permissions)],
)
@document_response(
    message="Users fetched successfully",
    success_example=[
        {
            "id": "64f1a2b3c4d5e6f7a8b9c0d3",
            "firstName": "Alice",
            "lastName": "Smith",
            "loginType": "EMAIL",
            "email": "alice.smith@example.com",
            "accountStatus": "ACTIVE",
            "permissionList": None,
            "date_created": 1712510000,
            "last_updated": 1712510800,
        }
    ],
    description="Retrieve a paginated list of all users in the system.",
    summary="List all users",
    include_meta=True,
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Invalid or expired token",
            "code": "AUTH_INVALID_TOKEN",
        },
        403: {
            "success": False,
            "message": "You do not have permission to perform this action",
            "code": "AUTH_PERMISSION_DENIED",
        },
    },
)
async def list_users(start: int = 0, stop: int = 100):
    items = await retrieve_users(start=start, stop=stop)
    return items


@router.get(
    "/me",
)
@document_response(
    message="User profile fetched successfully",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d3",
        "firstName": "Alice",
        "lastName": "Smith",
        "loginType": "EMAIL",
        "email": "alice.smith@example.com",
        "accountStatus": "ACTIVE",
        "permissionList": None,
        "date_created": 1712510000,
        "last_updated": 1712510800,
    },
    description="Retrieve the authenticated user's profile information.",
    summary="Get user profile",
    response_codes={
        401: "Unauthorized - invalid or missing token",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Invalid or expired token",
            "code": "AUTH_INVALID_TOKEN",
        },
    },
)
async def get_my_users(
    user: UserOut = Depends(check_user_account_status_and_permissions),
):
    return user


@router.post("/signup")
@document_response(
    message="User created successfully",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d4",
        "firstName": "Bob",
        "lastName": "Johnson",
        "loginType": "EMAIL",
        "email": "bob.johnson@example.com",
        "accountStatus": "ACTIVE",
        "permissionList": None,
        "date_created": 1712511000,
        "last_updated": 1712511000,
    },
    status_code=status.HTTP_201_CREATED,
    description="Create a new user account with email and password. Account status and permissions are system-assigned.",
    summary="Sign up new user",
    response_codes={
        409: "Conflict - email already exists",
        422: "Validation error - invalid input data or weak password",
    },
    error_examples={
        409: {
            "success": False,
            "message": "A user with this email already exists",
            "code": "VALIDATION_FAILED",
        },
        422: {
            "success": False,
            "message": "Password does not meet strength requirements",
            "code": "VALIDATION_FAILED",
        },
    },
)
async def signup_new_user(signup_data: UserSignupRequest):
    return await add_user_from_signup(signup_data=signup_data)


@router.post("/login")
@document_response(
    message="Login successful",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d3",
        "firstName": "Alice",
        "lastName": "Smith",
        "loginType": "EMAIL",
        "email": "alice.smith@example.com",
        "accountStatus": "ACTIVE",
        "permissionList": None,
        "date_created": 1712510000,
        "last_updated": 1712510800,
        "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI2NGYxYTJiM2M0ZDVlNmY3YThiOWMwZDMiLCJyb2xlIjoidXNlciJ9.def456",
        "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI2NGYxYTJiM2M0ZDVlNmY3YThiOWMwZDMiLCJ0eXBlIjoicmVmcmVzaCJ9.uvw012",
    },
    description="Authenticate a user with email and password. Returns access and refresh tokens.",
    summary="User login",
    response_codes={
        401: "Unauthorized - invalid credentials",
        422: "Validation error - missing or invalid email/password",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Invalid email or password",
            "code": "AUTH_INVALID_TOKEN",
        },
        422: {
            "success": False,
            "message": "Email and password are required",
            "code": "VALIDATION_FAILED",
        },
    },
)
async def login_user(request: Request, login_data: UserLogin):
    user = await authenticate_user(login_data=login_data)

    is_prod = get_settings().env == "production"
    request_id = getattr(request.state, "request_id", None)
    response = JSONResponse(
        content=jsonable_encoder(
            success_payload(user, message="Login successful", request_id=request_id)
        ),
    )
    set_auth_cookies(
        response,
        user.access_token or "",
        user.refresh_token or "",
        is_production=is_prod,
    )
    return response


@router.post("/refresh")
@document_response(
    message="Tokens refreshed successfully",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d3",
        "firstName": "Alice",
        "lastName": "Smith",
        "loginType": "EMAIL",
        "email": "alice.smith@example.com",
        "accountStatus": "ACTIVE",
        "permissionList": None,
        "date_created": 1712510000,
        "last_updated": 1712510800,
        "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI2NGYxYTJiM2M0ZDVlNmY3YThiOWMwZDMiLCJyb2xlIjoidXNlciJ9.def456",
        "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI2NGYxYTJiM2M0ZDVlNmY3YThiOWMwZDMiLCJ0eXBlIjoicmVmcmVzaCJ9.uvw012",
    },
    description="Refresh expired access tokens using a valid refresh token. Expired access token must be provided in Authorization header.",
    summary="Refresh user tokens",
    response_codes={
        401: "Unauthorized - invalid or mismatched tokens",
        422: "Validation error - missing refresh token",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Invalid or expired refresh token",
            "code": "AUTH_INVALID_TOKEN",
        },
        422: {
            "success": False,
            "message": "Refresh token is required",
            "code": "VALIDATION_FAILED",
        },
    },
)
async def refresh_user_tokens(
    request: Request,
    user_data: UserRefresh,
    principal: AuthPrincipal = Depends(verify_user_refresh_token),
):
    if not user_data.refresh_token:
        user_data.refresh_token = request.cookies.get(REFRESH_TOKEN_COOKIE, "")

    user = await refresh_user_tokens_reduce_number_of_logins(
        user_refresh_data=user_data,
        expired_access_token=principal.access_token_id,
    )

    is_prod = get_settings().env == "production"
    request_id = getattr(request.state, "request_id", None)
    response = JSONResponse(
        content=jsonable_encoder(
            success_payload(
                user, message="Tokens refreshed successfully", request_id=request_id
            )
        ),
    )
    set_auth_cookies(
        response,
        user.access_token or "",
        user.refresh_token or "",
        is_production=is_prod,
    )
    return response


@router.post("/logout")
@document_response(
    message="Logged out successfully",
    description="Clear auth cookies and invalidate the current session.",
    summary="User logout",
)
async def logout_user(request: Request):
    is_prod = get_settings().env == "production"
    request_id = getattr(request.state, "request_id", None)
    response = JSONResponse(
        content=jsonable_encoder(
            success_payload(
                None, message="Logged out successfully", request_id=request_id
            )
        ),
    )
    clear_auth_cookies(response, is_production=is_prod)
    return response


@router.delete("/account")
@document_response(
    message="User account deleted successfully",
    success_example={"deleted": True},
    description="Delete the authenticated user's account. This action is irreversible.",
    summary="Delete user account",
    response_codes={
        401: "Unauthorized - invalid or missing token",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Invalid or expired token",
            "code": "AUTH_INVALID_TOKEN",
        },
    },
)
async def delete_user_account(
    user: UserOut = Depends(check_user_account_status_and_permissions),
):
    result = await remove_user(user_id=user.id)  # type: ignore
    return result
