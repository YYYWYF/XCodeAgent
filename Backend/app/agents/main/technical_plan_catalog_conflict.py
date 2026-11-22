"""携带技术目录冲突的双方身份和候选，供原生成节点联合修正。"""

from copy import deepcopy


class CatalogContractConflict(ValueError):
    """区分跨分组决策冲突与仅需重试当前片段的输出错误。"""

    def __init__(self, reason: str, contract_ids: list[str], contracts: list[dict]):
        """保存可定位的冲突范围，不从自然语言诊断反向解析分组身份。"""

        self.contract_ids = tuple(dict.fromkeys(contract_ids))
        self.contracts = deepcopy(contracts)
        super().__init__(reason)
