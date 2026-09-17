import uuid
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool
from app.api import administration, health, infrastructure
from app.auth import routes as auth
from app.config import settings
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
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    return JSONResponse({'detail': [{'loc': list(e['loc']), 'type': e['type'], 'msg': e['msg']} for e in error.errors()]}, status_code=422)


@app.exception_handler(IntegrityError)
async def conflict(request, error):
    return JSONResponse({'detail': 'Resource already exists or is still referenced'}, status_code=409)


for router in (auth.router, administration.router, infrastructure.router, health.router):
    app.include_router(router, prefix='/api/v1')
