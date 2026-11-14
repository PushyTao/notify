from starlette.responses import JSONResponse


class RequestSizeLimit:
    """Bound actual bytes before JSON parsing, including chunked requests."""
    def __init__(self, app, maximum=65536):
        self.app, self.maximum = app, maximum

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope['path'].startswith('/api/') or scope['method'] in ('GET','HEAD'):
            return await self.app(scope,receive,send)
        headers = dict(scope['headers'])
        declared = headers.get(b'content-length',b'0')
        if not declared.isdigit() or int(declared) > self.maximum:
            return await JSONResponse({'detail':'请求过大'},status_code=413)(scope,receive,send)
        data = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            chunk = message.get('body',b'')
            if len(data) + len(chunk) > self.maximum:
                return await JSONResponse({'detail':'请求过大'},status_code=413)(scope,receive,send)
            data.extend(chunk)
            if not message.get('more_body',False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type':'http.request','body':bytes(data),'more_body':False}
            return await receive()

        await self.app(scope,replay,send)
