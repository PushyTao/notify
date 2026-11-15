from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator
from email_validator import validate_email, EmailNotValidError


def http_url(value, allow_empty=False):
    if allow_empty and not value:
        return value
    parsed = urlparse(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('请输入无内嵌凭据的 HTTP(S) 地址')
    return value.rstrip('/')


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Login(StrictModel):
    password: str = Field(min_length=1, max_length=1024)


class PasswordChange(StrictModel):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


class Author(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    source_id: str = Field(min_length=1, max_length=500)
    provider: Literal['self_hosted', 'generic'] = 'self_hosted'
    enabled: bool = True
    interval_seconds: int = Field(default=600, ge=60, le=86400)
    backfill: int = Field(default=0, ge=0, le=100)

    @field_validator('source_id')
    @classmethod
    def normalize_source(cls, value):
        value = value.strip()
        if value.startswith('http'):
            parsed = urlparse(value)
            if parsed.scheme != 'https' or parsed.hostname not in ('www.douyin.com', 'douyin.com') or not parsed.path.startswith('/user/'):
                raise ValueError('请输入抖音完整用户主页地址或 sec_user_id')
            value = parsed.path.removeprefix('/user/').strip('/')
        if not value or not all(c.isalnum() or c in '_-' for c in value):
            raise ValueError('无效的 sec_user_id')
        return value


class EmailInput(StrictModel):
    email: str = Field(max_length=254)

    @field_validator('email')
    @classmethod
    def valid_email(cls, value):
        try:
            return validate_email(value, check_deliverability=False).normalized
        except EmailNotValidError as exc:
            raise ValueError('邮箱格式不正确') from exc


class Subscriber(EmailInput):
    enabled: bool = True
    author_ids: list[int] = Field(default_factory=list, max_length=1000)


class ModelConfig(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(max_length=2000)
    model_id: str = Field(min_length=1, max_length=200)
    api_key: str = Field(default='', max_length=4096)
    enabled: bool = False
    timeout_seconds: int = Field(default=120, ge=10, le=300)

    @field_validator('base_url')
    @classmethod
    def valid_url(cls, value):
        return http_url(value)


class CookieImport(StrictModel):
    cookie: str = Field(min_length=1, max_length=32768)
    user_agent: str = Field(min_length=1, max_length=2000)


def validate_settings(group, values):
    from app.config import DEFAULTS, SECRETS
    if group not in DEFAULTS or not values.keys() <= DEFAULTS[group].keys():
        raise ValueError('未知设置字段')
    for key, value in values.items():
        if key in ('port', 'max_pages'):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= (65535 if key == 'port' else 100):
                raise ValueError('端口或最大页数超出范围')
        elif not isinstance(value, str) or len(value) > (32768 if key in SECRETS else 16000):
            raise ValueError('设置值必须为字符串且不能过长')
        if key in ('base_url', 'third_party_url'):
            http_url(value, allow_empty=True)
        if key == 'tls' and value not in ('starttls', 'ssl', 'none'):
            raise ValueError('无效的 TLS 模式')
        if key == 'backend' and value not in ('local', 'api', 'mimo'):
            raise ValueError('无效的转写方式')
        if key == 'text' and not value.strip():
            raise ValueError('提示词不能为空')
        if key == 'from_email' and value:
            EmailInput(email=value)
        if key in ('host', 'username') and ('\n' in value or '\r' in value):
            raise ValueError('配置含非法换行')
    return values


def merge_asr_settings(current, values):
    merged = {**current, **{k: v for k, v in values.items() if k != 'api_key' or v}}
    if merged['backend'] != current['backend']:
        merged['api_key'] = values.get('api_key', '')
    if merged['backend'] == 'mimo':
        if merged['model'].strip() != 'mimo-v2.5-asr' or merged['language'].strip() not in ('', 'auto', 'zh', 'en'):
            raise ValueError('小米 ASR 模型或语言无效')
        if not merged['api_key'].strip():
            raise ValueError('小米 ASR 需要 API Key')
    return merged
