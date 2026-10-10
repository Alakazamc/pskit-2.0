from app.contracts.capabilities import McpInvokeResult, McpTool


class MockMcp:
    def tools(self) -> list[McpTool]:
        """Advertise the two local demonstration research tools."""
        return [
            McpTool(
                name="search_pdb", description="检索 PDB 中的结构",
                input_schema={"type": "object", "properties": {"query": {"type": "string"}},
                              "required": ["query"], "additionalProperties": False},
            ),
            McpTool(
                name="fetch_uniprot", description="查询 UniProt 蛋白信息",
                input_schema={"type": "object", "properties": {"accession": {"type": "string"}},
                              "required": ["accession"], "additionalProperties": False},
            ),
        ]

    async def invoke(self, name: str, arguments: dict) -> McpInvokeResult | None:
        """Return deterministic demo data for a known tool name."""
        if name == "search_pdb":
            return McpInvokeResult(
                tool=name,
                result={
                    "query": arguments.get("query", ""),
                    "hits": [{"pdb_id": "1A9N", "title": "Protein-RNA complex", "score": 0.94}],
                },
            )
        if name == "fetch_uniprot":
            return McpInvokeResult(
                tool=name,
                result={"accession": arguments.get("accession", "P12345"), "name": "Demo protein"},
            )
        return None
