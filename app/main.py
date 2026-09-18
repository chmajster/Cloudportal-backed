import uuid
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool
from app.api import administration, automation, health, infrastructure, inventory, ipam, operations, proxmox_management
from app.auth import routes as auth
from app.config import settings
from app.observability import configure_telemetry
from app.security.core import throttle

app = FastAPI(title='Cloudportal-backed', version='1.0.0', docs_url='/docs', redoc_url=None)


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
                        response = await call_next(request)
                else:
                    response = await call_next(request)
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


# Document headers on the source routers before inclusion. This works with both
# eager and lazy router inclusion in supported FastAPI versions.
from fastapi.routing import APIRoute

for router in (auth.router, administration.router, infrastructure.router, automation.router, inventory.router, ipam.router, operations.router, proxmox_management.router, health.router):
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        parameters = [{'name': 'X-Request-ID', 'in': 'header', 'required': False,
                       'schema': {'type': 'string', 'format': 'uuid'},
                       'description': 'Correlation ID returned in the response and recorded in jobs and audit.'}]
        is_creation = 'POST' in route.methods and (route.path.rsplit('/', 1)[-1] in
                      {'users', 'roles', 'tokens', 'credentials', 'providers', 'deployments', 'jobs', 'blueprints',
                       'hostname-schemes', 'generate', 'execute', 'reset-password', 'destroy'})
        is_destroy = 'DELETE' in route.methods and route.path == '/deployments/{id}'
        if (is_creation or is_destroy) and route.path != '/auth/reset-password':
            required = route.path in {'/deployments', '/jobs', '/blueprints/{id}/execute', '/deployments/{id}/destroy'} or is_destroy
            parameters.append({'name': 'Idempotency-Key', 'in': 'header', 'required': required,
                               'schema': {'type': 'string', 'format': 'uuid'},
                               'description': 'Reuse the same UUID only when retrying the same operation and payload.'})
        route.openapi_extra = {**(route.openapi_extra or {}), 'parameters': parameters}
    app.include_router(router, prefix='/api/v1')


@app.get('/', include_in_schema=False)
def web_console():
    return RedirectResponse('/ui/', status_code=307)


app.mount('/ui', StaticFiles(directory=Path(__file__).with_name('web'), html=True), name='web-console')


configure_telemetry(app)
