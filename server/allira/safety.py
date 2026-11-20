"""Small, explicit safeguards, not an assertion of automated content moderation coverage."""

import unicodedata

from fastapi import APIRouter

from .config import Settings
from .db import APIError
from .schemas import DTO

TERMS_VERSION = "2026-11-19"
TERMS_CONTENT = """AlliraAi 测试版使用说明（2026-11-19）
仅供年满 18 周岁的用户使用。AI 是虚构角色，不是真人，也不替代医疗、心理、法律或其他专业服务；回答可能错误。
请勿提交密码、访问密钥、金融凭据或不愿被服务端保存的隐私。聊天、导入原文、核心记忆在服务器保存；具备授权的管理员可显式查看聊天并留下访问审计。当前测试版未提供端到端加密。
禁止涉及未成年人色情、剥削、暴力威胁、违法犯罪协助及侵犯他人权益的内容。基础字符串过滤不是完整内容审核，不能作为合规保证。
经济/极速模式的能量是测试系统中的整数额度，不是货币；不出售、不提现、无支付或真实模型价格承诺。运行时预留固定额度，完成结算，失败或取消退回；未实际发起真实模型请求时仍用于验证流程。
规则摘要会有遗漏，不是完整记忆；导入孤立消息仅归档，只有真实成对历史进入对话上下文。可编辑核心记忆或删除会话。
服务中断或上游异常可能造成回答不完整。遇到危机或紧急状况请联系当地专业援助/紧急服务。上线前仍需隐私政策、业务地区法律与内容治理审查。
"""


class Terms(DTO):
    version: str = TERMS_VERSION
    adultOnly: bool = True
    content: str = TERMS_CONTENT


class ModelRoute(DTO):
    mode: str
    provider: str
    configured: bool
    energyCost: int
    fallbackTo: str | None


def check_input(text: str, settings: Settings) -> None:
    # Permit line breaks/tabs; reject invisible control channels. This is not a jailbreak detector.
    if any(unicodedata.category(character) in {"Cc", "Cs"} and character not in "\n\r\t" for character in text):
        raise APIError(422, "unsafe_control_character", "Input contains unsupported control characters.")
    normalized = unicodedata.normalize("NFKC", text).casefold()
    if any(unicodedata.normalize("NFKC", term).casefold() in normalized for term in settings.safety_blocked_terms if term):
        raise APIError(422, "content_blocked", "This content is blocked by the server's configured safety rules.")


def configured_routes(settings: Settings) -> list[ModelRoute]:
    """One configuration view shared by public information and administrator diagnostics."""
    fast_configured = settings.provider == "mock" or bool(settings.fast_upstream_model)
    return [
        ModelRoute(mode="economy", provider=settings.provider, configured=True,
                   energyCost=settings.economy_energy_cost, fallbackTo=None),
        ModelRoute(mode="fast", provider=settings.provider, configured=fast_configured,
                   energyCost=settings.fast_energy_cost,
                   fallbackTo="economy" if settings.fallback_to_economy else None),
    ]


def public_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["information"])

    @router.get("/terms", response_model=Terms)
    async def terms():
        return Terms()

    @router.get("/models", response_model=dict[str, list[ModelRoute]])
    async def routes():
        return {"items": configured_routes(settings)}

    return router
