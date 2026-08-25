"""PSKit Harness 收据的真实 Ed25519 验签边界。

本模块只接受受控 resolver 注入的公钥记录，不生成、接收或保存私钥。
签名 payload 使用稳定的 PSKit 领域分隔符和 ``ExecutionReceipt.to_dict()`` 的
确定性 JSON；``to_dict`` 已经完整包含 evidence、provenance 和 artifacts，且不含 proof。
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.harness.contracts import ExecutionReceipt
from app.harness.runtime import ReceiptVerification


ED25519_ALGORITHM = "ed25519"
RECEIPT_PROOF_VERSION = "v1"
# wzf：域分隔符防止把收据签名误用到其他 PSKit 消息类型；末尾 NUL 保持边界明确。
RECEIPT_SIGNATURE_DOMAIN = b"PSKit-Harness-Receipt:v1\x00"
RECEIPT_SIGNATURE_DOMAIN_SEPARATOR = RECEIPT_SIGNATURE_DOMAIN
DEFAULT_RECEIPT_MAX_AGE = timedelta(hours=24)
DEFAULT_RECEIPT_CLOCK_SKEW = timedelta(seconds=5)

_KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
_PROOF_PATTERN = re.compile(r"^ed25519\.v1\.([A-Za-z0-9_-]{1,160})\.([A-Za-z0-9_-]{86})$")
_BASE64URL_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def _require_text(value: object, field_name: str, *, maximum: int = 400) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    text = value.strip()
    if len(text) > maximum:
        raise ValueError(f"{field_name} must be at most {maximum} characters")
    return text


def _coerce_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class ReceiptVerificationKey:
    """受控 resolver 返回的公钥记录。

    ``public_key`` 必须是 32 字节 Ed25519 原始公钥。记录没有 private-key 字段；
    ``revoked``/``disabled`` 任一为真都会拒绝验签。
    """

    issuer: str
    key_id: str
    algorithm: str
    public_key: bytes
    not_before: datetime | None = None
    not_after: datetime | None = None
    revoked: bool = False
    disabled: bool = False

    def __post_init__(self) -> None:
        issuer = _require_text(self.issuer, "issuer")
        key_id = _require_text(self.key_id, "key_id", maximum=160)
        if _KEY_ID_PATTERN.fullmatch(key_id) is None:
            raise ValueError("key_id must contain only alphanumeric, '_' or '-'")
        algorithm = _require_text(self.algorithm, "algorithm", maximum=80).lower()
        if not isinstance(self.public_key, bytes):
            raise TypeError("public_key must be raw bytes")
        # Ed25519PublicKey.from_public_bytes performs the same size check; doing it here
        # makes invalid records fail at resolver boundaries instead of during verification.
        if len(self.public_key) != 32:
            raise ValueError("public_key must contain 32 raw bytes")
        if not isinstance(self.revoked, bool) or not isinstance(self.disabled, bool):
            raise TypeError("revoked and disabled must be bools")
        not_before = self.not_before
        not_after = self.not_after
        if not_before is not None:
            not_before = _coerce_utc(not_before, "not_before")
        if not_after is not None:
            not_after = _coerce_utc(not_after, "not_after")
        if not_before is not None and not_after is not None and not_before > not_after:
            raise ValueError("not_before must not be after not_after")
        object.__setattr__(self, "issuer", issuer)
        object.__setattr__(self, "key_id", key_id)
        object.__setattr__(self, "algorithm", algorithm)
        object.__setattr__(self, "public_key", bytes(self.public_key))
        object.__setattr__(self, "not_before", not_before)
        object.__setattr__(self, "not_after", not_after)


class ReceiptPublicKeyResolver(Protocol):
    """只读、受控的公钥解析协议；不得实现为裸 Mapping。"""

    def resolve(
        self, *, issuer: str, key_id: str, algorithm: str
    ) -> ReceiptVerificationKey | None: ...


def encode_receipt_proof(key_id: str, signature_bytes: bytes) -> str:
    """编码严格的 ``ed25519.v1.<key_id>.<base64url-signature>`` proof。

    该辅助函数只编码调用方已经获得的签名，不接收私钥，也不会生成签名。
    """

    key_id = _require_text(key_id, "key_id", maximum=160)
    if _KEY_ID_PATTERN.fullmatch(key_id) is None:
        raise ValueError("key_id must contain only alphanumeric, '_' or '-'")
    if not isinstance(signature_bytes, bytes) or len(signature_bytes) != 64:
        raise ValueError("signature_bytes must contain 64 raw bytes")
    encoded = base64.urlsafe_b64encode(signature_bytes).rstrip(b"=").decode("ascii")
    return f"{ED25519_ALGORITHM}.{RECEIPT_PROOF_VERSION}.{key_id}.{encoded}"


def _parse_receipt_proof(proof: object) -> tuple[str, str, bytes] | None:
    if not isinstance(proof, str) or _PROOF_PATTERN.fullmatch(proof) is None:
        return None
    parts = proof.split(".")
    if len(parts) != 4 or parts[0] != ED25519_ALGORITHM or parts[1] != RECEIPT_PROOF_VERSION:
        return None
    key_id, encoded = parts[2], parts[3]
    if not _BASE64URL_PATTERN.fullmatch(encoded):
        return None
    try:
        # Exact round-trip rejects non-canonical encodings, padding, and hidden bytes.
        padded = encoded + "=" * (-len(encoded) % 4)
        signature = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeError):
        return None
    if len(signature) != 64:
        return None
    if base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii") != encoded:
        return None
    return ED25519_ALGORITHM, key_id, signature


def receipt_signature_payload(receipt: ExecutionReceipt) -> bytes:
    """返回不含 proof 的确定性签名 payload。"""

    if not isinstance(receipt, ExecutionReceipt):
        raise TypeError("receipt must be an ExecutionReceipt")
    body = receipt.to_dict()
    if not isinstance(body, dict):
        raise TypeError("receipt.to_dict() must return a mapping")
    # Receipt 合约默认省略这些字段；显式移除可防止不受信任的适配器把 proof 带入 payload。
    body = dict(body)
    body.pop("authenticity_proof", None)
    body.pop("signature", None)
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return RECEIPT_SIGNATURE_DOMAIN + encoded


def _clock_now(clock: Callable[[], datetime] | object | None) -> datetime:
    if clock is None:
        return datetime.now(timezone.utc)
    now = clock() if callable(clock) else getattr(clock, "now")()
    return _coerce_utc(now, "clock.now()")


def _duration(value: timedelta | int | float, field_name: str) -> timedelta:
    if isinstance(value, bool):
        raise TypeError(f"{field_name} must be a duration")
    duration = value if isinstance(value, timedelta) else timedelta(seconds=float(value))
    if duration < timedelta(0):
        raise ValueError(f"{field_name} must be non-negative")
    return duration


class Ed25519ReceiptVerifier:
    """使用受控公钥 resolver 验证 ExecutionReceipt 的真实性。"""

    def __init__(
        self,
        resolver: ReceiptPublicKeyResolver,
        *,
        expected_issuer: str | None = None,
        issuer: str | None = None,
        max_age: timedelta | int | float = DEFAULT_RECEIPT_MAX_AGE,
        skew: timedelta | int | float = DEFAULT_RECEIPT_CLOCK_SKEW,
        clock: Callable[[], datetime] | object | None = None,
    ) -> None:
        # Protocols are not runtime-checkable; resolve is the only allowed boundary.
        if not callable(getattr(resolver, "resolve", None)):
            raise TypeError("resolver must implement resolve")
        if expected_issuer is not None and issuer is not None and expected_issuer != issuer:
            raise ValueError("expected_issuer and issuer disagree")
        configured_issuer = issuer if issuer is not None else expected_issuer
        self.expected_issuer = (
            _require_text(configured_issuer, "expected_issuer")
            if configured_issuer is not None
            else None
        )
        self.max_age = _duration(max_age, "max_age")
        self.skew = _duration(skew, "skew")
        self.resolver = resolver
        self.clock = clock

    def verify(self, receipt: ExecutionReceipt, **_context: object) -> ReceiptVerification:
        """验签失败统一返回稳定 reason，不返回 proof、key 或底层异常文本。"""

        try:
            if not isinstance(receipt, ExecutionReceipt):
                return ReceiptVerification(False, "invalid_receipt")
            if self.expected_issuer is not None and receipt.issuer != self.expected_issuer:
                return ReceiptVerification(False, "wrong_issuer")
            parsed = _parse_receipt_proof(receipt.authenticity_proof)
            if parsed is None:
                return ReceiptVerification(False, "malformed_proof")
            algorithm, key_id, signature = parsed
            try:
                key = self.resolver.resolve(
                    issuer=receipt.issuer,
                    key_id=key_id,
                    algorithm=algorithm,
                )
            except Exception:
                return ReceiptVerification(False, "resolver_error")
            if not isinstance(key, ReceiptVerificationKey):
                return ReceiptVerification(False, "unknown_key")
            if key.issuer != receipt.issuer or key.key_id != key_id:
                return ReceiptVerification(False, "key_binding_mismatch")
            if key.algorithm != algorithm:
                return ReceiptVerification(False, "wrong_algorithm")
            if key.revoked or key.disabled:
                return ReceiptVerification(False, "key_disabled")
            now = _clock_now(self.clock)
            issued_at = _coerce_utc(receipt.issued_at, "receipt.issued_at")
            if issued_at > now + self.skew:
                return ReceiptVerification(False, "future_receipt")
            if now - issued_at > self.max_age:
                return ReceiptVerification(False, "expired_receipt")
            if key.not_before is not None and (issued_at < key.not_before or now < key.not_before):
                return ReceiptVerification(False, "key_not_yet_valid")
            if key.not_after is not None and (issued_at > key.not_after or now > key.not_after):
                return ReceiptVerification(False, "key_expired")
            try:
                public_key = Ed25519PublicKey.from_public_bytes(key.public_key)
                public_key.verify(signature, receipt_signature_payload(receipt))
            except (InvalidSignature, ValueError, TypeError):
                return ReceiptVerification(False, "invalid_signature")
            return ReceiptVerification(True, "verified")
        except Exception:
            # 包括时钟、Receipt 序列化和 resolver 适配器的意外异常，统一 fail-closed。
            return ReceiptVerification(False, "verification_error")


# 便于调用方用简短稳定名称注入，但不改变真实实现。
ReceiptVerifier = Ed25519ReceiptVerifier


__all__ = [
    "DEFAULT_RECEIPT_CLOCK_SKEW",
    "DEFAULT_RECEIPT_MAX_AGE",
    "ED25519_ALGORITHM",
    "Ed25519ReceiptVerifier",
    "RECEIPT_PROOF_VERSION",
    "RECEIPT_SIGNATURE_DOMAIN",
    "RECEIPT_SIGNATURE_DOMAIN_SEPARATOR",
    "ReceiptPublicKeyResolver",
    "ReceiptVerification",
    "ReceiptVerificationKey",
    "ReceiptVerifier",
    "encode_receipt_proof",
    "receipt_signature_payload",
]
