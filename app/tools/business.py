from app.application.dto import BusinessDataQueryResult, QueryBusinessDataCommand
from app.application.services import QueryBusinessDataService


class BusinessQueryTool:
    def __init__(self, service: QueryBusinessDataService) -> None:
        self._service = service

    async def invoke(self, command: QueryBusinessDataCommand) -> BusinessDataQueryResult:
        return await self._service.execute(command)
