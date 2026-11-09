"""初始管理员的严格写入校验与已保存配置的初始化投影。"""

from dataclasses import dataclass
import re
import unicodedata

MAX_ADMINS = 100


@dataclass(frozen=True)
class AdminMember:
    """保留原值并提供权限成员列，不关联当前登录者。"""

    value: str
    member_id: str
    member_name: str
    legacy: bool = False


def parse_new_initial_administrator(value: str) -> AdminMember:
    """仅接受八位员工号与一至三十二码点姓名，保留员工号首零。"""

    if not isinstance(value, str):
        raise ValueError("初始管理员必须是字符串。")
    normalized = value.strip()
    if normalized == "current-user":
        raise ValueError("初始管理员不能使用 current-user。")
    parts = normalized.split("/")
    if len(parts) != 2 or not re.fullmatch(r"[0-9]{8}", parts[0]):
        raise ValueError("初始管理员请使用“8位员工号/姓名”格式。")
    name = parts[1]
    if (not 1 <= len(name) <= 32 or name != name.strip()
            or any(char in "/／,，" or unicodedata.category(char) in {"Cc", "Cs", "Zl", "Zp"} for char in name)):
        raise ValueError("初始管理员姓名须为1至32个字符，且不能有首尾空白、斜线、逗号或控制字符。")
    return AdminMember(value=normalized, member_id=parts[0], member_name=name)


def parse_for_initialization(value: str) -> AdminMember:
    """只供已保存值读取和首次初始化使用，旧值保留原身份。"""

    if not isinstance(value, str) or not value.strip() or value.strip() == "current-user":
        raise ValueError("初始管理员配置包含无效成员。")
    try:
        return parse_new_initial_administrator(value)
    except ValueError:
        return AdminMember(value=value, member_id=value, member_name=value, legacy=True)


def parse_admins_for_initialization(values: list[str]) -> list[AdminMember]:
    """按成员自然键去重并拒绝冲突，防止两行指向同一权限成员。"""

    if not isinstance(values, list) or len(values) > MAX_ADMINS:
        raise ValueError("初始管理员数量不得超过100。")
    members: dict[str, AdminMember] = {}
    for value in values:
        member = parse_for_initialization(value)
        previous = members.get(member.member_id)
        if previous and previous.value != member.value:
            raise ValueError("初始管理员员工号重复或与已有成员冲突，请替换原项。")
        members[member.member_id] = member
    return list(members.values())


def validate_admin_change(before: list[str], after: list[str]) -> list[str]:
    """仅原样保留的已保存项获得旧值资格，其余项严格校验。"""

    if not isinstance(after, list) or len(after) > MAX_ADMINS:
        raise ValueError("初始管理员数量不得超过100。")
    for value in after:
        if not isinstance(value, str) or not value.strip() or value.strip() == "current-user":
            raise ValueError("初始管理员配置包含无效成员，不能使用 current-user。")
    if after == before:
        return list(after)
    result: list[str] = []
    for value in after:
        normalized = value if isinstance(value, str) and value in before else parse_new_initial_administrator(value).value
        parse_for_initialization(normalized)
        if normalized not in result:
            result.append(normalized)
    parse_admins_for_initialization(result)
    return result


def validate_new_admins(values: list[str]) -> list[str]:
    """新建和首次启用时全部按新格式校验，不授予旧值资格。"""

    return validate_admin_change([], values)
