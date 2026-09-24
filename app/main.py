import uuid
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool
from app.modules import backend_modules
from app.modules.openapi import decorate_router
from app.config import settings
from app.observability import configure_telemetry
from app.security.core import throttle
from app.version import build_version
from app.bootstrap import sync_existing_rbac
from app.instance_operation import InstanceOperationBusy, normal_instance_operation

app = FastAPI(title='Cloudportal-backed', version=build_version(), docs_url='/docs', redoc_url=None)


@app.on_event('startup')
def synchronize_builtin_rbac_on_startup():
    # Keep the built-in Administrator authoritative even when a previous update
    # stopped before the final bootstrap step. This repairs settings/LDAP access
    # without granting permissions based on role names inside authorization.
    sync_existing_rbac()


async def _call_with_instance_fence(request: Request, call_next):
    if request.method not in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        return await call_next(request)
    try:
        with normal_instance_operation(blocking=False):
            return await call_next(request)
    except InstanceOperationBusy:
        return JSONResponse(
            {'detail': 'Instance backup or restore is quiescing mutating operations'},
            status_code=503,
            headers={'Retry-After': '5'},
        )


@app.middleware('http')
async def boundary(request: Request, call_next):
    try:
        request_id = str(uuid.UUID(request.headers.get('X-Request-ID', '')))
    except ValueError:
        request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    try:
        if request.url.scheme != 'https' and not settings().allow_http and request.url.path != '/api/v1/health':
            response = JSONResponse({'detail': 'HTTPS required'}, status_code=400)
        else:
            # The instance-backup upload endpoint owns a much larger, streaming
            # multipart limit. Do not buffer it in the generic 1 MiB JSON guard.
            streaming_backup_upload = (
                request.method == 'POST'
                and request.url.path == '/api/v1/instance-backups/upload'
                and request.headers.get('content-type', '').lower().startswith('multipart/form-data')
            )
            if streaming_backup_upload:
                from fastapi import HTTPException
                try:
                    await run_in_threadpool(
                        throttle,
                        'api:' + (request.client.host if request.client else ''),
                        settings().request_limit,
                        60,
                    )
                except HTTPException as error:
                    response = JSONResponse({'detail': error.detail}, status_code=error.status_code, headers=error.headers)
                else:
                    response = await _call_with_instance_fence(request, call_next)
            else:
                size = 0
                body = []
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > 1024 * 1024:
                        response = JSONResponse({'detail': 'Request exceeds 1 MiB'}, status_code=413)
                        break
                    body.append(chunk)
                else:
                    request._body = b''.join(body)
                    if request.url.path not in {'/api/v1/health', '/docs', '/openapi.json'}:
                        from fastapi import HTTPException
                        try:
                            await run_in_threadpool(throttle, 'api:' + (request.client.host if request.client else ''), settings().request_limit, 60)
                        except HTTPException as error:
                            response = JSONResponse({'detail': error.detail}, status_code=error.status_code, headers=error.headers)
                        else:
                            response = await _call_with_instance_fence(request, call_next)
                    else:
                        response = await _call_with_instance_fence(request, call_next)
    except Exception:
        # Never log request bodies, authorization headers or exception strings containing secrets.
        response = JSONResponse({'detail': 'Internal service error', 'request_id': request_id}, status_code=500)
    response.headers.update({'X-Request-ID': request_id, 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                             'Referrer-Policy': 'no-referrer', 'Strict-Transport-Security': 'max-age=31536000'})
    if request.url.path == '/' or request.url.path.startswith('/ui'):
        response.headers.update({
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
            'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), payment=(), usb=()',
            'Cross-Origin-Opener-Policy': 'same-origin',
            'X-Frame-Options': 'DENY',
        })
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    return JSONResponse({'detail': [{'loc': list(e['loc']), 'type': e['type'], 'msg': e['msg']} for e in error.errors()]}, status_code=422)


@app.exception_handler(IntegrityError)
async def conflict(request, error):
    return JSONResponse({'detail': 'Resource already exists or is still referenced'}, status_code=409)


# Backend modules are discovered from app/modules/feature_*.py.
# Adding a domain no longer requires editing this central application file.
for module in backend_modules():
    decorate_router(module.router)
    app.include_router(module.router, prefix=module.prefix)


@app.get('/', include_in_schema=False)
def web_console():
    return RedirectResponse('/ui/', status_code=307)


app.mount('/ui', StaticFiles(directory=Path(__file__).with_name('web'), html=True), name='web-console')


configure_telemetry(app)
