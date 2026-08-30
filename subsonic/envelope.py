# OpenSubsonic JSON envelope for the Folia adapter (no XML).
# Folia baseline: 5f1c966daf25b414c94a866e4c16418b084a26f5

from typing import Any, Dict, Optional

from fastapi.responses import JSONResponse

API_VERSION = '1.16.1'
SERVER_TYPE = 'famyliam'
SERVER_VERSION = '0.1.0-folia-json'


def envelope_body(extra: Optional[Dict[str, Any]] = None, status: str = 'ok') -> Dict[str, Any]:
    body = {
        'status': status,
        'version': API_VERSION,
        'type': SERVER_TYPE,
        'serverVersion': SERVER_VERSION,
        'openSubsonic': True,
    }
    if extra:
        body.update(extra)
    return {'subsonic-response': body}


def ok_json(extra: Optional[Dict[str, Any]] = None) -> JSONResponse:
    return JSONResponse(content=envelope_body(extra, status='ok'))


def fail_json(code: int, message: str) -> JSONResponse:
    return JSONResponse(
        content=envelope_body(
            {
                'error': {
                    'code': int(code),
                    'message': str(message),
                }
            },
            status='failed',
        )
    )


# OpenSubsonic error codes used by this adapter:
# 10 missing parameter
# 40 auth failed
# 50 unauthorized (phase-1 mutations)
# 70 entity or media missing
# 0  unknown endpoint / other
CODE_GENERIC = 0
CODE_MISSING_PARAM = 10
CODE_AUTH = 40
CODE_UNAUTHORIZED = 50
CODE_NOT_FOUND = 70
