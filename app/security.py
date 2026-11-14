import hashlib
import hmac
import secrets


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 600_000).hex()
    return f'pbkdf2_sha256${salt}${digest}'


def verify_password(password, encoded):
    try:
        scheme, salt, expected = encoded.split('$')
        if scheme != 'pbkdf2_sha256':
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 600_000).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def safe_error(error):
    # Never store arbitrary provider messages or HTTP URLs: they may contain credentials.
    from httpx import HTTPStatusError, RequestError
    from app.integrations import IntegrationError

    if type(error) is IntegrationError:
        return str(error)
    if isinstance(error, HTTPStatusError):
        return f'上游服务返回 HTTP {error.response.status_code}，请检查凭据及服务状态'
    if isinstance(error, RequestError):
        return '网络连接失败或超时，请检查服务地址及网络'
    if isinstance(error, ValueError):
        return '数据或配置无效，请检查采集结果、转写内容和服务配置'
    return f'处理失败（{type(error).__name__}），请检查服务配置后重试'
